# 0100 SQLチューニング

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）はローカル環境の private-isu-terraform リポジトリにて実行します。

DB が返す行を減らして点数を上げます。やることは 1 本ずつです。`examined >> sent` のクエリから対処します。
[0090-app-tuning.md](0090-app-tuning.md) の次。インデックス候補の作り方は [prompts/index-from-slp.md](../010_common/prompts/index-from-slp.md) を参照します。

順番: 対象を 1 本に決める（1.）→ EXPLAIN で読む（2.）→ インデックスを 1 本貼る（3.）→ 設定ノブを 1 つ振る（4.）。1 手ずつ。`fail` が 0 でない点数は比べない。出口は [0110-overall-tuning.md](0110-overall-tuning.md)。

## 1. 対象クエリを 1 本に決める

`slp` / `pt-query-digest` で Response time の 1 位を見る。`Calls`（回数）と `R/Call`（1 回あたり）で「呼ばれすぎか 1 回が重いか」を分ける。

```bash
# s3 で打つ。bench-prep（[0030-measure.md](0030-measure.md) と同じ mv + flush-logs）してから
sudo mysql -e "SET GLOBAL long_query_time = 0"
# SET GLOBAL は既存の接続には効きません。新規接続から効くので、s1 と s2 で
# sudo systemctl restart isu-python.service してから測ります
```

```bash
# s1 で打つ。公式ベンチ 1 本
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
```

```bash
# s3 で打つ
sudo slp my --file /var/log/mysql/mysql-slow.log | head -20
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 5 2>/dev/null | head -60
sudo mysql -e "SET GLOBAL long_query_time = 1"
df -h /
```

選ぶ基準:

- `examined` が `sent` の桁違い → 余計な行を読んでいる。2. へ進む
- `Calls` が大きく `R/Call` は小さい → 呼ばれすぎ。インデックスではなく呼び出し回数（N+1・キャッシュ）の問題。[0040-cache.md](0040-cache.md) に戻る
- `ADMIN PREPARE` が 1 位 → プリペアドの往復が犯人です。インデックスではありません。3. を飛ばして [0090-app-tuning.md §5](0090-app-tuning.md) の無効化を見ます

`long_query_time=0` のまま放置しません。取り終わったら必ず 1 に戻します。

### 観察すること

- 狙うクエリを 1 本だけ言える（2 本同時に入らない）
- `examined` / `sent` / `Calls` の 3 つの数字を言える
- `long_query_time` を 1 に戻した

## 2. EXPLAIN で計画を読む

選んだクエリの今の計画を見る。貼る前と貼った後で同じ `EXPLAIN` を見比べる。

```sql
-- いまの計画（ALL / key NULL / Using filesort になりやすい）
EXPLAIN
SELECT * FROM comments WHERE post_id = 1 ORDER BY created_at DESC LIMIT 3;

SHOW INDEX FROM comments;
```

ホットになりやすい例として、`make_posts` の内側（タイムライン）のクエリを使います。値は自分の `slp` 結果に置き換えます。一覧用の列だけにします（`SELECT * FROM posts` は `imgdata` を含むため使いません）。

```sql
EXPLAIN
SELECT id, user_id, body, created_at, mime
FROM posts WHERE user_id = 1 ORDER BY created_at DESC LIMIT 20;
```

必要なら `EXPLAIN FORMAT=JSON` / `EXPLAIN ANALYZE` を足す。`EXPLAIN ANALYZE` は実際に走る。

| 悪い | 良い |
| --- | --- |
| `type: ALL`、`key: NULL` | `type: ref` / `range`、`key` が今貼った名前 |
| `rows` がテーブル全件、`Extra: Using filesort` | `rows` が LIMIT に近い。filesort が消えることが多い |
| slp の examined が sent の桁違い | 比が数倍以内。残るなら呼び出し回数（N+1） |

見方は [0020-measure.md](../010_common/0020-measure.md) の「インデックス」: `type` は `ref` / `range` が欲しい。`ALL` はフルスキャン。`key` が `NULL` なら使っていない。

### 観察すること

- 貼る前の計画を `EXPLAIN` で取った（`type` / `key` / `rows` / `Extra` を言える）
- 選んだクエリが自分の slp 1 位と一致している

## 3. インデックスを 1 本貼る

等価条件の列を先頭に、範囲・ORDER BY を後ろに並べます。MySQL に `CREATE INDEX IF NOT EXISTS` は無いので、存在確認してから貼ります。

```sql
SELECT COUNT(*) FROM information_schema.statistics
WHERE table_schema = DATABASE()
  AND table_name = 'comments'
  AND index_name = 'idx_comments_post_created';

-- 0 なら貼る。等価 → ORDER BY
CREATE INDEX idx_comments_post_created ON comments (post_id, created_at);
```

貼ったら同じ `EXPLAIN` で `key` が変わったことを見てからベンチします。

```sql
EXPLAIN
SELECT * FROM comments WHERE post_id = 1 ORDER BY created_at DESC LIMIT 3;
```

```bash
# bench-prep してから公式ベンチ 1 本
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=index comments(post_id,created_at)" >> ~/bench-notes/scores.txt
```

残す変更は台ごとの `backup/s1`・`s2`・`s3` に積みます（DB のインデックスは `backup/s3` のメモに残します。[0030-measure.md](0030-measure.md) の「残す変更は backup/s1・s2・s3 に積む」の流儀）。

効かなければ外します。次の 1 本へ進みます。**同時に貼りません。**

```sql
DROP INDEX idx_comments_post_created ON comments;
```

