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

output "ssh_command" {
  description = "Commande SSH"
  value       = "ssh -i ${var.ssh_private_key_path} ubuntu@${aws_eip.mlops_server.public_ip}"
}

output "ssh_mlflow_tunnel" {
  description = "Tunnel SSH pour MLflow"
  value       = "ssh -i ${var.ssh_private_key_path} -N -L 5000:127.0.0.1:5000 ubuntu@${aws_eip.mlops_server.public_ip}"
}
