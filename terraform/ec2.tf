locals {
  user_data = var.install_alp ? templatefile("${path.module}/user_data.sh.tftpl", {
    alp_version = var.alp_version
  }) : null
}

# 競技者用インスタンス。既定 3 台。AMI はオールインワンなので、
# 起動直後は各台で nginx / gunicorn / MySQL が動く。役割分割は手順書側。
# 1 台に戻す: webapp_instance_count = 1
resource "aws_instance" "webapp" {
  count = var.webapp_instance_count

  ami                    = var.ami_id
  instance_type          = var.webapp_instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.webapp.id]
  iam_instance_profile   = aws_iam_instance_profile.ssm.name
  key_name               = var.enable_ssh ? var.key_name : null

  associate_public_ip_address = true
  user_data                   = local.user_data

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_size

    # gp3 のベースライン値。ディスク I/O がボトルネックになる場合は
    # iops / throughput を引き上げる（9 章のストレージ性能に対応）。
    iops       = 3000
    throughput = 125
  }

  metadata_options {
    http_tokens   = "required" # IMDSv2 のみ
    http_endpoint = "enabled"
  }

  tags = { Name = "${var.name_prefix}-webapp-${count.index + 1}" }
}

# 4 章以降、ベンチマーカーの消費リソースが無視できなくなった場合に分離する
resource "aws_instance" "benchmarker" {
  count = var.enable_benchmarker_instance ? 1 : 0

  ami                    = var.ami_id
  instance_type          = var.benchmarker_instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.benchmarker[0].id]
  iam_instance_profile   = aws_iam_instance_profile.ssm.name
  key_name               = var.enable_ssh ? var.key_name : null

  associate_public_ip_address = true

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_size
  }

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  tags = { Name = "${var.name_prefix}-benchmarker" }
}
