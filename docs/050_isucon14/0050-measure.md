# 0050 重い処理を探す（計測）

スコアを上げる前に、どの処理に時間が食われているかを測ります。ツールを入れて、ベンチ 1 本と解析 1 往復を回すところまで固めます。出口は [0060-sql.md](0060-sql.md) です。

## 3 つの測定

| 見えるもの | ツール | 問うこと |
| --- | --- | --- |
| リクエストごとの応答時間 | nginx のアクセスログ + `alp` | どのエンドポイントが遅いのか |
| 1 回の SQL の所要時間 | MySQL の slow log | どのクエリが重いのか |
| Python の中で何に時間がかかっているか | `py-spy` | どの関数が重いのか |

ベンチはベンチ用インスタンスで走らせます。解析はアプリ用インスタンスの 2 つめの SSH セッションで行います（ベンチは 3〜5 分かかるため、同じセッションでは解析できません）。

## 1. nginx のログを LTSV 形式に変えます

AMI の nginx は Ubuntu 標準のままで、アクセスログは `combined` 形式です。まず設定を確認します。

```bash
sudo grep -n 'access_log' /etc/nginx/nginx.conf
```

成功の見え方: `access_log /var/log/nginx/access.log combined;` が `http` ブロックの中に 1 つある行が出力されます。既に `ltsv` に変わっている場合は、後半の「タブ区切りになっているか」の確認だけすれば足ります。

`alp` は LTSV（値をタブ区切りで並べる形式）を読むので、`log_format` を足します。

`log_format` は `http` ブロックにしか書けません。`/etc/nginx/conf.d/` は `http` に取り込まれるので、そこにファイルを作ります。

```bash
sudo tee /etc/nginx/conf.d/isucon14-ltsv.conf >/dev/null <<'EOF'
log_format ltsv '$time_iso8601\t$remote_addr\t$request_method\t$uri\t$server_protocol\t$status\t$body_bytes_sent\t$request_time\t$upstream_response_time';
EOF
sudo cat /etc/nginx/conf.d/isucon14-ltsv.conf
```

サイト設定は symlink を辿って `sites-available` 側を直します（[0020-https.md](0020-https.md) と同じ方針）。アプリ用の `server` ブロックに `access_log` を 1 行足します。`443 ssl` のブロック（`server_name xiv.isucon.net;` を持つ側）にだけ入れます。

```bash
sudo sed -i '/server_name \*.xiv.isucon.net;/a\  access_log /var/log/nginx/access.ltsv ltsv;' /etc/nginx/sites-available/isuride.conf
grep -n 'access_log\|server_name' /etc/nginx/sites-available/isuride.conf
sudo nginx -t && sudo systemctl reload nginx
```

成功の見え方: `grep` に `access_log /var/log/nginx/access.ltsv ltsv;` が 1 行出て、`nginx -t` が `syntax is ok` を返します。

?> `access_log` の指定は `server` ブロックの中に 1 つだけ入れてください。`http` ブロック（`/etc/nginx/nginx.conf`）に入れるとサイト全体のログが切り替わり、`/api/internal/` のマッチングログが混ざります。

ログが LTSV で出ているか確認します。

```bash
sudo tail -n 2 /var/log/nginx/access.ltsv | cat -A | head
```

成功の見え方: `$time_iso8601` の後に `^I`（タブ）が並び、`"GET /api/... HTTP/1.1" 401 ...` のような 1 行になります。

## 2. `alp` を入れます

AMI には `alp` が入っていないので入れます（[../040_practice/0015-sql-tuning.md](../040_practice/0015-sql-tuning.md) §1 と同じ手順です）。既にあれば入れ直しません。

```bash
ALP_VERSION=1.0.21
if ! which alp >/dev/null 2>&1; then
  sudo apt-get update -y && sudo apt-get install -y unzip
  curl -fsSL -o /tmp/alp.zip \
    "https://github.com/tkuchiki/alp/releases/download/v${ALP_VERSION}/alp_linux_amd64.zip"
  unzip -o /tmp/alp.zip -d /tmp && sudo install -m 0755 /tmp/alp /usr/local/bin/alp
fi
alp --version
```

成功の見え方: `alp version 1.0.21` が返ります。

## 3. ログを 1 往復で解析します

ベンチはベンチ用インスタンスで走らせます。アプリ用インスタンスのセッションで以下を実行します。まずログを回転します。

```bash
[ -f /var/log/nginx/access.ltsv ] || { echo 'ログが無いので手順 1 をやり直します'; exit 1; }
sudo mv /var/log/nginx/access.ltsv /var/log/nginx/access.ltsv.$(date +%s)
sudo nginx -s reopen
```

成功の見え方: 回転前のファイル名が `access.ltsv.<Unix 時刻>` に変わります。

1 つめのセッション（ベンチ用）でベンチを 1 本回します。`bench.env` は 0040 の手順 1 で作り直したものです。

```bash
source ~/bench.env
cd ~
./bench run --target https://isuride.xiv.isucon.net \
  --addr ${APP_IP}:443 --payment-url http://${BENCH_IP}:12345 -t 60
```

回ったら 2 つめ（アプリ用）で集計します。

```bash
sudo tail -n 10000 /var/log/nginx/access.ltsv | alp ltsv --sort count
sudo tail -n 10000 /var/log/nginx/access.ltsv | alp ltsv \
  --filter 'status == 200' --sort desc_time
```

- 1 つめは「どのエンドポイントがいちばん多く呼ばれたか」を数えます
- 2 つめは「200 で返ったもののうち、いちばん遅かったもの」を出します

## 4. 集計の例

