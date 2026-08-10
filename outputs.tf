output "webapp_public_ip" {
  description = "ブラウザで http://<この IP>/ にアクセスすると Iscogram のトップページが表示される"
  value       = aws_instance.webapp.public_ip
}

output "webapp_url" {
  value = "http://${aws_instance.webapp.public_ip}/"
}

output "webapp_instance_id" {
  description = "aws ssm start-session --target <この ID> でログインする"
  value       = aws_instance.webapp.id
}

output "webapp_private_ip" {
  description = "別インスタンスのベンチマーカーからリクエストを送信する際の宛先"
  value       = aws_instance.webapp.private_ip
}

output "benchmarker_instance_id" {
  value = try(aws_instance.benchmarker[0].id, null)
}

output "ssm_login_command" {
  value = "aws ssm start-session --target ${aws_instance.webapp.id} --region ${var.region}"
}
