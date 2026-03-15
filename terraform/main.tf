provider "aws" {
  region = var.aws_region
}

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

resource "local_file" "ansible_inventory" {
  content = templatefile("${path.module}/inventory.tftpl", {
    server_ip            = aws_eip.mlops_server.public_ip
    ssh_private_key_path = var.ssh_private_key_path
    s3_bucket            = aws_s3_bucket.mlops.id
    aws_region           = var.aws_region
  })
  filename = "${path.module}/../ansible/inventory.ini"
}
