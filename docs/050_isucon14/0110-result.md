# 0110 結果の読み方と、手を戻すところ

ここまでのスコアを `~/bench-notes/scores.txt` に並べ、ベンチの前後で必ず戻す設定をまとめます。出口はここで終了です。

## 1. スコアの読み方

ベンチの最終行が判定です。

```text
time=... level=INFO msg="結果" pass=true スコア=28431 種別エラー数=map[]
```

- `pass=true` ならスコアは有効です。`pass=false`（FAIL）のスコアは見ません
- `種別エラー数` が空でなければ、同じ `map` に出ている種類が数えられます
- 1 分半のあいだに出る `INFO` 行（新規登録数、利用をやめた人数、3 つの不満率）が「どこが遅いのか」を教えてくれます

スコアの式（[0100-matching.md](0100-matching.md) と同じ）を頭に入れて、増減を読みます。

```text
スコア = (マッチ位置 → 乗車位置の距離) * 0.1
       + (乗車位置 → 目的地の距離)
       + 完了ライド数 * 5
```

- 完了数が増えたなら「マッチングと状態遷移」を当てた
- 距離が増えたなら「就近でマッチする」側に当たった
- `種別エラー数` が増えたら「点数を上げる前」に直すべき問題があります

## 2. 記録の書き方

`~/bench-notes/scores.txt` は手元 PC に 1 行ずつ足します。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=28431  pass=true  note=matching interval 0.05 + 近距離マッチ" \
  >> ~/bench-notes/scores.txt
cat ~/bench-notes/scores.txt
```

### 比べないこと

- 1 回に複数の変更を入れて比べません。1 手ごとに 1 本、1 行
- `pass=false` の行は「どの変更で落ちたか」を残すために書きます（点数は比べません）
- ベンチの実行時間は 3〜5 分です。集中して 1 手ずつ回します

## 3. 整合性チェックだけを回します

`GET /api/owner/sales` や `GET /api/app/rides` の実装を変えると、ベンチの初期データチェックで不一致になります。負荷走行を待たずに確認するモードがあります。

ベンチ用インスタンスで:

```bash
source ~/bench.env
cd ~
./bench run --only-post-validation \
  --target https://isuride.xiv.isucon.net --addr ${APP_IP}:443
