# Inputs for the reference AWS deployment. Everything has a sane default
# except the domain: TLS is not optional for this server (it moves passwords
# and bearer tokens), and Caddy needs a real name to get a certificate for.

variable "domain" {
  description = "Fully qualified name the console will answer on (e.g. trace.example.com). Must resolve to the instance's Elastic IP before Caddy can issue a certificate."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9.-]+\\.[a-z]{2,}$", var.domain))
    error_message = "domain must be a fully qualified hostname, e.g. trace.example.com."
  }
}

variable "aws_region" {
  description = "Region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix applied to every resource name and Name tag."
  type        = string
  default     = "gaide-trace"
}

variable "instance_type" {
  description = "EC2 instance type. Must be arm64 (Graviton) — the AMI looked up below is arm64."
  type        = string
  default     = "t4g.small"
}

variable "data_volume_size" {
  description = "Size in GiB of the EBS volume holding the JSONL archive, transcripts and the SQLite index. Transcript snapshots dominate this; grow it before it fills."
  type        = number
  default     = 20
}

variable "ssh_allowed_cidrs" {
  description = "CIDRs allowed to reach port 22. Empty by default: the instance is reachable through SSM Session Manager, which needs no open port and leaves an audit trail."
  type        = list(string)
  default     = []
}

variable "repo_url" {
  description = "Git remote the instance clones the server from."
  type        = string
  default     = "https://github.com/jcarlos78/GAIDE-Trace"
}

variable "repo_ref" {
  description = "Branch or tag to deploy. Pin a tag for anything you care about — 'main' redeploys whatever is current at boot."
  type        = string
  default     = "main"
}

variable "route53_zone_id" {
  description = "Optional Route 53 hosted zone id. When set, the A record for var.domain is created automatically; when empty, point the record at the output IP yourself."
  type        = string
  default     = ""
}

variable "enable_backups" {
  description = "Create the S3 bucket and the nightly archive sync timer."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Extra tags merged into every resource."
  type        = map(string)
  default     = {}
}
