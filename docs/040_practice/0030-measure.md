# 0030 計測ツールチュートリアル

分割後（[0020-split-1.md](0020-split-1.md)〜[0020-split-3.md](0020-split-3.md) 済み）を前提に、計測サイクルを手で回します。計測ツール（alp / slp / oha / mysql クライアント）の導入と、bench-prep → 公式ベンチ → `scores.txt` の回し方を固めます。
環境構築は [0010-env.md](0010-env.md) で終えています。ここでは計測だけを行います。

打つ台を分けます。1 台に全部ある想定では打てません（s1 に mysqld はいません）：

- s1（nginx 役）: bench-prep（nginx）→ 公式ベンチ（`-t http://localhost`）→ `alp` → `scores.txt`
- s3（DB 役）: bench-prep（slow）→ `SET GLOBAL` → `slp` / `pt-query-digest`
- 共有 memcached は s2 です（`backup/common/ips.sh` の `MC_PRIV`）。s1 の `127.0.0.1:11211` は遊んでいる別物なので見ません

順番: ツールを入れる → サイクルを回す。出口は [0040-cache.md](0040-cache.md)。

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

## 2. 計測サイクルを回す

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

slow log の永続化（drop-in）は [0010-setup.md](../010_common/0010-setup.md) の「ログ（nginx LTSV / MySQL slow）」節を見ます。

### スロークエリを ON にする（s3 で打つ）

`long_query_time` を最初から 0 にはしません（ベンチ 1 本で数百 MB〜GB になりディスクが埋まるため）。まず 1 から始めます。

```bash
# s3 で打つ
sudo mysql -e "SET GLOBAL slow_query_log = 1"
sudo mysql -e "SET GLOBAL slow_query_log_file = '/var/log/mysql/mysql-slow.log'"
sudo mkdir -p /var/log/mysql
sudo touch /var/log/mysql/mysql-slow.log
sudo chown mysql:mysql /var/log/mysql/mysql-slow.log
# まず遅いものだけ。ディスクを守る
sudo mysql -e "SET GLOBAL long_query_time = 1"
sudo mysql -N -e 'SELECT @@slow_query_log, @@slow_query_log_file, @@long_query_time'
```

`SET GLOBAL` は既存の接続には効きません。新規接続から効くので、変えたらアプリ（s1 と s2 の `isu-python`）を restart してから測ります。再起動で消えるので、残したい場合は [0010-setup.md](../010_common/0010-setup.md) の「ログ（nginx LTSV / MySQL slow）」節の drop-in に書きます。

短い全件キャプチャ（ベンチ 1 本だけ）:

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
# s1 で打つ。LTSV 化（[0010-setup.md](../010_common/0010-setup.md) の「ログ」節）済みが前提
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH"
```

```bash
# s3 で打つ
sudo slp my --file /var/log/mysql/mysql-slow.log
# slp が無いとき
sudo pt-query-digest /var/log/mysql/mysql-slow.log
```

`alp ltsv` が空表だけ返すときは、LTSV 化前のログが残っています。LTSV 化してからログを回し直します。

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
git fetch origin
git switch feature/backup 2>/dev/null || git switch -c feature/backup
git pull --ff-only 2>/dev/null || true
# 例: 残す my.cnf の drop-in を置く（s3 の DB 用なら backup/s3）
mkdir -p backup/s3/etc/mysql/mysql.conf.d
# 各台で pull したあと sudo cp -a で /etc へ移す
git add -A && git commit -m "tune: 残す設定" && git push -u origin feature/backup
```

戻すときは逆に `cp` で戻す。以後のチュートリアル（0040〜0110）でも残す変更はここに積む。
