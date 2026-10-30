# 競技と同じ「全台で 1 つのセキュリティグループ」。アプリ用もベンチ用も
# 同じ SG に入れる。全インスタンスは同一サブネット上で相互に到達できる。
#   app → app : nginx(443) / gunicorn(8080) / mysqld(3306) / memcached(11211) / rsync(22)
#   bench → app: HTTPS(443)
#   app → bench: 決済モック(payment_port)
resource "aws_security_group" "isucon14" {
  name        = "${var.name_prefix}-sg"
  description = "ISUCon14 practice: every instance can reach every other instance"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${var.name_prefix}-sg" }
}

# 全インスタンス相互接続。プロトコル -1（全ポート・全プロトコル）を自分自身に許可する。
# これが「全ての EC2 サーバがお互いにつながる」の本体。0110-split.md の
# nginx upstream、台間 rsync、決済モックの往復はここで通る。
resource "aws_vpc_security_group_ingress_rule" "instances_all" {
  security_group_id            = aws_security_group.isucon14.id
  description                  = "all traffic between the fleet"
  referenced_security_group_id = aws_security_group.isucon14.id
  ip_protocol                  = "-1"
}

# 443 は全世界に開ける。ベンチ → アプリの HTTPS と、ブラウザでの動作確認のため。
# 0010-env.md のセキュリティグループと同じ。
resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.isucon14.id
  description       = "HTTPS from anywhere (bench to app, browser)"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

# 22 は運用元のみ。SSH ユーザは ubuntu。
resource "aws_vpc_security_group_ingress_rule" "ssh" {
  for_each = var.enable_ssh ? toset(var.allowed_cidrs) : toset([])

  security_group_id = aws_security_group.isucon14.id
  description       = "SSH from operator"
  cidr_ipv4         = each.value
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

# 決済モックの疎通確認用。アプリ → ベンチの本番経路は instances_all が担う。
# 443 は全世界、22 と決済モックは運用元だけに絞る。
resource "aws_vpc_security_group_ingress_rule" "payment_mock" {
  for_each = toset(var.allowed_cidrs)

  security_group_id = aws_security_group.isucon14.id
  description       = "payment mock from operator"
  cidr_ipv4         = each.value
  from_port         = var.payment_port
  to_port           = var.payment_port
  ip_protocol       = "tcp"
}

# apt、SSM エンドポイント、GitHub への通信に必要
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.isucon14.id
  description       = "allow all outbound"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