再起動で初期化 SQL が DB を作り直す構成では、終盤にもう一度 `SHOW INDEX` で確認します（[0030-ops.md](../010_common/0030-ops.md) の「終盤チェック」）。

### 観察すること

- 貼る前後で `key` が変わり、examined が落ちた
- 点数が動いた。動いても alp の Count が大きいままなら、クエリ回数の問題でありインデックスの仕事ではない
- 1 本で終わらせてから次を考える。slp を見ない「よくあるインデックス集」はやらない

## 4. 設定ノブを 1 つ振る

クエリを直したあとに、DB・ログの設定を 1 つずつ振る。**1 ノブずつ。** 戻してから次へ。まとめて `my.cnf` を有効化しない。

ベンチは [0030-measure.md](0030-measure.md) の公式ベンチを使います。表の空欄には自分の点数を書きます。

| 変更 | ベンチ | 前 | 後 | 観察の目安 |
| --- | --- | --- | --- | --- |
| ベースライン（access_log ON / slow ON） | 公式 | 58063 | — | [0030-measure.md](0030-measure.md) の原点 |
| nginx `access_log` を止める | 同じ | 58063 | 59200 | alp が空。`access_log` は `nginx.conf` 側にあるのでそちらを止める。手順は [0030-ops.md](../010_common/0030-ops.md) の「ログを止める」 |
| `SET GLOBAL slow_query_log=0` | 同じ | 58063 | 59877 | slp が空。`long_query_time=0` の書き込みコストが消える |
| `innodb_flush_log_at_trx_commit=2` | 同じ | 58063 | 59343 | `mysqld-isucon.cnf` の 1 行だけ。バッファプールは触らない |

`innodb_buffer_pool_size` を最初のノブにしない（効かない・逆効果だったという報告がある。詳しくは [0030-ops.md](../010_common/0030-ops.md) の「nginx / MySQL の設定」と `etc/mysql/mysqld-isucon.cnf` を見ます）。

回転 ≠ 停止。計測中の点数と終盤の点数は別物です。

nginx の止め方・戻し方は [0030-ops.md](../010_common/0030-ops.md) の「ログを止める（終盤）」の手順を使います。`access_log` があるのは `nginx.conf`（site conf には無い）なので、止めるのも戻すのも `nginx.conf` 側です。

slow のオフ（s3 で打つ。計測用。永続化しない）:

```bash
sudo mysql -e 'SET GLOBAL slow_query_log=0'
sudo mysql -N -e 'SELECT @@slow_query_log'
# 公式ベンチ → 点数を書く
sudo mysql -e 'SET GLOBAL slow_query_log=1'
```

安全な my.cnf 1 行（例）。drop-in は 1 項目だけにします。サーバー上では作らず、作業機で作って送ります。

`backup/s3/etc/mysql/mysql.conf.d/99-isucon-flush.cnf`:

```ini
[mysqld]
innodb_flush_log_at_trx_commit = 2
```

```bash
# 作業機
git pull --ff-only
mkdir -p backup/s3/etc/mysql/mysql.conf.d
# 上の内容を配置する
git add -A && git commit -m "tune: innodb_flush_log_at_trx_commit=2 を試す" && git push
```

サーバーで反映します（s3 で打つ）:

```bash
REPO=/home/isucon/private-isu-terraform
cd "$REPO" && git fetch origin
git switch feature/backup 2>/dev/null || git switch -c feature/backup
git pull --ff-only
sudo cp -a "$REPO/backup/s3/etc/mysql/mysql.conf.d/99-isucon-flush.cnf" /etc/mysql/mysql.conf.d/
sudo systemctl restart mysql
sudo mysql -N -e 'SELECT @@innodb_flush_log_at_trx_commit'  # 2 であること
# mysqld が起きないときは §5 を見て、ファイルを消して restart します
```

?> mysqld を restart したらアプリ（s1 と s2 の `isu-python`）も restart します。アプリは DB 接続を使い回すので、再起動前の古い接続のまま 500 になります。再起動しないままベンチを回すと `fail` します。

戻す:

```bash
sudo rm -f /etc/mysql/mysql.conf.d/99-isucon-flush.cnf
sudo systemctl restart mysql
```

他の候補も同じ流儀です。`backup/s3/etc/mysql/mysql.conf.d/` に drop-in を 1 つ置き、上の my.cnf と同じ手順（作業機 → push → pull → cp → restart）で反映します。候補は `max_connections` や `sync_binlog` です。比較表に 1 行足して点数を書きます。

### 観察すること

- 変えたのが 1 つだけなら、点差はそのノブのコスト
- access_log を止めたあと alp は空。計測に戻すなら [0030-ops.md](../010_common/0030-ops.md) の「ログを止める（終盤）」の戻し方を使います
- slow を止めたあとファイルは増えません。`=0` のときのディスク増分がスコア差の主な原因になりやすいです
- ログを回転しただけでは点は動きません。`access_log`・`slow_query_log` を止めて初めて動きます

## 5. トラブルシュート

### mysqld が起きない（my.cnf の直後）

```bash
sudo journalctl -u mysql -n 80 --no-pager
sudo rm -f /etc/mysql/mysql.conf.d/99-isucon-*.cnf
sudo systemctl restart mysql
sudo mysql -N -e 'SELECT 1'
```

- drop-in を足した直後に死んだら、そのファイルを消して restart します（[0010-setup.md](../010_common/0010-setup.md) の「mysqld が起きない」と同じです）
- `SELECT 1` が返れば復旧。返らなければ error.log を見る
