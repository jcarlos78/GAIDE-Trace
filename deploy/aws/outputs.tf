output "public_ip" {
  description = "Elastic IP of the server. Point var.domain here (an A record) if you are not letting Terraform manage the zone."
  value       = aws_eip.server.public_ip
}

output "console_url" {
  description = "Web console, once DNS resolves and Caddy has issued the certificate."
  value       = "https://${var.domain}/"
}

output "shell_command" {
  description = "Open a root shell on the instance without SSH or an open port."
  value       = "aws ssm start-session --region ${var.aws_region} --target ${aws_instance.server.id}"
}

output "first_run_credentials" {
  description = "How to get in the first time. The console forces a password change before anything else works."
  value       = "Sign in at https://${var.domain}/ as admin / admin. If that account is already claimed: sudo -u gaide-trace python3 /opt/gaide-trace/server/gaide_trace_server.py --data /var/lib/gaide-trace/data user reset admin"
}

output "bootstrap_log" {
  description = "Where to look when the console does not come up."
  value       = "sudo tail -n 100 /var/log/gaide-trace-bootstrap.log; systemctl status gaide-trace-server caddy"
}

output "backup_bucket" {
  description = "S3 bucket receiving the nightly archive sync (empty when backups are disabled)."
  value       = var.enable_backups ? aws_s3_bucket.backup[0].bucket : ""
}

output "data_volume_id" {
  description = "EBS volume holding the archive. It has prevent_destroy set — snapshot it before any deliberate teardown."
  value       = aws_ebs_volume.data.id
}
