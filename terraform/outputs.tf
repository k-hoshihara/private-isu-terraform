# 複数台の正本。1 台運用でもリストの [0] を使う。
output "webapp_public_ips" {
  description = "競技者インスタンスのパブリック IP（インデックス順。1 台目が [0]）"
  value       = aws_instance.webapp[*].public_ip
}

output "webapp_private_ips" {
  description = "競技者インスタンスのプライベート IP。nginx upstream と MYSQL_HOST にはこちら"
  value       = aws_instance.webapp[*].private_ip
}

output "webapp_instance_ids" {
  description = "競技者インスタンス ID（SSM / stop-instances は全台こちら）"
  value       = aws_instance.webapp[*].id
}

output "webapp_urls" {
  description = "各台の http://<public_ip>/ 。分割後のベンチ対象は nginx 役"
  value       = [for ip in aws_instance.webapp[*].public_ip : "http://${ip}/"]
}

output "ssm_login_commands" {
  description = "Name タグ → SSM ログインコマンド"
  value = {
    for inst in aws_instance.webapp :
    inst.tags["Name"] => "aws ssm start-session --target ${inst.id} --region ${var.region}"
  }
}

# 1 台目の便宜。docs の one-box 例向け。正本は上のリスト / マップ。
output "webapp_public_ip" {
  description = "1 台目のパブリック IP。複数台は webapp_public_ips"
  value       = aws_instance.webapp[0].public_ip
}

output "webapp_url" {
  description = "1 台目の URL。分割後のベンチは nginx 役の IP"
  value       = "http://${aws_instance.webapp[0].public_ip}/"
}

output "webapp_instance_id" {
  description = "1 台目のインスタンス ID。全台は webapp_instance_ids"
  value       = aws_instance.webapp[0].id
}

output "webapp_private_ip" {
  description = "1 台目のプライベート IP。全台は webapp_private_ips"
  value       = aws_instance.webapp[0].private_ip
}

output "ssm_login_command" {
  description = "1 台目への SSM ログイン。全台は ssm_login_commands"
  value       = "aws ssm start-session --target ${aws_instance.webapp[0].id} --region ${var.region}"
}

output "benchmarker_instance_id" {
  value = try(aws_instance.benchmarker[0].id, null)
}
