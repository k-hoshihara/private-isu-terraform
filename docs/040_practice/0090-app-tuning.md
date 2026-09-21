# 0090 アプリの処理方式を変えて差を見る

アプリの中身を 1 箇所ずつ変えて、点数が動くか数字で確認する。

- アップロード画像を MySQL のバイナリ（`posts.imgdata`）から静的ファイルにする
- SQL を速くする（`pt-query-digest` の読み方、インデックス 1 本、列指定の分離、N+1 のまとめ）
- ファイルディスクリプタの上限は [0080 §6](0080-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) で済ませる（ここでは新規に上げない）
- `pt-query-digest` で `ADMIN PREPARE` が最上位に来ていたら、サーバ側プリペアドステートメントを使わない形に変える

ゴールは「変えた 1 箇所の前後で、`slp` / `pt-query-digest` の該当行と alp の Sum、公式ベンチの点数」を言えること。「直したつもり」では終わらない。

前提:

- EC2 は 1 台（[0010-env.md](0010-env.md) で `webapp_instance_count = 1`）
- 実装は Python（[webapp-setup/python.md](../010_common/webapp-setup/python.md)、`isu-python.service`）。Go でも読み替え可
- [0030-measure.md](0030-measure.md) の計測サイクル（bench-prep、EXPLAIN、`scores.txt`）を回せること
- [0040 §1](0040-cache.md#1-正本は-mysql-と-ebs) の線引きを知っていること。正本は MySQL と EBS。`tmpfs`、`/dev/shm`、`/tmp` に置かない
- [0080](0080-infra-params.md) の土台は触り終わっているか、触らないと決めていること。アプリと土台を同時に振らない

順番: 対象を決める（1.）→ 画像ファイル化（2.）→ SQL（3.）→ FD 確認だけ（4.）→ `ADMIN PREPARE`（5.）→ ベンチ判定（6.）。1 手ずつ。`fail` が 0 でない点数は比べない。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  note=app-baseline" >> ~/bench-notes/scores.txt
```

## 1. 対象を 1 本に決める（alp + slp + pt-query-digest）

変える前に、3 つの道具で同じ犯人を指す。指さないままコードを触らない。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
```

```bash
# 短い全件キャプチャ（ベンチ 1 本だけ。取り終わったら long_query_time=1 に戻す）
sudo mysql -e "SET GLOBAL long_query_time = 0"
# 公式ベンチ 1 本（[0030 §1](0030-measure.md#1-ベースライン) と同じ bench-prep を先に）
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
sudo slp my --file /var/log/mysql/mysql-slow.log | head -30
sudo mysql -e "SET GLOBAL long_query_time = 1"
df -h /
```

```bash
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 10 2>/dev/null | head -80
```

読み方:

- alp の Sum 上位が `/image` で、slp でも `SELECT ... imgdata` が太い → 2.（画像ファイル化）
- slp で `examined >> sent` のクエリがある → 3.（インデックス 1 本）
- `pt-query-digest` の先頭（Rank 1）が `ADMIN PREPARE ...` → 5.（プリペアド無効化）。`ADMIN` 行が無ければ 5. は飛ばす
- どれも太くない → アプリの仕事ではない。土台（[0080](0080-infra-params.md)）かキャッシュ（[0040](0040-cache.md)）に戻る

コードの場所は `grep` で探す。関数名は決め打ちしない:

```bash
grep -rn 'def .*image\|send_file\|make_response\|Response\|imgdata' \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
grep -rn 'SELECT.*FROM posts\|SELECT.*FROM users\|SELECT.*FROM comments' \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
```

### 観察すること

- 自分の 1 手が「2. / 3. / 5.」のどれか言える（2 つ同時に入らない）
- alp の Sum 上位と slp / pt-query-digest の先頭行が同じ犯人を指している
- `long_query_time` を 1 に戻した（`df -h /` でディスクに余裕がある）

## 2. アップロード画像を静的ファイルにする（DB は正本のまま）

`/image` の素体は MySQL の `posts.imgdata`（MEDIUMBLOB）。毎回 BLOB を読むと重い。読む側をファイルに寄せる。ただし正本は MySQL のまま残す。ファイルだけを正本にしない。

線引き（[0040 §1](0040-cache.md#1-正本は-mysql-と-ebs)、[0020 §10](0020-split.md#10-ベンチ中の書き込みは再起動後も残す) と同じ）:

- `posts` / `users` / `comments` の正本は MySQL。投稿の唯一のコピーをファイルにも memcached にもしない
- 画像ファイルは **EBS 上**（`/home/isucon/private_isu/webapp/public/image/` など）。`tmpfs`、`/dev/shm`、`/tmp` にしない
- ベンチ中に書いた画像は再起動後も読めること。消えたら失格に近い

手順（読む側だけ先に。書く側と同時に入れない）:

```bash
# 1. 置き場。所有者は isucon のまま、nginx（www-data）が読める権限
sudo mkdir -p /home/isucon/private_isu/webapp/public/image
sudo chown isucon:www-data /home/isucon/private_isu/webapp/public/image
sudo chmod 775 /home/isucon/private_isu/webapp/public/image
sudo chmod o+x /home/isucon /home/isucon/private_isu /home/isucon/private_isu/webapp
sudo chmod -R a+rX /home/isucon/private_isu/webapp/public
ls -ld /home/isucon/private_isu/webapp/public/image
```

```bash
# 2. 変える前のバックアップ
cp -a /home/isucon/private_isu/webapp/python ~/kit-backup/app.before-image 2>/dev/null || \
  mkdir -p ~/kit-backup && cp -a /home/isucon/private_isu/webapp/python ~/kit-backup/app.before-image
```

方針（コピペ用の完成品は置かない。自分の読む側 1 箇所に当てる）:

1. 書き込み経路はまず変えない。DB への書き込みは残す（正本）。ファイルへの保存は読み替えが通ってから足す
2. 既存 BLOB をファイルへ移す移行スクリプトを 1 回だけ回す（`id` → `public/image/<id>.<ext>`）。ベンチ前に 1 回。ベンチ中は回さない
3. 読む側はファイル優先・DB フォールバックにする（ファイルが無ければ従来通り `imgdata` を返す）。いきなり DB 読みを消さない
4. nginx はファイル配信に寄せる（対象の `location /image/` に `try_files`。未移行分は `@app` へフォールバック。[0070 §3](0070-static-cache.md#3-nginx-でファイルを配信する形に寄せる条件付きの土台) と同じ）:

```nginx
# /image をファイル化済みのときだけ
location /image/ {
  root /home/isucon/private_isu/webapp/public/;
  try_files $uri @app;
}
location @app {
  proxy_set_header Host $host;
  proxy_pass http://127.0.0.1:8080;
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/image/1
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
sudo journalctl -u isu-python.service -n 40 --no-pager
```

判定（bench-prep → 公式ベンチ 1 本）:

```bash
sudo slp my --file /var/log/mysql/mysql-slow.log | grep -i -E 'imgdata|posts' | head -10
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
echo "$(date -Iseconds)  score=  pass=  fail=  note=image-file" >> ~/bench-notes/scores.txt
```

- slp の `imgdata` 系の Count が落ち、alp の `/image` Sum が落ちたら当たり
- 再起動試験: `sudo reboot` 後に画像と投稿が残ること（[0040 §1](0040-cache.md#1-正本は-mysql-と-ebs) と同じ）。消えたらファイルが正本になっているか `tmpfs` 置き
- ファイル化が通ったら [0070](0070-static-cache.md) の `expires` + `304` を重ねられる。ただし同時に入れて比べない。2. の点数を先に取る

戻す:

```bash
# nginx の try_files を外して reload。アプリはバックアップから戻して restart
cp -a ~/kit-backup/app.before-image/. /home/isucon/private_isu/webapp/python/
sudo systemctl restart isu-python.service
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/image/1
```

### 観察すること

- `imgdata` を読む slp の行と `/image` の alp Sum が両方落ちた（片方だけでは終わらない）
- 再起動後に画像が残る（正本が MySQL / EBS にある）
- `fail` が 0。更新漏れ（新投稿の画像が出ない）は書き込み経路の保存漏れ。読み側の TTL を疑わない

## 3. SQL を速くする（pt-query-digest → 1 本ずつ）

ルールは [0030 §4](0030-measure.md#4-explain-からクエリを直す) と同じ。**1 本ずつ**。`examined >> sent` を優先。LLM に投げるなら [prompts/index-from-slp.md](../010_common/prompts/index-from-slp.md)。

```bash
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 5 2>/dev/null | head -60
sudo slp my --file /var/log/mysql/mysql-slow.log | head -20
```

`pt-query-digest` の読み方（見るのは先頭の数行だけ）:

- `Rank` / `Response time` の 1 位が今回の犯人。`Calls`（回数）と `R/Call`（1 回あたり）で「呼ばれすぎか 1 回が重いか」を分ける
- `ADMIN PREPARE` が 1 位なら 3. ではなく 5. へ（プリペアドの往復が犯人。インデックスではない）
- `Rows examine` と `Rows sent` の比が桁違いならインデックス不足。下の EXPLAIN へ

1 本の型（選んだクエリに置き換える）:

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

列指定の分離（一覧に BLOB を読まない）:

- `SELECT * FROM posts` は `imgdata`（MEDIUMBLOB）を含む。一覧用のクエリは画像以外の列だけにする（[0030 §5](0030-measure.md#5-py-spy-を取ってボトルネックを直す) と同じ）。画像が必要な箇所だけ別クエリか 2. のファイル読みにする
- 直し方は自分の 1 クエリだけ。全部の `SELECT *` を同時に直さない

N+1 のまとめ（インデックスで比が数倍以内になっても Count が大きいままなら）:

- コメント・ユーザー参照をループで 1 件ずつ読んでいたら、`IN (...)` でまとめて取る 1 箇所に変える。キャッシュ（[0040 §4](0040-cache.md#4-アプリ層-memcached-に-1-本入れてヒット率で評価する)）とは別々にやる。同時に入れて比べない

```bash
echo "$(date -Iseconds)  score=  pass=  fail=  note=index comments(post_id,created_at)" >> ~/bench-notes/scores.txt
```

効かなければ `DROP INDEX idx_comments_post_created ON comments`。次の 1 本へ。再起動で初期化 SQL が DB を作り直す回は、終盤にもう一度 `SHOW INDEX`（[0030-ops.md](../010_common/0030-ops.md#終盤チェック1700-以降)）。

### 観察すること

- 貼る前後で同じ `EXPLAIN` の `key` が変わり、examined が落ちた
- slp の該当行と alp の Sum が両方動いた（ヒット率や点数だけでは終わらない）
- 1 本で終わらせてから次を考えた（「よくあるインデックス集」はやらない）

## 4. ファイルディスクリプタの上限は確認だけ（新規に上げない）

このドリルでは FD 上限を上げない。[0080 §6](0080-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) で済ませたか確認するだけ。

```bash
systemctl show isu-python.service -p LimitNOFILE
PID="$(systemctl show -p MainPID --value isu-python.service)"
grep -i 'open files' /proc/$PID/limits
sudo journalctl -u isu-python.service --grep='Too many open files' --no-pager | head -5
```

- 枯渇ログが無ければ「足りている」。0090 の作業（worker 増なし、画像ファイル化、SQL 1 本、プリペアド無効化）で新たに FD が詰まることはほぼ無い
- 枯渇ログが出たらアプリ変更の前に [0080 §6](0080-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) に戻る。0090 と同時進行にしない
- gunicorn の worker 数と DB プール数（[0050 §6](0050-http-client.md#6-同一ホストへの上限を確認する絞りすぎない)、[0060 §6](0060-timeout.md#6-同一ホストへの上限を確認する絞りすぎない)）を上げた回は、FD も見るだけ（`ss -s`、`lsof` 件数）。上げるのは枯渇が決まってから

### 観察すること

- `LimitNOFILE` の実効値と枯渇ログの有無を言える
- 上げていない（確認だけ）。上げたくなったら 0080 に戻ると決めた

## 5. `ADMIN PREPARE` が最上位ならプリペアドを使わない形に変える

`pt-query-digest` の先頭が `ADMIN PREPARE ...` のとき、犯人はインデックスではなく往復回数。サーバ側プリペアドステートメントは 1 クエリに 2 往復（PREPARE + EXECUTE）かかる。件数が多いとその倍増分が支配的になる。検知したら自分の 1 箇所をクライアント側実行に変える。

検知（1. の続き。`--limit` を小さくして先頭だけ見る）:

```bash
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 3 2>/dev/null | head -60
# Rank 1 の Query ID 行に ADMIN PREPARE と出るか。例:
# # Query 1: 12.3 QPS, ...  ADMIN PREPARE ...
sudo slp my --file /var/log/mysql/mysql-slow.log | head -10
# slp 側は通常クエリが並ぶ。pt-query-digest の ADMIN 行と突き合わせて件数の倍増を見る
```

- `ADMIN PREPARE` が無ければこの節は飛ばす。5. の変更は入れない（「対応済み」と書いて 6. へ）
- キャプチャは `long_query_time=0` の短い 1 本分（1. と同じ）。`=0` のまま放置しない

場所の特定（ドライバ・ORM で書き方が違う。自分の `grep` 結果に当てる）:

```bash
grep -rn 'prepare\|Prepare\|PREPARE\|prepared=True\|cursor.*prepared\|server_side\|use_server_side\|prepare_threshold' \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
grep -rn 'SQLAlchemy\|asyncmy\|aiomysql\|pymysql\|MySQLdb\|mysqlclient\|connector' \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
```

方針（コピペ用の完成品は置かない。自分の 1 箇所だけ）:

1. 対象はホットな 1 経路だけ（1. で指したクエリを発行する箇所）。全体のフラグをひっくり返さない
2. サーバ側プリペア（`prepare=True` 相当、サーバに実行計画を持たせる方式）をやめ、クライアント側で値を埋める通常実行にする（ドライバの既定の `execute`）。SQL 文字列を自前で組み立てない（エスケープはドライバに任せる。`%s` / `%(name)s` の渡し方は変えない）
3. ORM の自動プリペアなら設定フラグ 1 つだけ変える。クエリ書き換えと同時に入れない

```bash
# 変える前のバックアップ
cp -a /home/isucon/private_isu/webapp/python ~/kit-backup/app.before-prepare 2>/dev/null || \
  mkdir -p ~/kit-backup && cp -a /home/isucon/private_isu/webapp/python ~/kit-backup/app.before-prepare
```

```bash
sudo systemctl restart isu-python.service
systemctl is-active isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
sudo journalctl -u isu-python.service -n 40 --no-pager
```

検証（同じキャプチャを取り直す）:

```bash
sudo mysql -e "SET GLOBAL long_query_time = 0"
# bench-prep（[0030 §1](0030-measure.md#1-ベースライン) と同じ mv + reopen / flush-logs）してから公式ベンチ 1 本
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 3 2>/dev/null | head -40
sudo mysql -e "SET GLOBAL long_query_time = 1"
echo "$(date -Iseconds)  score=  pass=  fail=  note=no-prepare" >> ~/bench-notes/scores.txt
```

- `ADMIN PREPARE` の行が消えた（または Rank 外に落ちた）→ 当たり。そのまま 6. の判定へ
- 残る → 変えた箇所がホットな経路ではない。戻して別の 1 箇所へ（全体フラグに広げない）

戻す:

```bash
cp -a ~/kit-backup/app.before-prepare/. /home/isucon/private_isu/webapp/python/
sudo systemctl restart isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- 変える前後で `pt-query-digest` 先頭行の `ADMIN PREPARE` の有無を言える
- 変えたのは 1 箇所だけ（全体フラグ＋クエリ書き換えの同時入れをしていない）
- エスケープを自前で組み立てていない（`fail` や文字化けが出たらここを疑う）

## 6. 公式ベンチで判定する

2.・3.・5. のどれか 1 手だけ残し、他は戻した状態で公式ベンチ 1 本。bench-prep は [0030-measure.md](0030-measure.md#1-ベースライン) と同じ `mv` + `reopen` / `flush-logs`。

```bash
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysqladmin flush-logs 2>/dev/null || sudo mysql -e 'FLUSH SLOW LOGS'
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
sudo slp my --file /var/log/mysql/mysql-slow.log | head -10
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
echo "$(date -Iseconds)  score=  pass=  fail=  note=app-image-file" >> ~/bench-notes/scores.txt
```

判定は slp（または pt-query-digest）＋ alp ＋点数。点だけ見ない。効かなければ残さない。

### 観察すること

- 狙った slp / pt-query-digest の行が落ち、alp の Sum が落ち、点数が動いた（3 点セット）
- `fail` が 0。画像の更新漏れ・他人表示は 2. の保存漏れ、文字化け・500 は 5. のエスケープ崩れを疑う
- `long_query_time` を 1 に戻した（`df -h /` で余裕がある）

## 7. トラブルシュート

### 画像が出ない / 403 / 新投稿の画像が出ない

- 403 → 権限。`isucon:www-data`、`a+rX`、`/home/isucon` からの `o+x` の順（[0020 §静的](0020-split.md#静的ファイルと画像) と同じ）
- 旧画像が出て新画像が出ない → 書き込み経路の保存漏れ。読み側の TTL やキャッシュを疑わない
- 再起動で消える → `tmpfs` / `/dev/shm` / `/tmp` 置きか、ファイルだけが正本。EBS + DB 残しに戻す

### インデックスが効かない

- 同時貼りしていないか（1 本ずつ）。`SHOW INDEX` で名前を確認してから再 `EXPLAIN`
- `examined` は落ちたが Count が大きいまま → N+1（呼び出し回数）の仕事。`IN` まとめへ
- `posts` の一覧がまだ重い → `SELECT *` の `imgdata` 混入を疑う。列指定の分離に戻る

### プリペアドを外したら文字化け / 500 / fail

- SQL 文字列の自前結合が一番多い。値埋めはドライバに任せ、プレースホルダの渡し方を変えない
- ORM の全体フラグを変えたら 1 経路に戻す。ホットな 1 箇所だけに狭める
- `ADMIN PREPARE` が消えない → 変えた箇所がホットではない。バックアップから戻して別の 1 箇所へ

### FD 不足と取り違える

- `Too many open files` が出たら 0090 の作業を止めて [0080 §6](0080-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) へ。アプリ変更と FD 上げを同時に入れない
- worker 増と DB 接続増は対で見る（[0080 §5](0080-infra-params.md#5-gunicorn-の-worker-プロセス数少なすぎも多すぎも遅い)）。`too many connections` は worker を戻す
