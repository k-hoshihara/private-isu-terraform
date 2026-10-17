# 0015 SQLチューニング（単一構成）

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）は、手元の private-isu-terraform リポジトリで実行します。

- データベース（DB）が返す行を減らして、点数を上げます。
- この章は、[0010-env.md](0010-env.md) で立てた **1 台の状態**で進めます。
- 役割を分ける前です。`sudo mysql` も `curl` も `alp` も、同じ台で打てます。

**SQL の調整を先にやる理由**

- 索引（インデックス。検索を速くするための目次）の追加と N+1（同じ問い合わせが、表示する行の数だけ繰り返される問題）の解消は、後段の調整（キャッシュ・keepalive・worker 数）より効果が大きいです。
- 影響は、DB の定義 1 箇所とアプリの 1 関数に収まります。
- 複数台に分けたあとにやると、どの変更が点数に効いたかの切り分けが難しくなります。

- 順番: 計測ツール（1.）→ Python 切替（2.）→ 計測サイクル（3.）→ 対象クエリを 1 本に決める（4.）→ EXPLAIN で読む（5.）→ インデックスを 1 本貼る（6.）→ N+1 を一括化する（7.）→ 公式ベンチで判定する（8.）。
- 1 つずつ進めます。
- `fail`（エラーの件数）が 0 でない点数は、比べません。
- 出口は [0020-measure.md](0020-measure.md) です。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  note=sql-baseline" >> ~/bench-notes/scores.txt
```

<a id="1-計測ツールを入れる"></a>

## 1. 計測ツールを入れる

- ツールが入っていれば、入れ直しません。無いものだけ足します。
- まず `which` で有無を見ます。

```bash
which alp slp oha mysql mysqladmin pt-query-digest
alp --version 2>/dev/null
oha --version 2>/dev/null
mysql --version 2>/dev/null
slp --help >/dev/null && echo 'slp ok'
```

apt で入るもの:

```bash
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  htop sysstat git jq unzip curl ca-certificates python3-pip percona-toolkit netcat-openbsd
```

- `mysql-client` を入れると `mysql` と `mysqladmin` が両方使えます
- `percona-toolkit` を入れると `pt-query-digest` が使えます（`slp` が無いときの代替です）
- `netcat-openbsd` を入れると `nc` が使えます

- バイナリ（そのまま動くプログラム）で入れるものは、下のとおりです。
- バージョンは Releases の latest を見て書き換えます。ここでは、動作確認済みの値を置きます。

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

- mysql クライアントと、アプリの接続情報を見ます。
- `/home/isucon/env.sh` には `export` が付いていません。読み込む前に `set -a` します。

```bash
which mysql || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y mysql-client

set -a
. /home/isucon/env.sh
set +a
mysql -h"${ISUCONP_DB_HOST:-127.0.0.1}" -P"${ISUCONP_DB_PORT:-3306}" \
  -u"$ISUCONP_DB_USER" -p"$ISUCONP_DB_PASSWORD" "$ISUCONP_DB_NAME"
```

- 1 台に全部入った構成では、mysqld が同じ台にあります。`sudo mysql` で、そのまま入れます。
- 今の slow 設定（遅い問い合わせを記録する設定）を確認します。

```bash
sudo mysql -e 'SELECT @@version, @@slow_query_log, @@slow_query_log_file, @@long_query_time'
```

### ログを LTSV にする

- `alp ltsv` が読めるように、nginx のアクセスログを LTSV 形式（1 行を「項目名:値」の組で並べる形式）にします。
- AMI の既定は `combined` です。このままだと、`alp ltsv` が空表を返します。
- 変更前に、`.orig`（変更前のコピー）を残します。

```bash
# 変更前の確認。combined のままだと log_format が無い
sudo nginx -T 2>/dev/null | grep -E 'log_format|access_log' | head -5

sudo test -f /etc/nginx/nginx.conf.orig || sudo cp -a /etc/nginx/nginx.conf /etc/nginx/nginx.conf.orig
```

- フォーマット定義を、`/etc/nginx/ltsv-format.conf` に置きます。
- `cache` は、nginx 自身の判定（`$upstream_cache_status`）を見ます。

```bash
sudo tee /etc/nginx/ltsv-format.conf >/dev/null <<'CONF'
log_format ltsv "time:$time_iso8601\t"
                "host:$remote_addr\t"
                "forwardedfor:$http_x_forwarded_for\t"
                "req:$request\t"
                "method:$request_method\t"
                "uri:$request_uri\t"
                "status:$status\t"
                "size:$body_bytes_sent\t"
                "referer:$http_referer\t"
                "ua:$http_user_agent\t"
                "reqtime:$request_time\t"
                "runtime:$upstream_http_x_runtime\t"
                "apptime:$upstream_response_time\t"
                "cache:$upstream_cache_status\t"
                "vhost:$host";
CONF
```

- `log_format` は、使う場所より前で定義します。
- `conf.d/` は、`access_log` の行より後で読み込まれます。`mime.types` の直後に読み込ませます。
- **定義を読む前に `access_log ... ltsv` を書くと、`unknown log_format "ltsv"` で起動に失敗します。**

```bash
# include を mime.types の直後に足す（重複しないよう存在確認）
sudo grep -qF 'include /etc/nginx/ltsv-format.conf;' /etc/nginx/nginx.conf || \
  sudo sed -i '\#include /etc/nginx/mime.types;#a include /etc/nginx/ltsv-format.conf;' /etc/nginx/nginx.conf
sudo nginx -t || exit 1

