# 0030 計測ツールチュートリアル

[0020-split-3.md](0020-split-3.md) の続きです。分割後の 3 台で、計測ツール（alp / slp / oha / mysql クライアント）を入れ、ログの形式を整え、bench-prep → 公式ベンチ → `scores.txt` のサイクルを手で回します。

?> [0020-split-3.md](0020-split-3.md)（起動順・再起動試験）が未完了でも、[0020-split-2.md](0020-split-2.md) の締めのベンチが `fail` 0 で通っていれば、この 0030 は進められます。

打つ台を分けます。1 台に全部ある想定では打てません（s1 に mysqld はいません）：

- s1（nginx 役）: bench-prep（nginx）→ 公式ベンチ（`-t http://localhost`）→ `alp` → `scores.txt`
- s3（DB 役）: bench-prep（slow）→ `SET GLOBAL` → `slp` / `pt-query-digest`
- 共有 memcached は s2 です（`backup/common/ips.sh` の `MC_PRIV`）。s1 の `127.0.0.1:11211` は遊んでいる別物なので見ません

順番: ツールを入れる（1.）→ ログの形式を整える（2.）→ サイクルを回す（3.）。出口は [0040-cache.md](0040-cache.md)。

## 1. 計測ツールを入れる

ツールが入っていれば入れ直しません。無いものだけ足します。まず `which` で有無を見ます:

```bash
which alp slp oha mysql mysqladmin pt-query-digest
alp --version 2>/dev/null
oha --version 2>/dev/null
mysql --version 2>/dev/null
slp --help >/dev/null && echo 'slp ok'
```

### apt にあるもの

```bash
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  htop sysstat git jq unzip curl ca-certificates python3-pip percona-toolkit netcat-openbsd
```

- `mysql-client` を入れると `mysql` と `mysqladmin` が両方使えます
- `percona-toolkit` を入れると `pt-query-digest` が使えます（`slp` が無いときの代替です）
- `netcat-openbsd` を入れると `nc` が使えます（memcached の `stats` 確認で使います）

### バイナリで入れるもの

バージョンは Releases の latest を見て書き換えます。ここでは動作確認済みの値を置きます。

```bash
ALP_VERSION=1.0.21
SLP_VERSION=0.2.1
OHA_VERSION=1.12.1

if ! which alp >/dev/null; then
  curl -fsSL -o /tmp/alp.zip \
    "https://github.com/tkuchiki/alp/releases/download/v${ALP_VERSION}/alp_linux_amd64.zip"
  unzip -o /tmp/alp.zip -d /tmp && sudo install -m 0755 /tmp/alp /usr/local/bin/alp
fi
if ! which slp >/dev/null; then
  curl -fsSL -o /tmp/slp.zip \
    "https://github.com/tkuchiki/slp/releases/download/v${SLP_VERSION}/slp_linux_amd64.zip"
  unzip -o /tmp/slp.zip -d /tmp && sudo install -m 0755 /tmp/slp /usr/local/bin/slp
fi
if ! which oha >/dev/null; then
  curl -fsSL -o /tmp/oha \
    "https://github.com/hatoo/oha/releases/download/v${OHA_VERSION}/oha-linux-amd64"
  sudo install -m 0755 /tmp/oha /usr/local/bin/oha
fi
which alp slp oha mysql pt-query-digest
```

GitHub に届かないときは作業機でバイナリを取って `/usr/local/bin` へ置きます。

### mysql クライアントと env.sh

```bash
which mysql || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y mysql-client
```

アプリと同じ接続情報は `/home/isucon/env.sh` にあります（`export` が付いていないので読み込む前に `set -a` します）。private-isu の変数名は `ISUCONP_*` です。

```bash
set -a
. /home/isucon/env.sh
set +a
mysql -h"${ISUCONP_DB_HOST:-127.0.0.1}" -P"${ISUCONP_DB_PORT:-3306}" \
  -u"$ISUCONP_DB_USER" -p"$ISUCONP_DB_PASSWORD" "$ISUCONP_DB_NAME"
```

`FLUSH` や `SET GLOBAL` は mysqld がいる s3 で `sudo mysql -e '...'` を打ちます（s1・s2 に mysqld はいません）。反映は `SELECT @@slow_query_log, ...` で確認します。

```bash
# s3 で打つ
sudo mysql -e 'SELECT @@version, @@slow_query_log, @@slow_query_log_file, @@long_query_time'
```

