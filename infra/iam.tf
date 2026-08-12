# Čtyři role, čtyři různé věci, které smí. Nikdo nemá "spouštět i měnit": kdo smí
# pipeline spustit, nesmí měnit, co dělá; kdo smí měnit kód, nesmí sahat na data.

# ---------------------------------------------------------------------------
# Lambda. Tohle je skutečný blast radius: co dokáže kompromitovaná pipeline.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "lambda" {
  name = "${local.name}-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda" {
  name = "data-and-logs"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.lambda.arn}:*"
      },
      {
        # Žádná AWSLambdaBasicExecutionRole: ta dává logs:* na celý účet.
        Sid      = "DataObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["${aws_s3_bucket.data.arn}/raw/*", "${aws_s3_bucket.data.arn}/curated/*", "${aws_s3_bucket.data.arn}/rejects/*"]
      },
      {
        # ListBucket je právo na bucket, ne na objekt -- bez podmínky by pipeline
        # viděla i prefixy, ke kterým nemá číst.
        Sid       = "ListOwnPrefixes"
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = aws_s3_bucket.data.arn
        Condition = { StringLike = { "s3:prefix" = ["raw/*", "curated/*", "rejects/*"] } }
      },
      {
        # Pipeline nikdy nemaže: přepis partition je PutObject přes tentýž klíč.
        # Explicitní Deny, ne "prostě to nedáme" -- tohle přežije i policy, kterou
        # k roli za rok někdo přilepí navrch.
        Sid      = "NeverDelete"
        Effect   = "Deny"
        Action   = ["s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutBucketVersioning", "s3:PutLifecycleConfiguration"]
        Resource = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Step Functions a scheduler: každý smí přesně jedno volání na jeden zdroj.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "states" {
  name = "${local.name}-states"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

resource "aws_iam_role_policy" "states" {
  name = "invoke-and-log"
  role = aws_iam_role.states.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = [aws_lambda_function.app.arn, "${aws_lambda_function.app.arn}:*"]
      },
      {
        # Vended logs vyžadují Resource "*" -- CloudWatch je vyhodnocuje na úrovni
        # účtu, ne log groupy. Doložené omezení služby, ne neochota to dopsat.
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role" "scheduler" {
  name = "${local.name}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "start-execution"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "states:StartExecution"
      Resource = aws_sfn_state_machine.pipeline.arn
    }]
  })
}

# ---------------------------------------------------------------------------
# CI. Žádný přístupový klíč v GitHub Secrets: OIDC dá token na 15 minut.
# ---------------------------------------------------------------------------

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_role" "ci" {
  name                 = "${local.name}-ci"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          # Přesná shoda, ne StringLike s hvězdičkou: `sub` obsahuje jméno
          # repozitáře i větve, takže tuhle roli nepřevezme fork ani pull request
          # z cizího repozitáře -- a to je jediné, co brání komukoli na GitHubu
          # pushnout si sem vlastní image.
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:ref:${var.github_ref}"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "ci" {
  name = "push-and-deploy"
  role = aws_iam_role.ci.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrLogin"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*" # API bere jen "*", token je stejně bez oprávnění k repozitáři
      },
      {
        Sid    = "PushImage"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
        ]
        Resource = aws_ecr_repository.app.arn
      },
      {
        # CI nasazuje kód, ne konfiguraci. UpdateFunctionConfiguration by dovolilo
        # přepsat APP_*_URI -- tedy odklonit celou pipeline jinam bez jediné změny
        # v image. To patří do terraformu, kde je to vidět v diffu.
        Sid      = "DeployCode"
        Effect   = "Allow"
        Action   = "lambda:UpdateFunctionCode"
        Resource = aws_lambda_function.app.arn
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Operátor: kdo smí pipeline spustit. Nesmí nic, co by změnilo, co pipeline dělá.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "operator" {
  name                 = "${local.name}-operator"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        AWS = length(var.operator_principals) > 0 ? var.operator_principals : ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
      }
      Action = "sts:AssumeRole"
      # Dočasné credentials z MFA session, ne trvalý klíč v ~/.aws/credentials.
      Condition = var.require_mfa ? { Bool = { "aws:MultiFactorAuthPresent" = "true" } } : {}
    }]
  })
}

resource "aws_iam_role_policy" "operator" {
  name = "run-and-read"
  role = aws_iam_role.operator.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Jediná páka, kterou operátor má, je vstup exekuce: rozsah měsíců a force.
        # Definici stroje ani prostředí Lambdy měnit nemůže, takže "spustit
        # pipeline" nedokáže víc, než co pipeline dělá každou noc sama.
        Sid      = "RunPipeline"
        Effect   = "Allow"
        Action   = ["states:StartExecution", "states:DescribeStateMachine", "states:ListExecutions"]
        Resource = aws_sfn_state_machine.pipeline.arn
      },
      {
        Sid      = "WatchExecution"
        Effect   = "Allow"
        Action   = ["states:DescribeExecution", "states:GetExecutionHistory", "states:StopExecution"]
        Resource = "arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:execution:${aws_sfn_state_machine.pipeline.name}:*"
      },
      {
        Sid      = "ReadLogs"
        Effect   = "Allow"
        Action   = ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"]
        Resource = ["${aws_cloudwatch_log_group.lambda.arn}:*", "${aws_cloudwatch_log_group.states.arn}:*"]
      },
      {
        # Výstup ano, raw ne: v raw jsou surová data zdroje, v curated agregát.
        Sid      = "ReadCurated"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.data.arn}/curated/*"
      },
      {
        Sid       = "ListCurated"
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = aws_s3_bucket.data.arn
        Condition = { StringLike = { "s3:prefix" = "curated/*" } }
      },
      {
        # Hranice role napsaná explicitně, aby se dala přečíst bez odvozování
        # z toho, co v Allow chybí.
        Sid    = "NotAnAdmin"
        Effect = "Deny"
        Action = [
          "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration", "lambda:InvokeFunction",
          "states:UpdateStateMachine", "states:CreateStateMachine", "states:DeleteStateMachine",
          "s3:PutObject", "s3:DeleteObject", "iam:*", "ecr:PutImage",
        ]
        Resource = "*"
      },
    ]
  })
}