# 上の nginx -t が通ってから access_log を書き換える
sudo sed -i -E 's#access_log[[:space:]]+/var/log/nginx/access\.log([[:space:]]+combined)?;#access_log /var/log/nginx/access.log ltsv;#' /etc/nginx/nginx.conf
sudo nginx -t && sudo systemctl reload nginx
```

- 1 リクエストして、LTSV になっていることを確認します。
- それ以前の combined 行は、`alp ltsv` が捨てます。ログを回します。

```bash
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
fi
sudo nginx -s reopen
curl -fsS -o /dev/null http://127.0.0.1/login
sudo tail -1 /var/log/nginx/access.log | cut -c1-140
```

### 観察すること

- `which alp slp oha mysql pt-query-digest` が全部返ること
- `mysql` でアプリと同じ接続先に入れること
- `sudo mysql -e 'SELECT ...'` で今の slow 設定値が見えること

<a id="2-python-に切り替える"></a>

## 2. Python に切り替える

- AMI 直後は、Ruby です。
- N+1 の解消は、Python アプリのコードを変える手順です。先に Python に切り替えます。
- 起動するサービスの名前（unit）は回ごとに違います。先に一覧を見ます。

```bash
systemctl list-unit-files --type=service
systemctl list-unit-files --type=service | grep -Ei 'isu'
systemctl cat isu-python.service
```

```bash
# 他の service を停止させ、gunicorn の service だけ起動させる
sudo systemctl disable --now isu-ruby.service
sudo systemctl disable --now isu-go.service 2>/dev/null || true
sudo systemctl disable --now isu-node.service 2>/dev/null || true
sudo systemctl enable --now isu-python.service

# 起動確認
systemctl list-unit-files --type=service | grep -Ei 'isu'

cd /home/isucon/private_isu/webapp/python

# AMI 同梱の venv を使う。uv が無くても黙って成功扱いにしない
if command -v uv >/dev/null; then
  sudo -u isucon -H uv sync
else
  test -x .venv/bin/python3 && test -x .venv/bin/gunicorn || exit 1
  echo 'uv は未導入。AMI 同梱の .venv を使用します'
fi

curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/ || \
  sudo journalctl -u isu-python.service -n 80 --no-pager
```

?> AMI に `uv` は入っていません。`uv sync` の行は、条件分岐で飛ばされます。AMI 同梱の `.venv`（Python のライブラリ置き場）が、そのまま使われます。

### 観察すること

- `isu-ruby` が停止・無効、`isu-python` が起動・有効であること
- `curl http://127.0.0.1:8080/` が 200 を返すこと
- 失敗したときは `sudo journalctl -u isu-python.service -n 80 --no-pager` で理由を見ること

## 3. 計測サイクルを回す

- ログを回してから、公式ベンチを 1 本回します。
- 空にする操作は、ログの**回転（`mv` + `reopen`）**です。ログ停止ではありません。
- 点数は、`~/bench-notes/scores.txt` に書きます。

### bench-prep

- ベンチ前の準備（bench-prep）として、nginx のアクセスログと MySQL の slow log（遅い問い合わせの記録）を、同じ台の両方で回します。

```bash
# nginx 側
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
fi
sudo nginx -s reopen

# MySQL 側
if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysql -e 'FLUSH SLOW LOGS'

ls -l /var/log/nginx/access.log
sudo ls -l /var/log/mysql/mysql-slow.log
df -h /
```

?> `FLUSH SLOW LOGS` は、ログを閉じて同じパスを開き直します。日付付きファイルへの変更は、その前の `mv` が行います。`FLUSH` だけでは、古い内容は消えません。

### slow log を ON にする

- `long_query_time`（この秒数を超えた問い合わせだけを記録する、という基準）を、最初から 0 にはしません。
- ベンチ 1 本で数百 MB〜GB になり、ディスクが埋まるためです。
- まず 1 から始めます。

```bash
sudo mysql -e "SET GLOBAL slow_query_log = 1"
sudo mysql -e "SET GLOBAL slow_query_log_file = '/var/log/mysql/mysql-slow.log'"
sudo mysql -N -e 'SELECT @@slow_query_log, @@slow_query_log_file, @@long_query_time'
```

- `SET GLOBAL long_query_time` は、既につながっている接続の値を変えません。
- 変えるときは、アプリ（`isu-python`）を restart してから測ります。
- restart すると、新しい接続に効きます。

短い全件キャプチャ（速い問い合わせも全部記録する取り方。ベンチ 1 本だけ）:

```bash
sudo mysql -e "SET GLOBAL long_query_time = 0"
sudo systemctl restart isu-python.service   # 新規接続に 0 を効かせる
# 公式ベンチを 1 本回す。単一構成なので同じ台で打つ
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
sudo mysql -e "SET GLOBAL long_query_time = 1"
sudo systemctl restart isu-python.service
sudo slp my --file /var/log/mysql/mysql-slow.log \
  --sort sum-query-time --reverse --output standard
df -h /
```

- 実測サンプル: 全件キャプチャ（`long_query_time = 0`）を伴うベンチです。
- 点数は、比較に使いません。比較の原点は、下の「公式ベンチ」の `score=943` です。

- 取り終わったら、`long_query_time` を 1 に戻します。
- `sudo systemctl restart isu-python.service` で、アプリを再起動します。
- `df -h /` と、回転済みの `.log.$TS` のサイズを確認します。

### 公式ベンチ

```bash
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
```

- JSON の `score` / `pass` / `fail` を残します。
- `fail` が 0 でない点数は、比べません。