### 観察すること

- `which alp slp oha mysql pt-query-digest` が全部返る（打つ台ごとに確認します。alp は s1、slp は s3）
- `mysql` でアプリと同じ接続先に入れる
- s3 の `sudo mysql -e 'SELECT ...'` で今の slow 設定値が見える

## 2. ログの形式を整える（s1: nginx LTSV / s3: slow log）

alp は LTSV 形式のアクセスログを読みます。素体の nginx は combined 形式なので、そのままでは `alp ltsv` が空の表しか返しません。slow log も素体ではコメントアウトされています。分割後は nginx が s1、mysqld が s3 にいるので、台ごとに入れます。最後に、s3 で初期状態のスキーマとインデックスを記録します。

設定ファイルはサーバで直接編集しません。作業機で `backup/s1`・`backup/s3` に作る → push → 各台で pull → コピー → 反映、の順にします（[0020-split-2.md](0020-split-2.md) §8 と同じ流れです）。以下の `BR` は自分の feature ブランチ名に置き換えます。

### 確認: もう入っているか

入っていれば 2. は飛ばします。

```bash
# s1 で打つ。ltsv の行が 2 つ（log_format と access_log）出れば LTSV 化済み
sudo nginx -T 2>/dev/null | grep -n 'ltsv' || echo '未LTSV'
```

```bash
# s3 で打つ。1 と /var/log/mysql/mysql-slow.log が出れば slow は有効
sudo mysql -N -e 'SELECT @@slow_query_log, @@slow_query_log_file, @@long_query_time'
```

### s1: nginx を LTSV にする

作業機でファイルを 2 つ作ります。フォーマット定義（`ltsv-format.conf`）と、それを読み込む `nginx.conf` です。

?> `log_format` は使う場所（`access_log` の行）より前で定義する必要があります。`conf.d/` は `access_log` より後で読み込まれるので、`conf.d/` に置くと `nginx -t` が `unknown log format "ltsv"` で失敗します。フォーマット定義は別ファイルにして、`mime.types` の直後で読み込みます。

```bash
# 作業機（手元の private-isu-terraform）で打つ
BR=feature/work-<名前>
git switch "$BR" && git pull --ff-only
mkdir -p backup/s1/etc/nginx
cp etc/nginx/ltsv.conf backup/s1/etc/nginx/ltsv-format.conf
# s1 の nginx.conf が backup/s1 に無ければ素体から持ってくる（分割で nginx.conf 自体は変えていない）
[ -f backup/s1/etc/nginx/nginx.conf ] || cp backup/base/etc/nginx/nginx.conf backup/s1/etc/nginx/nginx.conf

F=backup/s1/etc/nginx/nginx.conf
# mime.types の直後に include を 1 行足す（2 回打っても増えない）
grep -q 'ltsv-format.conf' "$F" || \
  perl -i -pe 's#^(\s*)include /etc/nginx/mime.types;#$&\n$1include /etc/nginx/ltsv-format.conf;#' "$F"
# access_log の形式を ltsv にする
perl -i -pe 's#access_log /var/log/nginx/access.log;#access_log /var/log/nginx/access.log ltsv;#' "$F"
grep -n 'ltsv' "$F"
```

成功すると `include /etc/nginx/ltsv-format.conf;` と `access_log /var/log/nginx/access.log ltsv;` の 2 行が出ます。出なければ `nginx.conf` の該当行の書き方が素体と違うので、エディタで直します。

```bash
# 作業機
git add backup/s1/etc/nginx && git commit -m "measure: s1 の nginx を LTSV に" && git push
```

s1 で反映します。変える前に `.orig` を残し、`nginx -t` が通ってから reload します。

```bash
# s1 で打つ
REPO=/home/isucon/private-isu-terraform
BR=feature/work-<名前>
cd "$REPO" && git fetch origin && git switch "$BR" && git pull --ff-only

sudo test -f /etc/nginx/nginx.conf.orig || sudo cp -a /etc/nginx/nginx.conf /etc/nginx/nginx.conf.orig
sudo install -m 0644 -o root -g root "$REPO/backup/s1/etc/nginx/ltsv-format.conf" /etc/nginx/ltsv-format.conf
sudo install -m 0644 -o root -g root "$REPO/backup/s1/etc/nginx/nginx.conf" /etc/nginx/nginx.conf
sudo nginx -t && sudo systemctl reload nginx
```

