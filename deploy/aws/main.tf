# Reference AWS deployment for the GAIDE-Trace team server.
#
# Shape: one small Graviton instance with a dedicated EBS data volume, Caddy
# terminating TLS in front of the server on 127.0.0.1:8321, nightly sync of the
# JSONL archive to S3.
#
# Why a VM and not Lambda/App Runner/Fargate: the server's truth layer is an
# append-only directory of JSONL files plus a single-writer SQLite index (D6/D7
# in docs/ARCHITECTURE.md). That needs one process with one persistent local
# disk. Serverless compute has neither, and SQLite over EFS trades correctness
# for elasticity this workload never uses.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge({
      Project   = "gaide-trace"
      ManagedBy = "terraform"
    }, var.tags)
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

# Canonical publishes the current Ubuntu AMI ids as public SSM parameters, so
# the deployment never pins a stale image id per region.
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id"
}

locals {
  az     = data.aws_availability_zones.available.names[0]
  prefix = var.name_prefix
}

resource "random_id" "suffix" {
  byte_length = 4
}

# --- Network -----------------------------------------------------------------
# A dedicated VPC rather than the default one: accounts often have no default
# VPC, and a self-contained network makes `terraform destroy` actually complete.

resource "aws_vpc" "this" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = local.prefix }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = local.prefix }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = "10.42.1.0/24"
  availability_zone       = local.az
  map_public_ip_on_launch = true

  tags = { Name = "${local.prefix}-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "${local.prefix}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "server" {
  name        = "${local.prefix}-server"
  description = "GAIDE-Trace server: public HTTP/HTTPS, optional SSH"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${local.prefix}-server" }
}

# Port 80 stays open because Caddy needs the ACME HTTP-01 challenge to issue and
# renew the certificate; it serves nothing else but the redirect to 443.
resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.server.id
  description       = "ACME HTTP-01 challenge and redirect to HTTPS"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.server.id
  description       = "Console and ingest API"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  for_each = toset(var.ssh_allowed_cidrs)

  security_group_id = aws_security_group.server.id
  description       = "SSH"
  cidr_ipv4         = each.value
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.server.id
  description       = "Package installs, ACME, S3 backups"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- Backups -----------------------------------------------------------------

resource "aws_s3_bucket" "backup" {
  count = var.enable_backups ? 1 : 0

  bucket = "${local.prefix}-backup-${random_id.suffix.hex}"
}

resource "aws_s3_bucket_public_access_block" "backup" {
  count = var.enable_backups ? 1 : 0

  bucket                  = aws_s3_bucket.backup[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "backup" {
  count = var.enable_backups ? 1 : 0

  bucket = aws_s3_bucket.backup[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backup" {
  count = var.enable_backups ? 1 : 0

  bucket = aws_s3_bucket.backup[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "backup" {
  count = var.enable_backups ? 1 : 0

  bucket = aws_s3_bucket.backup[0].id

  rule {
    id     = "expire-noncurrent"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# --- Instance identity -------------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "server" {
  name               = "${local.prefix}-server-${random_id.suffix.hex}"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# Session Manager instead of a key pair: shell access without an open port, and
# every session is logged against a real IAM identity.
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.server.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "backup" {
  count = var.enable_backups ? 1 : 0

  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.backup[0].arn]
  }

  # No DeleteObject: the instance pushes an append-only archive, so a
  # compromised server must not be able to erase its own history off-box.
  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.backup[0].arn}/*"]
  }
}

resource "aws_iam_role_policy" "backup" {
  count = var.enable_backups ? 1 : 0

  name   = "backup"
  role   = aws_iam_role.server.id
  policy = data.aws_iam_policy_document.backup[0].json
}

resource "aws_iam_instance_profile" "server" {
  name = "${local.prefix}-server-${random_id.suffix.hex}"
  role = aws_iam_role.server.name
}

# --- Compute and storage -----------------------------------------------------

# The archive lives on its own volume so the instance stays disposable: a
# rebuild, a resize or an instance-type change never touches the data.
resource "aws_ebs_volume" "data" {
  availability_zone = local.az
  size              = var.data_volume_size
  type              = "gp3"
  encrypted         = true

  tags = { Name = "${local.prefix}-data" }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_instance" "server" {
  ami                    = data.aws_ssm_parameter.ubuntu.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.server.id]
  iam_instance_profile   = aws_iam_instance_profile.server.name

  root_block_device {
    volume_type = "gp3"
    volume_size = 12
    encrypted   = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  user_data_replace_on_change = true
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    domain = var.domain
    # The EBS volume shows up under an unpredictable NVMe name, but its serial
    # always carries the volume id with the dash stripped.
    data_volume_serial = replace(aws_ebs_volume.data.id, "-", "")
    repo_url           = var.repo_url
    repo_ref           = var.repo_ref
    backup_enabled     = var.enable_backups
    backup_bucket      = var.enable_backups ? aws_s3_bucket.backup[0].bucket : ""
    aws_region         = var.aws_region
  })

  tags = { Name = local.prefix }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.server.id
}

# A stable address so the DNS record survives instance replacement.
resource "aws_eip" "server" {
  domain   = "vpc"
  instance = aws_instance.server.id

  tags = { Name = local.prefix }

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route53_record" "server" {
  count = var.route53_zone_id == "" ? 0 : 1

  zone_id = var.route53_zone_id
  name    = var.domain
  type    = "A"
  ttl     = 300
  records = [aws_eip.server.public_ip]
}
