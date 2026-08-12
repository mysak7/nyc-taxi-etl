output "ecr_repository" {
  value = aws_ecr_repository.app.repository_url
}

output "bucket" {
  value = aws_s3_bucket.data.id
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.pipeline.arn
}

output "ci_role_arn" {
  description = "Patří do .github/workflows/ci.yml jako role-to-assume."
  value       = aws_iam_role.ci.arn
}

output "operator_role_arn" {
  description = "aws sts assume-role --role-arn <tohle> --role-session-name run"
  value       = aws_iam_role.operator.arn
}

output "web_role_arn" {
  description = "Patří do .github/workflows/web.yml jako role-to-assume."
  value       = aws_iam_role.web.arn
}
