# 0050 重い処理を探す（計測）

0060-sql-index.md でインデックスを 1 手入れました。次は、どの処理に時間が食われているかを測る道具をそろえます。ベンチ 1 本と解析 1 往復の型を作り、以降の章はこの型を繰り返します。出口は **0080-sql-n1.md**（この章はこれから） です。

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
log_format ltsv 'time:$time_iso8601\thost:$remote_addr\tmethod:$request_method\turi:$uri\tstatus:$status\tsize:$body_bytes_sent\treqtime:$request_time\tapptime:$upstream_response_time';
EOF
sudo cat /etc/nginx/conf.d/isucon14-ltsv.conf
```

?> 値の前に `time:` `host:` `method:` `uri:` `status:` `size:` `reqtime:` `apptime:` という**ラベルを必ず付けます**。タブ区切りで値だけを並べると、`alp` が列名を解釈できず、`--sort reqtime` や `--filter 'status == 200'` が動きません。この 8 つは `alp` が既定で認識する名前です。

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

成功の見え方: `time:2026-...` のようにラベル付きの値が `^I`（タブ）で並びます。`"GET /api/... HTTP/1.1" 401 ...` のような combined 形式の行は出ません。

## 2. `alp` を入れます

AMI には `alp` が入っていないので入れます（[../040_practice/0015-sql-tuning.md](../../040_practice/0015-sql-tuning.md) §1 と同じ手順です）。既にあれば入れ直しません。

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
  --addr ${APP_IP}:443 --payment-url http://${BENCH_PRIV}:12345 -s -t 60
```

回ったら 2 つめ（アプリ用）で集計します。

```bash
sudo tail -n 10000 /var/log/nginx/access.ltsv | alp ltsv --reverse --sort count
sudo tail -n 10000 /var/log/nginx/access.ltsv | alp ltsv \
  --filters 'status == 200' --sort sum --reverse \
  -m '/api/chair/rides/.*/status'
```

- 1 つめは「どのエンドポイントがいちばん多く呼ばれたか」を数えます
- 2 つめは「200 で返ったもののうち、合計時間が長かったもの」を出します

?> 2 つ、間違えやすい点があります。
>
> - `--sort` だけでは**昇順**に並びます。件数の多い順・遅い順に見たいときは `--reverse`（`-r`）を付けます
> - 条件は `--filter` ではなく **`--filters`**（`-f`）です。`--filter` はエラーになります
> - `POST /api/chair/rides/{ride_id}/status` のようにパスに ID が入る URI は、`-m '/api/chair/rides/.*/status'` を付けて 1 行にまとめます。付けないと ID ごとに別の行になり、集計になりません

## 4. 集計の例

`alp ltsv --reverse --sort count` の結果は次のようになります（数値は例です）。読み方は「上位のエンドポイントに実装が集中している」です。

```text
+-------+-----+-------+-----+-----+-----+--------+--------------------------+-------+-------+---------+-------+-------+-------+-------+--------+
| COUNT | 1XX |  2XX  | 3XX | 4XX | 5XX | METHOD |           URI            |  MIN  |  MAX  |   SUM   |  AVG  |  P90  |  P95  |  P99  | STDDEV |
+-------+-----+-------+-----+-----+-----+--------+--------------------------+-------+-------+---------+-------+-------+-------+-------+--------+
| 41253 | 0   | 41253 | 0   | 0   | 0   | GET    | /api/app/notification    | 0.001 | 0.088 | 402.145 | 0.010 | 0.021 | 0.030 | 0.052 | 0.009  |
| 12841 | 0   | 12841 | 0   | 0   | 0   | POST   | /api/app/rides           | 0.004 | 0.412 | 398.221 | 0.031 | 0.070 | 0.095 | 0.180 | 0.041  |
|  3677 | 0   |  3677 | 0   | 0   | 0   | GET    | /api/app/nearby-chairs   | 0.010 | 1.204 | 720.441 | 0.196 | 0.420 | 0.610 | 0.980 | 0.150  |
|  1802 | 0   |  1802 | 0   | 0   | 0   | POST   | /api/chair/coordinate    | 0.003 | 0.201 | 108.132 | 0.060 | 0.110 | 0.160 | 0.240 | 0.030  |
|   654 | 0   |   654 | 0   | 0   | 0   | GET    | /api/chair/notification  | 0.001 | 0.043 |  9.810  | 0.015 | 0.030 | 0.040 | 0.060 | 0.008  |
+-------+-----+-------+-----+-----+-----+--------+--------------------------+-------+-------+---------+-------+-------+-------+-------+--------+
```

