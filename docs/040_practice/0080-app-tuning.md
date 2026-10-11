# 0080 アプリの処理方式を変えて差を見る

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）は、手元の private-isu-terraform リポジトリで実行します。

- アプリの中身を、1 箇所ずつ変えます。
- 点数が動くか、数字で確認します。

- アップロード画像を MySQL のバイナリ（`posts.imgdata`）から静的ファイルにします
- SQL の索引と N+1 の一括化は [0015-sql-tuning.md](0015-sql-tuning.md) で済ませています（複数台に分けたあとに残っているかの確認は [0110-split-4.md](0110-split-4.md) でやります）
- ファイルディスクリプタ（同時に開けるファイルや接続の数。FD と略します）の上限は [0070-infra-params.md §6](0070-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) で済ませます（ここでは新規に上げません）
- `pt-query-digest` で `ADMIN PREPARE` が最上位に来ていたら、サーバ側プリペアドステートメント（SQL の形を先に登録しておく方式）を使わない形に変えます

- ゴールは、変えた 1 箇所の前後を言えることです。
- 見るのは、`slp` / `pt-query-digest` の該当行と alp の Sum（合計時間）、公式ベンチの点数です。
- 「直したつもり」では、終わりません。

- [0070-infra-params.md](0070-infra-params.md) の続きです。
- Python（`isu-python.service`）で進めます。

- 順番: 対象を決める（1.）→ 画像ファイル化（2.）→ FD 確認だけ（3.）→ `ADMIN PREPARE`（4.）→ ベンチ判定（5.）。
- 1 つずつ進めます。
- `fail`（エラーの件数）が 0 でない点数は、比べません。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  note=app-baseline" >> ~/bench-notes/scores.txt
```

<a id="1-対象を-1-本に決めるalp--slp--pt-query-digest"></a>

## 1. 対象を 1 本に決める（alp + slp + pt-query-digest）

- 変える前に、3 つの道具で同じ原因を特定します。
- 特定しないまま、コードを触りません。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
```

?> `alp ltsv` の **`-m` は、グループ分けの指定です。除外フィルタではありません**。一致しない URI は、それぞれ元の URI のまま集計されます。`/posts?max_created_at=` は、クエリ文字列が既定で除かれるため `GET /posts` と表示されます。`/posts/[0-9]+` とは、別のグループです。ページ送りだけを見たいときは、`-f` で絞ります。

```bash
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse \
  -f 'Method == "GET" and Uri matches "^/posts([?].*)?$"' | head -12
# クエリの種類も見る場合。値ごとの細分化は避けます
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse \
  -f 'Method == "GET" and Uri matches "^/posts([?].*)?$"' -q --qs-ignore-values | head -12
```

- `GET /posts` だけが表示されます。`/posts/1` などの詳細ページが混ざらないことを確認します。

```bash
# 1 台で打つ。短い全件キャプチャ（ベンチ 1 本だけ。取り終わったら long_query_time=1 に戻す）
sudo mysql -e "SET GLOBAL long_query_time = 0"
# SET GLOBAL は既存の接続には効きません。新規接続から効くので、
# sudo systemctl restart isu-python.service してから測ります
```

```bash
# 1 台で打つ。公式ベンチ 1 本（[0020-measure.md](0020-measure.md) の bench-prep を先に）
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
```

