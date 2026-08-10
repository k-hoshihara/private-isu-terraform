variable "region" {
  description = "AMI が公開されているリージョン。通常は変更しない。"
  type        = string
  default     = "ap-northeast-1"
}

variable "name_prefix" {
  description = "作成するリソース名の接頭辞"
  type        = string
  default     = "private-isu"
}

variable "allowed_cidrs" {
  description = <<-EOT
    HTTP(80) と SSH(22) を許可する接続元 CIDR のリスト。
    自身のグローバル IP を /32 で指定する。
    0.0.0.0/0 を指定すると、無差別なアクセスが計測結果に混入する。
    確認方法: curl -s https://checkip.amazonaws.com
  EOT
  type        = list(string)

  validation {
    condition     = !contains(var.allowed_cidrs, "0.0.0.0/0")
    error_message = "0.0.0.0/0 は計測結果への影響とセキュリティの両面で非推奨です。自身の IP を /32 で指定してください。"
  }
}

variable "ami_id" {
  description = <<-EOT
    private-isu 競技者用 AMI（ベンチマーカー同梱、Ubuntu 24.04 amd64）。
    特定日時のスナップショットのため、より新しい AMI が公開されている場合がある。
    最新版は https://github.com/catatsuy/private-isu#ami を参照。
  EOT
  type        = string
  default     = "ami-09201e964bee13733" # catatsuy_private_isu_amd64_20260524
}

variable "webapp_instance_type" {
  description = <<-EOT
    競技者用（Web サービス）インスタンスタイプ。
    リポジトリの推奨は c7a.large。書籍執筆時点は c5.large。
    T 系（バーストパフォーマンス）は負荷試験中に性能が安定しないため使用しない。
  EOT
  type        = string
  default     = "c7a.large"
}

variable "enable_benchmarker_instance" {
  description = <<-EOT
    ベンチマーカー専用インスタンスを作成するかどうか。
    3 章では Web サーバー上から localhost に対して実行するため false を指定する。
    4 章以降、ベンチマーカーの負荷が無視できなくなった時点で true に変更する。
  EOT
  type        = bool
  default     = false
}

variable "benchmarker_instance_type" {
  description = "ベンチマーカー用インスタンスタイプ。リポジトリの推奨は c7a.xlarge。"
  type        = string
  default     = "c7a.xlarge"
}

variable "root_volume_size" {
  description = "ルートボリュームのサイズ(GiB)。初期データが 1GB を超えるため余裕を持たせる。"
  type        = number
  default     = 40
}

variable "enable_ssh" {
  description = "TCP/22 を allowed_cidrs に開放するか。SSM のみで運用する場合は false を推奨。"
  type        = bool
  default     = false
}

variable "key_name" {
  description = "SSH 用キーペア名。enable_ssh = false の場合は null のままとする。"
  type        = string
  default     = null

  # enable_ssh = true で未指定だと、22 番ポートを開けたのにキーペアが設定されず、
  # SSH でログインできないインスタンスになる。
  validation {
    condition     = !var.enable_ssh || (var.key_name != null && var.key_name != "")
    error_message = "enable_ssh = true の場合は key_name に既存の EC2 キーペア名を指定してください。"
  }
}

variable "install_alp" {
  description = "起動時に alp（アクセスログ集計ツール）を自動インストールするか。3-2 で使用する。"
  type        = bool
  default     = true
}

variable "alp_version" {
  description = "インストールする alp のバージョン"
  type        = string
  default     = "1.0.21"
}

variable "vpc_cidr" {
  description = "VPC の CIDR"
  type        = string
  default     = "10.42.0.0/16"
}

variable "subnet_cidr" {
  description = "パブリックサブネットの CIDR"
  type        = string
  default     = "10.42.0.0/24"
}