`nginx -t` が `syntax is ok` と `test is successful` を返せば成功です。失敗したら `sudo cp -a /etc/nginx/nginx.conf.orig /etc/nginx/nginx.conf` で戻し、`sudo nginx -t` を通してから原因を見ます。

LTSV になったか確かめます。LTSV 化より前の combined 形式の行は `alp ltsv` が読めないので、ここでログを 1 回回します。

```bash
# s1 で打つ
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
curl -s -o /dev/null http://127.0.0.1/
sudo tail -1 /var/log/nginx/access.log | cut -c1-120
```

成功すると `time:2026-...	host:127.0.0.1	...` のように `項目名:値` がタブ区切りで並んだ行が出ます。`127.0.0.1 - - [...] "GET / HTTP/1.1"` の形なら、まだ combined です。

### s3: slow log を有効にする

作業機で drop-in を作ります。`long_query_time` は 1 にします。0 にするとベンチ 1 本で数百 MB〜GB になり、ディスクが埋まります（全件を取るときは 3. で一時的に 0 にします）。

```bash
# 作業機で打つ
mkdir -p backup/s3/etc/mysql/mysql.conf.d
cat > backup/s3/etc/mysql/mysql.conf.d/99-isucon-slow.cnf <<'EOF'
[mysqld]
slow_query_log = 1
slow_query_log_file = /var/log/mysql/mysql-slow.log
long_query_time = 1
log_queries_not_using_indexes = 0
EOF
git add backup/s3/etc/mysql && git commit -m "measure: s3 の slow log を有効に" && git push
```

?> 素体の `mysqld.cnf` では slow 関連の行がすべてコメントアウトされているので、`99-` の名前でも打ち消されません（`zz-bind-all.cnf` のように後勝ちを気にする必要はありません）。

s3 で反映します。mysqld を restart するので、そのあとアプリ（s1 と s2 の `isu-python`）も restart します。アプリは DB 接続を使い回すので、しないと古い接続のまま 500 になります。

```bash
# s3 で打つ
REPO=/home/isucon/private-isu-terraform
BR=feature/work-<名前>
cd "$REPO" && git fetch origin && git switch "$BR" && git pull --ff-only

sudo install -m 0644 -o root -g root \
  "$REPO/backup/s3/etc/mysql/mysql.conf.d/99-isucon-slow.cnf" /etc/mysql/mysql.conf.d/99-isucon-slow.cnf
sudo mkdir -p /var/log/mysql
sudo touch /var/log/mysql/mysql-slow.log
sudo chown mysql:mysql /var/log/mysql/mysql-slow.log
sudo systemctl restart mysql
sudo mysql -N -e 'SELECT @@slow_query_log, @@slow_query_log_file, @@long_query_time'
```

成功すると `1	/var/log/mysql/mysql-slow.log	1.000000` が出ます。mysqld が起きないときは `sudo rm /etc/mysql/mysql.conf.d/99-isucon-slow.cnf` で外して restart し、`sudo journalctl -u mysql -n 80 --no-pager` を見ます。

```bash
# s1 と s2 で打つ
sudo systemctl restart isu-python.service
```

```bash
# s1 で打つ。200 が返れば、s1 → s2 → s3 の経路が生きている
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/
```

### s3: スキーマとインデックスを記録する（初期状態）

チューニングを始める前に、テーブル定義（スキーマ）と今あるインデックスをファイルに書き出して git に残します。これが比較の原点です。リポジトリ内に `.sql` は無いので、実 DB から取ります。以後インデックスを足したら同じコマンドで書き出し直し、`git diff` で増減を見ます（[0100-sql-tuning.md](0100-sql-tuning.md) の 3.）。

?> リポジトリは isucon 所有です。SSM セッションで `sudo su - isucon` してから打ちます。root のまま書き出すとファイルが root 所有になり、`git pull` が失敗します。DB 名は `/home/isucon/env.sh` の `ISUCONP_DB_NAME`（素体では `isuconp`）です。

まず画面で見ます。テーブルごとの行数（概算）と、インデックスの一覧です。

```bash
# s3 で打つ
sudo mysql -t isuconp -e "SELECT table_name, table_rows FROM information_schema.tables
  WHERE table_schema = DATABASE() ORDER BY table_name"
sudo mysql -t isuconp -e "SELECT table_name, index_name, non_unique,
  GROUP_CONCAT(column_name ORDER BY seq_in_index) AS columns
  FROM information_schema.statistics WHERE table_schema = DATABASE()
  GROUP BY table_name, index_name, non_unique ORDER BY table_name, index_name"
```