```

成功の見え方: `post validationが終了しました` が出て、`passed=true` になります。内容が変わっている場合は `初期データのバリデーションに失敗` が出ます。

?> `--only-post-validation` は初期データ（`GET /api/owner/chairs`、`GET /api/owner/sales`、`GET /api/app/rides` など）の応答だけを検証します。走行中の負荷は走りません。

## 4. 再起動を 1 回試します

アプリが状態を持たない構成（DB に書くだけ）になっているかを確認します。

アプリ用インスタンスでライドを1件作ってから再起動し、データが残っているかを確認します。

```bash
sudo systemctl reboot
```

再起動後は `isuride-go.service` が自動で戻ってきます（AMI 作成時に `enabled: true`）。Python を使い続けるなら `sudo systemctl disable --now isuride-go.service` をやり直します。

```bash
systemctl is-active isuride-go isuride-python
sudo systemctl disable --now isuride-go.service
sudo systemctl restart isuride-python.service
```

成功の見え方: `isuride-go` が `inactive`、`isuride-python` が `active` に戻ります。

?> ベンチの `--only-post-validation` は再起動後のデータ保持までは見ません。保持を見たい場合は、`POST /api/app/users` で利用者を 1 人作ってから再起動し、`GET /api/app/rides` の結果が変わらないことを確かめます。

## 5. ベンチの前後で戻す設定

ベンチの「前」に毎回確認します。

| 対象 | 確認コマンド | 期待する状態 |
| --- | --- | --- |
| 言語 | `systemctl is-active isuride-python` | `active` |
| 決済先がベンチ | `mysql -uisucon -pisucon isuride -e "SELECT value FROM settings WHERE name='payment_gateway_url';"` | ベンチの `--payment-url` に入っている（回したら 0040 の手順 9 で戻します） |
| slow log | `mysql -uisucon -pisucon -e "SHOW VARIABLES LIKE 'slow_query_log';"` | `OFF`（測る時だけ `ON`） |
| マッチング周期 | `grep ISUCON_MATCHING_INTERVAL /home/isucon/env.sh` | 試している値 |
| nginx | `sudo nginx -t` | `syntax is ok` |

アプリ用インスタンスでまとめて見ます。

```bash
cd ~/webapp/python
ls app/*.orig
systemctl is-active isuride-python
grep ISUCON_MATCHING_INTERVAL /home/isucon/env.sh
mysql -uisucon -pisucon isuride -e "
  SELECT index_name FROM information_schema.statistics
  WHERE table_schema='isuride' AND table_name='ride_statuses';"
sudo nginx -t
```

## 6. 競技では再現しないもの

このフォルダでは再現していないものがいくつかあります。競技当日には次が入ります。

| 項目 | 練習での扱い | 競技での扱い |
| --- | --- | --- |
| CloudFormation テンプレート | 使わない（AMI から直接作る） | チームごとに固有のテンプレートで 3 台作る |
| `envcheck.service` | 起動時に 1 回だけ走る（情報送信先は存在しない） | 起動のたびに競技环境的を確認してポータルへ送信する |
| ポータル | 使わない（ベンチを自分で叩く） | ポータルから負荷走行をリクエストする |
| 追試 | 3. と 4. で一部だけ試す | 主催者が 3 台を再起動して実行する |
| セキュリティグループ | 22 / 443 / 12345 を開ける | 22 / 443 のみ（`12345` は(application 外から開けない） |
| ベンチマーカー | EC2 1 台（`c5.xlarge`） | ECS Fargate 8 vCPU |

`envcheck.service` を止める場合はこうします。止めなくても害はありません。

```bash
sudo systemctl disable envcheck.service
sudo systemctl stop envcheck.service
```

## 7. 3 台構成にしたい場合

競技は競技者 3 台構成です。アプリ用インスタンスを 2 台増やし、nginx 役の 1 台から `upstream` でまとめます。

手元 PC で 2 台を追加します（AMI と同じ AMI、`c5.large`）。

```bash
source ~/isucon14/env.sh
for name in app2 app3; do
  aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type c5.large \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --subnet-id "$SUBNET_ID" \
    --associate-public-ip-address \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=20,VolumeType=gp3,DeleteOnTermination=true,Encrypted=true}' \
    --metadata-options HttpTokens=required \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=isucon14-${name}},{Key=Project,Value=isucon14}]" \
    --query 'Instances[0].InstanceId' --output text
done
```

> `upstream` は `http` ブロックにしか書けません。`server` ブロックの中に書くと `nginx -t` が落ちます。sites-enabled の site conf とは別ファイル（`/etc/nginx/conf.d/upstream.conf`）に分けます。

nginx 役のアプリ用インスタンスで:

```bash
sudo tee /etc/nginx/conf.d/upstream.conf >/dev/null <<EOF
upstream isuride {
  server 127.0.0.1:8080;
  server ${APP2_IP}:443;
  server ${APP3_IP}:443;
}
EOF
sudo nginx -t && sudo systemctl reload nginx
```

ただし素体は `proxy_pass http://localhost:8080` の loopback です。3 台構成にするには、各台で loopback を残したまま `proxy_pass` の向き先を変える必要があります。ここまでの手順は 1 台構成のものです。

?> 3 台構成でのログと計測は private-isu の [../040_practice/0110-split-1.md](../040_practice/0110-split-1.md) に台役割の分け方があります。ただし private-isu 専用の手順なので、そのまま当てはめることはできません。

## 8. 注意点として覚えておくこと

- ベンチマーカーが `c5.xlarge`（4 vCPU）です。競技は 8 vCPU なので、要求はもっと大きい圧力です。スコアは競技の値になりません
- 通信遅延が 0 ではありません。`--addr` で宛先 IP を直接叩いています
- ベンチの初期化で DB は毎回作り直されます。インデックスやスキーマの変更は必ず `~/webapp/sql/` に書いて初期化に追随させてください

## 9. 後片付け

[0010-ami.md](0010-ami.md) の停止と削除に戻ります。

```bash
source ~/isucon14/env.sh
aws ec2 describe-instances --filters \
  "Name=tag:Project,Values=isucon14" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[].InstanceId' --output text \
  | xargs -r aws ec2 terminate-instances --instance-ids
```

AMI にはセキュリティ更新が入りません。長いあいだ起動したままにしません。

| 期間 | 対応 |
| --- | --- |
| 数日〜数週間 | インスタンス停止（EBS のみ課金） |
| しばらく使わない | 上記の削除手順 |

## 出口

ここで [../README.md](../README.md) に戻ります。 scores.txt に「どの変更で何点になった」が残っていれば、このフォルダは 1 周できていることになります。
