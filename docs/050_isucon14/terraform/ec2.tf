locals {
  # キーペアを Terraform で作るか（公開鍵を渡したときだけ）。作らない場合は
  # 0010-env.md の手順 3 で作成した既存キー（key_name）をそのまま使う。
  create_key_pair    = var.enable_ssh && var.ssh_public_key_path != null && var.ssh_public_key_path != ""
  effective_key_name = local.create_key_pair ? coalesce(var.key_name, "${var.name_prefix}-key") : var.key_name

  # インスタンスに渡すキー名。enable_ssh = false なら付けない（SSM のみ）。
  # aws_key_pair を参照することで、キー作成 → インスタンス起動 の順序を保証する。
  instance_key_name = var.enable_ssh ? (local.create_key_pair ? aws_key_pair.this[0].key_name : var.key_name) : null
}

resource "aws_key_pair" "this" {
  count = local.create_key_pair ? 1 : 0

  key_name   = local.effective_key_name
  public_key = file(var.ssh_public_key_path)

  tags = { Name = local.effective_key_name }
}

# アプリ用（競技者役）。既定 3 台。AMI はオールインワンで、起動直後は各台で
# Go 実装（isuride-go）・matcher・payment_mock が動く。Python への切替は 0030。
# 1 台に戻す: app_instance_count = 1
resource "aws_instance" "app" {
  count = var.app_instance_count

  ami                    = var.ami_id
  instance_type          = length(var.instance_types) > count.index ? var.instance_types[count.index] : var.app_instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.isucon14.id]
  iam_instance_profile   = var.enable_ssm ? aws_iam_instance_profile.ssm.name : null
  key_name               = local.instance_key_name

  associate_public_ip_address = true

  root_block_device {
    volume_type = "gp3"
    volume_size = var.app_volume_size

    # 0010-env.md の表と同じ gp3 20GB。
    iops       = 3000
    throughput = 125
  }

  metadata_options {
    http_tokens   = "required" # IMDSv2 のみ
    http_endpoint = "enabled"
  }

  tags = {
    Name = "${var.name_prefix}-app-${count.index + 1}"
    Role = "app"
  }
}

# ベンチ用（0010-env.md の isucon14-bench）。対象と同じ台で回すと CPU を奪い合うため別台。
resource "aws_instance" "benchmarker" {
  count = var.enable_benchmarker_instance ? 1 : 0

  ami                    = var.ami_id
  instance_type          = var.benchmarker_instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.isucon14.id]
  iam_instance_profile   = var.enable_ssm ? aws_iam_instance_profile.ssm.name : null
  key_name               = local.instance_key_name

  associate_public_ip_address = true

  root_block_device {
    volume_type = "gp3"
    volume_size = var.benchmarker_volume_size

    # 0010-env.md の表と同じ gp3 30GB。
    iops       = 3000
    throughput = 125
  }

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  tags = {
    Name = "${var.name_prefix}-bench"
    Role = "bench"
  }
}
