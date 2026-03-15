variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ca-central-1"
}

variable "project_name" {
  description = "Prefixe des ressources AWS"
  type        = string
  default     = "tlc-mlops"
}

variable "instance_type" {
  description = "Type d'instance EC2"
  type        = string
  default     = "m6i.large"
}

variable "allowed_cidr" {
  description = "CIDR autorise pour SSH et Grafana, ex: x.x.x.x/32"
  type        = string
}

variable "key_pair_name" {
  description = "Nom de la key pair AWS existante"
  type        = string
}

variable "ssh_private_key_path" {
  description = "Chemin local vers la cle privee utilisee par Ansible"
  type        = string
}