`posts`・`comments`・`users` などのテーブルと、それぞれの `PRIMARY` が出れば成功です。`non_unique` が 0 のものは一意制約（例: `users.account_name`）です。主キー以外にどの列にインデックスが無いかを、ここで確認しておきます。

次にファイルへ書き出します。`AUTO_INCREMENT=` の値はデータ件数で毎回変わり、差分が読みにくくなるので消します。

```bash
# s3 で打つ（isucon で）
REPO=/home/isucon/private-isu-terraform
mkdir -p "$REPO/backup/s3/sql"
sudo mysqldump --no-data --skip-comments --skip-dump-date isuconp \
  | sed -E 's/ AUTO_INCREMENT=[0-9]+//' > "$REPO/backup/s3/sql/schema.sql"
sudo mysql -N isuconp -e "SELECT table_name, index_name, non_unique,
  GROUP_CONCAT(column_name ORDER BY seq_in_index)
  FROM information_schema.statistics WHERE table_schema = DATABASE()
  GROUP BY table_name, index_name, non_unique ORDER BY table_name, index_name" \
  > "$REPO/backup/s3/sql/indexes-current.txt"
grep -c 'CREATE TABLE' "$REPO/backup/s3/sql/schema.sql"
cat "$REPO/backup/s3/sql/indexes-current.txt"
```

`grep -c` がテーブル数（0 でない数）を返し、`indexes-current.txt` に画面で見たのと同じ一覧が入っていれば成功です。

s3 から push します。作業機からも同じブランチに push しているので、先に `pull --rebase` します。

```bash
# s3 で打つ（isucon で）
BR=feature/work-<名前>
cd "$REPO"
git add backup/s3/sql
git commit -m "s3: 初期スキーマとインデックスを記録"
git pull --rebase origin "$BR"
git push origin "$BR"
```

作業機で `git pull --ff-only` し、`backup/s3/sql/` の 2 ファイルが取り込まれたことを確認します。

### 観察すること

- s1 の `access.log` の最新行が `time:...` で始まる LTSV になっている
- s3 で `@@slow_query_log` が 1、`@@long_query_time` が 1 になっている
- s1 の `curl` が 200 を返す
- `backup/s1/etc/nginx/` と `backup/s3/etc/mysql/mysql.conf.d/` に、反映したファイルが入っている
- `backup/s3/sql/schema.sql` と `indexes-current.txt` が git に入り、初期状態でどの列にインデックスがあるかを言える

## 3. 計測サイクルを回す

ログを回してから s1 で公式ベンチを 1 本回します。slow 側の操作は s3 で打ちます。点数を `~/bench-notes/scores.txt` に書きます。空にする操作はログの**回転（`mv` + `reopen`）**であり、ログ停止ではありません。

?> `aws ssm send-command` は root で実行されます。リポジトリは isucon 所有なので、git を触るときは `sudo -u isucon` を付けるか、SSM セッションで isucon になってから打ちます。

### bench-prep（s1: nginx 側）

```bash
# s1 で打つ
TS=$(date +%Y%m%d%H%M%S)

if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
  sudo nginx -s reopen
fi

ls -l /var/log/nginx/access.log
df -h /
sudo du -xh /var/log /home /tmp 2>/dev/null | sort -h | tail -n 10
```

### bench-prep（s3: slow 側）

```bash
# s3 で打つ。s1 に mysqld はいないので s1 では打てません
TS=$(date +%Y%m%d%H%M%S)

if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysqladmin flush-logs 2>/dev/null || sudo mysql -e 'FLUSH SLOW LOGS'

ls -l /var/log/mysql/mysql-slow.log 2>/dev/null || echo 'slow log はまだ無い（slow を入れてから使う）'
df -h /
```

nginx のログは `mv` + `nginx -s reopen` で回します。mysqld は slow log に `truncate -s 0` を使いません（途中が **NUL で埋まります**。ファイルだけ巨大になり、`slp` / `pt-query-digest` が壊れます）。

### slow log の設定を確認する（s3 で打つ）

slow log は 2. の drop-in で有効になっています（`long_query_time = 1`。1 秒以上かかったクエリだけを記録します）。ベンチの前に値を確認します。

