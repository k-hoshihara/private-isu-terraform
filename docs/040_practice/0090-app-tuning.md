# 0090 アプリの処理方式を変えて差を見る

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）はローカル環境の private-isu-terraform リポジトリにて実行します。

アプリの中身を 1 箇所ずつ変えて、点数が動くか数字で確認します。

- アップロード画像を MySQL のバイナリ（`posts.imgdata`）から静的ファイルにします
- SQL を速くします（`pt-query-digest` の読み方、インデックス 1 本、列指定の分離、N+1 のまとめ）
- ファイルディスクリプタの上限は [0080-infra-params.md §6](0080-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) で済ませます（ここでは新規に上げません）
- `pt-query-digest` で `ADMIN PREPARE` が最上位に来ていたら、サーバ側プリペアドステートメントを使わない形に変えます

ゴールは「変えた 1 箇所の前後で、`slp` / `pt-query-digest` の該当行と alp の Sum、公式ベンチの点数」を言えることです。「直したつもり」では終わりません。

[0080-infra-params.md](0080-infra-params.md) の続きです。Python（`isu-python.service`）で進めます。

順番: 対象を決める（1.）→ 画像ファイル化（2.）→ SQL（3.）→ FD 確認だけ（4.）→ `ADMIN PREPARE`（5.）→ ベンチ判定（6.）。1 手ずつ進めます。`fail` が 0 でない点数は比べません。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  note=app-baseline" >> ~/bench-notes/scores.txt
```

## 1. 対象を 1 本に決める（alp + slp + pt-query-digest）

変える前に、3 つの道具で同じ犯人を特定します。特定しないままコードを触りません。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
```

```bash
# s3 で打つ。短い全件キャプチャ（ベンチ 1 本だけ。取り終わったら long_query_time=1 に戻す）
sudo mysql -e "SET GLOBAL long_query_time = 0"
# SET GLOBAL は既存の接続には効きません。新規接続から効くので、s1 と s2 で
# sudo systemctl restart isu-python.service してから測ります
```

```bash
# s1 で打つ。公式ベンチ 1 本（[0030-measure.md](0030-measure.md) の bench-prep を先に）
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
```

```bash
# s3 で打つ
sudo slp my --file /var/log/mysql/mysql-slow.log | head -30
sudo mysql -e "SET GLOBAL long_query_time = 1"
df -h /
```

```bash
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 10 2>/dev/null | head -80
```

読み方:

- alp の Sum 上位が `/image` で、slp でも `SELECT ... imgdata` が太い → §2（画像ファイル化）
- slp で `examined >> sent` のクエリがある → §3（インデックス 1 本）
- `pt-query-digest` の先頭（Rank 1）が `ADMIN PREPARE ...` → §5（プリペアド無効化）。`ADMIN` 行が無ければ §5 は飛ばします
- どれも太くない → アプリの仕事ではありません。土台（[0080-infra-params.md](0080-infra-params.md)）かキャッシュ（[0040-cache.md](0040-cache.md)）に戻ります

コードの場所は `grep` で探します。関数名は決め打ちしません:

```bash
grep -rn 'def .*image\|send_file\|make_response\|Response\|imgdata' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
grep -rn 'SELECT.*FROM posts\|SELECT.*FROM users\|SELECT.*FROM comments' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
```

### 観察すること

- 自分の 1 手が「2. / 3. / 5.」のどれかを言えます（2 つ同時に入れません）
- alp の Sum 上位と slp / pt-query-digest の先頭行が同じ犯人を指しています
- `long_query_time` を 1 に戻しました（`df -h /` でディスクに余裕があります）

## 2. アップロード画像を静的ファイルにする（投稿の本体データは DB に残す）

`/image` の素体は MySQL の `posts.imgdata`（MEDIUMBLOB）です。毎回 BLOB を読むと重いです。読む側をファイルに寄せます。ただし投稿の本体データは MySQL のまま残します。ファイルだけにしません。

