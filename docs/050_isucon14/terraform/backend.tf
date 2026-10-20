# state は S3 に保存する。親の terraform/ と同じバケットを使い、key で分ける。
# bucket 名はアカウント固有のため backend.hcl で渡す（tfvars では指定できない）。
#   cp backend.hcl.example backend.hcl
#   # bucket を編集したうえで:
#   terraform init -backend-config=backend.hcl
#
# バケットの作成はリポジトリ README「3. state 用 S3 バケット」を参照。
terraform {
  backend "s3" {
    key          = "isucon14/terraform.tfstate"
    region       = "ap-northeast-1"
    encrypt      = true
    use_lockfile = true
  }
}