```bash
echo "$(date -Iseconds)  score=  pass=  fail=  note=baseline" >> ~/bench-notes/scores.txt
```

#### 実行例：

2026-10-13、`c7a.large` 1 台 / `ap-northeast-1` / 単一構成（nginx + Python + MySQL が同じ台）/ `nproc=2` / メモリ 3.7Gi。LTSV 化済み・索引無し・N+1 解消前の状態です（新規 AMI 素体に撮り直した実測）。

```text
$ sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
    -u /home/isucon/private_isu/benchmarker/userdata \
    -t http://localhost
{"pass":true,"score":943,"success":810,"fail":0,"messages":[]}
```

alp の Sum 上位（この時点）:

| COUNT | URI | SUM | AVG | P99 |
| --- | --- | --- | --- | --- |
| 447 | `/image/[0-9]+` | 316.838 | 0.709 | 3.473 |
| 63 | `/` | 134.100 | **2.129** | 6.633 |
| 34 | `/favicon.ico` | 53.721 | 1.580 | 5.754 |
| 47 | `POST /login` | 39.085 | 0.832 | 3.540 |
| 34 | `/js/timeago.min.js` | 27.941 | 0.822 | 4.415 |

- 静的ファイルまで AVG 0.7〜2.1 秒なのは、1 台に全部入った構成で nginx・gunicorn・MySQL が同じ 2 vCPU を奪い合うためです。
- `/` の AVG 2.129 秒が、最も重い動的経路です。

### 観察すること

- `access.log` / `mysql-slow.log` が回転済みで、サイズが小さい（または 0 から増え始めた）こと
- `df -h /` に余裕があること
- ベンチが `pass: true` になること。これがこのあとの比較原点です
- ログを回しただけで点数はほぼ変わらないこと（止めたわけではない）

<a id="4-対象クエリを-1-本に決める"></a>

## 4. 対象クエリを 1 本に決める

- `slp` / `pt-query-digest`（遅い問い合わせの記録を集計するツール）で、Response time（合計時間）の 1 位を見ます。
- `Calls`（回数）と `R/Call`（1 回あたりの時間）で、「呼ばれすぎか」「1 回が重いか」を分けます。

```bash
sudo mysql -e "SET GLOBAL long_query_time = 0"
sudo systemctl restart isu-python.service
# 公式ベンチ 1 本
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
sudo mysql -e "SET GLOBAL long_query_time = 1"
sudo systemctl restart isu-python.service
sudo slp my --file /var/log/mysql/mysql-slow.log \
  --sort sum-query-time --reverse --output standard
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 5 | head -60
df -h /
```

- 実測サンプル: 同じく全件キャプチャ中です。点数は、比較に使いません。
- この回は、slp / pt-query-digest の中身を読みます。

?> `alp` と `slp` の **`--limit` は、上位 N 件を出す引数ではありません**。結果の件数が指定値を超えると、`Too many URI's (N or less)` / `Too many Queries (N or less)` で失敗します（exit 1）。上位だけ見たいときは `| head` を使います。`pt-query-digest` の `--limit` は、件数制限として機能します。そのまま使えます。

?> `slp my` の合計時間のソートキーは、**`sum-query-time`** です（`alp` の `sum` とは違います）。既定は件数の昇順です。`head` だけでは、時間の上位を選べません。キーは `slp my --help` で確認します。解析エラーの stderr は、隠しません。