`alp ltsv --sort count` の結果は次のようになります（数値は例です）。読み方は「上位のエンドポイントに実装が集中している」です。

```text
  68.5%  41253  GET  /api/app/notification
  21.3%  12841  POST /api/app/rides
   6.1%   3677  GET  /api/app/nearby-chairs
   3.0%   1802  POST /api/chair/coordinate
   1.1%    654  GET  /api/chair/notification
```

`/api/app/notification` が 6 割を超えるのは、通知のポーリング間隔が 30 ms だからです（[公式 ISURIDE.md の通知エンドポイント](https://github.com/isucon/isucon14/blob/main/docs/ISURIDE.md)）。実装を直すならここが第 1 候補です。

## 5. MySQL の slow log を短期で有効にします

Ubuntu の MySQL 8 は既定で slow log が無効です（公式の Ansible 投入手順にも設定がありません）。ベンチの間だけ有効にします。`SET GLOBAL` は再起動で元に戻るので、戻し忘れても恒久的な変更にはなりません。

まず現状を見ます。

```bash
mysql -uisucon -pisucon -e "
  SHOW VARIABLES WHERE Variable_name IN
  ('slow_query_log','long_query_time','slow_query_log_file','log_output');"
```

ログファイルを用意してから有効にします。

```bash
sudo mkdir -p /var/log/mysql
sudo touch /var/log/mysql/isuride-slow.log
sudo chown mysql:mysql /var/log/mysql/isuride-slow.log
mysql -uisucon -pisucon -e "
  SET GLOBAL slow_query_log_file = '/var/log/mysql/isuride-slow.log';
  SET GLOBAL long_query_time = 0.1;
  SET GLOBAL slow_query_log = ON;
  SHOW VARIABLES WHERE Variable_name IN ('slow_query_log','long_query_time');"
```

成功の見え方: `slow_query_log` が `ON`、`long_query_time` が `0.1` になります。

この状態でベンチを 1 本回します。回ったら 2 つめのセッションで集計します。

```bash
# 0.1 秒以上かかったクエリの件数
sudo grep -c '^# Query_time: ' /var/log/mysql/isuride-slow.log

# Query_time が大きい順（ Slow log には DESC で並ばないのでソートします）
sudo grep -E '^# Query_time: ' /var/log/mysql/isuride-slow.log \
  | sed 's/# Query_time: //' | sort -rn | head -20
```

- 1 つめが「0.1 秒以上かかったクエリの件数」です
- 2 つめが「その中の `Query_time` が大きい順」の先頭です

SQL 本文は `Query_time` の行のすぐ次の行にきます（`# Query_time: 0.512345  Lock_time: 0.000123 Rows_sent: 1` の次行）。本文だけを見たいときは次の様にします。

```bash
sudo grep -A 1 -E '^# Query_time: [0-9]' /var/log/mysql/isuride-slow.log | head -40
```

ベンチが終わったら元に戻します。

```bash
mysql -uisucon -pisucon -e "
  SET GLOBAL long_query_time = 10;
  SET GLOBAL slow_query_log = OFF;"
```

?> `long_query_time = 0`（全部記録）はログが数 GB になります。使わないでください。0.1 秒で足りません。

## 6. Python の中で何が起きているか測ります

`py-spy` はプロファイルを取るツールです。アプリ用インスタンスに入れます。

```bash
if ! which py-spy >/dev/null 2>&1; then
  ~/.local/bin/uv tool install py-spy
fi
~/.local/bin/py-spy --version
```

成功の見え方: `py-spy <バージョン>` が返ります（`uv tool install` なら `~/.local/bin/py-spy` に置かれます）。

ベンチの実行中に計測します。gunicorn の master（1 つめの PID）に付け、`--subprocesses` で worker も含めます。

```bash
pgrep -f gunicorn | head -n 3
sudo ~/.local/bin/py-spy record \
  --pid "$(pgrep -f gunicorn | head -1)" \
  --duration 60 --subprocesses \
  --output /tmp/flame.svg
```

成功の見え方: 60 秒後に `Wrote flame data to '/tmp/flame.svg'` と出て、フレームグラフが作られます。

図で「一番太い箱」が一番時間を食った関数です。`record` は `--duration` の秒数で終わります。長く取りたい場合は `--duration` を増やします。手元で開くには SCP で取り直します。

```bash
sudo chmod 644 /tmp/flame.svg
scp -i ~/.ssh/isucon14.pem /tmp/flame.svg ~/isucon14/flame.svg
```

生きた画面が見たいときは `top` を使います。ベンチ中に 2 つめのセッションで実行します。

```bash
sudo ~/.local/bin/py-spy top --pid "$(pgrep -f gunicorn | head -1)"
```

## 7. クエリの本数を数えます

「重いクエリ」だけでなく「クエリが殺到しているか」を見ます。MySQL のカウンタは累積値なので、前後の差を取ります。

```bash
mysql -uisucon -pisucon -e "SHOW GLOBAL STATUS LIKE 'Questions';"
# ここでベンチを 1 本回す
mysql -uisucon -pisucon -e "SHOW GLOBAL STATUS LIKE 'Questions';"
```

回す前後の差が、そのベンチで発行された SQL の本数です。[0070-nearby-chairs.md](0070-nearby-chairs.md) では「1 リクエストあたりのクエリ数」を、これで測ります。

## まとめ

1 手ごとの順番はこのとおりです。

```text
nginx のログを回転 → ベンチ 1 本 → alp / slow log / py-spy を読む → scores.txt に 1 行
```

## 出口

「重いクエリ」が見えたところで [0060-sql.md](0060-sql.md) へ進みます。次はインデックスを足します。