`/api/app/notification` が 6 割を超えるのは、通知のポーリング間隔が 30 ms だからです（[公式 ISURIDE.md の通知エンドポイント](https://github.com/isucon/isucon14/blob/main/docs/ISURIDE.md)）。実装を直すならここが第 1 候補です。

`AVG` と `SUM` の列を合わせて見ます。呼ばれる回数が多くて `AVG` が小さいものは「まとめて直す」、回数は少なくて `SUM` が大きいものは「1 本のクエリを直す」対象です。

## 5. MySQL の slow log を短期で有効にします

Ubuntu の MySQL 8 は既定で slow log が無効です（公式の Ansible 投入手順にも設定がありません）。ベンチの間だけ有効にします。`SET GLOBAL` は再起動で元に戻るので、戻し忘れても恒久的な変更にはなりません。

まず現状を見ます。`long_query_time` は**セッション変数とグローバル変数の両方**にあるので、`SHOW GLOBAL VARIABLES` でグローバルの値を見ます。

```bash
mysql -uisucon -pisucon -e "
  SHOW GLOBAL VARIABLES WHERE Variable_name IN
  ('slow_query_log','long_query_time','slow_query_log_file','log_output');"
```

?> `SHOW VARIABLES`（`GLOBAL` なし）だと、その接続のセッション値が返ります。既定のセッション値は `10` なので、`long_query_time = 10` と表示されて「slow log が効いていない」と誤解します。`SET GLOBAL long_query_time = 0.1` を実行したあとも、`SHOW VARIABLES` を別の接続で叩くと `10` のままです。必ず `SHOW GLOBAL VARIABLES` を使ってください。

ログファイルを用意してから有効にします。

```bash
sudo mkdir -p /var/log/mysql
sudo touch /var/log/mysql/isuride-slow.log
sudo chown mysql:mysql /var/log/mysql/isuride-slow.log
mysql -uisucon -pisucon -e "
  SET GLOBAL slow_query_log_file = '/var/log/mysql/isuride-slow.log';
  SET GLOBAL long_query_time = 0.1;
  SET GLOBAL slow_query_log = ON;
  SHOW GLOBAL VARIABLES WHERE Variable_name IN ('slow_query_log','long_query_time');"
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

### 実測で何が見えたか

この教材の環境で 1 本回したときの実測です。

- 0.1 秒以上かかったクエリは約 240 件
- そのうち大半（約 230 件）が `COMMIT`。1 本の SQL ではなく、トランザクションの終了が遅れています
- いちばん重かったのは `GET /api/owner/chairs` の椅子一覧（`chair_locations` 全体に窓関数をかけるクエリ）で、1 回あたり 10〜21 秒
- その結果、ベンチのオーナー役が `CODE=25`（`GET /api/owner/chairs` のタイムアウト）を 1 本あたり 20 件前後出します

?> `GET /api/owner/chairs` の改善はこの教材では扱いません。配車完了数に直結しないためです。ただし `種別エラー数` に `25` が出続けるので、余力があるときに `chair_locations` に索引を足して窓関数を支える手を試します。

スコアは、この章ではコードを変えないので 0050 の直後と同じ値になります。1 本では判断できないので、0060 は「計測の型を作る章」と考え、比べるための数字は次の章から取ります。

| 回 | score | tw | メモ |
| --- | --- | --- | --- |
| 0040 ベースライン（2026-10-17） | 661 | - | インデックスなし |
| 0050 インデックス 1 本 | 727 | - | |
| 0050 インデックス 9 本 | 1518 | - | |
| 0060 計測の型を作る（コード変更なし）1 本目 | 1567 | - | 0050 と同じ構成 |
| 0060 計測の型 2 本目 | 1304 | - | 同じ構成で 1567 から -263 |

?> 同じ構成を 2 回回して 1567 と 1304 でした。**数百点は平気で動きます。** 次の章からは 1 本の数字だけで判断せず、`Questions` の差や不満率と合わせて読みます。

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

回す前後の差が、そのベンチで発行された SQL の本数です。**0080-sql-n1.md**（この章はこれから） では「1 リクエストあたりのクエリ数」を、これで測ります。

## まとめ

1 手ごとの順番はこのとおりです。

```text
nginx のログを回転 → ベンチ 1 本 → alp / slow log / py-spy を読む → scores.txt に 1 行
```

## 出口

計測の型が固まったら [0055-measure-digest.md](0055-measure-digest.md) へ進みます。次は、`performance_schema` でもう一段細かく見ます。

?> この章の変更を残すなら、作業機で `backup/isucon14/s1` に積みます（[0015-backup.md](0015-backup.md) の手順 4）。
