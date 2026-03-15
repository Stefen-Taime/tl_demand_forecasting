provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

locals {
  github_actions_environments = length(var.github_environments) > 0 ? sort(tolist(var.github_environments)) : (
    var.github_environment != null && trimspace(var.github_environment) != "" ? [trimspace(var.github_environment)] : []
  )
  github_actions_oidc_enabled = var.enable_github_actions_oidc && trimspace(var.github_repository) != "" && length(local.github_actions_environments) > 0
  github_actions_subjects = [
    for environment_name in local.github_actions_environments :
    "repo:${var.github_repository}:environment:${environment_name}"
  ]
}

resource "aws_s3_bucket" "mlops" {
  bucket = "${var.project_name}-${random_id.bucket_suffix.hex}"

  tags = {
    Project   = var.project_name
    ManagedBy = "terraform"
  }
}

resource "aws_s3_bucket_versioning" "mlops" {
  bucket = aws_s3_bucket.mlops.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "mlops" {
  bucket = aws_s3_bucket.mlops.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "mlops" {
  bucket                  = aws_s3_bucket.mlops.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "folders" {
  for_each = toset(["raw/", "features/", "holdout/", "mlflow-artifacts/", "reports/"])
  bucket   = aws_s3_bucket.mlops.id
  key      = each.value
  content  = ""
}

resource "aws_iam_role" "ec2_mlops" {
  name = "${var.project_name}-ec2-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRole"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_openid_connect_provider" "github_actions" {
  count = local.github_actions_oidc_enabled ? 1 : 0

  url = "https://token.actions.githubusercontent.com"
  client_id_list = [
    "sts.amazonaws.com"
  ]
  # AWS ignores the thumbprint for GitHub's OIDC endpoint, but Terraform still requires a value.
  thumbprint_list = [
    "ffffffffffffffffffffffffffffffffffffffff"
  ]
}

resource "aws_iam_role" "github_actions_deployer" {
  count = local.github_actions_oidc_enabled ? 1 : 0

  name = "${var.project_name}-github-actions-deployer"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRoleWithWebIdentity"
      Principal = {
        Federated = aws_iam_openid_connect_provider.github_actions[0].arn
      }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        "ForAnyValue:StringEquals" = {
          "token.actions.githubusercontent.com:sub" = local.github_actions_subjects
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions_deployer" {
  count = local.github_actions_oidc_enabled ? 1 : 0

  name = "${var.project_name}-github-actions-deployer-policy"
  role = aws_iam_role.github_actions_deployer[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:AllocateAddress",
          "ec2:AssociateAddress",
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:CreateSecurityGroup",
          "ec2:CreateTags",
          "ec2:DeleteSecurityGroup",
          "ec2:DeleteTags",
          "ec2:Describe*",
          "ec2:DisassociateAddress",
          "ec2:ModifyInstanceAttribute",
          "ec2:ReleaseAddress",
          "ec2:RevokeSecurityGroupIngress",
          "ec2:RunInstances",
          "ec2:StartInstances",
          "ec2:StopInstances",
          "ec2:TerminateInstances"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:CreateBucket",
          "s3:DeleteBucketPolicy",
          "s3:DeleteBucket",
          "s3:DeleteObject",
          "s3:Get*",
          "s3:ListAllMyBuckets",
          "s3:ListBucket",
          "s3:PutBucketPublicAccessBlock",
          "s3:PutBucketTagging",
          "s3:PutBucketVersioning",
          "s3:PutEncryptionConfiguration",
          "s3:PutObject"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "iam:AddRoleToInstanceProfile",
          "iam:CreateInstanceProfile",
          "iam:CreateOpenIDConnectProvider",
          "iam:CreateRole",
          "iam:DeleteInstanceProfile",
          "iam:DeleteOpenIDConnectProvider",
          "iam:DeleteRole",
          "iam:DeleteRolePolicy",
          "iam:GetInstanceProfile",
          "iam:GetOpenIDConnectProvider",
          "iam:GetRole",
          "iam:GetRolePolicy",
          "iam:ListAttachedRolePolicies",
          "iam:ListInstanceProfilesForRole",
          "iam:ListOpenIDConnectProviders",
          "iam:ListRolePolicies",
          "iam:PassRole",
          "iam:PutRolePolicy",
          "iam:RemoveRoleFromInstanceProfile",
          "iam:TagOpenIDConnectProvider",
          "iam:TagRole",
          "iam:UntagOpenIDConnectProvider",
          "iam:UntagRole",
          "iam:UpdateAssumeRolePolicy"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:DeleteItem",
          "dynamodb:DescribeTable",
          "dynamodb:GetItem",
          "dynamodb:PutItem"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "sts:GetCallerIdentity"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "ec2_mlops" {
  name = "${var.project_name}-ec2-policy"
  role = aws_iam_role.ec2_mlops.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = [
          aws_s3_bucket.mlops.arn,
          "${aws_s3_bucket.mlops.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "ec2_mlops" {
  name = "${var.project_name}-instance-profile"
  role = aws_iam_role.ec2_mlops.name
}

resource "aws_security_group" "mlops" {
  name        = "${var.project_name}-sg"
  description = "Security group for Grafana and SSH"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_cidr]
    description = "SSH"
  }

  ingress {
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = [var.allowed_cidr]
    description = "Grafana"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Project = var.project_name
  }
}

resource "aws_instance" "mlops_server" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = data.aws_subnets.default.ids[0]
  vpc_security_group_ids      = [aws_security_group.mlops.id]
  key_name                    = var.key_pair_name
  iam_instance_profile        = aws_iam_instance_profile.ec2_mlops.name
  associate_public_ip_address = true

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  tags = {
    Name      = "${var.project_name}-server"
    Project   = var.project_name
    ManagedBy = "terraform"
  }
}

resource "aws_eip" "mlops_server" {
  instance = aws_instance.mlops_server.id
  domain   = "vpc"

  tags = {
    Project = var.project_name
  }
}