?> `long_query_time = 0` の全件ログは、大きくなります。収集後は必ず 1 に戻します。`sudo systemctl restart isu-python.service` で、アプリも再起動します。容量の回復は [0100-overall-tuning.md のスローログ対処](0100-overall-tuning.md#ディスクが埋まるスローログ) を使います。

選ぶ基準:

- `examined`（読んだ行数）が `sent`（返した行数）の桁違い → 余計な行を読んでいる。5. へ進みます
- `Calls` が大きく `R/Call` は小さい → 呼ばれすぎ。索引ではなく呼び出し回数（N+1）の問題。7. へ進みます
- `ADMIN PREPARE` が 1 位 → 原因は準備済み文の往復（プリペアド）です。索引ではありません（配布された元の状態（素体）の `MySQLdb` は出しません）

### 観察すること

- 狙うクエリを 1 本だけ言えること（2 本同時に入らない）
- `examined` / `sent` / `Calls` の 3 つの数字を言えること
- `long_query_time` を 1 に戻したこと

## 5. EXPLAIN で計画を読む

- 選んだクエリの、今の読み方（実行計画）を見ます。
- 貼る前と貼った後で、同じ `EXPLAIN` を見比べます。

```sql
USE isuconp;
-- いまの計画（ALL / key NULL / Using filesort になりやすい）
EXPLAIN
SELECT * FROM comments WHERE post_id = 1 ORDER BY created_at DESC LIMIT 3;

SHOW INDEX FROM comments;
```

- もう 1 つの候補は、`make_posts()` に渡す投稿一覧です。
- 一覧用の列だけにします。
- `SELECT * FROM posts` は `imgdata`（画像の中身）を含むため、使いません。

```sql
EXPLAIN
SELECT id, user_id, body, created_at, mime
FROM posts WHERE user_id = 1 ORDER BY created_at DESC LIMIT 20;
```

| 悪い | 良い |
| --- | --- |
| `type: ALL`、`key: NULL` | `type: ref` / `range`、`key` が今貼った名前 |
| `rows` がテーブル全件、`Extra: Using filesort` | `rows` が LIMIT に近い。filesort（並べ直し）が消えることが多い |
| slp の examined が sent の桁違い | 比が数倍以内。残るなら呼び出し回数（N+1） |

#### 実行例：

全件キャプチャ（`long_query_time = 0`）の slp 上位です。

| COUNT | SUM(QUERYTIME) | AVG | ROWSEXAMINED(平均) | クエリ |
| --- | --- | --- | --- | --- |
| 1784 | **49.639** | 0.0278 | **100008** | `SELECT * FROM comments WHERE post_id = N ORDER BY created_at DESC LIMIT N` |
| 1809 | **18.384** | 0.0102 | **100005** | `SELECT COUNT(*) AS count FROM comments WHERE post_id = N` |
| 535 | 2.258 | 0.0042 | 1 | `SELECT * FROM posts WHERE id = N` |
| 25 | 0.696 | 0.0278 | **100013** | `SELECT * FROM comments WHERE post_id = N ORDER BY created_at DESC` |
| 70 | 0.721 | 0.0103 | **20008** | `SELECT id,user_id,body,created_at,mime FROM posts ORDER BY created_at DESC` |
| 6591 | 0.447 | 0.00007 | 1 | `SELECT * FROM users WHERE id = N` |

- `SUM` が突出している 2 本とも `comments` です。**`ROWSEXAMINED` が約 10 万**（テーブル全件）です。
- `users WHERE id` は 6591 回と最多です。ただし `AVG 0.00007` 秒で、主キー索引が効いています。
- **回数が多くても 1 回が軽いなら、次の候補は索引の追加ではなく N+1 の削減です。**

EXPLAIN（貼る前。`IGNORE INDEX` で索引を無効化した計画。2026-10-13 実測）:

```text
        type: ALL
possible_keys: NULL
          key: NULL
         rows: 100142       ← 全件を走査
        Extra: Using where; Using filesort
```

- 見方は [0020-measure.md](../010_common/0020-measure.md) の「インデックス」です。
- `type` は `ref` / `range` が欲しいです。`ALL` はフルスキャン（テーブル全部を読むこと）です。
- `key` が `NULL` なら、索引を使っていません。
- `rows` は推定値です。実際の読んだ行数は、`Rows_examined` で確認します。

### 観察すること

- 貼る前の計画を `EXPLAIN` で取ったこと（`type` / `key` / `rows` / `Extra` を言える）
- 選んだクエリが自分の slp 1 位と一致していること

<a id="6-インデックスを-1-本貼る"></a>

## 6. インデックスを 1 本貼る

- 等しい値で探す列を先頭に、範囲・ORDER BY を後ろに並べます。
- MySQL には、`CREATE INDEX IF NOT EXISTS` がありません。存在確認してから貼ります。

```sql
USE isuconp;
SELECT COUNT(*) FROM information_schema.statistics
WHERE table_schema = 'isuconp'
  AND table_name = 'comments'
  AND index_name = 'idx_comments_post_created';

-- 0 なら貼る。等価 → ORDER BY
CREATE INDEX idx_comments_post_created ON comments (post_id, created_at);
```

- 貼ったら、同じ `EXPLAIN` で `key` が変わったことを確認します。それからベンチします。

```sql
EXPLAIN
SELECT * FROM comments WHERE post_id = 1 ORDER BY created_at DESC LIMIT 3;
```

```bash
# bench-prep してから公式ベンチ 1 本
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
fi
sudo nginx -s reopen
if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysql -e 'FLUSH SLOW LOGS'
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=index comments(post_id,created_at)" >> ~/bench-notes/scores.txt
```

- 効かなければ、今回貼った索引だけ外して測り直します。
- 次の 1 本へ進む前に、判定を記録します。
- **同時に貼りません。**

```sql
DROP INDEX idx_comments_post_created ON comments;
```

?> 索引は DB 内の定義です。ファイルになりません。`~/bench-notes/scores.txt` とメモに、索引名・対象テーブル・対象列・貼る前の `EXPLAIN` を残します。複数台に分けたあとは MySQL が s3 の台で動きます。この章で入れた索引は、**s3 に入れ直す必要があります**（[0110-split-4.md §1](0110-split-4.md#1-分割後の-sql-を確認する) で確認します）。

#### 実行例：

```sql
CREATE INDEX idx_comments_post_created ON comments (post_id, created_at);
```

貼った後の `EXPLAIN`（同じクエリ）:

```text
        type: ref
possible_keys: idx_comments_post_created
          key: idx_comments_post_created
      key_len: 4
          ref: const
         rows: 12
        Extra: Backward index scan
```

| | 貼る前 | 貼った後 |
| --- | --- | --- |
| `type` | `ALL` | `ref` |
| `key` | `NULL` | `idx_comments_post_created` |
| `rows` | **100142** | **12** |
| `Extra` | `Using where; Using filesort` | `Backward index scan` |

ベンチ（2026-10-13、`c7a.large` 1 台 / 単一構成 / `nproc=2`）:

```text
{"pass":true,"score":9867,"success":8364,"fail":0,"messages":[]}
```

alp の変化:

| URI | 貼る前 AVG | 貼った後 AVG | COUNT 変化 |
| --- | --- | --- | --- |
| `/` | 2.129 秒 | **0.110 秒** | 63 → 714 |
| `/image/[0-9]+` | 0.709 秒 | **0.076 秒** | 447 → 4365 |
| `/posts/[0-9]+` | 0.522 秒 | **0.069 秒** | 33 → 424 |

- **`score` が 943 → 9867（約 10 倍）**になりました。
- `comments` の全文走査（1 リクエストあたり約 10 万行）が、索引経由の 12 行に変わったためです。

### 観察すること

- 貼る前後で `key` が変わり、`rows` が減ったこと
- 点数が動いたこと。動いても alp の Count が大きいままなら、クエリ回数の問題であり索引の仕事ではありません
- 1 本で終わらせてから次を考えること（「よくあるインデックス集」はやらない）

<a id="7-n1-を一括化する"></a>

## 7. N+1 を一括化する

- 索引で `examined` が落ちても、alp の Count が大きいままなら、呼び出し回数の問題です。
- 素体の `make_posts()` は、1 投稿につき 6 本のクエリを出します（コメント件数・コメント本文・投稿主・表示コメントごとのユーザー）。
- 20 投稿表示で、最大 120 本です。

- 1 つ目は、`get_index()` の全件取得をやめます。
- 削除済みユーザーを SQL で除外してから、20 件に制限します。
- 単に `LIMIT 20` を足すだけでは足りません。削除済みの投稿を Python 側で除いたあと、20 件未満になることがあります。

```python
@app.route("/")
def get_index():
    me = get_session_user()
    cursor = db().cursor()
    cursor.execute(
        "SELECT p.`id`, p.`user_id`, p.`body`, p.`created_at`, p.`mime` "
        "FROM `posts` p JOIN `users` u ON u.`id` = p.`user_id` "
        "WHERE u.`del_flg` = 0 ORDER BY p.`created_at` DESC LIMIT 20"
    )
    posts = make_posts(cursor.fetchall())
    return flask.render_template("index.html", posts=posts, me=me)
```

- 2 つ目に、`make_posts()` だけを置き換えます。
- MySQL 8 の窓関数（行ごとに順位を付ける書き方）で、投稿ごとの新しい 3 コメントを取得します。件数・ユーザーもまとめます。
- 詳細ページの `all_comments=True` は、全コメントを返すままです。

```python
def make_posts(results, all_comments=False):
    posts = []
    if not results:
        return posts
    cursor = db().cursor()
    users = {}
    chunk_size = 200

    for start in range(0, len(results), chunk_size):
        chunk_posts = results[start : start + chunk_size]
        post_ids = [post["id"] for post in chunk_posts]
        marks = ",".join(["%s"] * len(post_ids))
        if all_comments:
            query = (
                "SELECT `id`, `post_id`, `user_id`, `comment`, `created_at` "
                f"FROM `comments` WHERE `post_id` IN ({marks}) "
                "ORDER BY `created_at` DESC"
            )
        else:
            query = (
                "SELECT `id`, `post_id`, `user_id`, `comment`, `created_at` FROM ("
                "SELECT `id`, `post_id`, `user_id`, `comment`, `created_at`, "
                "ROW_NUMBER() OVER (PARTITION BY `post_id` ORDER BY `created_at` DESC) AS rn "
                f"FROM `comments` WHERE `post_id` IN ({marks})"
                ") AS ranked WHERE rn <= 3 ORDER BY `created_at` DESC"
            )
        cursor.execute(query, post_ids)
        comments_by_post = {}
        user_ids = set()
        for comment in cursor:
            comments_by_post.setdefault(comment["post_id"], []).append(comment)
            user_ids.add(comment["user_id"])

        cursor.execute(
            f"SELECT `post_id`, COUNT(*) AS `count` FROM `comments` "
            f"WHERE `post_id` IN ({marks}) GROUP BY `post_id`",
            post_ids,
        )
        counts = {row["post_id"]: row["count"] for row in cursor}
        user_ids.update(post["user_id"] for post in chunk_posts)
        user_ids.difference_update(users)
        if user_ids:
            user_marks = ",".join(["%s"] * len(user_ids))
            cursor.execute(
                "SELECT `id`, `account_name`, `authority`, `del_flg`, `created_at` "
                f"FROM `users` WHERE `id` IN ({user_marks})",
                list(user_ids),
            )
            users.update((user["id"], user) for user in cursor)

        for post in chunk_posts:
            post["user"] = users[post["user_id"]]
            if post["user"]["del_flg"]:
                continue
            post["comment_count"] = counts.get(post["id"], 0)
            comments = comments_by_post.get(post["id"], [])
            for comment in comments:
                comment["user"] = users[comment["user_id"]]
            post["comments"] = list(reversed(comments))
            posts.append(post)
            if len(posts) >= POSTS_PER_PAGE:
                return posts
    return posts
```

?> 1 チャンク（まとまり）で最大 3 クエリです。「入力全件をまとめてから、最後に 20 件を切る」形には戻さないでください。`get_posts()` の `fetchall()` 自体は、この変更では残ります。同じ `created_at` のコメントの順序は、素体と同様に未指定です。同時刻の行がある場合も、公式ベンチで表示を検証します。

- 変更は、`/home/isucon/private_isu/webapp/python/app.py` に入れます。
- **`backup/base` は基準線です。書き換えません。**
- 複数台に分ける前です。`backup/s1` などの台ごとの置き場は、まだありません。この節では、台のファイルを編集します。
- 編集前の状態を、必ず 1 つ退避してから進めます。

```bash
# サーバー。編集前の退避を作る（これがないと下の「戻す」が使えない）
cd /home/isucon/private_isu/webapp/python
sudo cp -a app.py app.py.before-sql
sudo chown isucon:isucon app.py.before-sql
ls -l app.py app.py.before-sql
```

編集後、差分と構文を確認します。

```bash
# サーバー。退避との差分と構文を見る
cd /home/isucon/private_isu/webapp/python
diff -u app.py.before-sql app.py | head -80
/home/isucon/private_isu/webapp/python/.venv/bin/python3 -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('syntax OK')"
```

restart して疎通を確認します。

```bash
sudo systemctl restart isu-python.service
systemctl is-active isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/posts/1
```

?> 変更内容は、`~/bench-notes/` に diff を残します。[0110-split-1.md](0110-split-1.md) §3 のバックアップで、変更済みの `app.py` は `backup/s1` に拾われます。複数台に分けるときは s2 へも配ります（[0110-split-2.md](0110-split-2.md) §3）。

ベンチを 1 本回して判定します。

```bash
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
fi
sudo nginx -s reopen
if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysql -e 'FLUSH SLOW LOGS'
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -12
echo "$(date -Iseconds)  score=  pass=  fail=  note=make-posts-batch" >> ~/bench-notes/scores.txt
```

実測サンプル: `{"pass":true,"score":14040,"success":11826,"fail":0,"messages":[]}`（§7。§6 の 9867 から +4173、`fail=0`。下の「実行例」参照）。

- `fail=0` を確認します。あわせて、一覧の件数・削除済みユーザーの非表示・詳細ページの全コメント・ページ送りも確認します。

### 点数が落ちたときの切り分け（元に戻して測り直す）

- **1 回点数が落ちても、変更が原因とは限りません。**
- 変更前の内容を確実に指定して戻します。同じ条件で、再測定します。

```bash
# サーバー。この章の編集前に取っておいたコピーから戻す
# （編集前に `cp app.py app.py.before-sql` を必ず実行しておく）
sudo cp -a /home/isucon/private_isu/webapp/python/app.py.before-sql \
           /home/isucon/private_isu/webapp/python/app.py
sudo chown isucon:isucon /home/isucon/private_isu/webapp/python/app.py
sudo systemctl restart isu-python.service
systemctl is-active isu-python.service
```

| 状態 | ベンチで記録すること | 判定 |
| --- | --- | --- |
| 変更前 | 同じ設定で複数回の score・fail・alp | 基準のばらつき |
| 変更後 | 同じ項目を再測定 | 点数と対象経路が改善したか |
| 変更前へ戻す | さらに再測定 | 差が変更に追従するか |

- 比較条件を揃えます。
- 対象経路の遅延と点数の変化が、繰り返し再現するか確認します。
- 繰り返しの確認が足りないなら、効果は確定しません。実行した事実だけを残します。

#### 実行例：

`get_index` の全件取得を JOIN + `LIMIT 20` に、`make_posts` をチャンク化（まとまりごとに処理する形）に置き換えた結果です（退避との差分 111 行）。

```text
$ curl -fsS -o /dev/null -m 5 -w 'GET / -> %{http_code} %{time_total}s\n' http://127.0.0.1:8080/
GET / -> 200 0.024328s

$ curl -fsS -o /dev/null -m 5 -w 'GET /posts/1 -> %{http_code} %{time_total}s\n' http://127.0.0.1:8080/posts/1
GET /posts/1 -> 200 0.003059s
```

ベンチ（2026-10-13、`c7a.large` 1 台 / 単一構成 / `nproc=2`）:

```text
{"pass":true,"score":14040,"success":11826,"fail":0,"messages":[]}
```

alp の変化:

| URI | 索引のみ | 索引 + N+1 一括化 | COUNT 変化 |
| --- | --- | --- | --- |
| `/` | 0.110 秒 | **0.059 秒** | 714 → 1044 |
| `/posts/[0-9]+` | 0.069 秒 | **0.053 秒** | 424 → 618 |
| `/image/[0-9]+` | 0.076 秒 | 0.056 秒 | 4365 → 6038 |

| 回 | score | fail | 変更内容 |
| --- | --- | --- | --- |
| ベースライン | 943 | 0 | LTSV 化のみ |
| 索引 1 本 | 9867 | 0 | `comments(post_id, created_at)` |
| **N+1 一括化** | **14040** | **0** | `get_index` JOIN+LIMIT / `make_posts` チャンク化 |

- 再計測でも `score=14817`（§8）が出ました。14000 台後半で、安定しています。

### 観察すること

- 索引だけでは減らなかった alp の Count が、一括化で減ったこと
- 点数と対象経路の遅延が両方動いたこと
- 直していない重い場所（ホットスポット）が残っているなら、次の 1 つにすること（また 1 つだけ）

## 8. 公式ベンチで判定する

- 残す変更を 1 つに絞ります。他は、戻した状態にします。
- その状態で、公式ベンチを 1 本回します。

```bash
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
fi
sudo nginx -s reopen
if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysql -e 'FLUSH SLOW LOGS'
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
echo "$(date -Iseconds)  score=  pass=  fail=  note=sql-final" >> ~/bench-notes/scores.txt
```

- 判定は、slp（または pt-query-digest）＋ alp ＋点数の 3 点セットです。
- 点だけを見ません。
- 効かなければ、残しません。

#### 実行例：

この章で残す 2 つ（索引 + N+1 一括化）を残した状態です（2026-10-13、`c7a.large` 1 台 / 単一構成 / `nproc=2`）。

```text
{"pass":true,"score":14817,"success":12454,"fail":0,"messages":[]}
```

alp の Sum 上位:

| COUNT | URI | SUM | AVG | P99 |
| --- | --- | --- | --- | --- |
| 6333 | `/image/[0-9]+` | 327.850 | 0.052 | 0.184 |
| 1111 | `/` | 63.554 | 0.057 | 0.191 |
| 829 | `POST /login` | 43.604 | 0.053 | 0.177 |
| 654 | `/posts/[0-9]+` | 32.728 | 0.050 | 0.185 |
| 508 | `/js/main.js` | 25.469 | 0.050 | 0.173 |

- 再計測でも `score=14040`（§7）が出ました。14000 台後半で、安定しています。

この章の推移:

| 回 | score | fail | 変更内容 |
| --- | --- | --- | --- |
| 0010 §7（Ruby オールインワン） | 936 | 0 | 初期状態（索引無し・Ruby） |
| Python 切替後 | 1013 | 0 | `isu-python` へ（索引無し・アプリ素体） |
| LTSV 化後のベースライン | 943 | 0 | 計測の土台 |
| §6 索引 1 本 | 9867 | 0 | `comments(post_id, created_at)` |
| §7 N+1 一括化 | 14040 | 0 | `get_index` JOIN+LIMIT / `make_posts` チャンク化 |
| **§8 判定** | **14817** | **0** | 同じ 2 つを残した状態 |

- **943 → 14817（約 16 倍）**です。
- 内訳は、約 10 倍が索引、1.5 倍が N+1 一括化です。

### 観察すること

- 狙った slp / pt-query-digest の行が落ち、alp の Sum が落ち、点数が動いたこと
- `fail` が 0 であること。文字化け・500 が出たら 7. のクエリ書き換えを疑います
- `long_query_time` を 1 に戻したこと（`df -h /` で余裕があること）

## 9. 出力例

この章の実測（2026-10-13、`c7a.large` 1 台 / `ap-northeast-1` / 単一構成（nginx + Python + MySQL が同じ台）/ `nproc=2` / メモリ 3.7Gi）。

| 節 | 内容 | 判定 |
| --- | --- | --- |
| §1 | 計測ツールの導入 | alp / slp / oha / pt-query-digest が揃った |
| §2 | Python 切替 | `isu-python` が起動、`curl :8080/` が 200 |
| §3 | 計測サイクル | ベースラインを記録 |
| §4 | 対象クエリを 1 本に決める | `comments` の 1 本に絞った |
| §5 | EXPLAIN で計画を読む | `type: ALL` / `rows: 100142` |
| §6 | インデックスを 1 本貼る | `rows: 12`。点数は構成で変わる |
| §7 | N+1 を一括化する | クエリ本数 120 → 3 |

### §4: 対象クエリを 1 本に決める

`long_query_time = 0` の全件キャプチャ:

```text
COUNT   SUM(QUERYTIME)  AVG(QUERYTIME)  クエリ
 1784        49.639        0.027825   SELECT * FROM comments WHERE post_id = N ORDER BY created_at DESC LIMIT N
 1809        18.384        0.010162   SELECT COUNT(*) AS count FROM comments WHERE post_id = N
  535         2.258        0.004220   SELECT * FROM posts WHERE id = N
   70         0.721        0.010306   SELECT id,user_id,body,created_at,mime FROM posts ORDER BY created_at DESC
   25         0.696        0.027848   SELECT * FROM comments WHERE post_id = N ORDER BY created_at DESC
 6591         0.447        0.000068   SELECT * FROM users WHERE id = N
    8         0.183        0.022828   INSERT INTO posts (user_id, mime, imgdata, body)
   17         0.177        0.010423   SELECT COUNT(*) AS count FROM comments WHERE user_id = N
```

- `SUM` が突出しているのは、`comments` の 1 本だけです。
- `users WHERE id` は 6591 回と最多です。ただし `AVG 0.00007` 秒で、主キー索引が効いています。
- **回数が多くても 1 回が軽いなら、次の候補は索引の追加ではなく N+1 の削減です。**

### §5・§6: EXPLAIN の変化

行数と既存インデックス（貼る前）:

```text
comments = 100011 行（InnoDB）   posts = 10008 行   users = 1012 行

SHOW INDEX FROM comments;
Key_name    Seq_in_index  Column_name  Cardinality
PRIMARY     1             id           99918
```

- `post_id` の索引が無く、全文走査でした。

| | 貼る前 | 貼った後 |
| --- | --- | --- |
| `type` | `ALL` | `ref` |
| `key` | `NULL` | `idx_comments_post_created` |
| `rows` | **100142** | **12** |
| `Extra` | `Using where; Using filesort` | `Backward index scan` |

- もう 1 つの候補 `posts(user_id, created_at)` も、同じです（`rows` 9536 → 12）。
- 一時索引を作って `EXPLAIN` を取り、測定後に `DROP INDEX` します。
- 残す索引は、`comments` の 1 本のままです。

### §7: N+1 の一括化

- 変更前の `make_posts()` は、投稿ごとに件数・コメント本文・投稿主を取得しました。表示コメントごとに、ユーザーも取得します。
- コメントが 3 件ある投稿なら 6 クエリです。20 投稿すべてがその状態なら、120 クエリです。
- 一括化後は、1 チャンクあたり最大 3 クエリです。

py-spy（処理中のどこが重いかを記録するツール）で観測した変化（2026-10-13 に単一構成の同台で撮り直し。`app.py.before-sql` と現行の一括化版を切り替えて `py-spy record --pid MainPID --subprocesses --duration 110 --rate 100` を公式ベンチと同時に記録。変更前 3280 サンプル / 変更後 2388 サンプル。数値は speedscope の on-stack 重み（秒））:

| | 変更前（`before-sql`） | 変更後（一括化版） |
| --- | --- | --- |
| `get_index` が返す行数 | 10139（`posts` 全件） | 20（`LIMIT 20`） |
| `get_index` の on-stack | 16.79 | 2.07 |
| `DateTime_or_None` の on-stack | 9.29 | 1.53 |
| `execute`（DB 往復）の on-stack | 20.28 | 7.26 |
| 行変換（`_fetch_row`）の on-stack | 14.77 | 2.54 |
| 同時測定の公式ベンチ | 10006（fail 0） | 15911（fail 0） |

### 点数について

- この章の実測では、**943 → 14817（約 16 倍）**でした（§8 の実行例に推移があります）。
- 索引 1 本で 10 倍、N+1 一括化で 1.5 倍です。
- 複数台に分けた 3 台構成では、[0080-app-tuning.md §6](0080-app-tuning.md#6-出力例) と [0100-overall-tuning.md §3](0100-overall-tuning.md#3-出力例) に別の実測があります。

### この節でつまずいた点

?> **この表の問題は、すべて本文へ反映済みです。**「直し方」列のとおりに直してあります。同じ症状が出たら、本文の手順どおりに直して再計測します。

| # | 箇所 | 問題 | 修正内容 |
| --- | --- | --- | --- |
| 1 | §4 | `slp --sort sum` の誤用と既定順の読み違い | `--sort sum-query-time --reverse` と `\| head` を使用します |
| 2 | §4 | 全件ログの期間が混在し、既存接続の閾値も戻していなかった | 収集前に回転し、収集後は閾値を戻してアプリを再起動します |
| 3 | §3・§8 | `FLUSH SLOW LOGS` 単独が回転すると誤解していた | `mv` → `FLUSH SLOW LOGS` の役割を区別しました |
| 4 | §5 | DB を選ばず `DATABASE()` を使うと存在判定を誤る | `USE isuconp` と明示した schema 条件を使用します |
| 5 | §7 | 「全件をまとめてから 20 件を切る」形にすると `get_posts()` で悪化する | チャンクごとに 20 件で終了する形にしました |
| 6 | §7 | `sudo cp -a` で root 所有のまま app.py を置く | `chown isucon:isucon` を手順に入れました |

### 観察したこと（実測）

- 索引 1 本で `rows` が 100142 → 12 に減り、`Using filesort` が消えました
- N+1 一括化で 20 投稿表示のクエリが最大 120 本から 3 本に減りました
- `ADMIN PREPARE` は 0 件でした。素体の `MySQLdb` はサーバ側プリペアドを出しません
- 点数の絶対値は構成で変わるため、`EXPLAIN` と alp の変化を主な判定にしました

## 10. トラブルシュート

### インデックスが効かない

- 同時貼りしていませんか（1 本ずつです）。`SHOW INDEX` で名前を確認してから再 `EXPLAIN` します
- `examined` は落ちたが Count が大きいまま → N+1（呼び出し回数）の仕事です。7. へ進みます
- `posts` の一覧がまだ重い → `SELECT *` の `imgdata` 混入を疑います。列指定の分離に戻ります

### 文字化け / 500 / fail が出る

- SQL 文字列の自前結合が一番多いです。値埋めはドライバに任せ、プレースホルダ（値の埋め込み場所）の渡し方を変えません
- `make_posts()` を書き換えた場合、`comments` と `users` の列を既存のテンプレートが使う名前に合わせます（`comment` / `account_name` / `authority` / `del_flg` / `created_at`）
- 1 クエリにまとめる前と比べて、削除済みユーザーの扱いが変わっていないか確認します

### `mv` して `FLUSH SLOW LOGS` しても slow log が再作成されない

- **`mv` で退避してから `FLUSH SLOW LOGS` しても、MySQL が新しいファイルを作らないことがあります**（実測で確認）。
- `slp` が `open ...: no such file or directory` で失敗します。
- 退避後にファイルを作り直してから、`FLUSH` します。

```bash
# s3（または単一構成の同じ台）
sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
sudo install -o mysql -g mysql -m 0640 /dev/null /var/log/mysql/mysql-slow.log
sudo mysql -e 'FLUSH SLOW LOGS'
sudo ls -l /var/log/mysql/mysql-slow.log
```

?> `SET GLOBAL slow_query_log_file` は、**MySQL の再起動で元に戻ります**（実測で `/var/lib/mysql/<host>-slow.log` に戻った）。この章のキャプチャは、再起動を挟まない範囲で行います。戻った状態を疑うときは、`SELECT @@global.slow_query_log_file` で確認します。永続化するには [0010-setup.md](../010_common/0010-setup.md) の drop-in（設定の追加ファイル）を使います。

### slow log でディスクが埋まる

- `long_query_time = 0` の全件ログは、ベンチ 1 本で数百 MB〜GB になります。
- 回復手順は [0100-overall-tuning.md のスローログ対処](0100-overall-tuning.md#ディスクが埋まるスローログ) を使います。
- `sudo ls /var/log/mysql/mysql-slow.log*` の `*` は、sudo より前に呼び出し側のシェルが展開します。`sudo ls -lh /var/log/mysql/` の形で見ます。

### 分割したら索引が無い

- [0110-split-1.md](0110-split-1.md) で役割を分けると、MySQL は s3 の台だけで動きます。
- この章で入れた索引は、その台の DB にありません。
- [0110-split-4.md §1](0110-split-4.md#1-分割後の-sql-を確認する) の手順で、s3 に入れ直します（`mysqldump` で移してもよい）。
- アプリの N+1 一括化は、ソースの変更です。`backup/s1` に積んで s2 へ配れば、複数台に分けたあとも残ります。
