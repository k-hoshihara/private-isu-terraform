# private-isu SQL パフォーマンスチューニング チェックリスト

`backup/base/` に入っている private-isu の実アプリコードと設定ファイルを読み、「SQL パフォーマンスの観点でやるべきこと」を全部並べたものです。

手順書（[040_practice/](040_practice/README.md)）は **Python 専用**です。このファイルは SQL レイヤだけ見たものです。[backup/base/](../backup/base/) には 5 言語のアプリが入っていますが、**5 言語で SQL 文はすべて同一**でした（[付録](#付録-sql-文の一覧)）。なので SQL の項目は言語によらず当てはまります。コードの書き方（DictCursor、プリペアドステートメントの出し方）は言語ごとに違います。

---

## 0. 最初に必ず確認すること

**このリポジトリには DDL が含まれていません。** `backup/base/` にも `docs/` にも `CREATE TABLE` が 1 つもありません。テーブル定義はベンチマーカーのセットアップが流します。インデックスを決める前に、現物の定義を必ず取り出してください。

```bash
# DB の台（分割後なら s3）で打つ
sudo mysql -e "SHOW CREATE TABLE isuconp.users\G"
sudo mysql -e "SHOW CREATE TABLE isuconp.posts\G"
sudo mysql -e "SHOW CREATE TABLE isuconp.comments\G"
sudo mysql -e "SHOW INDEX FROM isuconp.users;"
sudo mysql -e "SHOW INDEX FROM isuconp.posts;"
sudo mysql -e "SHOW INDEX FROM isuconp.comments;"
```

以下は**アプリのコードから逆引きしたカラム**です。型、NOT NULL、既存インデックスは `SHOW CREATE TABLE` で確認してから使ってください。

| テーブル | コードから確定したカラム | 相当コード |
| --- | --- | --- |
| `users` | `id`, `account_name`, `passhash`, `authority`, `del_flg`, `created_at` | `python/app.py:88,127,262,300,480` |
| `posts` | `id`, `user_id`, `body`, `mime`, `created_at`, `imgdata`（MEDIUMBLOB と `0090-app-tuning.md:83` に明記） | `python/app.py:288,308,361,373,413,429` |
| `comments` | `id`, `post_id`, `user_id`, `comment`, `created_at` | `python/app.py:137,143,461` |

MySQL のバージョンと、既定のまま残っている変数を確認します。

```bash
sudo mysql -e "SELECT VERSION();"
sudo mysql -e "SHOW VARIABLES LIKE 'innodb_buffer_pool_size';"
sudo mysql -e "SHOW VARIABLES LIKE 'innodb_flush_log_at_trx_commit';"
sudo mysql -e "SHOW VARIABLES LIKE 'long_query_time';"
sudo mysql -e "SHOW VARIABLES LIKE 'slow_query_log';"
```

---

## 1. 優先度一覧

| # | 項目 | 対象 | 期待効果 | このリポジトリでの検証 |
| --- | --- | --- | --- | --- |
| 1 | `comments(post_id, created_at)` の複合インデックス | SQL | 特大 | 実測済み |
| 2 | `make_posts` の N+1 を set 化 | コード | 大 | 未検証 |
| 3 | 一覧クエリの `LIMIT` なしと全件 `fetchall` | コード | 大 | 未検証 |
| 4 | 一覧クエリからの `imgdata` 除去、画像ファイル化 | コードと設定 | 大 | 実測済み |
| 5 | `innodb_buffer_pool_size`（既定 128M のまま） | 設定 | 大 | 未検証 |
| 6 | `innodb_flush_log_at_trx_commit = 2` | 設定 | 中 | 実測済み |
| 7 | `comments(user_id)`、`posts(user_id, created_at)` | SQL | 中 | 未検証 |
| 8 | `users(account_name)` のインデックス | SQL | 中 | 未検証 |
| 9 | `ORDER BY created_at` 系をインデックスで満たす | SQL | 中 | 未検証 |
| 10 | `users` からの `SELECT *` 削減 | コード | 小から中 | 未検証 |
| 11 | redo log、`tmpdir`、IO 系 | 設定 | 中 | 未検証 |
| 12 | 統計情報の再収集（`ANALYZE TABLE`） | SQL | 小から中 | 未検証 |

「実測済み」は [0030-measure.md](040_practice/0030-measure.md) から [0110-overall-tuning.md](040_practice/0110-overall-tuning.md) までで、`fail=0` のベンチを取ったものです。「未検証」はこのリポジトリではまだ点数を取っていない候補です。**未検証の項目は、入れる前に [§7](#7-検証手順) の手順で 1 手ずつ測ってください。**

---

## 2. インデックス

MySQL に `CREATE INDEX IF NOT EXISTS` はありません。存在確認、貼る、再確認、の順でやります。貼ったあとの `EXPLAIN` が `ALL` から `ref` または `range` に変わったことを確認するまでが 1 手です。

### 2-1. `comments(post_id, created_at)`、最重要

**これが 1 本目の当たりです。** タイムラインの `make_posts` が 1 リクエストごとに発行する 2 本です。

- `SELECT COUNT(*) AS count FROM comments WHERE post_id = %s`（`python/app.py:136-140`）
- `SELECT * FROM comments WHERE post_id = %s ORDER BY created_at DESC`、必要なら `LIMIT 3`（`python/app.py:142-148`）

`post_id` にしかインデックスが無ければ、`ORDER BY created_at` で `Using filesort` になり、`comments` は 10 万行なので全件ソートになります。`post_id, created_at` の順なら等価条件が先頭、順序付けが後ろに来て filesort が消えます。

```sql
-- 1. 貼る前に存在確認
SELECT COUNT(*) FROM information_schema.statistics
 WHERE table_schema = DATABASE()
   AND table_name = 'comments'
   AND index_name = 'idx_comments_post_created';

-- 2. 0 なら貼る
CREATE INDEX idx_comments_post_created ON comments (post_id, created_at);

-- 3. 確認
EXPLAIN SELECT * FROM comments WHERE post_id = 1 ORDER BY created_at DESC LIMIT 3;
SHOW INDEX FROM comments;
```

**実測値**。このリポジトリの `fail=0` ベンチです。

| | `type` | `key` | `rows` | `Extra` | score |
| --- | --- | --- | --- | --- | --- |
| 前 | `ALL` | `NULL` | 99920 | `Using where; Using filesort` | 3578 |
| 後 | `ref` | `idx_comments_post_created` | 12 | `Backward index scan` | 58063 |

**列の順序について。** 等価条件を先頭、範囲条件と `ORDER BY` を後ろに並べます。`post_id` が先頭でないと `ORDER BY` を満たせません。逆に `(created_at, post_id)` にすると `post_id = ?` の等価検索が使えません。

**`DESC` を添字に付ける必要はありません。** MySQL 8 は通常の B-tree を逆向きに走査して `Backward index scan` を出すので、通常の昇順インデックスで `ORDER BY ... DESC` を満たします。

戻すとき:

```sql
DROP INDEX idx_comments_post_created ON comments;
```

> 再起動で DB が初期化される構成ではインデックスも消えます。終盤に `SHOW INDEX` で 1 回確認してください（[010_common/0030-ops.md](010_common/0030-ops.md) の終盤チェック）。

### 2-2. `comments(user_id)`

`/@<account_name>` の `comment_count` 用です（`python/app.py:313-316`）。

```sql
EXPLAIN SELECT COUNT(*) FROM comments WHERE user_id = 1;
CREATE INDEX idx_comments_user ON comments (user_id);
```

`idx_comments_post_created` の左端は `post_id` なので `user_id` 検索には使えません。別のインデックスが必要です。

### 2-3. `posts(user_id, created_at)`

`/@<account_name>` の投稿一覧用です（`python/app.py:307-310`）。

```sql
EXPLAIN SELECT id, user_id, body, mime, created_at
  FROM posts WHERE user_id = 1 ORDER BY created_at DESC;
CREATE INDEX idx_posts_user_created ON posts (user_id, created_at);
```

同じインデックスで `SELECT id FROM posts WHERE user_id = %s`（`python/app.py:318-320`）も index only scan になります。

### 2-4. `posts(created_at)`

タイムラインの 2 本です。

- `SELECT id, user_id, body, created_at, mime FROM posts ORDER BY created_at DESC`（`python/app.py:287-289`、`get_index`）
- `WHERE created_at <= %s ORDER BY created_at DESC`（`python/app.py:360-363`、`get_posts` のページ送り）

```sql
EXPLAIN SELECT id, user_id, body, created_at, mime
  FROM posts ORDER BY created_at DESC;
CREATE INDEX idx_posts_created ON posts (created_at);
```

**`created_at` の精度に注意してください。** `TIMESTAMP` の既定は秒精度です。ベンチは短時間に大量の投稿を `INSERT` するので、同じ `created_at` の行が並んでしまいます。結果の並びが安定しない場合は `(created_at, id)` にして、索引側で tie-break を持たせることを検討してください。まず `EXPLAIN` で `Backward index scan` が出るかだけ見ます。

### 2-5. `users(account_name)`

ログインと、登録の重複チェックです。

- `SELECT * FROM users WHERE account_name = %s AND del_flg = 0`（`python/app.py:88`、`try_login`）
- `SELECT * FROM users WHERE account_name = %s AND del_flg = 0`（`python/app.py:299-302`、`/@<account_name>`）
- `SELECT 1 FROM users WHERE account_name = %s`（`python/app.py:262`、登録の重複チェック）

```sql
EXPLAIN SELECT * FROM users WHERE account_name = 'alice' AND del_flg = 0;

SELECT COUNT(*) FROM information_schema.statistics
 WHERE table_schema = DATABASE() AND table_name = 'users'
   AND index_name = 'idx_users_account_name';

-- 未定義なら
CREATE UNIQUE INDEX idx_users_account_name ON users (account_name);
```

**`UNIQUE` で作れるか必ず確認します。** `account_name` に重複があれば `UNIQUE` は失敗します。そのときは重複データを確認します。

```sql
SELECT account_name, COUNT(*) FROM users GROUP BY account_name HAVING COUNT(*) > 1;
```

重複があれば、ベンチの `/initialize`（`python/app.py:61-69`）が `id > 1000` を消すので、そのあとにもう一度確認します。

### 2-6. `users(authority, del_flg, created_at)`、任意

`/admin/banned` 専用です（`python/app.py:479-481`）。

```sql
EXPLAIN SELECT * FROM users WHERE authority = 0 AND del_flg = 0 ORDER BY created_at DESC;
CREATE INDEX idx_users_banned ON users (authority, del_flg, created_at);
```

ベンチではあまり叩かれません。点数に出なければ**入れません**。

### 2-7. 貼らないインデックス

以下は**意図的に無い**ので、そのままにします。

| クエリ | 理由 |
| --- | --- |
| `SELECT * FROM users WHERE id = %s`（`python/app.py:127,151,158`） | 主キーで十分 |
| `SELECT * FROM posts WHERE id = %s`（`python/app.py:373,429`） | 主キーで十分。問題は `imgdata` なので [§3-1](#3-1-一覧クエリに-imgdata-が入っている) で扱います |
| `DELETE FROM users WHERE id > 1000` ほか（`python/app.py:62-64`） | 主キーのレンジですで |
| `UPDATE users SET del_flg = %s WHERE id = %s`（`python/app.py:500`） | 主キーで十分 |

### 2-8. インデックスは 1 本ずつ

**インデックスは 1 本ずつ足します。** まとめて貼ると、どれが効いたか分からなくなります。手順は [040_practice/0100-sql-tuning.md](040_practice/0100-sql-tuning.md) と同じ流れです。

```bash
# 1 手 = EXPLAIN、その 1 本だけの CREATE INDEX、再 EXPLAIN、fail=0 のベンチ
# 進まなければ DROP して次へ
```

---

## 3. クエリとコードの書き換え

インデックスだけでは直らない部分です。`make_posts()` が悪い根源なので、ここを直します。

### 3-1. 一覧クエリに `imgdata` が入っている

`SELECT * FROM posts` は MEDIUMBLOB まで取ります。

| 箇所 | クエリ | 問題 |
| --- | --- | --- |
| `python/app.py:373` | `SELECT * FROM posts WHERE id = %s`（`get_posts_id`、投稿詳細） | 画面で使わない `imgdata` まで 1 枚分転送されます |
| `python/app.py:429` | `SELECT * FROM posts WHERE id = %s`（`get_image`） | 画像本体は要るのでこれは正しい。ただし `/image` の重さの源です |
| `python/app.py:287-289` | 一覧取得 | 列は明示されていますが、ここは `imgdata` が入っていません。ただし `LIMIT` が無いので行数が問題です（[§3-3](#3-3-全件-fetchall-して-20-件で捨てる)） |

**対処は 2 択です。**

1. **列を明示する。** 投稿詳細では `imgdata` を外します。
   ```sql
   SELECT id, user_id, body, mime, created_at FROM posts WHERE id = %s;
   ```
2. **画像を EBS 上のファイルに寄せる。** [0090-app-tuning.md](040_practice/0090-app-tuning.md) §2 の手順です。`/image` は nginx が直接返します。

**このリポジトリで実測済み。** 画像ファイル化で 3411 から 3578、さらに `comments` のインデックスで 58063。ただし、列を明示するだけで `make_posts` の転送量は減るので、コードだけの変更だけでも試す価値はあります。

### 3-2. `make_posts()` が N+1

`python/app.py:132-166` です。1 投稿あたりの発行クエリは次のとおりです。

| 回数 | クエリ | 行 |
| --- | --- | --- |
| 1 | `COUNT(*) FROM comments WHERE post_id` | 136-140 |
| 1 | `SELECT * FROM comments WHERE post_id` に `LIMIT 3` | 142-148 |
| コメント数 | `SELECT * FROM users WHERE id`、1 コメントにつき 1 回 | 150-154 |
| 1 | `SELECT * FROM users WHERE id`、投稿者 | 158 |

1 画面 20 投稿、それぞれコメント 3 件で、**最悪 20 × 2 に 20 × 3 を足して 100 本**のクエリが 1 リクエストで走ります。投稿者が重複していても毎回引きます。

**対処は set 化です。** 手順は次のとおりです。

1. `results` から必要な `post_id` を集める
2. `post_id IN (...)` で `comments` を 1 回取る
3. `user_id` を全部集めて `SELECT * FROM users WHERE id IN (...)` を 1 回取る
4. 投稿者の `user_id` も同じクエリに含める
5. Python の dict に詰める

**注意。** `LIMIT 3` を SQL 側で保てば `(post_id, created_at)` のインデックスを活かせます。全件取ってから Python 側で切る方法は、インデックスに filesort  المطلوبです。1 投稿ごとに 3 件だけ取るのをインデックスに任せるか、窓関数で 1 本に畳むか、**測って決めます**。

```sql
-- MySQL 8 の窓関数で 1 本に畳む例
SELECT * FROM (
  SELECT c.*, ROW_NUMBER() OVER (PARTITION BY post_id ORDER BY created_at DESC) AS rn
    FROM comments c
   WHERE post_id IN (1,2,3,4,5)
) t WHERE rn <= 3;
```

10 万行の repartition は重いです。**まず [§2-1](#2-1-commentspost_id-created_at最重要) のインデックスだけ入れて測ってください。** それで点数が上がれば、N+1 は後回しで構いません。

### 3-3. 全件 `fetchall()` して 20 件で捨てる

`get_index`（`python/app.py:287-290`）です。

```python
cursor.execute("SELECT `id`, `user_id`, `body`, `created_at`, `mime` FROM `posts` ORDER BY `created_at` DESC")
posts = make_posts(cursor.fetchall())
```

`POSTS_PER_PAGE = 20` ですが、SQL 側に `LIMIT` がありません。`posts` 1 万行を**全部** Python に取り込んでから、`make_posts` の中で 20 件で `break` しています（`python/app.py:164-165`）。**9980 行が無駄です。**

**対処は 2 択です。**

1. **SQL に `LIMIT` を付ける。** 索引 `idx_posts_created` が無いと意味が無いので [§2-4](#2-4-postscreated_at) が前提です。
   ```sql
   SELECT `id`, `user_id`, `body`, `created_at`, `mime`
     FROM `posts` ORDER BY `created_at` DESC LIMIT 20;
   ```
   **ただし素の挙動が変わります。** `make_posts` は `del_flg` の立っている投稿を `posts` に積みません（`python/app.py:161-162`）。単純に `LIMIT 20` を入れると、削除ユーザーが混ざった場合 20 件未満になります。対象ユーザーを SQL で除外するか、足りなければ補充するクエリを足す必要があります。
2. **ストリーミングカーソルにする。** `SSCursor` または `SSDictCursor` を使って `fetchall` をやめます。1 行ずつ読んで 20 件で止めます。サーバ側の送信量と Python のメモリが同時に減ります。

**警告。** どちら的点数は上がりますが、仕様が変わります。ベンチの `pass` 数が減っていないことを必ず確認してください。

`get_posts`（`python/app.py:360-366`）と `get_user_list`（`python/app.py:307-311`）も同じ構造で `LIMIT` がありません。同じ検討が必要です。

### 3-4. 投稿 ID を全件読んで `IN` リストを組み立てている

`get_user_list`（`python/app.py:318-328`）です。

```python
cursor.execute("SELECT `id` FROM `posts` WHERE `user_id` = %s", (user["id"],))
post_ids = [p["id"] for p in cursor]
post_count = len(post_ids)
...
cursor.execute("SELECT COUNT(*) AS count FROM `comments` WHERE `post_id` IN %s", (post_ids,))
```

投稿数の**カウントをするのに全 ID を引いてきます**。しかもその ID リストが `IN (...)` に展開されます。投稿者が 500 投稿すれば 500 要素の `IN` になります。

**対処。**

```sql
-- post_count は DB に任せます
SELECT COUNT(*) FROM posts WHERE user_id = %s;

-- commented_count は JOIN で 1 本にします
SELECT COUNT(*) FROM comments c
  JOIN posts p ON p.id = c.post_id
 WHERE p.user_id = %s;
```

これで `posts(user_id)` のインデックスだけで済み、`IN` の長いクエリも消えます。`JOIN` の駆動側が変わると `EXPLAIN` の結果も変わるので、まず測ってください。

### 3-5. `users` の `SELECT *` が `passhash` を引いている

`passhash` は SHA-512 の hex で 128 文字です。`VARCHAR` なら 512 桁ぶんあります。画面に出さない項目のまま毎回運んでいます。

| 箇所 | 必要な列 |
| --- | --- |
| `python/app.py:127`、`get_session_user` | `id`, `account_name`, `authority`, `del_flg` |
| `python/app.py:150-154`、コメントの作者 | `id`, `account_name` |
| `python/app.py:158`、投稿者 | `id`, `account_name` |
| `python/app.py:88`、`try_login` | `passhash` が要ります。これは落とせません |
| `python/app.py:479-481`、`/admin/banned` | 全列。落ちにくいです |

**`get_session_user()` は認証済みリクエストすべてで走ります**（`app.py:221,228,242,251,284,330,378,384,447,471,492`）。ここを削ると全体の I/O 量に効きます。

**代償。** 将来権限やメールアドレスの列を足したいときは足りません。列一覧は自分で選んでください。

---

## 4. 接続とドライバ

### 4-1. 接続が 1 本だけ

各言語の実装はこうなっています。

| 言語 | 実装 | 場所 |
| --- | --- | --- |
| Python | モジュールグローバルの `_db`。プロセス 1 本につき 1 接続 | `python/app.py:45-56` |
| Node | `createPool({ connectionLimit: 1 })` | `node/src/app.ts:30-38` |
| Ruby | `Thread.current[:isuconp_db]`、スレッドごと | `ruby/app.rb:35-46` |
| PHP | `new PDO(...)`、リクエストごとに新規 | `php/index.php:55` |

**意味するのは、DB の同時実行クエリ数がワーカー数に等しいということです。** MySQL 側は 1 スレッドしか使えません。`innodb_buffer_pool_size` をいくら大きくしても、ワーカー 1 台では 1 クエリずつしか流れません。

**これが SQL チューニングの前提条件です。** インデックスやクエリを書き換えても点数が上がらないとき、原因は DB ではなくここにある可能性があります。

**手順。**

1. ワーカー数を上げる。`isu-python.service` の `-w` です（[0080-infra-params.md](040_practice/0080-infra-params.md) §5。実測で 1057 から 3411）
2. そのあとで `innodb_buffer_pool_size` を大きくする（[§5-1](#5-1-innodb_buffer_pool_size最重要の設定ノブ)）
3. `max_connections` が足りているか確認する（[§5-9](#5-9-その他の既定値)）

**逆方向に注意。** DB が 1 台で CPU 飽和しているなら、ワーカーを増やすと点数は下がります。DB 台の load average と `SHOW PROCESSLIST` を見てから決めてください。

### 4-2. プリペアドステートメント

`python/app.py:87-89` のように `cur.execute(query, (params,))` としています。`MySQLdb` はクライアント側で補間して**毎回テキスト SQL を送ります**。つまり毎回パースと最適化が走ります。

サーバ側プリペアを使うかどうかを**スロークエリログで測定してから**決めてください。手順は [0090-app-tuning.md](040_practice/0090-app-tuning.md) §5 にあります。

**期待値は小さいです。** このクエリはインデックスが効けば 1 ms 未満なので、パースコストは全体をカバーしません。このリポジトリで実測した `digest()` の 58063 から 58506 と同じく、効果が小さければ「小さい」と書くのが正しい結論です。

### 4-3. `DictCursor` のコスト

`python/app.py:53` で `MySQLdb.cursors.DictCursor` を指定しています。1 行ごとに dict を作ります。`get_index` で 1 万行取り出すときは dict が 1 万個できます。

- `post["comment_count"]` のようなアクセス写法に慣れているなら tuple に変えます
- [§3-3](#3-3-全件-fetchall-して-20-件で捨てる) のストリーミングカーソルにするなら `SSDictCursor` または `SSCursor`

**この議論は Python 限定です。** 他の言語は行を構造体や Map に取るので同じ話はできません。

### 4-4. パラメータの型

`get_posts_id`（`python/app.py:369-373`）は URL の `id` を文字列のまま渡しています。

```python
@app.route("/posts/<id>")
def get_posts_id(id):
    cursor.execute("SELECT * FROM `posts` WHERE `id` = %s", (id,))
```

`id` は `str` です。`INT` の主キーなので、MySQL は暗黙の型変換を当てます。インデックスは使えますが、1 行ずつ変換が走ります。`get_image`（`python/app.py:423`）は `int(id)` しているので正しいです。揃えます。

```python
pid = int(id)
cursor.execute("SELECT ... FROM `posts` WHERE `id` = %s", (pid,))
```

**逆に、文字列のカラムに int を流すのも危険です。** 暗黙変換でインデックスが消えることがあります。特に [§0](#0-最初に必ず確認すること) の collation が違う場合です。

---

## 5. MySQL サーバ設定

**現状は素の Ubuntu デフォルトです。** `backup/base/etc/mysql/mysql.conf.d/mysqld.cnf` には `key_buffer_size = 16M` と `bind-address = 127.0.0.1` 以外は**すべてコメントアウト**されています。`mysql.conf.d/mysql.cnf` は空の `[mysql]`、`conf.d/mysql.cnf` も空です。

つまり以下は**全部 MySQL の既定値のまま**です。drop-in は `backup/s3/etc/mysql/mysql.conf.d/` に置きます。`mysqld.cnf` より後に読まれるので上書きされます。

### 5-1. `innodb_buffer_pool_size`、最重要の設定ノブ

既定は **128M** です。データセットは `posts` 1 万行、そのうち画像 BLOB、`comments` 10 万行、`users` 1000 行。buffer pool に乗らないページは毎回ディスクから読みます。

```ini
# backup/s3/etc/mysql/mysql.conf.d/zz-tuning.cnf
[mysqld]
innodb_buffer_pool_size = 2G
```

**値は推測で決めないこと。** こう決めます。

```bash
free -h   # MemAvailable を見ます
sudo mysql -e "SHOW VARIABLES LIKE 'innodb_buffer_pool_size';"
```

- 共有ホストなら OS とアプリに 1 GB くらい残して、残り 50% から 70% を当てる
- MySQL が別台なら、その台の実メモリで決めます
- 1 GB 以下しかないのに 2G を書くと swap が出て、**むしろ遅くなります**

確認:

```bash
sudo mysql -e "SHOW ENGINE INNODB STATUS\G" | grep -iE 'buffer pool|hit rate|pending'
```

`Buffer pool hit rate` が 99.9% 以上なら、十分に効いています。

### 5-2. `innodb_flush_log_at_trx_commit`

既定は `1` で、コミットごとに fsync します。ベンチは `INSERT INTO comments` を大量に行うので、ここが効きます。

**このリポジトリで実測済み。** `= 2` で 59200 から 59343（`fail=0`）。効果は小さいですが、耐久性とのトレードとして妥当です。OS クラッシュで最大 1 秒分を失うだけで、MySQL 自体のクラッシュでは失いません。

```ini
[mysqld]
innodb_flush_log_at_trx_commit = 2
```

**`0` は使いません。** OS クラッシュでコミット済みトランザクションが全部消えます。ベンチの `/initialize` で消えた投稿が復活するのと整合しません。

手順は [040_practice/0100-sql-tuning.md](040_practice/0100-sql-tuning.md) にあります。

### 5-3. redo log のサイズ

既定は `innodb_log_file_size` 48M が 2 本です。コメントの連続 `INSERT` だと redo log がすぐ枯れて、checkpoint の flush スパイクが出ます。

```bash
sudo mysql -e "SHOW VARIABLES LIKE 'innodb_log_file_size';"
sudo mysql -e "SHOW VARIABLES LIKE 'innodb_redo_log_capacity';"
sudo mysql -e "SHOW ENGINE INNODB STATUS\G" | grep -i 'history list length'
```

`History list length` が数千行で張り付いていたら、history list の purge が追いついていません。

```ini
[mysqld]
# MySQL 8.0.30 以降
innodb_redo_log_capacity = 1G
# それより前
# innodb_log_file_size = 512M
```

**バージョンで変数名が変わります。** `SHOW VARIABLES LIKE 'innodb_%redo%'` で確認してください。両方に書くと起動しません。

**注意。** redo log を大きくするとリカバリ時間が伸びます。ベンチでは問題ありませんが、本番では覚えておいてください。

### 5-4. スロークエリログは最初から無効

`mysqld.cnf` では `slow_query_log`、`slow_query_log_file`、`long_query_time` が**すべてコメントアウト**されています。[010_common/0020-measure.md](010_common/0020-measure.md) の slow log 採取手順で必ず**有効化**します。

```ini
[mysqld]
slow_query_log = 1
slow_query_log_file = /var/log/mysql/mysql-slow.log
long_query_time = 0.1
```

- `long_query_time = 0` は**使いません**。ディスクが埋まります。採取するときだけ設定して、すぐ 1 に戻します
- `log_queries_not_using_indexes` は**使いません**。インデックスが利かないクエリが大量に出るためログが巨大になり、ベンチ前に時間がかかります

### 5-5. `sort_buffer_size` と `tmpdir`

インデックスが効いていない `ORDER BY` は `Using filesort` になり、`sort_buffer_size`（既定 2M）を超えると**ディスクに落します**。`/tmp` が小さいとディスクが埋まります。

```bash
df -h /tmp /var/lib/mysql
sudo mysql -e "SHOW VARIABLES LIKE 'sort_buffer_size';"
sudo mysql -e "SHOW VARIABLES LIKE 'tmp_table_size';"
sudo mysql -e "SHOW VARIABLES LIKE 'tmpdir';"
```

```ini
[mysqld]
sort_buffer_size = 4M
tmp_table_size = 64M
max_heap_table_size = 64M
```

**ただし、インデックスを足すのが先です。** filesort が消えれば sort buffer には到りません。大きすぎると INSERT が遅くなります。`EXPLAIN` の `Extra` に `Using filesort` が無くなってから触ってください。

### 5-6. charset と collation の不整合

**暗黙変換でインデックスが消える罠です。** アプリは `charset: utf8mb4` を指定しています（`python/app.py:52`）。テーブル側の定義を確認します。

```bash
sudo mysql -e "SHOW VARIABLES LIKE 'character_set%';"
sudo mysql -e "SHOW VARIABLES LIKE 'collation%';"
SELECT table_name, table_collation FROM information_schema.tables
 WHERE table_schema = DATABASE();
SELECT table_name, column_name, character_set_name, collation_name
  FROM information_schema.columns
 WHERE table_schema = DATABASE() AND character_set_name IS NOT NULL;
```

**接続の charset がテーブルと違うと、インデックスが使われません。** `WHERE account_name = 'x'` のような条件が `EXPLAIN` で `type: ALL` になります。このリポジトリのアプリ 5 言語すべてが `utf8mb4` を明示しています（`python/app.py:52`、`node/src/app.ts:37`）。テーブルが別の charset なら**そっちを揃えます**。

### 5-7. `sql_mode`

```bash
sudo mysql -e "SHOW VARIABLES LIKE 'sql_mode';"
```

`ONLY_FULL_GROUP_BY` が入っている場合、窓関数や `GROUP BY` を書いたときに構文エラーになります。[§3-2](#3-2-make_posts-が-n1) の窓関数を試す前に必ず確認します。**`sql_mode` を黙って緩めないでください。** 既定の制約で壊します。

### 5-8. `optimizer_switch` と optimizer hint

```bash
sudo mysql -e "SHOW VARIABLES LIKE 'optimizer_switch';"
```

`use_condition_pushdown` が `on` なら、窓関数と派生テーブルで効きます。既定は `on` なので基本は触りません。

**MySQL 8 の per-query hint** も一応あります。

```sql
SELECT /*+ MAX_EXECUTION_TIME(1000) */ ... ;
SELECT /*+ SET_VAR(sort_buffer_size = 4M) */ ... ;
```

アプリ側に対応実装が要ります。**インデックスを先にやります。** hint で症状を隠すと、インデックス選びが壊れたまま点数だけが上がります。

### 5-9. その他の既定値

効果が小さいもの、触らないものをまとめます。

| 変数 | 既定 | 扱い |
| --- | --- | --- |
| `max_connections` | 151 | [§4-1](#4-1-接続が-1-本だけ) のワーカー数ぶんしか繋がないなら足ります。まず `SHOW PROCESSLIST` の頂点を見ます |
| `table_open_cache` | 4000 | テーブルは 3 つ。**無意味** |
| `table_definition_cache` | 2000 | 同上 |
| `thread_cache_size` | -1（自動） | 触りません |
| `key_buffer_size` | 16M | MyISAM 用です。全テーブル InnoDB なので**意味なし** |
| `innodb_io_capacity` | 400 | gp3 なら上げ余地。実測してから |
| `innodb_io_capacity_max` | 2000 | 同上 |
| `innodb_read_io_capacity` | 400 | 同上 |
| `innodb_flush_neighbors` | 1 | SSD なら 0 にします |
| `innodb_doublewrite` | ON | 信頼性のトレード。触りません |
| `innodb_adaptive_hash_index` | ON | 衝突すると悪化します。`SHOW ENGINE INNODB STATUS` で確認してから |
| `innodb_stats_persistent_sample_pages` | 20 | [§6-2](#6-2-統計情報を再収集する) で触ります |
| `max_allowed_packet` | 64M | 画像は 10 MB 上限（`app.py:16`）なので足ります |
| `tmpdir` | `/tmp` | 小さいと sort の spill でディスクが埋まります |
| binlog | 無効 | 有効にすると `sync_binlog` と相まって write が重くなります。ベンチでは**無効のまま**にします |

---

## 6. データ配置と統計

### 6-1. `imgdata` を DB から追い出す

`posts.imgdata` は MEDIUMBLOB です。`SELECT * FROM posts` で必ず乗っかってきます。

対応は [0090-app-tuning.md](040_practice/0090-app-tuning.md) §2 の画像ファイル化です。ファイルに置いたら次の SQL を考えます。

```sql
-- 先に EBS にファイルがあることを確認します。順序を逆にすると画像が消えます
UPDATE posts SET imgdata = NULL;
ANALYZE TABLE posts;
OPTIMIZE TABLE posts;
```

**このリポジトリのチュートリアルは「投稿の本体データは MySQL のまま残す」「DB の `imgdata` は消さない」というルールを明示しています**（[0070-static-cache.md](040_practice/0070-static-cache.md)、[0040-cache.md](040_practice/0040-cache.md)）。手順書に沿うなら上の `UPDATE` と `OPTIMIZE` はやりません。

OPTIMIZE TABLE は InnoDB のテーブル再構築です。時間はかかりますが、buffer pool に乗るデータ量が減ります。

### 6-2. 統計情報を再収集する

インデックスを足した直後や、大量のデータ読み込みの後、MySQL の統計は古くなります。統計が乱れているとインデックスの選択を誤ります。

```sql
ANALYZE TABLE comments;
ANALYZE TABLE posts;
ANALYZE TABLE users;
```

`comments` は 10 万行あります。既定の `innodb_stats_persistent_sample_pages = 20` では偏ったデータで精度が落ちます。

```bash
sudo mysql -e "SHOW VARIABLES LIKE 'innodb_stats_persistent_sample_pages';"
sudo mysql -e "SHOW VARIABLES LIKE 'innodb_stats_on_metadata';"
```

```ini
[mysqld]
innodb_stats_persistent_sample_pages = 64
```

**注意。** 再起動しても統計は再収集されません。persistent 設定だからです。ただしベンチの `/initialize` で大量 DELETE が走ると統計と実データがずれます。ベンチの前後に `ANALYZE TABLE` を挟むのはありです。

### 6-3. `passhash` を `BINARY(64)` にする、任意

`passhash` は SHA-512 の hex 文字列で 128 文字です。`CHAR(128)` か `BINARY(64)` にすると容量が半減します。ただし `VARCHAR` から `BINARY` に変えると `try_login`（`app.py:92`）の文字列比較が変わります。`digest()` は hex 文字列を返すので、`bytes.fromhex()` を通す必要があります。

**効果が小さいので優先度は低いです。** 入れるなら 1 手として測ってください。

### 6-4. 外部キー

**足しません。** 外部キーはあると `INSERT` と `DELETE` のたびに親テーブルを検査するため遅くなります。

### 6-5. `/initialize` の副作用

`db_initialize()`（`python/app.py:59-69`）です。

```sql
DELETE FROM users WHERE id > 1000;
DELETE FROM posts WHERE id > 10000;
DELETE FROM comments WHERE id > 100000;
UPDATE users SET del_flg = 0;
UPDATE users SET del_flg = 1 WHERE id % 50 = 0;
```

- `UPDATE users SET del_flg = 1 WHERE id % 50 = 0` はカラムに関数があるのでインデックスが使えず**フルスキャン**です。`users` は 1000 行なので実害は小さいですが、原則として索引を使える形に書きます
- 大量 DELETE で undo が積み上がります。ベンチ前に `SHOW ENGINE INNODB STATUS` の `History list length` を見てください

---

## 7. 検証手順

**すべての項目にこの流れを通します。** 一度に複数入れません。

### 7-1. 最初の状態を取る

```bash
# DB の台（分割後なら s3）で打つ
sudo mysql -e "SHOW INDEX FROM isuconp.comments;"
sudo mysql -e "SHOW INDEX FROM isuconp.posts;"
sudo mysql -e "SHOW INDEX FROM isuconp.users;"
sudo mysql -e "SHOW VARIABLES LIKE 'innodb_buffer_pool_size';"
sudo mysql -e "SHOW VARIABLES LIKE 'innodb_flush_log_at_trx_commit';"
sudo mysql -e "SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_read%';"
```

### 7-2. スロークエリログを出す

[010_common/0020-measure.md](010_common/0020-measure.md) の手順です。要点だけ。

```ini
[mysqld]
slow_query_log = 1
slow_query_log_file = /var/log/mysql/mysql-slow.log
long_query_time = 0.1
```

```bash
sudo systemctl restart mysql
# アプリ側（分割後なら s1 と s2 の両方）も restart します
sudo systemctl restart isu-python.service
```

> **落とし穴。** `SET GLOBAL long_query_time` は**既存の接続には効きません**。`isu-python` を restart しないと、ベンチのクエリが 1 つもログに残りません。

### 7-3. ベンチ 1 本を s1 で打つ

分割後のベンチ先は nginx 役（s1）の `localhost` です。

```bash
# s1 で打つ
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
```

**`fail` が 0 でない点数は比べません。**

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  note=" >> ~/bench-notes/scores.txt
```

### 7-4. そのあと何を見るか

| 見るもの | コマンド |
| --- | --- |
| `EXPLAIN` の `type`、`key`、`rows`、`Extra` | `EXPLAIN` で貼ったクエリ |
| スロークエリの増減 | `sudo slp my --file /var/log/mysql/mysql-slow.log` |
| クエリの順位 | `pt-query-digest` |
| buffer pool の hit 率 | `SHOW ENGINE INNODB STATUS` の `Buffer pool hit rate` |
| 同時実行数 | `SHOW PROCESSLIST`、`SHOW GLOBAL STATUS LIKE 'Threads_connected'` |
| DB 台の CPU と I/O | DB 台で `uptime`、`iostat -x 1`、`vmstat 1` |

### 7-5. 進まなければ戻す

```sql
DROP INDEX idx_comments_post_created ON comments;
```

```bash
# 設定だけ戻す場合
sudo systemctl restart mysql
# コードだけ戻す場合
sudo systemctl restart isu-python.service
```

---

## 付録: SQL 文の一覧

5 言語で SQL 文が同じであることを確認しました。インデックスを足すときは、この表のどの行が重い経路かを基準に考えます。

| 番号 | クエリ | Python | PHP | Ruby | Node | Go |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `DELETE FROM users WHERE id > 1000` | `app.py:62` | `index.php` | `app.rb` | `app.ts` | `app.go` |
| 2 | `DELETE FROM posts WHERE id > 10000` | `app.py:63` | 同上 | 同上 | 同上 | 同上 |
| 3 | `DELETE FROM comments WHERE id > 100000` | `app.py:64` | 同上 | 同上 | 同上 | 同上 |
| 4 | `UPDATE users SET del_flg = 0` | `app.py:65` | 同上 | 同上 | 同上 | 同上 |
| 5 | `UPDATE users SET del_flg = 1 WHERE id % 50 = 0` | `app.py:66` | 同上 | 同上 | 同上 | 同上 |
| 6 | `SELECT * FROM users WHERE account_name = ? AND del_flg = 0`（ログイン） | `app.py:88` | 同上 | 同上 | 同上 | 同上 |
| 7 | `SELECT * FROM users WHERE id = ?` | `app.py:127,151,158` | 同上 | 同上 | 同上 | 同上 |
| 8 | `SELECT COUNT(*) FROM comments WHERE post_id = ?` | `app.py:137` | 同上 | 同上 | 同上 | 同上 |
| 9 | `SELECT * FROM comments WHERE post_id = ? ORDER BY created_at DESC` | `app.py:143` | 同上 | 同上 | 同上 | 同上 |
| 10 | `SELECT 1 FROM users WHERE account_name = ?` | `app.py:262` | 同上 | 同上 | 同上 | 同上 |
| 11 | `INSERT INTO users (account_name, passhash) VALUES (?, ?)` | `app.py:268` | 同上 | 同上 | 同上 | 同上 |
| 12 | `SELECT id,user_id,body,created_at,mime FROM posts ORDER BY created_at DESC` | `app.py:288` | 同上 | 同上 | 同上 | 同上 |
| 13 | `SELECT * FROM users WHERE account_name = ? AND del_flg = 0`（ユーザーページ） | `app.py:300` | 同上 | 同上 | 同上 | 同上 |
| 14 | `SELECT id,user_id,body,mime,created_at FROM posts WHERE user_id = ? ORDER BY created_at DESC` | `app.py:308` | 同上 | 同上 | 同上 | 同上 |
| 15 | `SELECT COUNT(*) FROM comments WHERE user_id = ?` | `app.py:314` | 同上 | 同上 | 同上 | 同上 |
| 16 | `SELECT id FROM posts WHERE user_id = ?` | `app.py:318` | 同上 | 同上 | 同上 | 同上 |
| 17 | `SELECT COUNT(*) FROM comments WHERE post_id IN (...)` | `app.py:325` | 同上 | 同上 | 同上 | 同上 |
| 18 | `SELECT id,user_id,body,mime,created_at FROM posts WHERE created_at <= ? ORDER BY created_at DESC` | `app.py:361` | 同上 | 同上 | 同上 | 同上 |
| 19 | `SELECT * FROM posts WHERE id = ?`（詳細、`/image`） | `app.py:373,429` | 同上 | 同上 | 同上 | 同上 |
| 20 | `INSERT INTO posts (user_id,mime,imgdata,body) VALUES (?,?,?,?)` | `app.py:413` | 同上 | 同上 | 同上 | 同上 |
| 21 | `INSERT INTO comments (post_id,user_id,comment) VALUES (?,?,?)` | `app.py:460` | 同上 | 同上 | 同上 | 同上 |
| 22 | `SELECT * FROM users WHERE authority = 0 AND del_flg = 0 ORDER BY created_at DESC` | `app.py:480` | 同上 | 同上 | 同上 | 同上 |
| 23 | `UPDATE users SET del_flg = ? WHERE id = ?` | `app.py:500` | 同上 | 同上 | 同上 | 同上 |

アプリが発行する SQL は論理的には 22 種類です（`SELECT` 13 種類、`INSERT` 3 種類、`UPDATE` 3 種類、`DELETE` 3 種類）。上の表は呼び出し箇所ごとにまとめたもので、23 行あります。番号 6 と 13 は同じクエリの 2 箇所です。

Node には `SELECT * FROM users WHERE account_name = ?` で `del_flg` を持たないものが 1 つ余分にあります（`app.ts`）。`/login` の判定に使われる変種です。

`/` と `/posts` のタイムラインは `get_index` と `get_posts` で、それぞれ表の番号 12 と 18 が該当します。**ここが一番重い経路です。** インデックスを足す順番はここを基準に考えます。

---

## このリストに無いもの

意図的に除外したものです。

| 項目 | 理由 |
| --- | --- |
| Go、Ruby、PHP、Node での実装 | 手順書は Python 専用です（[010_common/webapp-setup/python.md](010_common/webapp-setup/python.md)）。SQL レイヤは付録のとおり同一なので、インデックスと設定はそのまま使えます。コードの書き方の議論は Python 限定です |
| クエリキャッシュ | MySQL 8 で削除済み。**追わないでください** |
| Redis などの別のキャッシュ | SQL チューニングとは別の話題です。[0040-cache.md](040_practice/0040-cache.md) を参照 |
| 分散 DB、レプリカ | ベンチマークの規模では不要です |
| テーブルの垂直分割 | `posts`、`comments`、`users` を分割すると管理コストに見合いません |
| 本番での `EXPLAIN ANALYZE` | 実際に走ります。まず `EXPLAIN` と `EXPLAIN FORMAT=JSON` を使い、それで足りなければ `ANALYZE` へ |
| SQL インジェクション | 5 言語すべてがプレースホルダを使っています。ここは性能の話ではありません |
| EBS のボリュームタイプ変更 | 性能には効きますが、基盤の話です |

---

## 関連文書

- [040_practice/0100-sql-tuning.md](040_practice/0100-sql-tuning.md) — SQL チューニングの手順書
- [040_practice/0090-app-tuning.md](040_practice/0090-app-tuning.md) — 画像ファイル化、プリペアドステートメントの計測
- [010_common/0020-measure.md](010_common/0020-measure.md) — 計測の基本（alp、slp、EXPLAIN、インデックス）
- [040_practice/0080-infra-params.md](040_practice/0080-infra-params.md) — ワーカー数、somaxconn、FD 上限
- [040_practice/0110-overall-tuning.md](040_practice/0110-overall-tuning.md) — py-spy で残りの重さを見る