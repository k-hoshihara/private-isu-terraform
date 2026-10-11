# 0120 DB の仕事量を減らす

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）は、手元の private-isu-terraform リポジトリで実行します。

- 複数台に分けた 3 台構成（[0110-split-4.md](0110-split-4.md) の終了状態）で、**データベース（DB）に頼む仕事そのもの**を減らします。
- CPU やメモリは増やしません。問い合わせ（クエリ）の回数・読む行数・返す行数を削ります。
- 出口は [0130-response-build.md](0130-response-build.md)（応答を作る処理）です。

- 分けたあとは、リクエスト 1 つごとに s1 → s2 → s3 と機械をまたぐ通信が入ります。DB への問い合わせ 1 回が、その通信 1 往復ぶんの手間になります。
- まず、ベンチを 1 本回したときに問い合わせが何回出るかを数えます。それから、1 つずつ減らします。
- `fail`（エラーの件数）が 0 でない点数は、比べません。

- 順番: 仕事量を数える（1.）→ 画像の整合を取る（2.）→ 索引と `FORCE INDEX`（3.）→ `get_posts` の全件取得をやめる（4.）→ コメント数を列に持つ（5.）→ 残った上位から 1 つ（6.）→ 公式ベンチで判定する（7.）。

- ベンチは、[0110-split-4.md](0110-split-4.md) と同じです。
- ベンチ前の準備（bench-prep）は、s1 でログファイルの退避と開き直し（`mv` + `reopen`）、s3 で遅い問い合わせの記録（slow log）の回転です。ベンチは s1 の `localhost` に打ちます。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  note=dbload-baseline" >> ~/bench-notes/scores.txt
```

<a id="1-仕事量を数える"></a>

## 1. 仕事量を数える

- いきなり直さずに、まず **ベンチ 1 本で問い合わせが何回出ているか**を数えます。
- [0080-app-tuning.md §1](0080-app-tuning.md#1-対象を-1-本に決めるalp--slp--pt-query-digest) と同じ「全件キャプチャ」を、s3 で取ります（速い問い合わせも全部記録させる設定です。短い 1 本だけ取り、`long_query_time` は必ず `1` に戻します）。

```bash
# s3 で打つ
sudo mysql -e "SET GLOBAL slow_query_log = 1"
sudo mysql -e "SET GLOBAL slow_query_log_file = '/var/log/mysql/mysql-slow.log'"
sudo mysql -e "SET GLOBAL long_query_time = 0"
```

```bash
# s1 と s2 で打つ（新規接続に 0 を効かせる）
sudo systemctl restart isu-python.service
```

```bash
# s1 で打つ（ベンチ 1 本。この点数がこの章の基準になります）
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=dbload-capture-baseline" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13、`c7a.large` 3 台 / `ap-northeast-1` / `nproc=2` / メモリ 3.7Gi）: `{"pass":true,"score":97232,"success":92519,"fail":0,"messages":[]}`（`fail=0`）。

- 集計します（この段階ではログを回転しません。今も書き込み中のログに truncate（中身の消去）は使いません）。

```bash
# s3 で打つ
sudo mysql -e "SET GLOBAL long_query_time = 1"
sudo mysql -e "SET GLOBAL slow_query_log = 0"
sudo grep -c '^# Time' /var/log/mysql/mysql-slow.log    # 合計クエリ数
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 8 2>/dev/null \
  | grep -E '^# +[0-9]+ ' | head -8
```

### 観察すること

- 「問い合わせが何万回出ているか」と「1 種類の問い合わせが全体の何割を占めるか」を言えます
- `R/Call`（1 回あたりの時間）が大きい＝1 回が重い（索引や `LIMIT` で読む量を減らす仕事）、`Calls`（回数）が大きい＝呼ばれすぎ（メモリに覚えさせる・数を列に持たせる仕事）で、直し方が違います
- `long_query_time`（記録する基準の時間）を `1` に戻しました

<a id="2-画像の整合を取るfail-が出たときの切り分け"></a>

## 2. 画像の整合を取る（fail が出たときの切り分け）