線引き（[0040-cache.md §1](0040-cache.md#1-投稿の本体データは-mysql-と-ebs)、[0020-split-3.md §10](0020-split-3.md#10-ベンチ中の書き込みは再起動後も残す) と同じです）:

- 投稿の本体データ（`posts` / `users` / `comments`）は MySQL にあります。投稿の唯一のコピーをファイルにも memcached にもしません
- 画像ファイルは **EBS 上**（`/home/isucon/private_isu/webapp/public/image/` など）に置きます。`tmpfs`、`/dev/shm`、`/tmp` にしません
- ベンチ中に書いた画像は再起動後も読めることです。消えたら失格に近いです

手順（読む側だけ先にします。書く側と同時に入れません）。移行先のファイルは nginx が配信する台（分割後は s1）に置きます。アプリの読む側は `/image` を捌く台（分割後は heavy の s2）に入れます:

```bash
# s1 で打つ。1. 置き場。所有者は isucon のまま、nginx（www-data）が読める権限
sudo mkdir -p /home/isucon/private_isu/webapp/public/image
sudo chown isucon:www-data /home/isucon/private_isu/webapp/public/image
sudo chmod 775 /home/isucon/private_isu/webapp/public/image
sudo chmod o+x /home/isucon /home/isucon/private_isu /home/isucon/private_isu/webapp
sudo chmod -R a+rX /home/isucon/private_isu/webapp/public
ls -ld /home/isucon/private_isu/webapp/public/image
```

コードは作業機で変えます。編集前の状態は git が持っているので、サーバー上でバックアップは取りません:

```bash
# 作業機。対象ファイルは自分の grep 結果に置き換える
git pull --ff-only
# 重い処理（`/image` は heavy）は backup/s2 の <対象>.py を編集する（読む側 1 箇所）
git add -A && git commit -m "tune: <対象> をファイル読みにする" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s2/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
```

方針（コピペ用の完成品は置きません。自分の読む側 1 箇所に当てます）:

1. 書き込み経路はまず変えません。DB への書き込みは残します（投稿の本体データのためです）。ファイルへの保存は読み替えが通ってから足します
2. 既存 BLOB をファイルへ移す移行スクリプトを用意し、ベンチ前に 1 回だけ回します（`id` → `public/image/<id>.<ext>`）。ベンチ中は回しません
   - 移行した画像ファイルは git に入れません（GB 級になるため）
   - `/home/isucon/env.sh` は `export` が付いていないので、スクリプト内で直接読み込みます
   - root 所有のファイルができるので、`chown -R isucon:www-data` と `chmod -R a+rX` を掛け直します
   - 確認は 3 点: `ls public/image/ | wc -l` と `SELECT COUNT(*) FROM posts` が一致、`curl -fsS http://127.0.0.1/image/<id>.jpg` が 200（拡張子なしは 404 になります）
3. 読む側はファイル優先・DB フォールバックにします（ファイルが無ければ従来通り `imgdata` を返します）。いきなり DB 読みを消しません。未移行の id はアプリが 500 を返す構成のままです（素体の挙動で、この変更の回帰ではありません）
4. nginx はファイル配信に寄せます。分割後の素体は既に `location /image/` に `try_files $uri @app;` があります。先に `sudo nginx -T` で確認し、あるなら足しません。無いときだけ対象の `location /image/` に `try_files` を足します（未移行分は `@app` へフォールバック。[0070-static-cache.md §3](0070-static-cache.md#3-nginx-でファイルを配信する形に寄せる条件付きの土台) と同じです）:

`backup/s1/etc/nginx/sites-enabled/isucon.conf`（`try_files` が無い場合のみ）:

```nginx
location /image/ {
  root /home/isucon/private_isu/webapp/public/;
  try_files $uri @app;
}

location @app {
  proxy_set_header Host $host;
  proxy_pass http://heavy;
}
```

`backup/s2/home/private_isu/webapp/python/app.py` の `get_image` の頭:

```python
IMAGE_DIR = pathlib.Path(__file__).resolve().parent.parent / "public" / "image"
IMAGE_MIME = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif"}


@app.route("/image/<id>.<ext>")
def get_image(id, ext):
    if not id:
        return ""
    id = int(id)
    if id == 0:
        return ""
    if ext in IMAGE_MIME:
        fp = IMAGE_DIR / f"{id}.{ext}"
        if fp.is_file():
            return flask.Response(fp.read_bytes(), mimetype=IMAGE_MIME[ext])

    cursor = db().cursor()  # ファイルが無い場合は従来通り DB へ
```

```bash
# 作業機
git pull --ff-only
# 上の内容を配置する
git add -A && git commit -m "tune: /image をファイル読みにする" && git push
```

反映します。s2 でアプリ、s1 で nginx（`try_files` を足したときだけ）:

```bash
# s2 で打つ
git pull --ff-only
sudo cp -a backup/s2/home/private_isu/webapp/python/app.py /home/isucon/private_isu/webapp/python/app.py
sudo systemctl restart isu-python.service
sudo journalctl -u isu-python.service -n 40 --no-pager
```

```bash
# s1 で打つ
git pull --ff-only
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/image/1.jpg
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

判定（bench-prep → 公式ベンチ 1 本。alp は s1、slp は s3 で打ちます）:

```bash
# s3 で打つ
sudo slp my --file /var/log/mysql/mysql-slow.log | grep -i -E 'imgdata|posts' | head -10
```

```bash
# s1 で打つ
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
echo "$(date -Iseconds)  score=  pass=  fail=  note=image-file" >> ~/bench-notes/scores.txt
```

- slp の `SELECT * FROM posts WHERE id` が画像パス分の量から落ちれば成功です（実測では `/image` 2170 件分の大量読みが消え、`/posts/<id>` 詳細ページ分の少数だけ残りました）。alp の `/image` Sum が落ちたことも確認します（実測: Sum 331 → 151、平均 0.89 → 0.07）
- 再起動試験: `sudo reboot` 後に画像と投稿が残ります（[0040-cache.md §1](0040-cache.md#1-投稿の本体データは-mysql-と-ebs) と同じです）。消えたらファイルが投稿の本体データになっているか、`tmpfs` 置きを疑います
- ファイル化が通ったら [0070-static-cache.md](0070-static-cache.md) の `expires` + `304` を重ねられます。ただし同時に入れて比べません。§2 の点数を先に取ります

戻す（作業機で戻してサーバーに反映します）:

```bash
# 作業機。try_files を外し、アプリを戻す
git pull --ff-only
# backup/s1/etc/nginx/sites-enabled/isucon.conf と <対象>.py を戻す
git add -A && git commit -m "tune: 画像ファイル化を外す" && git push
```

```bash
# サーバーで反映（site conf は s1、アプリは s2 で打つ）
git pull --ff-only
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo nginx -t && sudo systemctl reload nginx
# s2 で打つ
sudo cp -a backup/s2/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1/image/1.jpg
```

### 観察すること

- `imgdata` を読む slp の行と `/image` の alp Sum が両方落ちました（片方だけでは終わりません）
- 再起動後に画像が残ります（投稿の本体データが MySQL / EBS にあります）
- `fail` が 0 です。更新漏れ（新投稿の画像が出ない）は書き込み経路の保存漏れです。読み側の TTL を疑いません

## 3. SQL を速くする（pt-query-digest → 1 本ずつ）

ルールは [0100-sql-tuning.md §1](0100-sql-tuning.md#1-対象クエリを1本に決める) と同じです。**1 本ずつ**進めます。`examined >> sent` を優先します。LLM に投げるなら [prompts/index-from-slp.md](../010_common/prompts/index-from-slp.md) を見ます。

```bash
# s3 で打つ
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 5 2>/dev/null | head -60
sudo slp my --file /var/log/mysql/mysql-slow.log | head -20
```

`pt-query-digest` の読み方（見るのは先頭の数行だけです）:

- `Rank` / `Response time` の 1 位が今回の犯人です。`Calls`（回数）と `R/Call`（1 回あたり）で「呼ばれすぎか 1 回が重いか」を分けます
- `ADMIN PREPARE` が 1 位なら §3 ではなく §5 へ進みます（プリペアドの往復が犯人です。インデックスではありません）
- `Rows examine` と `Rows sent` の比が桁違いならインデックス不足です。下の EXPLAIN へ進みます

1 本の型（選んだクエリに置き換える。インデックスは DB 内の定義なので、ファイルではなくコマンドで流します。s3 で打ちます）:

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

実測: 貼る前 `type: ALL`、`key: NULL`、`rows: 99920`、`Extra: Using where; Using filesort` → 貼った後 `type: ref`、`key: idx_comments_post_created`、`rows: 12`、`Extra: Backward index scan`。ベンチは 3578 → 58063 でした。

| 悪い | 良い |
| --- | --- |
| `type: ALL`、`key: NULL` | `type: ref` / `range`、`key` が今貼った名前 |
| `rows` がテーブル全件、`Extra: Using filesort` | `rows` が LIMIT に近い。filesort が消えることが多い |
| slp の examined が sent の桁違い | 比が数倍以内。残るなら呼び出し回数（N+1） |

列指定の分離（一覧に BLOB を読みません）:

- `SELECT * FROM posts` は `imgdata`（MEDIUMBLOB）を含みます。一覧用のクエリは画像以外の列だけにします（[0110-overall-tuning.md §1](0110-overall-tuning.md#1-py-spy-でボトルネックを取る) と同じです）。画像が必要な箇所だけ別クエリか §2 のファイル読みにします
- 直し方は自分の 1 クエリだけです。全部の `SELECT *` を同時に直しません

N+1 のまとめ（インデックスで比が数倍以内になっても Count が大きいままなら）:

- コメント・ユーザー参照をループで 1 件ずつ読んでいたら、`IN (...)` でまとめて取る 1 箇所に変えます。キャッシュ（[0040-cache.md §4](0040-cache.md#4-アプリ層-memcached-に-1-本入れてヒット率で評価する)）とは別々にやります。同時に入れて比べません

```bash
echo "$(date -Iseconds)  score=  pass=  fail=  note=index comments(post_id,created_at)" >> ~/bench-notes/scores.txt
```

効かなければ `DROP INDEX idx_comments_post_created ON comments` します。次の 1 本へ進みます。再起動で初期化 SQL が DB を作り直す構成では、終盤にもう一度 `SHOW INDEX` で確認します（[0030-ops.md](../010_common/0030-ops.md#終盤チェック1700-以降)）。

### 観察すること

- 貼る前後で同じ `EXPLAIN` の `key` が変わり、examined が落ちました
- slp の該当行と alp の Sum が両方動きました（ヒット率や点数だけでは終わりません）
- 1 本で終わらせてから次を考えました（「よくあるインデックス集」はやりません）

## 4. ファイルディスクリプタの上限は確認だけ（新規に上げない）

このドリルでは FD 上限を上げません。[0080-infra-params.md §6](0080-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) で済ませたか確認するだけです。

```bash
systemctl show isu-python.service -p LimitNOFILE
PID="$(systemctl show -p MainPID --value isu-python.service)"
grep -i 'open files' /proc/$PID/limits
sudo journalctl -u isu-python.service --grep='Too many open files' --no-pager | head -5
```

- 枯渇ログが無ければ「足りている」です。0090 の作業（worker 増なし、画像ファイル化、SQL 1 本、プリペアド無効化）で新たに FD が詰まることはほぼありません
- 枯渇ログが出たらアプリ変更の前に [0080-infra-params.md §6](0080-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) に戻ります。0090 と同時進行にしません
- gunicorn の worker 数と DB プール数（[0050-http-client.md §6](0050-http-client.md#6-同一ホストへの上限を確認する絞りすぎない)、[0060-timeout.md §6](0060-timeout.md#6-同一ホストへの上限を確認する絞りすぎない)）を上げた回は、FD も見るだけです（`ss -s`、`lsof` 件数）。上げるのは枯渇が決まってからです

### 観察すること

- `LimitNOFILE` の実効値と枯渇ログの有無を言えます
- 上げていません（確認だけです）。上げたくなったら [0080-infra-params.md §6](0080-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) に戻ると決めました

## 5. `ADMIN PREPARE` が最上位ならプリペアドを使わない形に変える

`pt-query-digest` の先頭が `ADMIN PREPARE ...` のとき、犯人はインデックスではなく往復回数です。サーバ側プリペアドステートメントは 1 クエリに 2 往復（PREPARE + EXECUTE）かかります。件数が多いとその倍増分が支配的になります。検知したら自分の 1 箇所をクライアント側実行に変えます。

?> 出るかどうかはドライバで決まります。素体の Python（`MySQLdb` / mysqlclient）はクライアント側実行でサーバ `PREPARE` を出しません。出ないのが正常で、この節は飛ばしてよいです。

検知（§1 の続き。`--limit` を小さくして先頭だけ見ます）:

```bash
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 3 2>/dev/null | head -60
# Rank 1 の Query ID 行に ADMIN PREPARE と出るか。例:
# # Query 1: 12.3 QPS, ...  ADMIN PREPARE ...
sudo slp my --file /var/log/mysql/mysql-slow.log | head -10
# slp 側は通常クエリが並ぶ。pt-query-digest の ADMIN 行と突き合わせて件数の倍増を見る
```

- `ADMIN PREPARE` が無ければこの節は飛ばします。§5 の変更は入れません（「対応済み」と書いて §6 へ進みます）
- キャプチャは `long_query_time=0` の短い 1 本分です（§1 と同じです）。`=0` のまま放置しません

場所の特定（ドライバ・ORM で書き方が違う。自分の `grep` 結果に当てる）:

```bash
grep -rn 'prepare\|Prepare\|PREPARE\|prepared=True\|cursor.*prepared\|server_side\|use_server_side\|prepare_threshold' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
grep -rn 'SQLAlchemy\|asyncmy\|aiomysql\|pymysql\|MySQLdb\|mysqlclient\|connector' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
```

方針（コピペ用の完成品は置きません。自分の 1 箇所だけです）:

1. 対象はホットな 1 経路だけです（§1 で指したクエリを発行する箇所です）。全体のフラグをひっくり返しません
2. サーバ側プリペア（`prepare=True` 相当、サーバに実行計画を持たせる方式）をやめ、クライアント側で値を埋める通常実行にします（ドライバの既定の `execute` です）。SQL 文字列を自前で組み立てません（エスケープはドライバに任せます。`%s` / `%(name)s` の渡し方は変えません）
3. ORM の自動プリペアなら設定フラグ 1 つだけ変えます。クエリ書き換えと同時に入れません

`backup/s2/home/private_isu/webapp/python/<対象>.py`（重い処理なら s2、軽い処理なら s1）の該当箇所を編集します:

```python
# 例: prepared=True を外してクライアント側実行に戻す
# 変更前
cursor.execute(query, params, prepared=True)
# 変更後（ドライバの既定の execute。エスケープはドライバに任せる）
cursor.execute(query, params)
```

```bash
# 作業機
git pull --ff-only
# 上の内容を配置する
git add -A && git commit -m "tune: プリペアドを外す" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s2/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
systemctl is-active isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
sudo journalctl -u isu-python.service -n 40 --no-pager
```

検証（同じキャプチャを取り直す）:

```bash
sudo mysql -e "SET GLOBAL long_query_time = 0"
# bench-prep（[0030-measure.md](0030-measure.md) と同じ mv + reopen / flush-logs。slow 側は s3）してから公式ベンチ 1 本
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 3 2>/dev/null | head -40
sudo mysql -e "SET GLOBAL long_query_time = 1"
echo "$(date -Iseconds)  score=  pass=  fail=  note=no-prepare" >> ~/bench-notes/scores.txt
```

- `ADMIN PREPARE` の行が消えた（または Rank 外に落ちた）→ 成功です。そのまま §6 の判定へ進みます
- 残る → 変えた箇所がホットな経路ではありません。戻して別の 1 箇所へ進みます（全体フラグに広げません）

戻す（作業機で戻してサーバーに反映します）:

```bash
# 作業機
git pull --ff-only
# 変えた台の backup/s2（または s1）の <対象>.py を戻す
git add -A && git commit -m "tune: プリペアド外しを戻す" && git push
```

```bash
# サーバーで反映
git pull --ff-only
sudo cp -a backup/s2/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- 変える前後で `pt-query-digest` 先頭行の `ADMIN PREPARE` の有無を言えます
- 変えたのは 1 箇所だけです（全体フラグとクエリ書き換えを同時に入れていません）
- エスケープを自前で組み立てていません（`fail` や文字化けが出たらここを疑います）

## 6. 公式ベンチで判定する

§2・§3・§5 のどれか 1 手だけ残し、他は戻した状態にして公式ベンチを 1 本回します。bench-prep は [0030-measure.md](0030-measure.md) と同じです（s1 で `mv` + `reopen`、s3 で `flush-logs`）。

```bash
# s1 で打つ
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
echo "$(date -Iseconds)  score=  pass=  fail=  note=app-image-file" >> ~/bench-notes/scores.txt
```

```bash
# s3 で打つ
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysqladmin flush-logs 2>/dev/null || sudo mysql -e 'FLUSH SLOW LOGS'
sudo slp my --file /var/log/mysql/mysql-slow.log | head -10
```

残す変更は台ごとの `backup/s1`・`s2`・`s3` に積みます（[0030-measure.md](0030-measure.md) の「残す変更は backup/s1・s2・s3 に積む」の流儀）。

判定は slp（または pt-query-digest）＋ alp ＋点数です。点だけ見ません。効かなければ残しません。

### 観察すること

- 狙った slp / pt-query-digest の行が落ち、alp の Sum が落ち、点数が動きました（3 点セットです）
- `fail` が 0 です。画像の更新漏れ・他人表示は §2 の保存漏れ、文字化け・500 は §5 のエスケープ崩れを疑います
- `long_query_time` を 1 に戻しました（`df -h /` で余裕があります）

0090 まで終わったら [0100-sql-tuning.md](0100-sql-tuning.md) に進みます。残した変更は台ごとの `backup/s1`・`s2`・`s3` に積み、feature ブランチでやり取りします（流儀は [0020-split-1.md](0020-split-1.md) と同じです）。

## 7. トラブルシュート

### 画像が出ない / 403 / 新投稿の画像が出ない

- 403 → 権限です。`isucon:www-data`、`a+rX`、`/home/isucon` からの `o+x` の順です（[0020-split-2.md §静的ファイルと画像](0020-split-2.md#静的ファイルと画像) と同じです）
- 旧画像が出て新画像が出ない → 書き込み経路の保存漏れです。読み側の TTL やキャッシュを疑いません
- 再起動で消える → `tmpfs` / `/dev/shm` / `/tmp` 置きか、ファイルだけが投稿の本体データになっています。EBS + DB 残しに戻します

### インデックスが効かない

- 同時貼りしていませんか（1 本ずつです）。`SHOW INDEX` で名前を確認してから再 `EXPLAIN` します
- `examined` は落ちたが Count が大きいまま → N+1（呼び出し回数）の仕事です。`IN` まとめへ進みます
- `posts` の一覧がまだ重い → `SELECT *` の `imgdata` 混入を疑います。列指定の分離に戻ります

### プリペアドを外したら文字化け / 500 / fail

- SQL 文字列の自前結合が一番多いです。値埋めはドライバに任せ、プレースホルダの渡し方を変えません
- ORM の全体フラグを変えたら 1 経路に戻します。ホットな 1 箇所だけに狭めます
- `ADMIN PREPARE` が消えない → 変えた箇所がホットではありません。git で戻して別の 1 箇所へ進みます

### FD 不足と取り違える

- `Too many open files` が出たら 0090 の作業を止めて [0080-infra-params.md §6](0080-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) へ進みます。アプリ変更と FD 上げを同時に入れません
- worker 増と DB 接続増は対で見ます（[0080-infra-params.md §5](0080-infra-params.md#5-gunicorn-の-worker-プロセス数少なすぎも多すぎも遅い)）。`too many connections` は worker を戻します
