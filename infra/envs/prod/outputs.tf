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

# The demo host's public IP goes in DNS for var.demo_domain; its private IP goes in the
# merchant source URLs SettleIQ's connectors use (docs/DEMO.md).
output "demo_instance_id" {
  value = aws_instance.demo.id
}

output "demo_public_ip" {
  value = aws_eip.demo.public_ip
}

output "demo_private_ip" {
  value = aws_instance.demo.private_ip
}
