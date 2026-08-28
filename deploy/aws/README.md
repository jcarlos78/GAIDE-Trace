# GAIDE-Trace on AWS

Terraform for a single-tenant team server: one Graviton instance, a dedicated
EBS volume for the archive, Caddy terminating TLS, and a nightly sync of the
JSONL truth layer to S3.

## Why this shape

The server's source of truth is an append-only directory of JSONL files, with
SQLite as a rebuildable index (`docs/ARCHITECTURE.md`, D6/D7). One process, one
persistent local disk. Lambda and App Runner have no persistent disk at all,
and SQLite over EFS swaps correctness for an elasticity this workload never
needs — so the deployment is a VM, deliberately.

The instance is disposable and the data is not: the archive lives on its own
EBS volume (`prevent_destroy`), so rebuilding, resizing or changing instance
type never touches it.

## Cost

Roughly **US$ 20/month** in `us-east-1`, on-demand:

| Item | Monthly |
|---|---|
| `t4g.small` (2 vCPU, 2 GiB) | ~$12 |
| 12 GiB gp3 root + 20 GiB gp3 data | ~$2.60 |
| Elastic IP (attached) | ~$3.60 |
| S3 backup + egress | <$1 |

`t4g.micro` (~$6) runs it fine for a small team — set `instance_type`.

## Prerequisites

- Terraform ≥ 1.5 and valid AWS credentials (`aws sts get-caller-identity`).
- A domain you control. TLS is not optional here: the console and the ingest
  API both move bearer credentials.

## Deploy

```bash
cd deploy/aws
cp terraform.tfvars.example terraform.tfvars   # edit: domain, region
terraform init
terraform apply
```

Then point `domain` at the `public_ip` output with an A record — or set
`route53_zone_id` and let Terraform create it. Caddy issues the certificate on
the first request once DNS resolves; give propagation a minute.

Open `https://<domain>/` and sign in as **admin / admin**. The console forces a
password change before anything else works — do it immediately, because the
default account is reachable from the internet the moment DNS resolves.

## Operating it

```bash
# root shell, no SSH and no open port (the ssh_allowed_cidrs rule stays empty)
aws ssm start-session --target <instance-id>

sudo tail -f /var/log/gaide-trace-bootstrap.log    # first-boot progress
systemctl status gaide-trace-server caddy
sudo systemctl start gaide-trace-backup.service    # force a backup now
```

Upgrading: bump `repo_ref` to a tag and `terraform apply`. That replaces the
instance (`user_data_replace_on_change`), which re-clones the pinned ref and
re-attaches the same data volume. For an in-place upgrade instead:

```bash
sudo git -C /opt/gaide-trace pull && sudo systemctl restart gaide-trace-server
```

## Backups and restore

The nightly timer syncs `data/` to the bucket, excluding `trace.db` — a derived
index that `rebuild-index` reconstructs from the archive, and one that cannot be
copied consistently while the server is writing to it.

To restore onto a fresh deployment:

```bash
sudo aws s3 sync s3://<bucket>/data/ /var/lib/gaide-trace/data/
sudo chown -R gaide-trace:gaide-trace /var/lib/gaide-trace/data
sudo -u gaide-trace python3 /opt/gaide-trace/server/gaide_trace_server.py \
  --data /var/lib/gaide-trace/data rebuild-index
sudo systemctl restart gaide-trace-server
```

The instance role can `PutObject` and `GetObject` but not `DeleteObject`, and
the bucket is versioned: a compromised server cannot erase its own history
off-box.

## Teardown

`terraform destroy` will refuse while the data volume exists — `prevent_destroy`
is there so a stray destroy cannot take the archive with it. Snapshot it, then
remove the guard deliberately:

```bash
aws ec2 create-snapshot --volume-id <data_volume_id> --description gaide-trace
# drop the lifecycle block in main.tf, then:
terraform destroy
```

## Security notes

- SSH is closed by default; shell access goes through SSM Session Manager,
  which needs no open port and logs every session against an IAM identity.
- IMDSv2 is required, both volumes are encrypted, and the data directory is
  `0750` under the service account — transcript snapshots are raw by design
  (D5), so treat that disk like the source code it describes.
- Port 80 is open only for the ACME HTTP-01 challenge and the redirect to 443.
- Before sharing any dataset this server collects, apply your institution's
  ethics and consent requirements.