実測サンプル（§1 / `note=app-baseline-slp-capture`）: `{"pass":true,"score":40129,"success":35566,"fail":0,"messages":[]}`（全件キャプチャ込みの 1 本。[§6](#6-出力例) の表ではこの回を比較の原点にしています）。

```bash
# 1 台で打つ
# 収集が終わったら、解析より先に戻す
sudo mysql -e "SET GLOBAL long_query_time = 1"
sudo slp my --file /var/log/mysql/mysql-slow.log \
  --sort sum-query-time --reverse --output standard
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 5 | head -60
df -h /
```

?> `slp my` の合計時間のソートキーは、**`sum-query-time`** です（`alp` の `sum` とは違います）。既定は件数の昇順です。`head` だけでは、時間の上位を選べません。キーは `slp my --help` で確認します。解析エラーの stderr は、隠しません。

?> 全件ログは、大きくなります。今回の全件ログは約 700MB・386 万行でした。複数回の記録が混ざったサイズです。ベンチ 1 回分とは言えません。収集後は `long_query_time` を 1 に戻します。`sudo systemctl restart isu-python.service` で、アプリも再起動します。容量の回復は [0100-overall-tuning.md のスローログ対処](0100-overall-tuning.md#ディスクが埋まるスローログ) を使います。

```bash
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 10 2>/dev/null | head -80
```

読み方:

- alp の Sum 上位が `/image` で、slp でも `SELECT ... imgdata` が太い → §2（画像ファイル化）
- slp で `examined`（読んだ行数）が `sent`（返した行数）より桁違いに多い問い合わせがある → [0015-sql-tuning.md](0015-sql-tuning.md)（インデックス）に戻ります
- `pt-query-digest` の先頭（Rank 1）が `ADMIN PREPARE ...` → §4（プリペアド無効化）。`ADMIN` 行が無ければ §4 は飛ばします
- どれも太くない → アプリの仕事ではありません。土台（[0070-infra-params.md](0070-infra-params.md)）かキャッシュ（[0030-cache.md](0030-cache.md)）に戻ります

- コードの場所は、`grep` で探します。関数名は決め打ちしません。

```bash
grep -rn 'def .*image\|send_file\|make_response\|Response\|imgdata' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
grep -rn 'SELECT.*FROM posts\|SELECT.*FROM users\|SELECT.*FROM comments' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
```

### 観察すること

- 自分の 1 つが「2. / 4.」のどれかを言えます（2 つ同時に入れません）
- alp の Sum 上位と slp / pt-query-digest の先頭行が同じ原因を指しています
- `long_query_time` を 1 に戻しました（`df -h /` でディスクに余裕があります）

## 2. アップロード画像を静的ファイルにする（投稿の本体データは DB に残す）

- `/image` の素体（配布された元の状態）は、MySQL の `posts.imgdata`（MEDIUMBLOB＝画像などの大きなデータの列）です。
- 毎回この大きなデータを読むと、重いです。読む側をファイルに寄せます。
- ただし、投稿の本体データは MySQL のまま残します。ファイルだけにしません。

- 線引きは、次のとおりです（[0030-cache.md §1](0030-cache.md#1-投稿の本体データは-mysql-と-ebs)、[0110-split-3.md §10](0110-split-3.md#10-ベンチ中の書き込みは再起動後も残す) と同じです）。

- 投稿の本体データ（`posts` / `users` / `comments`）は MySQL にあります。投稿の唯一のコピーをファイルにも memcached にもしません
- 画像ファイルは **EBS 上**（`/home/isucon/private_isu/webapp/public/image/` など）に置きます。`tmpfs`、`/dev/shm`、`/tmp` にしません（再起動で消えます）
- ベンチ中に書いた画像は再起動後も読めることです。消えたら失格に近いです

- 手順は、読む側だけ先にします。書く側と同時に入れません。
- 画像ファイルは、nginx が配信する `public/image/` に置きます。
- アプリの読む側を変えます。

```bash
# 1 台で打つ。1. 置き場。所有者は isucon のまま、nginx（www-data）が読める権限
sudo mkdir -p /home/isucon/private_isu/webapp/public/image
sudo chown isucon:www-data /home/isucon/private_isu/webapp/public/image
sudo chmod 775 /home/isucon/private_isu/webapp/public/image
sudo chmod o+x /home/isucon /home/isucon/private_isu /home/isucon/private_isu/webapp
sudo chmod -R a+rX /home/isucon/private_isu/webapp/public
ls -ld /home/isucon/private_isu/webapp/public/image
```

- コードは、作業機で変えます。
- 編集前の状態は、git が持っています。サーバー上では、バックアップを取りません。

```bash
# 作業機。対象ファイルは自分の grep 結果に置き換える
git pull --ff-only
# backup/s1 の <対象>.py を編集する（読む側 1 箇所）
git add -A && git commit -m "tune: <対象> をファイル読みにする" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s1/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
```

- 方針は、次のとおりです（コピペ用の完成品は置きません。自分の読む側 1 箇所に当てます）。

1. 書き込み経路はまず変えません。DB への書き込みは残します（投稿の本体データのためです）。ファイルへの保存は読み替えが通ってから足します
2. 既存の画像データをファイルへ移す移行スクリプトを用意し、ベンチ前に 1 回だけ回します（`id` → `public/image/<id>.<ext>`）。ベンチ中は回しません
   - 移行した画像ファイルは git に入れません（GB 級になるため）
   - `/home/isucon/env.sh` は `export` が付いていないので、スクリプト内で直接読み込みます
   - root 所有のファイルができるので、`chown -R isucon:www-data` と `chmod -R a+rX` を掛け直します
   - 確認は 3 点: `ls public/image/ | wc -l` と `SELECT COUNT(*) FROM posts` が一致、`curl -fsS http://127.0.0.1/image/<id>.jpg` が 200（拡張子なしは 404 になります）
3. 読む側はファイル優先・DB フォールバック（ファイルが無ければ DB に読みに行く形）にします（ファイルが無ければ従来通り `imgdata` を返します）。いきなり DB 読みを消しません。未移行の id はアプリが 500 を返す構成のままです（素体の挙動で、この変更の回帰ではありません）
4. nginx はファイル配信に寄せます。対象の `location /image/` に `try_files` を足します（未移行分は `@app` へフォールバック。[0060-static-cache.md §3](0060-static-cache.md#3-nginx-でファイルを配信する形に寄せる条件付きの土台) と同じです）。既に足してあれば足しません（先に `sudo nginx -T` で確認します）:

- `backup/s1/etc/nginx/sites-enabled/isucon.conf`（`try_files` が無い場合のみ）です。

```nginx
location /image/ {
  root /home/isucon/private_isu/webapp/public/;
  try_files $uri @app;
}

location @app {
  proxy_set_header Host $host;
  proxy_pass http://app;
}
```

- `@app` は、[0040-http-client.md](0040-http-client.md) で足した upstream `app` へ向けます。
- 0030-cache.md §5 の `proxy_cache` の行は、この location に残します。

`backup/s1/home/private_isu/webapp/python/app.py` の `get_image` の頭:

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

- 反映します。アプリと nginx を、同じ台で反映します。
- nginx は、`try_files` を足したときだけです。

```bash
# 1 台で打つ
git pull --ff-only
sudo cp -a backup/s1/home/private_isu/webapp/python/app.py /home/isucon/private_isu/webapp/python/app.py
sudo systemctl restart isu-python.service
sudo journalctl -u isu-python.service -n 40 --no-pager
```

```bash
# 1 台で打つ
git pull --ff-only
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/image/1.jpg
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

- 判定します（bench-prep → 公式ベンチ 1 本。alp / slp とも同じ台で打ちます）。

```bash
# 1 台で打つ
sudo slp my --file /var/log/mysql/mysql-slow.log | grep -i -E 'imgdata|posts' | head -10
```

```bash
# 1 台で打つ
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
echo "$(date -Iseconds)  score=  pass=  fail=  note=image-file" >> ~/bench-notes/scores.txt
```

- slp の `SELECT * FROM posts WHERE id` から画像分の量が減れば成功です（今回: 全件キャプチャ 6945 件 → 最終ベンチの slow SELECT 0 件。ファイル配信化の証拠は §2 の応答ヘッダでも確認）。alp の `/image` は高速化で件数が増える（17437 → 23771）ため、Sum の増減ではなく slow 消滅と点数で判定します
- 再起動試験: `sudo reboot` 後に画像と投稿が残ります（[0030-cache.md §1](0030-cache.md#1-投稿の本体データは-mysql-と-ebs) と同じです）。消えたらファイルが投稿の本体データになっているか、`tmpfs` 置きを疑います
- ファイル化が通ったら [0060-static-cache.md](0060-static-cache.md) の `expires` + `304` を重ねられます。ただし同時に入れて比べません。§2 の点数を先に取ります

- 戻します（作業機で戻して、サーバーに反映します）:

```bash
# 作業機。try_files を外し、アプリを戻す
git pull --ff-only
# backup/s1/etc/nginx/sites-enabled/isucon.conf と <対象>.py を戻す
git add -A && git commit -m "tune: 画像ファイル化を外す" && git push
```

```bash
# サーバーで反映（site conf とアプリを同じ台で反映）
git pull --ff-only
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo nginx -t && sudo systemctl reload nginx
# 1 台で打つ
sudo cp -a backup/s1/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1/image/1.jpg
```

### 観察すること

- `imgdata` を読む slp の行と `/image` の alp Sum が両方落ちました（片方だけでは終わりません）
- 再起動後に画像が残ります（投稿の本体データが MySQL / EBS にあります）
- `fail` が 0 です。更新漏れ（新投稿の画像が出ない）は書き込み経路の保存漏れです。読み側の TTL（有効期限）を疑いません

## 3. ファイルディスクリプタの上限は確認だけ（新規に上げない）

- この演習では、FD 上限を上げません。
- [0070-infra-params.md §6](0070-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) で済ませたか、確認するだけです。

```bash
systemctl show isu-python.service -p LimitNOFILE
PID="$(systemctl show -p MainPID --value isu-python.service)"
grep -i 'open files' /proc/$PID/limits
sudo journalctl -u isu-python.service --grep='Too many open files' --no-pager | head -5
```

- 枯渇ログが無ければ「足りている」です。この章の作業（worker 増なし、画像ファイル化、プリペアド無効化）で新たに FD が詰まることはほぼありません
- 枯渇ログが出たらアプリ変更の前に [0070-infra-params.md §6](0070-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) に戻ります。この章と同時進行にしません
- gunicorn の worker 数と DB プール数（[0040-http-client.md §6](0040-http-client.md#6-同一ホストへの上限を確認する絞りすぎない)、[0050-timeout.md §6](0050-timeout.md#6-同一ホストへの上限を確認する絞りすぎない)）を上げた回は、FD も見るだけです（`ss -s`、`lsof` 件数）。上げるのは枯渇が決まってからです

### 観察すること

- `LimitNOFILE` の実効値と枯渇ログの有無を言えます
- 上げていません（確認だけです）。上げたくなったら [0070-infra-params.md §6](0070-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) に戻ると決めました

<a id="4-admin-prepareが最上位ならプリペアドを使わない形に変える"></a>
## 4. `ADMIN PREPARE` が最上位ならプリペアドを使わない形に変える

- `pt-query-digest` の先頭が `ADMIN PREPARE ...` のとき、原因は索引ではありません。往復回数です。
- サーバ側プリペアドステートメントは、問い合わせ 1 回に 2 往復（PREPARE + EXECUTE）かかります。
- 件数が多いと、その倍増分が支配的になります。
- 検知したら、自分の 1 箇所をクライアント側実行（値の埋め込みを手元で行う方式）に変えます。

?> 出るかどうかは、ドライバ（DB に接続する部品）で決まります。素体の Python（`MySQLdb` / mysqlclient）は、クライアント側実行でサーバ `PREPARE` を出しません。出ないのが正常です。この節は、飛ばしてよいです。

- 検知します（§1 の続き。`--limit` を小さくして、先頭だけ見ます）。

```bash
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 3 2>/dev/null | head -60
# Rank 1 の Query ID 行に ADMIN PREPARE と出るか。例:
# # Query 1: 12.3 QPS, ...  ADMIN PREPARE ...
sudo slp my --file /var/log/mysql/mysql-slow.log | head -10
# slp 側は通常クエリが並ぶ。pt-query-digest の ADMIN 行と突き合わせて件数の倍増を見る
```

- `ADMIN PREPARE` が無ければこの節は飛ばします。§4 の変更は入れません（「対応済み」と書いて §5 へ進みます）
- キャプチャは `long_query_time=0` の短い 1 本分です（§1 と同じです）。`=0` のまま放置しません

- 場所を特定します（ドライバ・ORM で書き方が違います。自分の `grep` 結果に当てます）。

```bash
grep -rn 'prepare\|Prepare\|PREPARE\|prepared=True\|cursor.*prepared\|server_side\|use_server_side\|prepare_threshold' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
grep -rn 'SQLAlchemy\|asyncmy\|aiomysql\|pymysql\|MySQLdb\|mysqlclient\|connector' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
```

- 方針は、次のとおりです（コピペ用の完成品は置きません。自分の 1 箇所だけです）。

1. 対象は負荷の大きい 1 経路だけです（§1 で指した問い合わせを発行する箇所です）。全体の設定をひっくり返しません
2. サーバ側プリペア（`prepare=True` 相当、サーバに実行計画を持たせる方式）をやめ、クライアント側で値を埋める通常実行にします（ドライバの既定の `execute` です）。SQL 文字列を自前で組み立てません（エスケープ＝記号の扱いはドライバに任せます。`%s` / `%(name)s` の渡し方は変えません）
3. ORM（SQL を自動生成する部品）の自動プリペアなら設定フラグ 1 つだけ変えます。問い合わせの書き換えと同時に入れません

- `backup/s1/home/private_isu/webapp/python/<対象>.py` の該当箇所を編集します（重い処理なら s2、軽い処理なら s1）。

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
sudo cp -a backup/s1/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
systemctl is-active isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
sudo journalctl -u isu-python.service -n 40 --no-pager
```

- 検証します（同じキャプチャを取り直します）。

```bash
sudo mysql -e "SET GLOBAL long_query_time = 0"
# bench-prep（[0020-measure.md](0020-measure.md) と同じ mv + reopen / flush-logs。同じ台です）してから公式ベンチ 1 本
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
sudo pt-query-digest /var/log/mysql/mysql-slow.log --limit 3 2>/dev/null | head -40
sudo mysql -e "SET GLOBAL long_query_time = 1"
echo "$(date -Iseconds)  score=  pass=  fail=  note=no-prepare" >> ~/bench-notes/scores.txt
```

- 実測サンプル: 今回は、`ADMIN PREPARE` が 0 件でした。**未実行**です。
- [§4](#4-admin-prepareが最上位ならプリペアドを使わない形に変える) の「出たら §4 へ進む」どおりです。
- 比較に使える点数は、§5 の 45814 です。

- `ADMIN PREPARE` の行が消えた（または Rank 外に落ちた）→ 成功です。そのまま §5 の判定へ進みます
- 残る → 変えた箇所は負荷の大きい経路ではありません。戻して別の 1 箇所へ進みます（全体フラグに広げません）

- 戻します（作業機で戻して、サーバーに反映します）:

```bash
# 作業機
git pull --ff-only
# backup/s1 の <対象>.py を戻す
git add -A && git commit -m "tune: プリペアド外しを戻す" && git push
```

```bash
# サーバーで反映
git pull --ff-only
sudo cp -a backup/s1/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- 変える前後で `pt-query-digest` 先頭行の `ADMIN PREPARE` の有無を言えます
- 変えたのは 1 箇所だけです（全体フラグと問い合わせの書き換えを同時に入れていません）
- エスケープを自前で組み立てていません（`fail` や文字化けが出たらここを疑います）

## 5. 公式ベンチで判定する

- §2・§4 のどれか 1 つだけ残します。他は、戻した状態にします。
- その状態で、公式ベンチを 1 本回します。
- bench-prep は [0020-measure.md](0020-measure.md) と同じです（nginx のログは `mv` + `reopen`、slow log は `flush-logs`。同じ台です）。

- **先に slow log を回転します。それから、ベンチを実行します。**
- ベンチ後に回転すると、解析すべき記録が退避ファイルへ移ります。

```bash
# 1 台。ベンチ前
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysql -e 'FLUSH SLOW LOGS'
```

```bash
# 1 台で打つ
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
fi
sudo nginx -s reopen
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
echo "$(date -Iseconds)  score=  pass=  fail=  note=app-image-file" >> ~/bench-notes/scores.txt
```

実測サンプル（§5 / `note=app-image-file`）: `{"pass":true,"score":45814,"success":43109,"fail":0,"messages":[]}`（§1 の 40129 から +5685、`fail=0`）。

```bash
# 1 台で打つ
# ベンチ後。ここでは回転せず、今回の記録を読む
sudo slp my --file /var/log/mysql/mysql-slow.log \
  --sort sum-query-time --reverse --output standard
```

?> `FLUSH SLOW LOGS` は、ログを閉じて同じパスを開き直します。日付付きファイルへの変更は、その前の `mv` が行います。**`FLUSH` だけでは、古い内容は消えません**。パスを `SET GLOBAL` で指定した場合も、同じです。回転はベンチ前に行います。ベンチ後は、回転済みログと現在のログを取り違えないようにします。

- 残す変更は、`backup/s1` に積みます（[0020-measure.md](0020-measure.md) の「残す変更は backup/s1 に積む」の流儀）。

- 判定は、slp（または pt-query-digest）＋ alp ＋点数です。
- 点だけを見ません。
- 効かなければ、残しません。

### 観察すること

- 狙った slp / pt-query-digest の行が落ち、alp の Sum が落ち、点数が動きました（3 点セットです）
- `fail` が 0 です。画像の更新漏れ・他人表示は §2 の保存漏れ、文字化け・500 は §4 のエスケープ崩れを疑います
- `long_query_time` を 1 に戻しました（`df -h /` で余裕があります）

- 0080 まで終わったら、[0100-overall-tuning.md](0100-overall-tuning.md) に進みます。
- 残した変更は、`backup/s1` に積みます。feature ブランチでやり取りします（流儀は [0020-measure.md](0020-measure.md) と同じです）。

<a id="6-出力例"></a>

## 6. 出力例

- 2026-10-13 の実測（`c7a.large` 1 台 / `ap-northeast-1` / 単一構成 / `nproc=2` / メモリ 3.7Gi / worker 4・keepalive 32・expires 1h・proxy_cache / 0060＋0070 適用済み）。
- 条件（日付、c7a.large 1 台、ap-northeast-1、nproc=2、メモリ 3.7Gi、単一構成）を明記します。

- **今回点数を動かしたのは、画像ファイル化（§2）の 1 つでした**（+5685）。
- FD 確認は、見るだけです。
- `ADMIN PREPARE` は、0 件のため対象外としました。

| 節 | 内容 | score | fail | 判定 |
| --- | --- | --- | --- | --- |
| 0070 §7 | ベースライン（worker 4） | 40682 | 0 | |
| §1 | slp 全件キャプチャで原因を 1 つに絞る（計測＋ベンチ 1 本） | 40129 | 0 | 確定: 画像ファイル化（§2） |
| **§2** | **`/image` をファイル読みに（10253 件移行＋`try_files`）** | **45814** | **0** | **+5685** |
| §3 | FD 上限（`LimitNOFILE`） | — | — | **見るだけ**（12 / 524288） |
| §4 | `ADMIN PREPARE` | — | — | **スキップ**（0 件） |
| **§5 最終** | §2 だけ残し | **45814** | **0** | |

### §1: slp 全件キャプチャで原因を 1 つに決める

- `long_query_time = 0` の全件キャプチャです（bench-prep 後にアプリを restart してから、1 本回します）。
- ベンチ自体も、有効な記録です。

```text
COUNT   SUM(QUERYTIME)  AVG(QUERYTIME)  クエリ
 2308       37.420        0.016213   SELECT p.id,p.user_id,p.body,p.created_at,p.mime FROM posts p
                                    JOIN users u ON u.id = p.user_id
                                    WHERE u.del_flg = N ORDER BY p.created_at DESC LIMIT N
                                    （examined 約 10166/件、sent 20/件）
  559       14.145        0.025304   SELECT COUNT(N) FROM comments WHERE ...
 6945件                 -          SELECT * FROM `posts` WHERE `id` = N（/image と /posts/<id> の合算）
  253件（grep）          -          imgdata への言及
    0件                  -          ADMIN PREPARE
```

- alp（同ベンチの access.log）では、`/` が Sum 首位です（69.824、COUNT 2308）。slp 首位のタイムライン用の問い合わせと、対応しています。
- `/image` は Sum 第2位です（60.968、COUNT 17437、AVG 0.003）。`SELECT * ... WHERE id` の大半を占めます（詳細ページ `/posts/<id>` は COUNT 2187）。

- [§1](#1-対象を-1-本に決めるalp--slp--pt-query-digest) の `ADMIN PREPARE` は、**0 件**でした（`grep -c ADMIN` = 0。pt-query-digest の先頭 5 件にも `ADMIN` なし）。
- §4 は、スキップします。

pt-query-digest の先頭（`--limit 5`）:

```text
# Query 1: 37.84 QPS, 0.61x concurrency（タイムライン系）
# Query 2: 9.16 QPS, 0.23x concurrency
# Query 3: 108.52 QPS, 0.14x concurrency
```

- 収集後は、`SET GLOBAL long_query_time = 1` に戻しました。アプリも restart しました（`SHOW VARIABLES` で 1.000000 を確認）。

- 判断: slp 首位は、タイムラインです（`examined >> sent`。ただし [0015-sql-tuning.md](0015-sql-tuning.md) の索引対応済み範囲で、別章送り）。
- **今回の 1 つは、§2 の画像ファイル化**に決めました（`/image` が alp 第2位かつ、毎回大きな画像データを読んでいるため）。

### §2: 画像ファイル化（読む側だけ。書く側は DB のまま）

- 置き場と移行は、次のとおりです（EBS 上。`tmpfs`・`/dev/shm`・`/tmp` には置きません）。

```text
/home/isucon/private_isu/webapp/public/image（isucon:www-data / 775、a+rX 済み）
移行: 10253/10253 件（jpg 9871 + png 254 + gif 128）/ 1.3G
ls public/image/ | wc -l = 10253 = SELECT COUNT(*) FROM posts = 10253（一致）
```

- アプリの読む側は、次のとおりです（`app.py` の `get_image` の頭。ファイル優先・DB フォールバック。DB 読みは消しません）。

```python
IMAGE_DIR = pathlib.Path(__file__).resolve().parent.parent / "public" / "image"
IMAGE_MIME = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif"}


@app.route("/image/<id>.<ext>")
def get_image(id, ext):
    ...
    if ext in IMAGE_MIME:
        fp = IMAGE_DIR / f"{id}.{ext}"
        if fp.is_file():
            return flask.Response(fp.read_bytes(), mimetype=IMAGE_MIME[ext])

    cursor = db().cursor()  # ファイルが無い場合は従来通り DB へ
```

- `syntax-ok`（`ast.parse`）を確認してから、`systemctl restart isu-python.service` しました。
- 書き込み経路（`INSERT ... imgdata`）は、変えていません（投稿の本体データは MySQL に残します）。

- nginx は、ファイル配信に寄せました。
- `try_files` が無かったので、足しました。`proxy_cache` の行は、この location に残します。

```nginx
location /image/ {
  root /home/isucon/private_isu/webapp/public/;
  try_files $uri @app;
  proxy_cache image;
  proxy_cache_valid 200 60s;
  ...
}

location @app {
  proxy_set_header Host $host;
  proxy_pass http://app;
}
```

- 確認は、`curl -sI http://127.0.0.1/image/1.jpg` です（2 回目以降で確定）。

```text
HTTP/1.1 200 OK
Content-Type: image/jpeg
Content-Length: 79316
Last-Modified: Tue, 13 Oct 2026 02:10:00 GMT（移行時刻）
ETag: "6acd92f8-135d4"（nginx 形式。アプリ由来の Python 形式から変化）
Accept-Ranges: bytes
X-Cache なし（ファイル配信。proxy 経由ではない）
```

### §3: FD 上限（見るだけ）

```text
unit LimitNOFILE = 524288
プロセス実効値（/proc/<MainPID>/limits）: Max open files 1024（soft）/ 524288（hard）
開いている fd = 12
journalctl（isu-python）に 'Too many open files' なし
```

- 枯渇ログが無く、開いている数も、上限を大きく下回ります。上限を変更しません（[§3](#3-ファイルディスクリプタの上限は確認だけ新規に上げない) どおり、確認だけです）。

### §4: `ADMIN PREPARE`（スキップ）

- §1 の全件キャプチャで、`ADMIN` は 0 件でした（`grep -c` = 0。pt-query-digest 先頭にもなし）。
- 素体の Python（`MySQLdb` / mysqlclient）は、クライアント側実行です。出ないのが正常です。
- §4 の変更は、入れていません。

### §5: 最終判定

- §2 だけ残します（§4 は変更なし。FD も変更なし）。
- その状態で、公式ベンチ 1 本です（bench-prep の `mv`＋`reopen`／`FLUSH SLOW LOGS` 済み）。

```json
{"pass":true,"score":45814,"success":43109,"fail":0,"messages":[]}
```

- §1 ベンチ（40129）比 **+5685**、0070 ベンチ（40682）比 +5132 でした。
- `fail=0` を確認しました。

alp（最終ベンチの access.log）:

| | §1 ベンチ（ファイル化前） | §5 最終（ファイル化後） |
| --- | --- | --- |
| `/image` COUNT | 17437 | **23771（1.36 倍をさばく）** |
| `/image` AVG | 0.003 秒 | 0.009 秒 |
| `/image` SUM | 60.968 | 208.652 |
| `/posts/<id>` AVG | 0.013 秒 | 0.034 秒 |

- `/image` の AVG は、上がっています。高速化でベンチマーカーの発行件数が 1.36 倍に増えたためです（SUM は、件数増の反映です）。
- ファイル配信の証拠は、§2 のヘッダ（`ETag` の nginx 化・`X-Cache` 消滅）と、slow log の変化です。

slp（最終ベンチ後。`long_query_time = 1` に戻し済み）:

```text
COUNT 1 / DELETE FROM `posts` WHERE `id` > N（initialize の掃除。1.3 秒）
SELECT 系の slow は 0 件（grep -c 'SELECT * FROM `posts` WHERE `id`' = 0）
```

- ファイル化前は、全件キャプチャで `SELECT * ... WHERE id` が 6945 件ありました。
- 最終ベンチでは、slow（>1s）が消滅しました。`cache:-` が 43036 件です（`/image` がファイル配信になり、`proxy_cache` を経由しないため。当然の変化です）。

- `ls public/image/ | wc -l` = 10253 のままです（ベンチ中の新規投稿の画像は DB のみです。読み側フォールバックが返します。`fail=0` で確認）。
- 再起動試験（`sudo reboot`）は、実施していません（フェーズ 4〜6 が使う環境のため。EBS 配置＋DB 残しで代替確認とします）。

### この節でつまずいた点

?> **この表の問題はすべて本文へ反映済みです。**「直し方」列のとおりに直してあります。同じ症状が出たら本文の手順どおりに直して再計測します。

| # | 箇所 | 問題 | 修正内容 |
| --- | --- | --- | --- |
| 1 | §3 | 別の測定例を期待点と読み違えやすい | 環境依存の例と明記し、今回の結果と分けました |
| 2 | §1・§3 | `slp --sort sum` の誤用と既定順の読み違い | `--sort sum-query-time --reverse` と `\| head` を使用します。過去の `input does not match format` の原因は、記録だけでは断定しません |
| 3 | §1 | 全件ログの期間が混在し、既存接続の閾値も戻していなかった | 収集前に回転し、収集後は閾値を戻して両アプリを再起動します |
| 4 | §6 | `FLUSH` 単独が自動で改名・消去すると誤解していた | `mv` → `FLUSH SLOW LOGS` の役割を区別しました。容量の回復は [0100の対処](0100-overall-tuning.md#ディスクが埋まるスローログ) を参照します |
| 5 | §1 | `alp -m` を除外フィルタとして使っていた | ページ送りだけを見るコマンドに `-f` を使用します |

### 観察したこと（実測）

- 画像ファイル化で +5685 になり、slow の `SELECT` が 0 件になりました（表の §2・§5）
- FD 上限（§3）は観測時の FD 数（12）と枯渇ログを確認し、変更しませんでした
- `ADMIN PREPARE` が 0 件なのでスキップしました（§4。手順書は「出たら §4 へ進む」と条件付きで書いてあります）

## 7. トラブルシュート

### 画像が出ない / 403 / 新投稿の画像が出ない

- 403 → 権限です。`isucon:www-data`、`a+rX`、`/home/isucon` からの `o+x` の順です（[0110-split-2.md §静的ファイルと画像](0110-split-2.md#静的ファイルと画像) と同じです）
- 旧画像が出て新画像が出ない → 書き込み経路の保存漏れです。読み側の TTL やキャッシュを疑いません
- 再起動で消える → `tmpfs` / `/dev/shm` / `/tmp` 置きか、ファイルだけが投稿の本体データになっています。EBS + DB 残しに戻します

### インデックスが効かない

- 同時貼りしていませんか（1 本ずつです）。`SHOW INDEX` で名前を確認してから再 `EXPLAIN` します
- `examined` は落ちたが Count が大きいまま → N+1（呼び出し回数）の仕事です。`IN` まとめへ進みます
- `posts` の一覧がまだ重い → `SELECT *` の `imgdata` 混入を疑います。列指定の分離に戻ります

### プリペアドを外したら文字化け / 500 / fail

- SQL 文字列の自前結合が一番多いです。値埋めはドライバに任せ、プレースホルダ（値の埋め込み場所）の渡し方を変えません
- ORM の全体フラグを変えたら 1 経路に戻します。負荷の大きい 1 箇所だけに狭めます
- `ADMIN PREPARE` が消えない → 変えた箇所は負荷の大きい経路ではありません。git で戻して別の 1 箇所へ進みます

### FD 不足と取り違える

- `Too many open files` が出たら、この章の作業を止めて [0070-infra-params.md §6](0070-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) へ進みます。アプリ変更と FD 上げを同時に入れません
- worker 増と DB 接続増は対で見ます（[0070-infra-params.md §5](0070-infra-params.md#5-gunicorn-の-worker-プロセス数少なすぎも多すぎも遅い)）。`too many connections` は worker を戻します
