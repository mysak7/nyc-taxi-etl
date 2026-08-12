terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Stav je lokální: tenhle stack je jediný svého druhu a nikdo jiný ho neaplikuje.
  # V týmu by tu byl S3 backend se zámkem -- jinak dva souběžné `apply` přepíšou stav.
  # backend "s3" {
  #   bucket       = "nyc-taxi-etl-tfstate-<account-id>"
  #   key          = "infra/terraform.tfstate"
  #   region       = "eu-central-1"
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      project   = var.project
      managedby = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  name   = var.project
  bucket = "${var.project}-${data.aws_caller_identity.current.account_id}"
  image  = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
}

# ---------------------------------------------------------------------------
# Úložiště. Jeden bucket, tři prefixy -- vrstvy se liší cestou, ne účtem.
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "data" {
  bucket = local.bucket
}

# Partition se přepisuje na místě (jedna partition = jeden soubor). Verzování je proto
# jediná věc, která odděluje "přepsali jsme leden novou verzí zdroje" od "přišli jsme
# o leden": role pipeline nemá právo mazat, takže staré verze přežijí i chybu v kódu.
resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_policy" "data" {
  bucket = aws_s3_bucket.data.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Bucket policy platí i pro admina; role pipeline tím není jediná pojistka.
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })
  depends_on = [aws_s3_bucket_public_access_block.data]
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  # Raw je 56 MB/měsíc a dá se kdykoli stáhnout znovu (ETag i sha256 jsou v manifestu),
  # takže staré verze držet nemá cenu. Curated je za 29 měsíců 4,6 MB -- tam se
  # nešetří, tam se archivuje.
  rule {
    id     = "raw-noncurrent"
    status = "Enabled"
    filter { prefix = "raw/" }
    noncurrent_version_expiration { noncurrent_days = 7 }
    transition {
      days          = 60
      storage_class = "STANDARD_IA"
    }
  }

  rule {
    id     = "curated-noncurrent"
    status = "Enabled"
    filter { prefix = "curated/" }
    noncurrent_version_expiration { noncurrent_days = 90 }
  }

  rule {
    id     = "abort-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 3 }
  }

  depends_on = [aws_s3_bucket_versioning.data]
}

# ---------------------------------------------------------------------------
# Image. Lambda umí táhnout jen z ECR ve stejném účtu a regionu, GHCR nestačí.
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "app" {
  name = var.project

  # Tag se nedá přepsat, takže `sha-abc123` znamená pořád tentýž image. Proto se do
  # ECR pushuje jen tag `sha-<git sha>`; pohyblivý `latest` zůstává v GHCR.
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Untagged image je zbytek po přerušeném pushi."
        selection    = { tagStatus = "untagged", countType = "sinceImagePushed", countUnit = "days", countNumber = 1 }
        action       = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Deset verzí zpátky stačí na rollback; starší jsou jen 300 MB navíc."
        selection    = { tagStatus = "tagged", tagPrefixList = ["sha-"], countType = "imageCountMoreThan", countNumber = 10 }
        action       = { type = "expire" }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Výpočet. Týž image jako lokálně, jen s přebitým entrypointem.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 30
}

resource "aws_lambda_function" "app" {
  function_name = local.name
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = local.image

  # Naměřeno: 6 z 20 sloupců = 146 MB, k tomu 56 MB stažený soubor. 3 GB je rezerva
  # 3x -- a protože CPU na Lambdě roste s pamětí, je to zároveň nejlevnější varianta:
  # dvojnásobná paměť za poloviční čas stojí totéž a doběhne dřív.
  memory_size = 3008
  timeout     = 600

  # Strop nákladů i dopadu: zaseknutý scheduler nevyrobí sto souběžných běhů.
  # Dva mapované měsíce + freshness = 3, jeden navíc na ruční spuštění.
  reserved_concurrent_executions = 4

  # Image nespouští argv, ale handler. Kompromis nezůstává v image, ale tady.
  image_config {
    entry_point = ["python", "-m", "awslambdaric"]
    command     = ["app.lambda_handler.handler"]
  }

  environment {
    variables = {
      APP_RAW_URI     = "s3://${aws_s3_bucket.data.id}/raw"
      APP_CURATED_URI = "s3://${aws_s3_bucket.data.id}/curated"
      APP_REJECTS_URI = "s3://${aws_s3_bucket.data.id}/rejects"
      GIT_SHA         = trimprefix(var.image_tag, "sha-")
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}