- [0080-app-tuning.md §2](0080-app-tuning.md#2-アップロード画像を静的ファイルにする投稿の本体データは-db-に残す) で画像をファイルで返す形（s1 の `public/image/`）にしてあると、**ディスク上のファイルが DB のデータより先に見られます**。
- ベンチの初期化で投稿が消えても、ファイルは残ります。
- 新しい投稿の ID が古いファイルの ID と重なると、内容の違う画像が返ってしまいます。すると `fail` になります。

```text
{"pass":false,"score":93926,"success":90543,"fail":128,
 "messages":["静的ファイルが正しくありません (GET /image/12764.jpg)", ...]}
```

- 原因の切り分けは、3 点を見ます。
- **`fail` が出た点数は、比べません。**

```bash
# s1 で打つ
curl -s http://127.0.0.1/image/<失敗したID>.jpg | md5sum
md5sum /home/isucon/private_isu/webapp/public/image/<失敗したID>.jpg
# s3 で打つ（DB 側の imgdata の長さと比べる）
mysql -uisuconp -pisuconp isuconp -N -e \
  "SELECT id, LENGTH(imgdata) FROM posts WHERE id = <失敗したID>"
```

- 直し方は、次のとおりです（複数台に分けた構成では、こちらが安全です）。

1. **投稿を受け付けた側でファイルを更新しない**（s2 が処理した投稿は s1 のディスクに届かず、食い違いが残ります）
2. **いま DB にある ID と重なっていない古いファイルを消します**。今の初期データ（fixture）の投稿 ID の範囲だけを残します:

```bash
# s1 で打つ（fixture の最大 ID は SHOW TABLE STATUS で確認して合わせる）
find /home/isucon/private_isu/webapp/public/image/ -type f \
  \( -name '*.jpg' -o -name '*.png' -o -name '*.gif' \) \
  | awk -F/ '{n=$NF; sub(/\.[a-z]+$/,"",n); if (n+0 > 10000) print}' | xargs -r rm -f
```

3. 新しく投稿された画像は、ファイルが無ければ DB から返す形（[0110-split-2.md §8](0110-split-2.md#8-nginx--アプリ--静的ファイル--mysql) の `@app`）＋ nginx の `proxy_cache`（一度見た物を覚えておく仕組み）で返します

実測サンプル（2026-10-13）: 古いファイル 253 件を削除（10253 → 10000 件）し、`{"pass":true,"score":98520,"success":93667,"fail":0,"messages":[]}`（`fail=0` に復帰）。書き込み側でファイルを更新する形（s1 のみ）では `fail=3` までしか減らなかったため、削除側を残しました。

### 観察すること

- `fail=0` になってから次の手に進みます。`fail` が出た点数は、点数の推移表に載せません
- 画像の「元のデータ」は DB（`posts.imgdata`）にだけあります。ファイルは覚えているコピー（キャッシュ）の一種なので、消しても DB から取り直せます（[0030-cache.md §1](0030-cache.md#1-投稿の本体データは-mysql-と-ebs)）

<a id="3-索引と-force-index"></a>

## 3. 索引と `FORCE INDEX`

- §1 で「1 回が重い」問い合わせが残っていれば、ここが仕事です。
- 索引（検索を速くするための目次）は、1 本ずつ貼ります。
- `EXPLAIN`（MySQL がどの順で読むかを先に見せる命令）で、読み方が変わったことを確認してから、ベンチします。

```sql
-- s3 で打つ
USE isuconp;
CREATE INDEX idx_posts_created ON posts (created_at DESC);
CREATE INDEX idx_comments_user ON comments (user_id);
```

- 一覧の問い合わせは、`ORDER BY created_at DESC LIMIT 20`（新しい順に 20 件だけ取る）が本命です。
- ところがこの形では、MySQL の自動調整機能（optimizer）が `users` 側から読んでしまい、`Using temporary; Using filesort`（一時作業と並べ直しが出る、という表示）になりがちです。
- **索引を貼っただけでは、読み方が変わらないことがあります。**

```sql
EXPLAIN
SELECT p.id, p.user_id, p.body, p.created_at, p.mime
FROM posts p JOIN users u ON u.id = p.user_id
WHERE u.del_flg = 0 ORDER BY p.created_at DESC LIMIT 20;
```

- 読み方が変わらなければ、`FORCE INDEX`（どの索引を使うかの指定）で索引を選びます（直すのはアプリ側の 1 箇所です）。

```python
# get_index() のクエリ（get_posts() にも同じ指定をします）
"FROM `posts` p FORCE INDEX (idx_posts_created) "
"JOIN `users` u ON u.`id` = p.`user_id` "
"WHERE u.`del_flg` = 0 ORDER BY p.`created_at` DESC LIMIT 20"
```

- 1 つずつ、ベンチ前の準備 → 公式ベンチ → `scores.txt` に記録、の順で回します。

```bash
# s1 で打つ（索引ごと、FORCE INDEX の前後で 1 本ずつ）
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=dbload-idx" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）:

| 手 | score | fail | 判定 |
| --- | --- | --- | --- |
| `idx_posts_created` だけ | 99403 | 0 | +883（ノイズ内）。`EXPLAIN` は `type: ALL` のままで読み方が変わらず |
| **`FORCE INDEX (idx_posts_created)`** | **135484** | **0** | **+36081**。`key: idx_posts_created` / `rows: 199` / 並べ直し（filesort）の消滅 |
| `idx_comments_user` | 150018 | 0 | +14534（ユーザーごとのコメント数の `COUNT` が索引経由に） |

### 観察すること

- `EXPLAIN` の `key`（使われる索引）と `rows`（読む行数の見積もり）が変わってからベンチします。点数だけ見ません
- 索引を貼っただけで動かなかったら、「MySQL がその索引を選ばなかっただけ」です。`FORCE INDEX` は逃げではなく、読む行数が `LIMIT`（20 件）に近づくための指定です

<a id="4-get_posts-の全件取得をやめる"></a>

## 4. `get_posts` の全件取得をやめる

- ページ送り（`GET /posts?max_created_at=`）では、`created_at <= ?`（その時刻より前の）投稿を**全部** `fetchall()`（全部読み込む）してから、20 件だけ切り出しています。
- [0015-sql-tuning.md §7](0015-sql-tuning.md#7-n1-を一括化する) の `get_index` と同じ形にします（削除済みユーザーの分を SQL の段階で外してから、`LIMIT 20` で 20 件に絞ります）。

```python
# get_posts() のクエリ
"SELECT p.`id`, p.`user_id`, p.`body`, p.`mime`, p.`created_at`, p.`comment_count` "
"FROM `posts` p FORCE INDEX (idx_posts_created) "
"JOIN `users` u ON u.`id` = p.`user_id` "
"WHERE p.`created_at` <= %s AND u.`del_flg` = 0 "
"ORDER BY p.`created_at` DESC LIMIT 20"
```

- s1 と s2 の両方に反映して、restart します。それから、ベンチします。

```bash
# s1 で打つ
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=dbload-getposts-limit" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）: `{"pass":true,"score":165407,"success":150657,"fail":0,"messages":[]}`（150018 から +15389）。ページ送り・一覧の件数・削除済みユーザーの非表示は `fail=0` で確認しました。

### 観察すること

- DB から返る行数が「全件」から「20 件」になります。slp の `ROWSSent`（返した行数）が減ります
- ページ送り（`max_created_at`）が壊れていないこと。`fail` が 0 かどうかで判定します

<a id="5-コメント数を列に持つ"></a>

## 5. コメント数を列に持つ（`COUNT(*)` をやめる）

- 一覧にコメントの件数を出すために、`comments` を `GROUP BY` で数える問い合わせが、表示する投稿 1 つごとに 1 回走っています。
- **数えた結果を `posts.comment_count` という列に持たせます。**コメントが書き込まれたときに、1 だけ増やします（同じ数を 2 箇所に持つ形で、「非正規化」と呼ばれます。投稿の本体データは MySQL のままです）。

```sql
-- s3 で打つ
USE isuconp;
ALTER TABLE posts ADD COLUMN comment_count INT NOT NULL DEFAULT 0;
UPDATE posts p SET p.comment_count =
  (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id);
-- 一致を確認（SUM が COUNT と等しいこと）
SELECT SUM(comment_count) FROM posts;
SELECT COUNT(*) FROM comments;
```

- アプリ側は、3 箇所です。

1. `make_posts()` から `COUNT` の問い合わせを外し、`post["comment_count"]` は SELECT の列から取る（各 SELECT に `comment_count` を足します）
2. `/comment` の `INSERT` のあとに `UPDATE posts SET comment_count = comment_count + 1 WHERE id = %s`
3. **`db_initialize()` の最後に件数を作り直す**（ベンチの初期化でコメントが消えるため。これがないと、件数が古いまま残ってずれます）

```python
# db_initialize() の最後に追加
cur.execute(
    "UPDATE posts p SET p.comment_count = "
    "(SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id)"
)
```

- s1 と s2 に反映して、restart → ベンチです。

```bash
# s1 で打つ
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=dbload-comment-count" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）: `{"pass":true,"score":173050,"success":157474,"fail":0,"messages":[]}`（165407 から +7643）。

### 観察すること

- 列の合計と実際の数が一致していること（`SUM(comment_count)` = `COUNT(*) FROM comments`）。表示件数がずれたら、初期化での再計算が抜けています
- 書き込みが 1 回増えますが、読みが減るので全体ではプラスです（slp に `UPDATE posts` が見えるのは正常です）

<a id="6-残った上位から-1-つ"></a>

## 6. 残った上位から 1 つ

- §5 のあとに、slp / pt-query-digest（遅い問い合わせの記録を集計するツール）を取り直します。
- **残った上位から 1 つ**を選びます。
- 選び方は [§1](#1-仕事量を数える) と同じです（1 回が重いか、回数が多すぎるか）。

- 今回の実測では、「ログイン中のユーザーを取得する問い合わせ（`SELECT * FROM users WHERE id = N`）が、リクエスト 1 つごとに 1 回」が残っていました。
- そのため、メモリに覚える仕組み（キャッシュ）を入れてみました（`get_session_user()` の結果を ID で覚える。`del_flg` を変える管理画面の操作と、ベンチの初期化で忘れさせます）。

```bash
# s1 で打つ
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=dbload-session-user-cache" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）: `score=169103` → 再計測 `score=172112`（ともに `fail=0`）。**効果はノイズ内でした。** 1 回が `AVG 0.00003` 秒と軽く、回数が多かっただけの問い合わせだったためです（[0015-sql-tuning.md §4](0015-sql-tuning.md#4-対象クエリを-1-本に決める) の「回数が多くても 1 回が軽いなら」に同じ）。効果が小さければ戻してよい、という判断例です。

### 観察すること

- 1 つずつです。効果が小さければ「小さかった」と書いて、戻すか次の 1 つにします
- 点数と、slp の該当行の変化をセットで言えます

<a id="7-公式ベンチで判定する"></a>

## 7. 公式ベンチで判定する

- 残す変更を、全部載せた状態にします。
- §1 の基準と同じ条件で、公式ベンチを 1 本回します。

```bash
# s1 で打つ
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=dbload-final" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）: `{"pass":true,"score":173356,"success":157732,"fail":0,"messages":[]}`（§1 の 97232 から **+76124**、1.78 倍）。

- もう一度全件キャプチャを取り、DB の仕事量が変わったことを見ます（手順は [§1](#1-仕事量を数える) と同じです）。

| | §1 の基準 | §7 の判定 |
| --- | --- | --- |
| 1 ベンチの問い合わせ数 | 76,565 | 129,499（処理量が 1.7 倍に増えたぶん） |
| 1 位の問い合わせ | `SELECT posts users`（JOIN＝2 つの表をつなげる読み方）2418 回・計 144.7 秒（57%） | 同じ形が 7750 回・計 **1.3 秒**（1 回 0.0598 秒 → 0.0002 秒） |
| 上位 10 種類の合計時間 | 約 244 秒 | 約 55 秒 |
| 1 回が重い上位 | JOIN と全件取得 | `INSERT posts` / `INSERT users`（**書き込みが上位に**） |

- DB でかかっていた時間が消えたぶん、次はアプリが応答を作る時間と、書き込みが上位に来ます。
- 続けて、[0130-response-build.md](0130-response-build.md) に進みます。

### 観察すること

- 最終点数が基準を超えています。超えた理由を「読む行数・返す行数・問い合わせの回数」の言葉で言えます
- slp の上位が、読み（`SELECT`）から書き込み（`INSERT`）に入れ替わっています
- `fail` が 0 です

### この節でつまずいた点

?> **この表の問題はすべて本文へ反映済みです。**「直し方」列のとおりに直してあります。同じ症状が出たら、本文の手順どおりに直して再計測します。

| # | 箇所 | 問題 | 修正内容 |
| --- | --- | --- | --- |
| 1 | §2 | 画像のファイルが DB より優先され、`fail=128` が出た | 古い移行ファイルを ID で整理し、新規画像は DB フォールバック＋キャッシュに寄せました |
| 2 | §2 | 書き込み側でファイルを更新する形は、s2 が処理した分だけ s1 に届かず `fail=3` が残った | 分割では書き込み側のファイル保存をやめ、削除側を残しました |
| 3 | §3 | 索引を貼っても `EXPLAIN` の読み方が変わらず効かなかった | `FORCE INDEX` で `posts(created_at DESC)` を選ばせました |
| 4 | §5 | コメント数を列にしたが、ベンチ初期化で件数がズレる | `db_initialize()` の最後に再計算を追加しました |
| 5 | §1 | 全件キャプチャを取り直したとき、前回分とログが合流して集計が混ざった | 取り直す前に `mv` + `FLUSH SLOW LOGS` で回転してから記録します |

## 8. トラブルシュート

### 索引を貼っても点数が動かない

- `EXPLAIN` の `key` が `NULL` なら、MySQL がその索引を選んでいないだけです（[§3](#3-索引と-force-index)）
- `rows`（読む行数）が `LIMIT` の 20 に近いのに遅いなら、遅いのは問い合わせ本体ではなく、台をまたぐ通信です（[0110-split-4.md](0110-split-4.md) の「転送とホップを減らす」の話です。ホップは台をまたぐ通信 1 回のことです）

### 表示の件数がズレる（`comment_count`）

- `db_initialize()` の再計算が抜けていませんか（[§5](#5-コメント数を列に持つ)）
- 書き込み側（`/comment`）の `UPDATE` が抜けていませんか。抜けていれば `fail` に出ます

### `fail` だけで点数が増えた / 減った

- **`fail` が 0 でない点数は比べません。** まず [§2](#2-画像の整合を取るfail-が出たときの切り分け) の切り分けをします
- 初期化した直後は、ID が振り直されて（`DELETE` と `AUTO_INCREMENT`）古いファイルとぶつかります。ファイル側は覚えているコピーなので、DB の方を正しいものとして扱います
