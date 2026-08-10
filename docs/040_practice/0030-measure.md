# 0030 計測ツールチュートリアル

同じ EC2 で計測サイクルを手で回す。計測ツール（alp / slp / oha / mysql クライアント）の導入と、bench-prep → 公式ベンチ → `scores.txt` の回し方を固める。
[0010-env.md](0010-env.md) のやり直しではない。

順番: ツールを入れる → サイクルを回す。出口は [0040-cache.md](0040-cache.md)。

## 1. 計測ツールを入れる

入っているものは入れ直さない。無いものだけ足す。まず有無を見る:

```bash
command -v alp slp oha mysql mysqladmin pt-query-digest
alp --version 2>/dev/null; slp --version 2>/dev/null; oha --version 2>/dev/null; mysql --version 2>/dev/null
```

### apt にあるもの

```bash
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  htop sysstat git jq unzip curl ca-certificates python3-pip percona-toolkit netcat-openbsd
```

`mysql-client` が入ると `mysql` と `mysqladmin` が両方使える。`percona-toolkit` が入ると `pt-query-digest` が使える（`slp` が無いときの代替）。`netcat-openbsd` が入ると `nc` が使える（memcached の `stats` 確認で使う）。

### バイナリで入れるもの

バージョンは Releases の latest を見て書き換える。ここでは動作確認済みの値を置く。

```bash
ALP_VERSION=1.0.21
SLP_VERSION=0.2.1
OHA_VERSION=1.12.1

if ! command -v alp >/dev/null; then
  curl -fsSL -o /tmp/alp.zip \
    "https://github.com/tkuchiki/alp/releases/download/v${ALP_VERSION}/alp_linux_amd64.zip"
  unzip -o /tmp/alp.zip -d /tmp && sudo install -m 0755 /tmp/alp /usr/local/bin/alp
fi
if ! command -v slp >/dev/null; then
  curl -fsSL -o /tmp/slp.zip \
    "https://github.com/tkuchiki/slp/releases/download/v${SLP_VERSION}/slp_linux_amd64.zip"
  unzip -o /tmp/slp.zip -d /tmp && sudo install -m 0755 /tmp/slp /usr/local/bin/slp
fi
if ! command -v oha >/dev/null; then
  curl -fsSL -o /tmp/oha \
    "https://github.com/hatoo/oha/releases/download/v${OHA_VERSION}/oha-linux-amd64"
  sudo install -m 0755 /tmp/oha /usr/local/bin/oha
fi
command -v alp slp oha mysql pt-query-digest
```

GitHub に届かないときは作業機でバイナリを取って `/usr/local/bin` へ置く。

### mysql クライアントと env.sh

```bash
command -v mysql || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y mysql-client
```

アプリと同じ接続情報は `/home/isucon/env.sh` にある（`export` が付いていないので読み込む前に `set -a` する）。private-isu の変数名は `ISUCONP_*`。

```bash
set -a
. /home/isucon/env.sh
set +a
mysql -h"${ISUCONP_DB_HOST:-127.0.0.1}" -P"${ISUCONP_DB_PORT:-3306}" \
  -u"$ISUCONP_DB_USER" -p"$ISUCONP_DB_PASSWORD" "$ISUCONP_DB_NAME"
```

`FLUSH` や `SET GLOBAL` は unix ソケット経由の root で実行すればよいことが多い。

```bash
sudo mysql -e 'SELECT @@version, @@slow_query_log, @@slow_query_log_file, @@long_query_time'
```

### 観察すること

- `command -v alp slp oha mysql pt-query-digest` が全部返る
- `mysql` でアプリと同じ接続先に入れる
- `sudo mysql -e 'SELECT ...'` で今の slow 設定値が見える

## 2. 計測サイクルを回す

ログを回してから公式ベンチ 1 本。点数を書く。空にする操作は **回転** であり、ログを止めることではない。

### bench-prep

```bash
TS=$(date +%Y%m%d%H%M%S)

if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
  sudo nginx -s reopen
fi

if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysqladmin flush-logs 2>/dev/null || sudo mysql -e 'FLUSH SLOW LOGS'

ls -l /var/log/nginx/access.log
ls -l /var/log/mysql/mysql-slow.log 2>/dev/null || echo 'slow log はまだ無い（slow を入れてから使う）'
df -h /
sudo du -xh /var/log /home /tmp 2>/dev/null | sort -h | tail -n 10
```

