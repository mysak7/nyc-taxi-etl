variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "project" {
  type    = string
  default = "nyc-taxi-etl"
}

# Lambda se nasazuje na konkrétní tag, ne na `latest`. Repozitář v ECR je IMMUTABLE,
# takže `sha-<git sha>` je navždy tentýž image -- to, co prošlo CI, je to, co běží.
variable "image_tag" {
  type = string
}

variable "github_repo" {
  type    = string
  default = "mysak7/nyc-taxi-etl"
}

# Roli pro CI smí převzít jen tahle větev tohohle repozitáře. `repo:*/*` v podmínce
# `sub` je klasická díra: pak si roli převezme workflow z libovolného repozitáře na
# GitHubu.
variable "github_ref" {
  type    = string
  default = "refs/heads/master"
}

# Immutable podoba téhož: `gh api /repos/<repo>/actions/oidc/customization/sub`
# vrátí ji v `sub_claim_prefix`.
variable "github_repo_id" {
  type    = string
  default = "mysak7@205718209/nyc-taxi-etl@1331874799"
}

# Kdo smí pipeline spouštět. Prázdné = kdokoli z tohohle účtu, koho na roli pustí jeho
# vlastní IAM policy (a MFA). Produkčně sem patří konkrétní ARN.
variable "operator_principals" {
  type    = list(string)
  default = []
}

variable "require_mfa" {
  type    = bool
  default = true
}

variable "schedule_expression" {
  type    = string
  default = "cron(0 5 * * ? *)"
}

variable "alert_email" {
  type    = string
  default = ""
}
