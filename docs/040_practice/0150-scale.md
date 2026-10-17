# 0150 スペックと構成を変える

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）は、手元の private-isu-terraform リポジトリで実行します。

- ここまでの章は、「同じマシンの中で速くする」でした。
- この章は、**マシンの割り当てそのもの**を変えます。
- 効果が一番大きいのは、**点数を測るための負荷を起こしているベンチマーカーを、計測の対象から外す**ことです。
- ここが、このチュートリアルの出口です。

- 点数の前に、1 つ注意があります。
- **台のスペックを変えると、Terraform の管理する内容と食い違います**（[§3](#3-db-台を大きくする)）。
- どの変更がどの台に残るかを、記録します。
- `fail`（エラーの件数）が 0 でない点数は、比べません。

- 順番: ベンチマーカーを分離する（1.）→ gzip を測り直す（2.）→ DB 台を大きくする（3.）→ 公式ベンチで判定する（4.）。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  note=scale-baseline" >> ~/bench-notes/scores.txt
```

<a id="1-ベンチマーカーを分離する"></a>

## 1. ベンチマーカーを分離する

- 今の点数には、**s1 で負荷を生成しているコストが乗っています**。
- ベンチマーカーは、10 万点規模になると nginx と gunicorn と同じ 2 vCPU を使います。
- [0010-env.md](0010-env.md) の設定で、専用の機械を立てます。

```hcl
# terraform/terraform.tfvars
enable_benchmarker_instance = true
# benchmarker_instance_type   = "c7a.xlarge"   # 既定
```

```bash
# 作業機
cd terraform && terraform plan     # `aws_instance.benchmarker[0]` が追加されること
terraform apply
terraform output -json webapp_instance_ids | jq -r '.[]'
```

- 専用機（c7a.xlarge）が、立ち上がります。
- ベンチは**その台から**打ちます。ターゲットは s1 の URL に向けます（[0110-split-3.md §11](0110-split-3.md#11-ヘルスチェックと公式ベンチ) と同じ）。

```bash
# ベンチマーカー専用機で打つ（NGINX_URL は s1 のプライベートまたはパブリック IP）
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://10.42.0.137/
```

```bash
# 点数は s1 の ~/bench-notes/scores.txt に 1 行残す
echo "$(date -Iseconds)  score=  pass=  fail=  note=scale-benchmarker-box" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13、`c7a.large` 3 台 + ベンチマーカー `c7a.xlarge` / `ap-northeast-1`）: `{"pass":true,"score":316465,"success":297949,"fail":0,"messages":[]}`。0140 最終の 178760 から **+137,705（1.77 倍）**です。

- **これは「アプリが速くなった」のではなく、「点数の測定から負荷生成を取り除いた」効果です。**
- 同じ構成のまま、ベンチマーカーの置き場を変えただけです。点数の見取り図が、変わります。
- ここまでの章の点数は、すべて「s1 でベンチを回した条件」の値です。

### 観察すること

- 点数が大きく動いたら、それまで何が限界だったかを言えます（この回は負荷生成の CPU）
- ベンチマーカーを戻す（`-t http://localhost`）と同じ構成に戻ること。条件を混ぜません

<a id="2-gzip-を測り直す"></a>

## 2. gzip を測り直す

- [0140 §3](0140-cut-remaining.md#3-gzip-の対象を広げる) で「localhost ベンチでは転送が 0 コストなので効かない」と読んだ手です。
- 専用機に分けた今なら、転送がネットワークを通ります。
- **同じ設定のまま、測り直す**のがこの節です。

```bash
# s1 で打つ（gzip の対象を広げる drop-in を外す → ベンチ）
sudo rm -f /etc/nginx/conf.d/gzip-types.conf
sudo nginx -t && sudo systemctl reload nginx
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=scale-gzip-off" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13、専用機に分けたあとのベンチ）: `score=319353`（gzip 外し、`fail=0`）→ `score=314435`（gzip 戻し、`fail=0`）。前の `score=316465`（gzip オン）と合わせて **3 点とも ±3000 の揺らぎ**で、**効果はほぼゼロ**と判定しました。

- 理由も、言えます。
- 社内ネットワーク上の転送は速く、HTML は元から圧縮の対象（`text/html`）です。
- css / js だけを足しても、節約分は小さいです。圧縮の CPU コストが、同じくらいあります。
- 効くのは、「帯域が細い・転送が大きい」環境です。

### 観察すること

- 専用機に分ける前の「効かない理由」と、分けたあとの「それでも効かない理由」が違うこと
- 3 点取ってから判定しています（1 回の増減では決めません）

<a id="3-db-台を大きくする"></a>

## 3. DB 台を大きくする

- ベンチ中、s3（MySQL）は 3 台の中で一番詰まっています（[0110-split-4.md §2](0110-split-4.md#2-下がった理由を特定する) で `%idle` 4% でした）。
- **DB 台だけ**を、大きくします。
- DB のデータを守るため、EC2 の **stop → インスタンスタイプ変更 → start** で行います（保存領域の EBS はそのまま残ります）。

```bash
# 作業機
IID3=$(aws ec2 describe-instances --region ap-northeast-1 \
  --filters "Name=tag:Name,Values=private-isu-webapp-3" "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].InstanceId" --output text)
aws ec2 stop-instances --instance-ids "$IID3" --region ap-northeast-1
aws ec2 wait instance-stopped --instance-ids "$IID3" --region ap-northeast-1
aws ec2 modify-instance-attribute --instance-id "$IID3" \
  --instance-type '{"Value":"c7a.xlarge"}' --region ap-northeast-1
aws ec2 start-instances --instance-ids "$IID3" --region ap-northeast-1
aws ec2 wait instance-running --instance-ids "$IID3" --region ap-northeast-1
```

- MySQL が起きたら、s1 と s2 のアプリを restart します（古い接続を捨てます）。
- それから、ベンチします。

```bash
# s1 と s2 で打つ
sudo systemctl restart isu-python.service
# s1 でベンチ（ベンチマーカー専用機から打ちます）
echo "$(date -Iseconds)  score=  pass=  fail=  note=scale-db-c7axlarge" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13、s3 を `c7a.large` → `c7a.xlarge`）: `{"pass":true,"score":324278,"success":307363,"fail":0,"messages":[]}`（316465 から **+7,813**）。**効果は想定より小さく、この時点の限界は s3 の CPU ではありませんでした**（ベンチマーカー側の発行能力か、アプリの worker 数が先に効いています）。

### メモリを使い切るつもりで詰めすぎない

- 増やしたメモリに合わせて `innodb_buffer_pool_size`（検索結果を覚えておく場所の大きさ）を振っても、**効くとは限りません**。

```ini
# /etc/mysql/mysql.conf.d/99-isucon-bufferpool.cnf
[mysqld]
innodb_buffer_pool_size = 4608M   # 7.5GiB の 60% から始める
```

実測サンプル（2026-10-13）:

| buffer pool | score | fail | 備考 |
| --- | --- | --- | --- |
| 2560M（変更前） | 324278 | 0 | |
| **4608M** | 310946 / 311169 | 0 | **2 回とも -7000〜13000**。詰めすぎ |
| **2560M に戻す** | **318783** | **0** | 回復。この値を残す |

- データの量が小さいときは、大きい pool が逆に効かないことがあります。
- **戻して測り直し、動かない値は残さない**。これが、このチュートリアルの一貫した流儀です。
- [0110-split-4.md §5](0110-split-4.md#5-mysql-のメモリを使い切るs3) も、「ヒット率が上がらない・`available` が枯れる → 1 つ戻す」です。

?> **Terraform と食い違います。** `terraform/terraform.tfvars` の `webapp_instance_type` は `c7a.large` のままです。s3 の実体は `c7a.xlarge` です。**次に `terraform apply` すると、s3 が `c7a.large` に戻る差分が出ます。**追従させるには、台ごとの設定を用意して実体と合わせます。このリポジトリでは `ec2.tf` の `instance_types` として実装済みです（台ごとのタイプ。空なら `webapp_instance_type` を全台に）。次を設定すると、`terraform plan` が「変更なし」になります。

```hcl
# terraform/terraform.tfvars
instance_types = ["c7a.large", "c7a.large", "c7a.xlarge"]
```

### 観察すること

- スペックを変えたら、何が限界だったかを言い直せます（この回は「DB の CPU ではなかった」）
- 変更がどの台に残るかを記録します（Terraform の管理外の変更は、食い違い（drift）になります）
- `fail` が 0 です

<a id="4-公式ベンチで判定する"></a>

## 4. 公式ベンチで判定する

- 残す変更（ベンチマーカー専用機への分離・s3 = `c7a.xlarge`・buffer pool 2560M・0120〜0140 の全部）を、載せた状態にします。
- 公式ベンチを 1 本回します。

```bash
# ベンチマーカー専用機で打つ
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://10.42.0.137/
echo "$(date -Iseconds)  score=  pass=  fail=  note=scale-final" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）: `{"pass":true,"score":317602,"success":298795,"fail":0,"messages":[]}`（2560M に戻した状態の測定 318783 と同水準）。

?> **ベンチをどの台で打ったかは、nginx のアクセスログの送信元 IP で確認します。**この検証では、再開後の 1 回で送信先を s1 のままにしていました（`ssm-send` の宛先取り違え）。**s1 で自分自身にベンチを打った状態**の `score=182165` を、「性能が半分になった」と誤読しました。アクセスログの `host` を数えると、速い回は `10.42.0.191`（ベンチマーカー機）、誤った回は `10.42.0.137`（s1 自身）でした。専用機の効果を測るときは、**毎回 `-t` の URL と実行場所の両方を確認します**。

```bash
# s1 で打つ（このベンチの送信元が誰かを見る）
sudo awk -F'\t' '{h=$2; sub(/^host:/,"",h); c[h]++} END {for (k in c) print c[k], k}' \
  /var/log/nginx/access.log | sort -rn | head -3
```

ベンチ中の稼働（この回の `mpstat 1 5` 平均 `%idle`＝CPU の空き）:

| 台 | %idle | 役割 |
| --- | --- | --- |
| ベンチマーカー（c7a.xlarge） | 約 99 | 負荷生成に余裕（上限ではない） |
| s1（c7a.large） | 5〜7 | **nginx + 軽いアプリで頭打ち** |
| s2（c7a.large） | 約 69 | 余裕あり |
| s3（c7a.xlarge） | 約 68 | 余裕あり（iowait 約 17%） |

### チュートリアル全体の推移（2026-10-13、`c7a.large` 1 台 → 3 台 + ベンチマーカー `c7a.xlarge`）

| 章 | score | 段階 |
| --- | --- | --- |
| 0010 §7 | 936 | Ruby オールインワンの初期 |
| 0015 §8 | 14817 | 索引 1 本 + N+1 一括化 |
| 0080 §5 | 45814 | 画像ファイル化 |
| 0100 §2 | 51143 | `digest()` → `hashlib` |
| 0110 split-1 §6 | 50920 | 分割前ベースライン |
| 0110 split-4 §7 | 96265 | 3 台分割 → worker・振り分けで回復 |
| 0120 §7 | 173356 | DB の仕事量削減（`FORCE INDEX`・LIMIT・件数列） |
| 0130 §6 | 181103 | レスポンス生成（ping 間引き・ログ停止・描画） |
| 0140 §6 | 178760 | 残りの削減（localhost ベンチの締め） |
| 0150 §1〜§4 | 317602 | ベンチマーカー分離 + DB 台拡張（318783 と同水準） |
| **0150 §5〜§6** | **336771** | **重み調整（322262）+ s1 を xlarge に（+14509）** |

- 約 340 倍です。
- うち大きな転機は、次の 4 つでした。
- 索引と N+1（0015-sql-tuning.md）/ worker と振り分け（0110-split-1〜4）/ DB の仕事量（0120-db-load.md）/ 点数の測定から負荷生成を外す（0150-scale.md）

### 観察すること

- どの一手が何点だったかを、章の名前で言えます
- 点数の条件（ベンチマーカーの置き場・台数・タイプ）を、点数とセットで記録しています

<a id="5-s1-の負荷を軽くする重みの再調整"></a>

## 5. s1 の負荷を軽くする（重みの再調整）

- [§4](#4-公式ベンチで判定する) の時点で、s1 は `%idle` 5〜7% と頭打ちでした。
- 一方、s2 は 69% 余っています。
- `upstream heavy` の重みを、s2 に寄せます（s1 は nginx と静的だけに集中させます）。

```nginx
upstream heavy {
  server 10.42.0.233:8080;   # s2 のみ（s1 の unix を外す）
  keepalive 32;
}
```

```bash
# s1 で反映 → ベンチ（ベンチマーカー専用機から）
echo "$(date -Iseconds)  score=  pass=  fail=  note=scale-weight-s2only" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）: `{"pass":true,"score":322262,"success":303872,"fail":0,"messages":[]}`（317602 から +4,660）。効果は小さめですが正の方向なので、この形を残しました。

### 観察すること

- 各台の `%idle` の差を「振り分け」で直せます（[0110-split-4.md §6](0110-split-4.md#6-upstream-の負荷を均す) と同じ考え方）
- 1 つずつです。外しすぎたら s1 の `mpstat` で分かります

<a id="6-s1-を大きくするボトルネック台の拡張"></a>

## 6. s1 を大きくする（ボトルネック台の拡張）

- 重みを寄せても、s1（nginx + 静的 + 軽いアプリ）が頭打ちなら、**その台だけ**大きくします。
- [§3](#3-db-台を大きくする) と同じ、stop/start の手順です。

```bash
aws ec2 stop-instances --instance-ids "$IID1" --region ap-northeast-1
aws ec2 wait instance-stopped --instance-ids "$IID1" --region ap-northeast-1
aws ec2 modify-instance-attribute --instance-id "$IID1" \
  --instance-type '{"Value":"c7a.xlarge"}' --region ap-northeast-1
aws ec2 start-instances --instance-ids "$IID1" --region ap-northeast-1
aws ec2 wait instance-running --instance-ids "$IID1" --region ap-northeast-1
```

- **再起動後は、unix socket の残骸を消してから起動します**（[0110-split-2.md §6](0110-split-2.md#6-s1-の-gunicornunix-socket) の注意が、そのまま効きます）。

```bash
# s1 で打つ
rm -f /home/isucon/tmp/gunicorn.sock
sudo systemctl restart isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1/login
```

- Terraform の `instance_types` も、実体に合わせます（[§3](#3-db-台を大きくする) の注と同じ）。

```hcl
# terraform/terraform.tfvars
instance_types = ["c7a.xlarge", "c7a.large", "c7a.xlarge"]
```

```bash
# s1 でベンチ（ベンチマーカー専用機から）
echo "$(date -Iseconds)  score=  pass=  fail=  note=scale-s1-c7axlarge" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13、s1 を `c7a.large` → `c7a.xlarge`）: `{"pass":true,"score":336771,"success":317909,"fail":0,"messages":[]}`（322262 から **+14,509**）。`terraform plan` が「変更なし」になることも確認しました。

### 観察すること

- 「どの台が頭打ちか」を測ってから広げます（この回は s1 が 5〜7% idle）
- 変更は Terraform の管理と一致させます（[§3](#3-db-台を大きくする)）

<a id="7-使わない時間のコストを減らす"></a>

## 7. 使わない時間のコストを減らす

- ここまでで、4 台（`c7a.xlarge` × 2 + `c7a.large` × 2 + ベンチマーカー `c7a.xlarge`）が動いています。
- **練習しない時間は、止めます。**
- 保存領域の EBS（DB の中身・画像・設定）は、消えません。

```bash
# 作業機（止める）
IDS=$(cd terraform && terraform output -json webapp_instance_ids | jq -r '.[]')
BID=$(cd terraform && terraform output -json benchmarker_instance_id | jq -r)
aws ec2 stop-instances --instance-ids $IDS $BID --region ap-northeast-1
# 始めるときは start-instances（SSM が戻るまで数分）
```

- 再開後にやることは、次のとおりです（[§6](#6-s1-を大きくするボトルネック台の拡張) の再起動後の注意と同じ）。

1. s1: `rm -f /home/isucon/tmp/gunicorn.sock` → `sudo systemctl restart isu-python.service`
2. s1・s2: `sudo systemctl restart isu-python.service`（DB 接続を新しく）
3. `curl` で `/` と `/login` が 200 であること

### 観察すること

- インスタンスを止めても、スコアの記録（`~/bench-notes`）と DB は EBS に残ります
- 再起動後の socket の残骸だけは毎回掃除します

## 8. トラブルシュート

### `terraform apply` で s3 のタイプが戻る

- [§3](#3-db-台を大きくする) の Terraform との食い違い（drift）です。実体と設定を合わせるか、apply を止めて記録を見直します

### ベンチマーカー専用機が s1 に届かない

- セキュリティグループ（`private-isu-benchmarker`）と s1 の受信側を見ます。VPC 内はプライベート IP で届く設計です
- `-t` の URL を s1 に向けているかを見ます（アプリ台や DB 台には向けません）

### nginx で `Too many open files` が出る

- 上げるのは**プロセス側の FD 上限**（同時に開けるファイルや接続の数）です（sysctl の `fs.file-max` は最初から広いので違います）
- nginx は 2 箇所を揃えます: `nginx.conf` の**メインコンテキスト**（`http` 内の `conf.d` では `not allowed here` になります）と systemd:

```nginx
# /etc/nginx/nginx.conf（worker_processes の直後）
worker_rlimit_nofile 65535;
```

```ini
# /etc/systemd/system/nginx.service.d/limits.conf
[Service]
LimitNOFILE=65535
```

```bash
sudo nginx -t && sudo systemctl daemon-reload && sudo systemctl restart nginx
# worker の実効値を見る（65535 であること）
for p in $(pgrep -u www-data nginx | head -2); do grep -i 'open files' /proc/$p/limits; done
```

- `/etc/security/limits.d/` は **ログイン時にだけ**効きます。systemd のサービスには `LimitNOFILE` を使います
- 上げるのは、足りなくなっていることが分かってからです（[0140 §5](0140-cut-remaining.md#5-ベンチマーカーの-fd-を見る)）

### 分離したのに点数が戻らない

- ベンチを**どの台で**打っていますか。専用機に分けた効果は「負荷生成を対象外にした」ことなので、対象の台で打つと同じ条件に戻ります（この検証でも 182165 に「下がった」と誤読しました）
- 確認は nginx のアクセスログの送信元 IP です（[§4](#4-公式ベンチで判定する) の注）

### DB 台を変えて点数が動かない

- 限界が別の場所です。`mpstat` でどの台が `%idle` 0 に近いかを見直します（[0110-split-4.md §2](0110-split-4.md#2-下がった理由を特定する)）
- スペックは足し算ではありません。詰めすぎた設定（buffer pool など）は戻します

- 0150 まで終わったら、[0160-memory-cache.md](0160-memory-cache.md)（読み込みのメモリキャッシュ）に進みます。
- 全部終わったら、終盤作業（[0030-ops.md](../010_common/0030-ops.md) の「ログを止める（終盤）」「終盤チェック（17:00 以降）」）に進みます。