nginx のログは `truncate` で空にできるが履歴が消える。残して alp を見比べるなら `mv` + `nginx -s reopen` を使う。mysqld はファイルのオフセットを自分で持っているので slow log に `truncate -s 0` を使わない（途中が **NUL で埋まる**。ファイルだけ巨大になり、`slp` / `pt-query-digest` が壊れる）。

slow log の永続化（drop-in）は [0010-setup.md](../010_common/0010-setup.md#ログnginx-ltsv--mysql-slow) を見る。

### スロークエリ: ON、`long_query_time` は段階的に

ドリルではまず今の値を見て、`long_query_time` を最初から 0 のまま放置しない。

```bash
sudo mysql -e "SET GLOBAL slow_query_log = 1"
sudo mysql -e "SET GLOBAL slow_query_log_file = '/var/log/mysql/mysql-slow.log'"
sudo mkdir -p /var/log/mysql
sudo touch /var/log/mysql/mysql-slow.log
sudo chown mysql:mysql /var/log/mysql/mysql-slow.log
# まず遅いものだけ。ディスクを守る
sudo mysql -e "SET GLOBAL long_query_time = 1"
sudo mysql -N -e 'SELECT @@slow_query_log, @@slow_query_log_file, @@long_query_time'
```

短い全件キャプチャ（ベンチ 1 本だけ）:

```bash
# 上の bench-prep のあと
sudo mysql -e "SET GLOBAL long_query_time = 0"
# 公式ベンチ 1 本（次の節）
sudo slp my --file /var/log/mysql/mysql-slow.log
# 取り終わったら戻す
sudo mysql -e "SET GLOBAL long_query_time = 1"
df -h /
```

`SET GLOBAL` は再起動で消える。残したい場合は 0010 の drop-in に書く。`long_query_time=0` のスローログはベンチ 1 本で数百 MB〜GB になる。`df -h` を見て、残した `.log.$TS` も消さないと埋まる。

### 公式ベンチ

```bash
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
```

JSON の `score` / `pass` / `fail` を残す。`fail` が 0 でない点数は比べない。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  note=baseline" >> ~/bench-notes/scores.txt
```

| 回 | 時刻 | score | pass | fail | メモ |
| --- | --- | --- | --- | --- | --- |
| ベースライン |  |  |  |  | ログ ON、回転済み |

### alp / slp で読む

マッチャは `etc/alp/matching_groups.json`（作り方は [prompts/alp-matchers.md](../010_common/prompts/alp-matchers.md)）。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH"

sudo slp my --file /var/log/mysql/mysql-slow.log
# slp が無いとき
sudo pt-query-digest /var/log/mysql/mysql-slow.log
```

読み方:

- alp の Sum 上位と slp の examined/sent 比を見る
- Count も Sum も大きい → 呼ばれすぎ。[0040-cache.md](0040-cache.md)（キャッシュ）へ
- examined が sent の桁違い → 余計な行を読んでいる。[0100-sql-tuning.md](0100-sql-tuning.md)（インデックス）へ

### 観察すること

- `access.log` / `mysql-slow.log` が回転済みで、サイズが小さい（または 0 から増え始めた）
- `df -h /` に余裕がある
- ベンチが `pass: true` になること。これがこのあとの比較原点
- ログを回しただけで点数はほぼ変わらない（止めたわけではない）

### 残す変更は backup/s1 に積む

試して残すと決めた変更は `backup/s1` に入れて feature ブランチに積む。1 台でも流儀は [0020-split-2 §8](0020-split-2.md) と同じ（作業機で作る → push → pull → `cp` で移す。1 台では作業機とサーバが同じホスト）。

```bash
REPO=~/srv-backup
[ -d "$REPO/.git" ] || gh repo clone k-hoshihara/private-isu-terraform "$REPO"
cd "$REPO" && git fetch origin
git switch feature/1box 2>/dev/null || git switch -c feature/1box
git pull --ff-only 2>/dev/null || true
# 例: 残す my.cnf の drop-in を置く
mkdir -p backup/s1/etc/mysql/mysql.conf.d
sudo cp -a /etc/mysql/mysql.conf.d/99-isucon-flush.cnf backup/s1/etc/mysql/mysql.conf.d/
git add -A && git commit -m "1box: 残す設定" && git push -u origin feature/1box
```

戻すときは逆に `cp` で戻す。以後のチュートリアル（0040〜0110）でも残す変更はここに積む。
