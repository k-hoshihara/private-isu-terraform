locals {
  # 0010-env.md の手順 7 の env.sh 用。1 台目は APP_ID / APP_IP、2 台目以降は APP2_ID の形。
  app_id_lines = [for i, inst in aws_instance.app : "export APP${i == 0 ? "" : tostring(i + 1)}_ID=\"${inst.id}\""]
  app_ip_lines = [for i, inst in aws_instance.app : "export APP${i == 0 ? "" : tostring(i + 1)}_IP=${inst.public_ip}"]

  key_name = coalesce(local.effective_key_name, "")
}

output "app_public_ips" {
  description = "アプリ用インスタンスのパブリック IP（インデックス順。1 台目が [0]）"
  value       = aws_instance.app[*].public_ip
}

output "app_private_ips" {
  description = "アプリ用インスタンスのプライベート IP。nginx upstream と MYSQL_HOST にはこちら"
  value       = aws_instance.app[*].private_ip
}

output "app_instance_ids" {
  description = "アプリ用インスタンス ID（SSM / stop-instances は全台こちら）"
  value       = aws_instance.app[*].id
}

output "app_urls" {
  description = "各台の https://<public_ip>/ 。3 台構成のベンチ対象は nginx 役"
  value       = [for ip in aws_instance.app[*].public_ip : "https://${ip}/"]
}

output "benchmarker_public_ip" {
  description = "ベンチ用インスタンスのパブリック IP（enable_benchmarker_instance = false なら null）"
  value       = try(aws_instance.benchmarker[0].public_ip, null)
}

output "benchmarker_private_ip" {
  description = "ベンチ用インスタンスのプライベート IP"
  value       = try(aws_instance.benchmarker[0].private_ip, null)
}

output "benchmarker_instance_id" {
  description = "ベンチ用インスタンス ID"
  value       = try(aws_instance.benchmarker[0].id, null)
}

output "ssh_commands" {
  description = "Name タグ → SSH コマンド。ISUCon 14 の AMI は ubuntu ユーザで入る"
  value = merge(
    { for inst in aws_instance.app : inst.tags["Name"] => "ssh -i ~/.ssh/${local.key_name}.pem ubuntu@${inst.public_ip}" },
    { for inst in aws_instance.benchmarker : inst.tags["Name"] => "ssh -i ~/.ssh/${local.key_name}.pem ubuntu@${inst.public_ip}" },
  )
}

output "ssm_login_commands" {
  description = "Name タグ → SSM ログインコマンド（enable_ssm = true のとき）"
  value = merge(
    { for inst in aws_instance.app : inst.tags["Name"] => "aws ssm start-session --target ${inst.id} --region ${var.region}" },
    { for inst in aws_instance.benchmarker : inst.tags["Name"] => "aws ssm start-session --target ${inst.id} --region ${var.region}" },
  )
}

output "env_sh" {
  description = "0010-env.md の手順 7 の ~/isucon14/env.sh に貼る内容（ID と IP を控える手順の代わり）"
  value = join("\n", concat(
    [
      "# 0010-env.md の手順 7 の ~/isucon14/env.sh",
      "export AWS_REGION=${var.region}",
      "export KEY_NAME=${local.key_name}",
      "export VPC_ID=\"${aws_vpc.main.id}\"",
      "export SUBNET_ID=\"${aws_subnet.public.id}\"",
      "export SG_ID=\"${aws_security_group.isucon14.id}\"",
      "export AMI_ID=${var.ami_id}",
    ],
    local.app_id_lines,
    local.app_ip_lines,
    [
      "export BENCH_ID=\"${try(aws_instance.benchmarker[0].id, "")}\"",
      "export BENCH_IP=${try(aws_instance.benchmarker[0].public_ip, "")}",
      "export BENCH_PRIV=${try(aws_instance.benchmarker[0].private_ip, "")}",
    ],
  ))
}
