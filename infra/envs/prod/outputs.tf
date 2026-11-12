# Each of these becomes a GitHub Actions repository variable (see docs/DEPLOYMENT.md).
output "instance_id" {
  value = aws_instance.app.id
}

output "public_ip" {
  value = aws_eip.app.public_ip
}

output "registry" {
  value = local.registry
}

output "release_bucket" {
  value = aws_s3_bucket.releases.bucket
}

output "deploy_role_arn" {
  value = aws_iam_role.gha_deploy.arn
}
