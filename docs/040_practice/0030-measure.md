# 0030 1台ドリル

環境構築のあと、同じ EC2 で計測サイクルを手で回す。  
[0010-env.md](0010-env.md) のやり直しではない。Python 切替済み、ツールは [0010-setup.md](../010_common/0010-setup.md) を起点にする。

前提:

- EC2 は 1 台。ベンチも同じホストから `http://localhost`
- 実装は [webapp-setup/python.md](../010_common/webapp-setup/python.md)（`isu-python.service`）
- サイクルの見方は [0020-measure.md](../010_common/0020-measure.md)
- 終盤のログ停止は [0030-ops.md](../010_common/0030-ops.md#ログを止める終盤)。ここでは複製しない

順番: ベースライン → 観測ツール → ノブ 1 つ → EXPLAIN → py-spy → 詰まったとき。

## 1. ベースライン

ログを回してから公式ベンチ 1 本。点数を書く。空にする操作は **回転** であり、ログを止めることではない。

### nginx: `mv` + `reopen`（推奨）

`truncate` は nginx では動く（`O_APPEND` なので書き込みは新しい EOF に乗る）。ただし直前のベンチの access.log が消える。残して alp を見比べるなら `mv` + `nginx -s reopen`。

### MySQL slow log: `truncate` しない

mysqld はファイルのオフセットを自分で持っている。`truncate -s 0` すると、デーモンは古い位置に書き続け、途中が **NUL で埋まる**。ファイルだけ巨大になり、`slp` / `pt-query-digest` が壊れる。

やることは `mv` + `mysqladmin flush-logs`（または `FLUSH SLOW LOGS`）。

### bench-prep

```bash
TS=$(date +%Y%m%d%H%M%S)

sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen

if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysqladmin flush-logs
# mysqladmin が無いとき: sudo mysql -e 'FLUSH SLOW LOGS'

ls -l /var/log/nginx/access.log
ls -l /var/log/mysql/mysql-slow.log 2>/dev/null || echo 'slow log はまだ無い（2. で入れる）'
df -h /
sudo du -xh /var/log /home /tmp 2>/dev/null | sort -h | tail -n 10
```

`long_query_time=0` のスローログはベンチ 1 本で数百 MB〜GB になる。prep のあとに `df -h` を見る。残した `.log.$TS` も消さないと埋まる。slow が未導入なら nginx だけ回して点数を取り、2. のあと prep をやり直す。

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

### 観察すること

- `access.log` / `mysql-slow.log` が新しい inode で、サイズが小さい（または 0 から増え始めた）
- `df -h /` に余裕がある。`/var/log/mysql` が主犯になりやすい
- ベンチが `pass: true`。これがこのあとの比較原点
- ログを回しただけで点数はほぼ変わらない（止めたわけではない）

## 2. 観測ツール

導入そのものは [0010-setup.md](../010_common/0010-setup.md#計測ツール)。入っていなければそこのブロックを打つ。ここでは確認と、このドリルでの使い方。

バージョン: `config.env.example` の `ALP_VERSION` / `SLP_VERSION` / `OHA_VERSION` は `latest`。0010 のコピー用ブロックは alp `1.0.21` / slp `0.2.1` / oha `1.12.1`。py-spy は pip（ピン無し）。

```bash
command -v alp slp oha py-spy mysql mysqladmin
alp --version; slp --version; py-spy --version; mysql --version
```

### py-spy

```bash
command -v py-spy >/dev/null || sudo python3 -m pip install py-spy
```

ptrace は [0010-setup.md](../010_common/0010-setup.md#権限py-spy--ulimit) と `etc/sysctl/99-isucon-ptrace.conf`。取れないときは [6.](#6-トラブルシュート)。

### mysql クライアントと env.sh

無いときだけ:

```bash
command -v mysql || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y mysql-client
```

アプリと同じ接続は `/home/isucon/env.sh`（`export` が無いので `set -a`）。private-isu の変数名は `ISUCONP_*`。

```bash
set -a
. /home/isucon/env.sh
set +a
mysql -h"${ISUCONP_DB_HOST:-127.0.0.1}" -P"${ISUCONP_DB_PORT:-3306}" \
  -u"$ISUCONP_DB_USER" -p"$ISUCONP_DB_PASSWORD" "$ISUCONP_DB_NAME"
```

`FLUSH` や `SET GLOBAL` は unix ソケットの root で足りることが多い。

```bash
sudo mysql -e 'SELECT @@version, @@slow_query_log, @@slow_query_log_file, @@long_query_time'
```

### スロークエリ: ON、`long_query_time` は段階的に

永続化の drop-in は [0010-setup.md](../010_common/0010-setup.md#ログnginx-ltsv--mysql-slow)。ドリルではまず今の値を見て、**最初から 0 で放置しない**。

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
# 公式ベンチ 1 本
sudo slp my --file /var/log/mysql/mysql-slow.log
# 取り終わったら戻す
sudo mysql -e "SET GLOBAL long_query_time = 1"
df -h /
```

`SET GLOBAL` は再起動で消える。残すなら 0010 の drop-in。終盤に止めるなら 0030。

### EXPLAIN（まだインデックスは貼らない）

ホットになりやすい例（`make_posts` の内側、タイムライン）。値は自分の `slp` に置き換える。

```sql
EXPLAIN
SELECT * FROM comments WHERE post_id = 1 ORDER BY created_at DESC LIMIT 3;

EXPLAIN FORMAT=JSON
SELECT * FROM comments WHERE post_id = 1 ORDER BY created_at DESC LIMIT 3;

EXPLAIN ANALYZE
SELECT * FROM comments WHERE post_id = 1 ORDER BY created_at DESC LIMIT 3;

EXPLAIN
SELECT id, user_id, body, created_at, mime
FROM posts WHERE user_id = 1 ORDER BY created_at DESC LIMIT 20;
```

`EXPLAIN ANALYZE` は実際に走る。`SELECT * FROM posts` は `imgdata`（MEDIUMBLOB）を含むので、ドリルの例では一覧用の列だけにする。

見方は [0020-measure.md](../010_common/0020-measure.md#インデックス): `type` は `ref` / `range` が欲しい。`ALL` はフルスキャン。`key` が `NULL` なら使っていない。

### alp / slp（導入は 0010）

マッチャは `etc/alp/matching_groups.json`。[0020-measure.md](../010_common/0020-measure.md#alp) と同じ。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH"

sudo slp my --file /var/log/mysql/mysql-slow.log
# slp が無いとき
sudo pt-query-digest /var/log/mysql/mysql-slow.log
```

### 観察すること

- `command -v` が揃っている。py-spy は `ptrace_scope=0` でないと後で Permission denied
- `slow_query_log=1`。`long_query_time=1` では数行、`=0` の 1 本だけ巨大
- EXPLAIN が返り、初期は `type: ALL` / `key: NULL` になりやすい
- alp の Sum 上位と slp の examined/sent 比が、次の一手の候補になる

## 3. 設定を 1 つ変えてスコアを動かす

**1 ノブずつ。** 戻してから次へ。まとめて `my.cnf` を有効化しない。`innodb_buffer_pool_size` を最初のノブにしない（効かない・逆効果の報告がある。[0030-ops.md](../010_common/0030-ops.md#nginx--mysql-の設定) と `etc/mysql/mysqld-isucon.cnf`）。

回転 ≠ 停止。ISUCON13 優勝チームは終盤にログを止めておおよそ 27 万 → 46 万（277,310 → 468,006）。計測中の点数と終盤の点数は別物。

ベンチコマンドは 1. の公式ベンチ。空欄は自分の点数。

| 変更 | ベンチ | 前 | 後 | 観察の目安 |
| --- | --- | --- | --- | --- |
| ベースライン（access_log ON / slow ON） | 公式 |  | — | 1. の原点 |
| nginx `access_log` を止める | 同じ |  |  | alp が空。手順は [0030-ops](../010_common/0030-ops.md#ログを止める終盤) |
| `SET GLOBAL slow_query_log=0` | 同じ |  |  | slp が空。`long_query_time=0` の書き込みコストが消える |
| `innodb_flush_log_at_trx_commit=2` | 同じ |  |  | `mysqld-isucon.cnf` の 1 行だけ。バッファプールは触らない |

nginx の止め方・戻し方は 0030（`access_log off` と `.logs-off.bak`）。こちらに sed を再掲しない。

slow のオフ（計測用。永続化しない）:

```bash
sudo mysql -e 'SET GLOBAL slow_query_log=0'
sudo mysql -N -e 'SELECT @@slow_query_log'
# 公式ベンチ → 点数を書く
sudo mysql -e 'SET GLOBAL slow_query_log=1'
```

安全な my.cnf 1 行（例）。drop-in は 1 項目だけ。

```bash
sudo tee /etc/mysql/mysql.conf.d/99-isucon-flush.cnf >/dev/null <<'EOF'
[mysqld]
innodb_flush_log_at_trx_commit = 2
EOF
sudo systemctl restart mysql
sudo mysql -N -e 'SELECT @@innodb_flush_log_at_trx_commit'
# mysqld が起きない → 6. へ。ファイルを消して restart
```

戻す:

```bash
sudo rm -f /etc/mysql/mysql.conf.d/99-isucon-flush.cnf
sudo systemctl restart mysql
```

他の候補も同じルール: `etc/mysql/mysqld-isucon.cnf` から **コメントを 1 行だけ外す**。`max_connections` や `sync_binlog` も可。比較表に 1 行足して点数を書く。

### 観察すること

- 変えたのが 1 つだけなら、点差はそのノブのコスト
- access_log を止めたあと alp は空。計測に戻すなら 0030 の「戻す」
- slow を止めたあとファイルは増えない。`=0` のときのディスク増分がスコア差の本体になりやすい
- 回転しただけでは点は動かない。止めて初めて動く

## 4. EXPLAIN からクエリを直す

ルールは [0020-measure.md](../010_common/0020-measure.md#インデックス): **1 本ずつ**。`examined >> sent` を優先。LLM に投げるなら [prompts/index-from-slp.md](../010_common/prompts/index-from-slp.md)。

1. bench-prep → `long_query_time=0` → 公式ベンチ 1 本 → `long_query_time=1` に戻す  
2. `slp` / `pt-query-digest` で 1 クエリだけ選ぶ  
3. その SQL で `EXPLAIN`（必要なら `EXPLAIN ANALYZE` / `FORMAT=JSON`）  
4. インデックス 1 本  
5. 同じ `EXPLAIN` で `key` が変わったことを見てから再ベンチ  

```bash
sudo slp my --file /var/log/mysql/mysql-slow.log
```

例（実際に選んだクエリに置き換える）:

```sql
-- いまの計画（ALL / key NULL / Using filesort になりやすい）
EXPLAIN
SELECT * FROM comments WHERE post_id = 1 ORDER BY created_at DESC LIMIT 3;

SHOW INDEX FROM comments;

SELECT COUNT(*) FROM information_schema.statistics
WHERE table_schema = DATABASE()
  AND table_name = 'comments'
  AND index_name = 'idx_comments_post_created';

-- 0 なら。等価 → ORDER BY。MySQL に CREATE INDEX IF NOT EXISTS は無い
CREATE INDEX idx_comments_post_created ON comments (post_id, created_at);

EXPLAIN
SELECT * FROM comments WHERE post_id = 1 ORDER BY created_at DESC LIMIT 3;
```

| 悪い | 良い |
| --- | --- |
| `type: ALL`、`key: NULL` | `type: ref` / `range`、`key` が今貼った名前 |
| `rows` がテーブル全件、`Extra: Using filesort` | `rows` が LIMIT に近い。filesort が消えることが多い |
| slp の examined が sent の桁違い | 比が数倍以内。残るなら呼び出し回数（N+1） |

効かなければ `DROP INDEX idx_comments_post_created ON comments`。次の 1 本へ。  
`posts (user_id, created_at)` は 0020 の例。**同時に貼らない。**

```bash
echo "$(date -Iseconds)  score=  pass=  fail=  note=index comments(post_id,created_at)" >> ~/bench-notes/scores.txt
```

再起動で初期化 SQL が DB を作り直す回は、終盤にもう一度 `SHOW INDEX`（[0030-ops.md](../010_common/0030-ops.md#終盤チェック1700-以降)）。

### 観察すること

- 貼る前: フルスキャン。貼ったあと: `key` が変わり、examined が落ちる
- 点数が動く（または、動いても alp の Count が大きいまま → クエリ回数の問題でインデックスの仕事ではない）
- 1 本で終わらせてから次を考える。slp を見ない「よくあるインデックス集」はやらない

## 5. py-spy を取ってボトルネックを直す

DB がまだ mysqld で飽和しているうちは alp / slp を先に見る（[0020-measure.md](../010_common/0020-measure.md#1-サイクル)）。抜けてからアプリ。

値は `config.env.example` の `PYSPY_DURATION=30`、`PYSPY_FORMAT=speedscope`。

負荷が乗っているあいだに取る。ベンチを仕掛けてからすぐ:

```bash
UNIT=isu-python.service
PID="$(systemctl show -p MainPID --value "$UNIT")"
echo "MainPID=$PID"
pgrep -af gunicorn

DURATION=30          # PYSPY_DURATION
FORMAT=speedscope    # PYSPY_FORMAT

# on-CPU だけ
sudo py-spy record --pid "$PID" --subprocesses \
  --format "$FORMAT" --duration "$DURATION" --rate 100 -o /tmp/pyspy.json

# wall clock（待ちを含む）
sudo py-spy record --pid "$PID" --subprocesses --idle \
  --format "$FORMAT" --duration "$DURATION" --rate 100 -o /tmp/pyspy-idle.json

ls -lh /tmp/pyspy.json /tmp/pyspy-idle.json
```

`MainPID` は gunicorn のマスタ。`--subprocesses` 無しだと worker を取りこぼす。

可視化:

1. JSON を作業機へ（SSH があるとき `scp`。SSM だけならファイルを持ち出す）
2. https://www.speedscope.app を開き、ドロップ
3. Left Heavy / Sandwich。SVG が欲しければ `--format svg`

`--idle` なしだけ見ると、DB 待ちやロック待ちはサンプルに出ない。「アプリの CPU は暇」に見える。on-CPU のホット関数と、idle 側で伸びている待ちを両方書く。

| 見え方 | 意味 | このアプリでやりがちな一手 |
| --- | --- | --- |
| on-CPU が `digest` / `subprocess` / `openssl` | パスワードハッシュをシェルに出している | 同じ処理をライブラリでやる（リクエスト経路の無駄） |
| `--idle` で `execute` / `make_posts` が厚い | N+1、余分なクエリ | コメントをまとめて取る。4. のインデックスと両建て |
| `/image` が Sum 上位で `SELECT * FROM posts` | 一覧に不要な BLOB まで読んでいる | 画像用クエリから `imgdata` 以外を外す、またはファイルへ |

どれか **1 つ** 直して再ベンチ。点と speedscope の同じ場所が痩せたかを見る。

```bash
echo "$(date -Iseconds)  score=  pass=  fail=  note=pyspy-tune " >> ~/bench-notes/scores.txt
```

終盤に py-spy を残さない（[0030-ops.md](../010_common/0030-ops.md#ログを止める終盤)）。

### 観察すること

- on-CPU と `--idle` で絵が違う。片側だけだと原因を取り違える
- 直した関数／クエリが Sandwich から落ち、スコアが動く
- 直していないホットスポットが残っているなら、次の 1 手（また 1 つだけ）

## 6. トラブルシュート

### ディスクが埋まる（スローログ）

```bash
df -h /
sudo du -xh /var/log /home /tmp 2>/dev/null | sort -h | tail -n 20
sudo ls -lh /var/log/mysql/mysql-slow.log*
```

生きている `mysql-slow.log` は **truncate しない**（1.）。やること:

1. `long_query_time` を 1 以上に戻す、またはキャプチャを止める  
2. bench-prep と同じ `mv` + `flush-logs`  
3. 残った `mysql-slow.log.$TS` / `access.log.$TS` を消して空きを返す  
4. 終盤ならログ自体を止める → [0030-ops.md](../010_common/0030-ops.md#ログを止める終盤)

0030 のディスク節にある truncate は nginx には使えるが、slow log には使わない。

### py-spy が Permission denied / ptrace

```bash
cat /proc/sys/kernel/yama/ptrace_scope
# 1 や 2 だと attach できない。0 にする（中身は etc/sysctl/99-isucon-ptrace.conf）
echo 'kernel.yama.ptrace_scope = 0' | sudo tee /etc/sysctl.d/99-isucon-ptrace.conf
sudo sysctl -p /etc/sysctl.d/99-isucon-ptrace.conf
cat /proc/sys/kernel/yama/ptrace_scope
```

`MainPID` が 0 なら unit が落ちている。`sudo` 無しの py-spy も拒否される。

### 再起動後 502

```bash
UNIT=isu-python.service
systemctl is-active nginx.service mysql.service "$UNIT"
systemctl is-enabled isu-ruby.service "$UNIT"
sudo journalctl -u "$UNIT" -n 80 --no-pager
ss -lntp | grep 8080
curl -v http://127.0.0.1:8080/
curl -v -o /dev/null http://127.0.0.1/
```

- Ruby が enabled に戻っている → [webapp-setup/python.md](../010_common/webapp-setup/python.md)
- 依存 → `sudo su - isucon` して `cd /home/isucon/private_isu/webapp/python && uv sync` から restart
- mysqld が 99-isucon*.cnf の直後に死んでいる → その drop-in を消して `systemctl restart mysql`（[0010-setup.md](../010_common/0010-setup.md#mysqld-が起きないslow-設定の直後)）
- nginx の `proxy_pass` と `APP_PORT`（8080）がずれていないか

### 観察すること

- `df` の Used がスローログと一致する。NUL 埋めなら `ls -l` は大きいが `slp` が即死する
- `ptrace_scope` が 0 になってから py-spy が JSON を書く
- 502 のとき、unix 8080 か nginx 80 か mysqld のどれが死んでいるか（3 つの `is-active`）
