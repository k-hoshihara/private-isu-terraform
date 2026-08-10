resource "aws_security_group" "webapp" {
  name        = "${var.name_prefix}-webapp"
  description = "private-isu webapp instance"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${var.name_prefix}-webapp" }
}

# ブラウザでの動作確認のための HTTP
resource "aws_vpc_security_group_ingress_rule" "webapp_http" {
  for_each = toset(var.allowed_cidrs)

  security_group_id = aws_security_group.webapp.id
  description       = "HTTP from operator"
  cidr_ipv4         = each.value
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

# SSM を使用する場合は不要。enable_ssh = true の場合のみ開放する。
resource "aws_vpc_security_group_ingress_rule" "webapp_ssh" {
  for_each = var.enable_ssh ? toset(var.allowed_cidrs) : toset([])

  security_group_id = aws_security_group.webapp.id
  description       = "SSH from operator"
  cidr_ipv4         = each.value
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

# 別インスタンスのベンチマーカーからの負荷リクエスト
resource "aws_vpc_security_group_ingress_rule" "webapp_http_from_benchmarker" {
  count = var.enable_benchmarker_instance ? 1 : 0

  security_group_id            = aws_security_group.webapp.id
  description                  = "HTTP from benchmarker instance"
  referenced_security_group_id = aws_security_group.benchmarker[0].id
  from_port                    = 80
  to_port                      = 80
  ip_protocol                  = "tcp"
}

# apt、SSM エンドポイント、GitHub への通信に必要
resource "aws_vpc_security_group_egress_rule" "webapp_all" {
  security_group_id = aws_security_group.webapp.id
  description       = "allow all outbound"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "benchmarker" {
  count = var.enable_benchmarker_instance ? 1 : 0

  name        = "${var.name_prefix}-benchmarker"
  description = "private-isu benchmarker instance"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${var.name_prefix}-benchmarker" }
}

# ベンチマーカーはインバウンド不要（SSM 経由でログインするため）
resource "aws_vpc_security_group_egress_rule" "benchmarker_all" {
  count = var.enable_benchmarker_instance ? 1 : 0

  security_group_id = aws_security_group.benchmarker[0].id
  description       = "allow all outbound"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
