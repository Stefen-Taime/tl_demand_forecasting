output "server_ip" {
  description = "IP publique de l'EC2"
  value       = aws_eip.mlops_server.public_ip
}

output "grafana_url" {
  description = "URL Grafana"
  value       = "http://${aws_eip.mlops_server.public_ip}:3000"
}

output "s3_bucket" {
  description = "Nom du bucket S3"
  value       = aws_s3_bucket.mlops.id
}

output "aws_region" {
  description = "Region AWS de l'environnement"
  value       = var.aws_region
}

output "security_group_id" {
  description = "Security group utilise par l'EC2"
  value       = aws_security_group.mlops.id
}

output "github_actions_role_arn" {
  description = "ARN du role IAM assume par GitHub Actions"
  value       = local.github_actions_oidc_enabled ? aws_iam_role.github_actions_deployer[0].arn : null
}

output "ssh_host" {
  description = "Cible SSH de l'instance"
  value       = "ubuntu@${aws_eip.mlops_server.public_ip}"
}
