# private-isu-terraform

[catatsuy/private-isu](https://github.com/catatsuy/private-isu) の環境を AWS 上に構築する Terraform 構成です。

『[達人が教える Web パフォーマンスチューニング 〜ISUCON から学ぶ高速化の実践](https://gihyo.jp/book/2022/978-4-297-12846-3)』3章「基礎的な負荷試験」に必要な EC2 環境を対象としています。  
書籍は2022年刊行のため、OS・インスタンスタイプ・IAM ポリシーなどが現在のリポジトリと異なります。  
この構成は2026年8月時点の private-isu に合わせています。

## 前提条件

必要なのは AWS アカウントのみです。  
作業はすべて AWS CloudShell 上で行うため、ローカル環境へのツールのインストールは不要です。

| 項目 | 内容 |
| --- | --- |
| AWS アカウント | EC2・VPC・IAM ロールを作成できる権限が必要 |
| 作業環境 | AWS CloudShell（AWS CLI v2 と Git がインストール済み） |
| Terraform | >= 1.5 |
| リージョン | `ap-northeast-1`（AMI の公開リージョン） |

> [!WARNING]
> `c7a.large` は起動している間、課金が発生します。  
> 作業を中断するときはインスタンスを停止し、不要になったら `terraform destroy` で削除してください。

## 作成されるリソース

| リソース | 役割 |
| --- | --- |
| VPC / パブリックサブネット / インターネットゲートウェイ / ルートテーブル | EC2 を外部公開するための最小構成 |
| セキュリティグループ | TCP/80 を指定 CIDR のみに許可。SSH は任意 |
| IAM ロール / インスタンスプロファイル | SSM Session Manager でのログイン用 |
| EC2（競技者用） | private-isu 公開 AMI。ベンチマーカー同梱 |
| EC2（ベンチマーカー用） | 任意。4章以降で分離する場合に有効化 |

設計上の方針は以下のとおりです。

- **T 系インスタンスは使用しない** — バーストパフォーマンスにより負荷試験中の性能が安定せず、スコアの変化がチューニングによるものか判別できなくなるため
- **TCP/80 は自身のグローバル IP に限定** — `0.0.0.0/0` に公開すると、無差別にアクセスするボットのリクエストが計測結果に混入するため。  
  `allowed_cidrs` に `0.0.0.0/0` を指定すると `terraform plan` の時点でエラーになります
- **SSH ではなく SSM Session Manager** — SSH ポートを開放せずにログインできるため。  
  IAM ポリシーは書籍記載の `AmazonEC2RoleforSSM`（非推奨）ではなく `AmazonSSMManagedInstanceCore` を使用します
- **AMI は固定値** — `most_recent = true` にすると AMI 更新時にミドルウェアのバージョンが変わり、過去に計測したスコアと比較できなくなるため
- **Web サーバーとベンチマーカーは同一 AZ** — AZ をまたぐとネットワークレイテンシが計測結果に影響するため

## ファイル構成

```
private-isu-terraform/
├── versions.tf              # プロバイダーとバージョン制約
├── variables.tf             # 設定値
├── network.tf               # VPC / サブネット / IGW / ルートテーブル
├── security_groups.tf       # セキュリティグループ
├── iam.tf                   # SSM 用 IAM ロール / インスタンスプロファイル
├── ec2.tf                   # EC2 インスタンス
├── outputs.tf               # 接続先 IP / インスタンス ID
├── user_data.sh.tftpl       # alp の自動インストール
├── terraform.tfvars.example # 設定値のサンプル
└── docs/
    └── webapp-setup/        # 言語実装ごとの切り替え手順
        ├── go.md
        └── python.md
```

## 使い方

### 1. Terraform を導入する（CloudShell 初回のみ）

CloudShell に Terraform は含まれていないため、tfenv 経由で導入します。  
ホームディレクトリは永続化されるため、次回以降のセッションでは不要です。

```bash
git clone --depth=1 https://github.com/tfutils/tfenv.git ~/.tfenv
mkdir -p ~/bin
ln -sf ~/.tfenv/bin/* ~/bin/
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

tfenv install latest
tfenv use latest
terraform -version
```

### 2. 設定値を記入する

```bash
cp terraform.tfvars.example terraform.tfvars
curl -s https://checkip.amazonaws.com   # 自身のグローバル IP
```

出力された IP を `/32` を付けて `allowed_cidrs` に記入します。

```hcl
allowed_cidrs = ["203.0.113.1/32"]

webapp_instance_type        = "c7a.large"
enable_benchmarker_instance = false
```

### 3. 構築する

```bash
terraform init
terraform plan
terraform apply
```

完了すると接続先が出力されます。

```
Outputs:

ssm_login_command  = "aws ssm start-session --target i-0123456789abcdef0 --region ap-northeast-1"
webapp_instance_id = "i-0123456789abcdef0"
webapp_private_ip  = "10.42.0.123"
webapp_public_ip   = "203.0.113.10"
webapp_url         = "http://203.0.113.10/"
```

`webapp_url` をブラウザで開き、Iscogram のトップページが表示されれば構築完了です。

### 4. サーバーにログインする

```bash
aws ssm start-session --target $(terraform output -raw webapp_instance_id)
```

インスタンスが SSM に登録されるまで起動後1〜2分ほどかかります。  
`TargetNotConnected` が返る場合は少し待ってから再実行してください。

## 主な変数

| 変数 | デフォルト | 説明 |
| --- | --- | --- |
| `region` | `ap-northeast-1` | AMI が公開されているリージョン。通常は変更しない |
| `allowed_cidrs` | （必須） | HTTP/SSH を許可する接続元 CIDR。`0.0.0.0/0` は不可 |
| `ami_id` | `ami-09201e964bee13733` | private-isu 競技者用 AMI（Ubuntu 24.04 amd64、ベンチマーカー同梱） |
| `webapp_instance_type` | `c7a.large` | 競技者用インスタンスタイプ |
| `enable_benchmarker_instance` | `false` | ベンチマーカー専用インスタンスを作成するか。3章では `false` |
| `benchmarker_instance_type` | `c7a.xlarge` | ベンチマーカー用インスタンスタイプ |
| `root_volume_size` | `40` | ルートボリューム(GiB)。初期データが 1GB を超えるため余裕を持たせる |
| `enable_ssh` | `false` | TCP/22 を開放するか。SSM のみで運用する場合は `false` |
| `key_name` | `null` | SSH 用キーペア名。`enable_ssh = false` なら不要 |
| `install_alp` | `true` | 起動時に alp を自動インストールするか（3-2 で使用） |
| `alp_version` | `1.0.21` | インストールする alp のバージョン |
| `vpc_cidr` | `10.42.0.0/16` | VPC の CIDR |
| `subnet_cidr` | `10.42.0.0/24` | パブリックサブネットの CIDR |

## AMI の最新版を確認する

private-isu の README に記載された AMI ID は特定日時のスナップショットのため、より新しい AMI が公開されている場合があります。  
CloudShell から確認できます。

```bash
aws ec2 describe-images --region ap-northeast-1 \
  --owners $(aws ec2 describe-images --region ap-northeast-1 \
    --image-ids ami-09201e964bee13733 \
    --query 'Images[0].OwnerId' --output text) \
  --filters 'Name=name,Values=catatsuy_private_isu_*' \
  --query 'sort_by(Images,&CreationDate)[].[CreationDate,Name,ImageId]' \
  --output table
```

更新頻度は年1回程度のため、書籍を読み始める時点で一度確認すれば十分です。

## 言語実装を切り替える

起動直後は Ruby の参考実装が動作しています。  
同時に起動できる実装は1つのため、別の言語を使うときは Ruby を停止してから切り替えます。

手順は言語ごとに分けています。

- [Go](docs/webapp-setup/go.md)
- [Python](docs/webapp-setup/python.md)

Ruby・PHP・Node.js については、private-isu の [manual.md](https://github.com/catatsuy/private-isu/blob/master/manual.md) を参照してください。

## ベンチマーカーを実行する

3章の範囲では、Web サービスと同じホスト上から `localhost` に対して実行します。

```bash
sudo su - isucon
/home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
```

チューニングが進みベンチマーカーがホストの CPU を無視できない割合で消費し始めたら、別インスタンスに分離します。  
`terraform.tfvars` を変更して `terraform apply` を実行してください。

```hcl
enable_benchmarker_instance = true
benchmarker_instance_type   = "c7a.xlarge"
```

## 後片付け

作業を中断する場合はインスタンスを停止します。

```bash
aws ec2 stop-instances --instance-ids $(terraform output -raw webapp_instance_id)
```

不要になったら削除します。

```bash
terraform destroy
```

配布されている AMI にはセキュリティアップデートが適用されないため、長期間起動したままにしないでください。

## 参考

- [catatsuy/private-isu](https://github.com/catatsuy/private-isu)
- [manual.md](https://github.com/catatsuy/private-isu/blob/master/manual.md) — 当日マニュアル。言語切り替えや MySQL の接続情報
- [public_manual.md](https://github.com/catatsuy/private-isu/blob/master/public_manual.md) — 事前公開レギュレーション
- [matsuu/cloud-init-isucon](https://github.com/matsuu/cloud-init-isucon/tree/main/private-isu) — cloud-init で構築する場合
- [達人が教える Web パフォーマンスチューニング｜技術評論社](https://gihyo.jp/book/2022/978-4-297-12846-3)
- [tatsujin-web-performance/tatsujin-web-performance](https://github.com/tatsujin-web-performance/tatsujin-web-performance) — 書籍のサポートリポジトリ
