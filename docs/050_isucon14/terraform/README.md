# terraform

[../0010-env.md](../valid/0010-env.md) の環境構築を担当する Terraform。ISUCon 14（ISURIDE）の練習環境を、アプリ用インスタンスとベンチ用インスタンスの 2 種類で作ります。

- アプリ用: **`c5.large`（2 vCPU / 4 GiB）＋ gp3 20 GB**。既定 **3 台**（競技と同じ）
- ベンチ用: **`c5.xlarge`（4 vCPU）＋ gp3 30 GB** × 1 台
- AMI: ISUCon 14 の AMI（Ubuntu 24.04、`ap-northeast-1`）。公式の `ami-0e334c50145a3ee41` は公開停止のため、既定は公開中の `ami-0fcf9e8e8675a9ee4`
- ネットワーク: VPC 1・パブリックサブネット 1・IGW 1。**全インスタンスを同一のセキュリティグループに入れ、相互に全ポートで到達できる**

[../0010-env.md](../valid/0010-env.md) の VPC / サブネット / セキュリティグループ / インスタンス作成は、このフォルダの Terraform が担当します。SSH 鍵の用意と、ログイン後の作業は 0010-env.md 以降の手順のままです。

## 構成

```text
VPC 10.0.0.0/16
└── public subnet 10.0.1.0/24（ap-northeast-1a）
    ├── isucon14-app-1   c5.large  gp3 20GB  ┐
    ├── isucon14-app-2   c5.large  gp3 20GB  ├─ SG isucon14（相互に全許可）
    ├── isucon14-app-3   c5.large  gp3 20GB  ┤
    └── isucon14-bench   c5.xlarge gp3 30GB  ┘
```

セキュリティグループのルール:

| ポート | 元 | 用途 |
| --- | --- | --- |
| 全 | SG 自身 | 全インスタンス相互（app↔app↔bench）。nginx upstream、台間 rsync、決済モックの往復 |
| 443 | `0.0.0.0/0` | ベンチ → アプリの HTTPS、ブラウザ確認 |
| 22 | `allowed_cidrs` | 運用元からの SSH（`enable_ssh = true` のとき） |
| 12345 | `allowed_cidrs` | 決済モックの疎通確認 |

## 使い方

```bash
cd docs/050_isucon14/terraform
cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
# allowed_cidrs を自分の IP に書き換える（curl -s https://checkip.amazonaws.com）
# backend.hcl の bucket を自分のアカウントのものに書き換える
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
terraform output
```

`terraform output env_sh` が、0010-env.md の手順 7 で作る `~/isucon14/env.sh` の内容をそのまま出力します。`APP_ID` / `APP_IP` / `BENCH_ID` / `BENCH_IP` を手で控える代わりに使えます。

```bash
terraform output -raw env_sh > ~/isucon14/env.sh
source ~/isucon14/env.sh
ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@${APP_IP}   # ログインユーザは ubuntu
```

入ったら `sudo su - isucon` でアプリ実行ユーザへ切り替えます（0010-env.md の手順 7）。以降は [../0020-https.md](../valid/0020-https.md) から番号順に進みます。

## 前提

- **SSH 鍵**: 0010-env.md の「3. SSH 鍵を用意する」で `isucon14` の鍵を作っておくか、`ssh_public_key_path` に公開鍵のパスを渡して Terraform に作らせます。同名のキーが既にあると `InvalidKeyPair.Duplicate` で失敗します
- **AMI**: 0010-env.md が指す公式 AMI `ami-0e334c50145a3ee41` は**公開停止済み**です（`describe-images` が `Images: []` を返します）。既定値は公開が続いている ISUCon14 AMI `ami-0fcf9e8e8675a9ee4`（`isucon14-20260818100152`、16GiB ルート）に差し替えています。別のものを使う場合は `ami_id` を上書きし、Packer で作り直す手順は [../0010-env.md](../valid/0010-env.md) の「12. AMI が使えないとき」を参照
- **state**: 親の [`../../../terraform/`](../../../terraform) と同じ S3 バケットを使い、`key = "isucon14/terraform.tfstate"` で分けています。バケット名は `backend.hcl` で渡します（`.gitignore` 済み）。バケットの作成は [../0010-env.md](../valid/0010-env.md) の「4. state 用 S3 バケット」を参照

## 台数の変更

| やりたいこと | 設定 |
| --- | --- |
| 0010〜0100 の 1 台構成をなぞる | `app_instance_count = 1` |
| 競技と同じ 3 台構成（0110-split.md） | `app_instance_count = 3`（既定） |
| 1 台だけスペックを変える | `instance_types = ["c5.large", "c5.xlarge", "c5.large"]` |

`app_instance_count` を増減すると、`Name` タグは `isucon14-app-1` から連番で振り直されます。IP は apply のたびに変わります。

## 競技との違い

| 項目 | このフォルダ | 競技 |
| --- | --- | --- |
| 台の作り方 | Terraform（このフォルダ） | CloudFormation テンプレート |
| セキュリティグループ | 22 / 443 / 12345 | 22 / 443 |
| ベンチマーカー | EC2 `c5.xlarge`（4 vCPU） | ECS Fargate 8 vCPU |
| ポータル / 追試 / `envcheck.service` | 使わない | あり |

**ベンチが 4 vCPU なので、この構成で回したスコアは競技の値にはなりません。** 変更前後を 1 手ずつ比べるための環境です。

## 停止と削除

```bash
# 中断（EBS のみ課金。パブリック IP は変わる）
terraform state list
aws ec2 stop-instances --instance-ids $(terraform output -json app_instance_ids | jq -r '.[]') $(terraform output -raw benchmarker_instance_id)

# 完全に削除
terraform destroy
```

AMI の EBS は `DeleteOnTermination` 相当なので、`terraform destroy` でインスタンスとボリュームが一緒に消えます。AMI にはセキュリティ更新が入らないため、長期間起動したままにしないでください。
