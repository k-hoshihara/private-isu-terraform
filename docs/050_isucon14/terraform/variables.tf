variable "region" {
  description = "AMI が公開されているリージョン。通常は変更しない。"
  type        = string
  default     = "ap-northeast-1"
}

variable "name_prefix" {
  description = "作成するリソース名の接頭辞"
  type        = string
  default     = "isucon14"
}

variable "allowed_cidrs" {
  description = <<-EOT
    SSH(22) と決済モック(payment_port) を許可する接続元 CIDR のリスト。
    自身のグローバル IP を /32 で指定する。
    確認方法: curl -s https://checkip.amazonaws.com
  EOT
  type        = list(string)

  validation {
    condition     = !contains(var.allowed_cidrs, "0.0.0.0/0")
    error_message = "0.0.0.0/0 は避け、自身の IP を /32 で指定してください。"
  }
}

variable "ami_id" {
  description = <<-EOT
    ISUCon 14 の AMI（競技と同じ構成、Ubuntu 24.04 amd64）。
    0010-env.md が指す公式 AMI `ami-0e334c50145a3ee41` は公開停止済み
    （describe-images が Images: [] を返す）。既定値は公開が続いている
    ISUCon14 AMI（`isucon14-20260818100152`、16GiB ルート）に差し替えている。
    別の AMI を使う場合は ami_id を上書きする。Packer で作り直す手順は
    0010-env.md の「9. AMI が使えないとき」。
  EOT
  type        = string
  default     = "ami-0fcf9e8e8675a9ee4" # isucon14-20260818100152
}

variable "app_instance_type" {
  description = <<-EOT
    アプリ用（競技者役）インスタンスタイプ。競技と同じ c5.large（2 vCPU / 4 GiB）。
    リソース計測の実務では大型化しても、当日のスコア比較は c5.large で行うこと。
  EOT
  type        = string
  default     = "c5.large"
}

variable "app_instance_count" {
  description = <<-EOT
    アプリ用インスタンスの台数。既定は 3（競技と同じ 3 台構成）。
    0010〜0100 の 1 台構成の手順をなぞるだけなら 1 にする。
  EOT
  type        = number
  default     = 3

  validation {
    condition     = var.app_instance_count >= 1
    error_message = "app_instance_count は 1 以上にしてください。"
  }
}

variable "instance_types" {
  description = <<-EOT
    台ごとのアプリ用インスタンスタイプ。省略時は app_instance_type を全台に使う。
    1 台だけ大きくしたとき（0110-split.md の 3 台構成など）に、実体と管理を一致させる。
  EOT
  type        = list(string)
  default     = []
}

variable "enable_benchmarker_instance" {
  description = "ベンチマーカー専用インスタンスを作成するか。競技再現では true（別台で回す）。"
  type        = bool
  default     = true
}

variable "benchmarker_instance_type" {
  description = <<-EOT
    ベンチマーカー用インスタンスタイプ。練習は c5.xlarge（4 vCPU）。
    競技は ECS Fargate 8 vCPU なので、要求は競技の方が大きい。
    この構成で回したスコアは競技の値にはならない。
  EOT
  type        = string
  default     = "c5.xlarge"
}

variable "app_volume_size" {
  description = "アプリ用ルートボリュームのサイズ(GiB)。0010-env.md のアプリ用と同じ 20。"
  type        = number
  default     = 20
}

variable "benchmarker_volume_size" {
  description = "ベンチ用ルートボリュームのサイズ(GiB)。ベンチのビルド成果物を置くため 0010-env.md のベンチ用と同じ 30。"
  type        = number
  default     = 30
}

variable "enable_ssh" {
  description = <<-EOT
    TCP/22 を allowed_cidrs に開放するか。ISUCon 14 の AMI の SSH ユーザは
    ubuntu（isucon ではない）で、0010〜0100 のログインは SSH 前提。既定は true。
    SSM エージェントが使える場合のみ false にしてよい。
  EOT
  type        = bool
  default     = true
}

variable "key_name" {
  description = <<-EOT
    既存の EC2 キーペア名。0010-env.md の「3. SSH 鍵を用意する」で作る isucon14 が既定。
    ssh_public_key_path を指定したときは、その公開鍵でこの名前のキーペアを作る。
    enable_ssh = false の場合は使わない。
  EOT
  type        = string
  default     = "isucon14"

  validation {
    condition     = !var.enable_ssh || ((var.key_name != null && var.key_name != "") || (var.ssh_public_key_path != null && var.ssh_public_key_path != ""))
    error_message = "enable_ssh = true の場合は key_name、または ssh_public_key_path のどちらかを指定してください。"
  }
}

variable "ssh_public_key_path" {
  description = <<-EOT
    キーペアを Terraform で作るときの公開鍵のパス（例: ~/.ssh/id_ed25519.pub）。
    指定すると key_name の名前で aws_key_pair を作る（秘密鍵は state に残らない）。
    既存キーを使う場合は null のまま。既に同名のキーがあると Duplicate で失敗する。
  EOT
  type        = string
  default     = null
}

variable "enable_ssm" {
  description = "SSM のインスタンスプロファイルを付けるか。SSH を使わない運用の逃げ道。"
  type        = bool
  default     = true
}

variable "payment_port" {
  description = <<-EOT
    ベンチが決済モックを待つポート（0040-bench.md の --payment-bind-port 既定値）。
    負荷走行中はアプリ → ベンチ:このポート で決済が飛ぶ。インスタンス間は
    同一 SG の全許可で通るため、このルールは運用元からの疎通確認用。
  EOT
  type        = number
  default     = 12345
}

variable "vpc_cidr" {
  description = "VPC の CIDR。10.0.0.0/16。"
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidr" {
  description = "パブリックサブネットの CIDR。10.0.1.0/24。"
  type        = string
  default     = "10.0.1.0/24"
}
