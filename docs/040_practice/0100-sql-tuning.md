# 0100 SQLチューニング

DB が返す行を減らして点数を上げる。やることは 1 本ずつ。`examined >> sent` のクエリから潰す。
[0090-app-tuning.md](0090-app-tuning.md) の次。LLM に投げるなら [prompts/index-from-slp.md](../010_common/prompts/index-from-slp.md)。

順番: 対象を 1 本に決める（1.）→ EXPLAIN で読む（2.）→ インデックスを 1 本貼る（3.）→ 設定ノブを 1 つ振る（4.）。1 手ずつ。`fail` が 0 でない点数は比べない。出口は [0110-overall-tuning.md](0110-overall-tuning.md)。

## 1. 対象クエリを1本に決める

`slp` / `pt-query-digest` で Response time の 1 位を見る。`Calls`（回数）と `R/Call`（1 回あたり）で「呼ばれすぎか 1 回が重いか」を分ける。

```bash
# bench-prep（[0030 §2](0030-measure.md#2-計測サイクルを回す) と同じ mv + reopen / flush-logs）してから
sudo mysql -e "SET GLOBAL long_query_time = 0"
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
sudo slp my --file /var/log/mysql/mysql-slow.log | head -20
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 5 2>/dev/null | head -60
sudo mysql -e "SET GLOBAL long_query_time = 1"
df -h /
```

選ぶ基準:

- `examined` が `sent` の桁違い → 余計な行を読んでいる。2. へ進む
- `Calls` が大きく `R/Call` は小さい → 呼ばれすぎ。インデックスではなく呼び出し回数（N+1・キャッシュ）の問題。[0040-cache.md](0040-cache.md) に戻る
- `ADMIN PREPARE` が 1 位 → プリペアドの往復が犯人。インデックスではない。3. を飛ばして [0090 §5](0090-app-tuning.md) の無効化を見る

`long_query_time=0` のまま放置しない。取り終わったら必ず 1 に戻す。

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

ホットになりやすい例として、`make_posts` の内側（タイムライン）のクエリを使う。値は自分の `slp` 結果に置き換える。一覧用の列だけにする（`SELECT * FROM posts` は `imgdata`（MEDIUMBLOB）を含むので、ドリルでは使わない）。

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

見方は [0020-measure.md](../010_common/0020-measure.md#インデックス): `type` は `ref` / `range` が欲しい。`ALL` はフルスキャン。`key` が `NULL` なら使っていない。

### 観察すること

- 貼る前の計画を `EXPLAIN` で取った（`type` / `key` / `rows` / `Extra` を言える）
- 選んだクエリが自分の slp 1 位と一致している

## 3. インデックスを1本貼る

等価条件の列を先頭に、範囲・ORDER BY を後ろに並べる。MySQL に `CREATE INDEX IF NOT EXISTS` は無いので、存在確認してから貼る。

```sql
SELECT COUNT(*) FROM information_schema.statistics
WHERE table_schema = DATABASE()
  AND table_name = 'comments'
  AND index_name = 'idx_comments_post_created';

-- 0 なら貼る。等価 → ORDER BY
CREATE INDEX idx_comments_post_created ON comments (post_id, created_at);
```

貼ったら同じ `EXPLAIN` で `key` が変わったことを見てからベンチする。

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

残す変更は `backup/s1` に積む（[0030 §2](0030-measure.md#残す変更は-backups1-に積む) の流儀）。

効かなければ外す。次の 1 本へ。**同時に貼らない。**

```sql
DROP INDEX idx_comments_post_created ON comments;
```

再起動で初期化 SQL が DB を作り直す構成では、終盤にもう一度 `SHOW INDEX` で確認する（[0030-ops.md](../010_common/0030-ops.md#終盤チェック1700-以降)）。

### 観察すること

- 貼る前後で `key` が変わり、examined が落ちた
- 点数が動いた。動いても alp の Count が大きいままなら、クエリ回数の問題でありインデックスの仕事ではない
- 1 本で終わらせてから次を考える。slp を見ない「よくあるインデックス集」はやらない

## 4. 設定ノブを1つ振る

クエリを直したあとに、DB・ログの設定を 1 つずつ振る。**1 ノブずつ。** 戻してから次へ。まとめて `my.cnf` を有効化しない。

ベンチは [0030](0030-measure.md) の公式ベンチを使う。表の空欄には自分の点数を書く。

| 変更 | ベンチ | 前 | 後 | 観察の目安 |
| --- | --- | --- | --- | --- |
| ベースライン（access_log ON / slow ON） | 公式 |  | — | [0030](0030-measure.md) の原点 |
| nginx `access_log` を止める | 同じ |  |  | alp が空。手順は [0030-ops](../010_common/0030-ops.md#ログを止める終盤) |
| `SET GLOBAL slow_query_log=0` | 同じ |  |  | slp が空。`long_query_time=0` の書き込みコストが消える |
| `innodb_flush_log_at_trx_commit=2` | 同じ |  |  | `mysqld-isucon.cnf` の 1 行だけ。バッファプールは触らない |

`innodb_buffer_pool_size` を最初のノブにしない（効かない・逆効果だったという報告がある。詳しくは [0030-ops.md](../010_common/0030-ops.md#nginx--mysql-の設定) と `etc/mysql/mysqld-isucon.cnf` を見る）。

回転 ≠ 停止。計測中の点数と終盤の点数は別物。

nginx の止め方・戻し方は 0030-ops.md を見る（`access_log off` と `.logs-off.bak`）。ここの手順には sed を書かない。

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
# mysqld が起きないときは 5. を見て、ファイルを消して restart する
```

戻す:

```bash
sudo rm -f /etc/mysql/mysql.conf.d/99-isucon-flush.cnf
sudo systemctl restart mysql
```

他の候補も同じルール: `etc/mysql/mysqld-isucon.cnf` から **コメントを 1 行だけ外す**。`max_connections` や `sync_binlog` も可。比較表に 1 行足して点数を書く。

### 観察すること

- 変えたのが 1 つだけなら、点差はそのノブのコスト
- access_log を止めたあと alp は空。計測に戻すなら 0030-ops.md の戻し方を使う
- slow を止めたあとファイルは増えない。`=0` のときのディスク増分がスコア差の主な原因になりやすい
- 回転しただけでは点は動かない。止めて初めて動く

## 5. トラブルシュート

### mysqld が起きない（my.cnf の直後）

```bash
sudo journalctl -u mysql -n 80 --no-pager
sudo rm -f /etc/mysql/mysql.conf.d/99-isucon-*.cnf
sudo systemctl restart mysql
sudo mysql -N -e 'SELECT 1'
```

- drop-in を足した直後に死んだら、そのファイルを消して restart する（[0010-setup.md](../010_common/0010-setup.md#mysqld-が起きないslow-設定の直後) と同じ）
- `SELECT 1` が返れば復旧。返らなければ error.log を見る
