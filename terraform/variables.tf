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
  description = "Chemin local vers la cle privee utilisee par Ansible. Deprecated: ne plus utiliser dans le tfstate partage."
  type        = string
  default     = null
  nullable    = true
}

variable "enable_github_actions_oidc" {
  description = "Active le role IAM assume par GitHub Actions via OIDC"
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "Repository GitHub autorise a assumer le role OIDC, format owner/repo"
  type        = string
  default     = ""
}

variable "github_environment" {
  description = "Environnement GitHub unique autorise a assumer le role OIDC. Deprecated au profit de github_environments."
  type        = string
  default     = null
  nullable    = true
}

variable "github_environments" {
  description = "Environnements GitHub proteges autorises a assumer le role OIDC"
  type        = set(string)
  default     = []
}