```bash
# s3 で打つ。1  /var/log/mysql/mysql-slow.log  1.000000 が出れば OK
sudo mysql -N -e 'SELECT @@slow_query_log, @@slow_query_log_file, @@long_query_time'
```

`0` が出たら 2. の「s3: slow log を有効にする」に戻ります。

### 全件を短く取る（ベンチ 1 本だけ）

1 秒未満のクエリも含めて全部取りたいときは、`SET GLOBAL` で一時的に `long_query_time` を 0 にします。`SET GLOBAL` は既存の接続には効かず、新しい接続から効くので、変えたらアプリ（s1 と s2 の `isu-python`）を restart してから測ります。mysqld を再起動すると drop-in の 1 に戻ります。

```bash
# 上の bench-prep（s3 側）のあと。s3 で打つ
sudo mysql -e "SET GLOBAL long_query_time = 0"
# s1 と s2 で sudo systemctl restart isu-python.service（新規接続に 0 を効かせる）
# s1 で公式ベンチ 1 本（次の節）。s3 で読む
sudo slp my --file /var/log/mysql/mysql-slow.log
# 取り終わったら戻す
sudo mysql -e "SET GLOBAL long_query_time = 1"
df -h /
```

取り終わったら `long_query_time` を 1 に戻し、`df -h /` と回転済みの `.log.$TS` のサイズを確認します。`=0` のままだとログでディスクが埋まります。

### 公式ベンチ（s1 で打つ）

分割後のベンチ先は nginx 役（s1）の `localhost` です。s2・s3 の `localhost` には nginx がいないので打てません。

```bash
# s1 で打つ
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
```

JSON の `score` / `pass` / `fail` を残します。`fail` が 0 でない点数は比べません。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  note=baseline" >> ~/bench-notes/scores.txt
```

| 回 | 時刻 | score | pass | fail | メモ |
| --- | --- | --- | --- | --- | --- |
| ベースライン |  |  |  |  | ログ ON、回転済み |

### alp / slp で読む（alp は s1、slp は s3）

マッチャは `etc/alp/matching_groups.json`（作り方は [prompts/alp-matchers.md](../010_common/prompts/alp-matchers.md)）。

```bash
# s1 で打つ。2. で LTSV 化済みが前提
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH"
```

```bash
# s3 で打つ
sudo slp my --file /var/log/mysql/mysql-slow.log
# slp が無いとき
sudo pt-query-digest /var/log/mysql/mysql-slow.log
```

`alp ltsv` が空の表だけ返すときは、LTSV 化前のログが残っているか、LTSV 化が反映されていません。`sudo tail -1 /var/log/nginx/access.log` が `time:` で始まるかを見て、始まらなければ 2. の「s1: nginx を LTSV にする」に戻ります。

読み方:

- alp の Sum 上位と slp の examined/sent 比を見る
- Count も Sum も大きい → 呼ばれすぎ。[0040-cache.md](0040-cache.md)（キャッシュ）へ
- examined が sent の桁違い → 余計な行を読んでいる。[0100-sql-tuning.md](0100-sql-tuning.md)（インデックス）へ

### 観察すること

- `access.log` / `mysql-slow.log` が回転済みで、サイズが小さい（または 0 から増え始めた）
- `df -h /` に余裕がある
- ベンチが `pass: true` になること。これがこのあとの比較原点
- ログを回しただけで点数はほぼ変わらない（止めたわけではない）

### 残す変更は backup/s1・s2・s3 に積む

試して残すと決めた変更は台ごとの `backup/s1`・`s2`・`s3` に入れて feature ブランチに積みます。流儀は [0020-split-2.md](0020-split-2.md) §8 と同じです（作業機で作る → push → 各台で pull → `cp` で移します）。

```bash
# 作業機（手元の private-isu-terraform）で打つ
BR=feature/work-<名前>   # 2. と同じ、自分の feature ブランチ
git fetch origin
git switch "$BR" && git pull --ff-only
# 例: 残す my.cnf の drop-in を置く（s3 の DB 用なら backup/s3）
mkdir -p backup/s3/etc/mysql/mysql.conf.d
# 各台で pull したあと sudo install で /etc へ移す（2. の反映手順と同じ）
git add -A && git commit -m "tune: 残す設定" && git push
```

成功すると、各台で `git pull --ff-only` したときに追加したファイルが取り込まれます。

戻すときは逆に `cp` で戻す。以後のチュートリアル（0040〜0110）でも残す変更はここに積む。
