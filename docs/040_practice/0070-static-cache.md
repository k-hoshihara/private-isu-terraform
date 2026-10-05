# 0070 静的・画像を条件付きリクエストで速くする

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）はローカル環境の private-isu-terraform リポジトリにて実行します。

本の 8-5「HTTPヘッダーを活用してクライアント側にキャッシュさせる」を、画像・静的の高速表示に絞って数字で確認します。

- `expires` で `Cache-Control: max-age` を返します（保持期間の指定です）
- `Last-Modified` / `ETag` による HTTP 条件付きリクエストで `304 NOT MODIFIED` を返します（転送量の削減です）
- 複数台では `ETag` の台間一致を確認します（`rsync -a` の mtime 同期か `etag off` です）
- 更新時はファイル名変更かクエリ文字列で古いキャッシュを外します

ゴールは「`expires` 付与の前後で `curl -sI` の `Cache-Control` と `304`（`size=0`）、転送量差、`access.log` の行数増加の有無、公式ベンチの点数」を言えることです。「付けた」だけでは終わりません。

[0060-timeout.md](0060-timeout.md) の続きです。[0040-cache.md](0040-cache.md) §6 までやった状態なら、先に戻してから入ります（付けた 1 行だけ消して restart / reload。[0040-cache.md](0040-cache.md) の「Cache-Control を付ける前後で挙動を比べる」 の戻し方です）。未実施なら §1〜§2 の「付ける前」から入ります。

順番: 対象を決める（1.）→ 付ける前（2.）→ 静的配信に寄せる（3.）→ `expires`（4.）→ `304` 再現（5.）→ 台間一致（6.、1 台なら読みだけ）→ 更新時の外し方（7.）→ ベンチ判定（8.）。1 手ずつ進めます。`fail` が 0 でない点数は比べません。出口は [0080-infra-params.md](0080-infra-params.md) です。

点数は `~/bench-notes/scores.txt` に 1 行。転送量の目安も一緒に書く:

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  bytes=  note=static-baseline" >> ~/bench-notes/scores.txt
```

| 回 | score | bytes（目安） | メモ |
| --- | --- | --- | --- |
| ベースライン |  | 例: 200 10KB | [0030-measure.md](0030-measure.md) までの状態。条件付き未設定 |
| 1 手目 |  | 例: 304 0KB | 例: `/image/` に `expires 1d` |

## 1. 対象を決める（GET の読み物だけ）

狙うのは更新頻度が低く、何度も参照される GET だけです。POST、ログイン後の私用ページは外します。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
# /image が Sum 上位なら筆頭候補。/css /js /img があれば次点
find /home/isucon/private_isu/webapp/public -type f | head -20
ls -l /home/isucon/private_isu/webapp/public/
```

- `public/` 配下（`css/style.css` など）は最初からファイルです。対象にしやすいです
- `/image/<id>` の素体は MySQL の `posts.imgdata`（ファイルではありません）。ファイル化済みならこのドリルの対象です。未済なら [0040-cache.md](0040-cache.md) の「nginx で 1 箇所だけキャッシュして HIT 率で評価する」 を先に済ませるか、今回は `public/` 静的だけにします
- 公開タイムラインの GET は認証不要部分だけです。迷ったら `/image` の匿名 GET か `public/` 静的のどちらか 1 つに絞ります

### 観察すること

- 自分の 1 手が「`/image/`」「`public/` 静的」のどちらかを言えます
- alp の Sum 上位と対象が一致しています（上位に無いパスを触りません）
- `/image` が未ファイル化なら対象を `public/` 静的に変えました（無理に広げません）

## 2. 付ける前: ヘッダが無くて毎回 200 であることを確認する

```bash
curl -sI http://127.0.0.1/image/1.jpg | grep -i -E 'cache-control|etag|last-modified|expires' || echo 'no cache headers (before)'
curl -sI http://127.0.0.1/css/style.css | grep -i -E 'cache-control|etag|last-modified|expires' || echo 'no cache headers (before)'
```

`curl` はデフォルトではキャッシュしません。2 回叩いた分だけ行が増えます（= 毎回サーバまで来ています）:

```bash
sudo cat /var/log/nginx/access.log | tail -2
curl -s -o /dev/null http://127.0.0.1/image/1.jpg
curl -s -o /dev/null http://127.0.0.1/image/1.jpg
sudo cat /var/log/nginx/access.log | tail -2
# 2 回叩いた分だけ行が増える = 毎回サーバまで来て毎回 200 で本文を返している
```

転送量の目安を取ります（`304` 応答後の比較原点にします）。`size_download` が本文サイズを表します:

```bash
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download} time=%{time_total}s\n' http://127.0.0.1/image/1.jpg
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download} time=%{time_total}s\n' http://127.0.0.1/css/style.css
```

### 観察すること

- 対象パスの応答ヘッダに `Cache-Control` / `ETag` / `Last-Modified` がありません（あるなら既設。§4 へ進みます）
- `curl` 2 回でログが 2 行増えます（毎回サーバまで来ています）
- `size_download` を控えました（`304` 応答時の 0 バイトと比較するためです）

## 3. nginx でファイルを配信する形に寄せる（条件付きの土台）

条件付きリクエスト（§5）は、nginx がファイルを配信する場合は自動で付きます。アプリ（Flask）が DB から読んで返すときは自動では付きません。

今の配信経路を見ます:

```bash
sudo nginx -t
sudo nginx -T 2>/dev/null | grep -E 'proxy_pass|root|location' | head -30
sudo nginx -T 2>/dev/null | grep -E 'proxy_pass|location /image|location /css' | head -20
```

- `location /css/ { }` のように空で `root` 配下を返す形なら、そのままファイル配信になっています。この節（3.）は何もしないで §4 へ進みます
- `/image/` が `proxy_pass` のまま（DB 配信）なら、ファイル化済みのときだけ `try_files` を前に足します。未ファイル化なら付けません（重いアプリへ proxy のままです。対象を `public/` 静的に変えます）

足すときは `backup/s1/etc/nginx/sites-enabled/isucon.conf` に書きます（§4 と同じ流し方です）。ファイル化済みで素体に出力が無いときだけ追加します:

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

ファイル化したら EBS 上に置きます（`/home/isucon/...`）。`tmpfs`、`/dev/shm`、`/tmp` にしません（[0040-cache.md](0040-cache.md) の「投稿の本体データは MySQL と EBS」 の線引きです）。DB の `imgdata` は消しません。投稿の本体データは MySQL のまま残します。

```bash
sudo chmod o+x /home/isucon /home/isucon/private_isu /home/isucon/private_isu/webapp
sudo chmod -R a+rX /home/isucon/private_isu/webapp/public
curl -fsS -o /dev/null -m 5 http://127.0.0.1/css/style.css
```

### 観察すること

- 対象がファイル配信かアプリ配信か言えます（`location` の中身で判定します）
- DB 配信のまま無理に `expires` を付けていません（付けるのはファイル配信の location だけです）
- 権限 403 が出ません（`www-data` が読めます）

## 4. `expires` で保持期間を付ける（1 箇所だけ）

`backup/s1/etc/nginx/sites-enabled/isucon.conf` の対象 location に `expires` 1 行を足して push → サーバーで pull → cp で反映 → reload します。全体には付けません。

`/image` を対象にする場合（ファイル配信の location）:

```nginx
location /image/ {
  root /home/isucon/private_isu/webapp/public/;
  expires 1d;
}
```

`public/` 静的なら対象を `/css/` などにします（どちらか 1 つです）:

```nginx
location /css/ {
  expires 1d;
}
```

`expires 1d;` で `Cache-Control: max-age=86400` と `Expires` が返ります。最初は `1d` から入れます。いきなり 1 年にはしません。`public/` 静的で試すなら対象を `/css/` などに変えます（どちらか 1 つです）。

```bash
# 作業機
git pull --ff-only
# 上の内容を配置する
git add -A && git commit -m "tune: /image に expires 1d を付ける" && git push
```

```bash
# サーバーで反映
git pull --ff-only
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo nginx -t && sudo systemctl reload nginx
curl -sI http://127.0.0.1/image/1.jpg | grep -i -E 'cache-control|expires|etag|last-modified'
# Cache-Control: max-age=86400 が見えること
# Last-Modified（mtime 起源）と ETag（mtime + サイズ起源）が自動で付くこと
```

`max-age` の振り方:

- まず `1d`（86400）です。静的は変更が入らないので大きめでよいですが、最初から 1 年にしません
- 私用ページに `public` を付けると他人に見えます。迷ったら匿名 GET だけにします
- `Cache-Control: private` / `no-store` を付けると `proxy_cache` が効かなくなることがあります（[0040-cache.md](0040-cache.md) の「Cache-Control を付ける前後で挙動を比べる」 と同じです）。対象の狭さとヘッダの中身を先に見ます

### 観察すること

- `curl -sI` で `Cache-Control: max-age=86400` が見えます
- `ETag` / `Last-Modified` のどちらか（または両方）が見えます。どちらも無ければ対象 location を間違えているか、アプリ配信のままです
- `fail` が出ません（出たら対象を狭めます。`max-age` を延ばしません）

## 5. 条件付きリクエストで `304` を再現する（転送量の差）

§4 までやってヘッダが出たら、ブラウザの 2 回目を `curl` で再現します。保存した `Last-Modified` / `ETag` をリクエストに付けて送り、変化が無ければ `304`（本文空）が返ります。

```bash
# 1. 値を保存する
curl -sI http://127.0.0.1/image/1.jpg | grep -i -E 'etag|last-modified'
# 2. ETag で再検証（値全体を " 付きでそのまま返す）
ETAG=$(curl -sI http://127.0.0.1/image/1.jpg | grep -i '^etag:' | tr -d '\r' | awk '{print $2}')
echo "etag=$ETAG"
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download}\n' -H "If-None-Match: $ETAG" http://127.0.0.1/image/1.jpg
# 304 size=0 なら当たり。本文を運んでいない
# 3. Last-Modified で再検証（値全体をそのまま返す）
LM=$(curl -sI http://127.0.0.1/image/1.jpg | grep -i '^last-modified:' | tr -d '\r' | cut -d' ' -f2-)
echo "lm=$LM"
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download}\n' -H "If-Modified-Since: $LM" http://127.0.0.1/image/1.jpg
# 304 size=0 なら当たり
```

読み方:

- `304 size=0` → 条件付きが効いています。本文転送が消えた分が高速化の効果になります
- ずっと `200` → アプリ配信のまま（nginx の自動付与が効かない経路）か、ヘッダ値の写し間違いです（`"` の脱落、`Date:` との取り違え）。§4 の `curl -sI` に戻ります
- `curl` で条件を付けずに 2 回叩いてもログは減りません（`curl` はキャッシュしないので正常です）。サーバへの到達の有無はブラウザの DevTools 手順で確認します

ブラウザで見ます（`curl` はキャッシュしないので、サーバへの到達の有無はここで確認します）:

1. DevTools → Network を開き、`Disable cache` のチェックを外します
2. 対象（`/image/1.jpg` か `/css/style.css`）を 2 回リロードします
3. 1 回目 `200`、2 回目 `200 (memory cache)` / `200 (disk cache)` か `304` ならブラウザが覚えています
4. その間 `access.log` に行が増えません（サーバに来ていない分です）

```bash
sudo cat /var/log/nginx/access.log | tail -2
# ブラウザで 2 回リロードしてからもう一度 tail。行が増えなければ来ていない
sudo cat /var/log/nginx/access.log | tail -2
```

### 観察すること

- `If-None-Match` か `If-Modified-Since` のどちらかで `304 size=0` を出しました（両方出ればなおよいです）
- `200` 時の `size_download` と `304` 時の `0` を 2 つの数字で言えます（転送量差が高速化の根拠です）
- ブラウザ 2 回目が cache / `304` で、サーバのログが増えません

## 6. 複数台では `ETag` の台間一致を確認する（1 台なら読みだけ）

配信サーバが複数台あるとき、台ごとに違う `Last-Modified` / `ETag` を返すとクライアントのキャッシュが効きません。合わせるのは mtime です。

1 台構成ならここは読みだけです。§4〜§5 が通っていれば次へ進みます。配信役（s1）が複数台ある構成の回だけ実施します（[0020-split-1.md](0020-split-1.md) の分割後です）。

```bash
# 各配信台で同じファイルの mtime と応答ヘッダを比べる
stat -c '%y %s %n' /home/isucon/private_isu/webapp/public/css/style.css
curl -sI http://127.0.0.1/css/style.css | grep -i -E 'etag|last-modified'
# s1 が複数台なら各台で同じ 2 本を取る。ETag / Last-Modified が一致すること
```

ずれたときの直し方（配信元で打ち、`WEB_PRIV` の配信先へ配ります）:

```bash
# 配信元で。s1 の IP は backup/common/ips.sh から
source /home/isucon/private-isu-terraform/backup/common/ips.sh  # WEB_PRIV
# 配信元から mtime 保持で配る。-a に -t が含まれるので -a でよい
rsync -av /home/isucon/private_isu/webapp/public/ "isucon@${WEB_PRIV}:/home/isucon/private_isu/webapp/public/"
# 両台で mtime と ETag を比べる
stat -c '%y %s %n' /home/isucon/private_isu/webapp/public/css/style.css
curl -sI "http://${WEB_PRIV}/css/style.css" | grep -i -E 'etag|last-modified'
# s1 が複数台なら各台で同じ 2 本を取る。ETag / Last-Modified が一致すること
```

- `rsync` には `-t`（mtime 保持）を付けます。`-a` に含まれているので `-a` で足ります。`-t` 無しの転送は mtime がずれて `ETag` が割れます
- `ETag` と `Last-Modified` のどちらか片方が一致すれば十分です
- 一致が取れないときは対象 location に `etag off;` を付けて `Last-Modified` に寄せます
- 両方を消す変更はしません

ETag を切るときだけ、`backup/s1/etc/nginx/sites-enabled/isucon.conf` の対象 location に足します（全体に付けません）:

```nginx
location /css/ {
  etag off;
}
```

```bash
# 作業機
git pull --ff-only
# 上の内容を配置する
git add -A && git commit -m "tune: ETag を切る" && git push
```

```bash
# サーバーで反映
git pull --ff-only
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo nginx -t && sudo systemctl reload nginx
```

### 観察すること

- 1 台なら「台間一致は対象外」と言えます（無理に rsync しません）
- 複数台なら各台の `ETag` / `Last-Modified` が一致しました（`curl -sI` の 2 行で判定します）
- 一致しないまま `max-age` を延ばしていません（延ばす前に mtime 同期か `etag off` を決めました）

## 7. 更新時はファイル名変更かクエリ文字列で外す

`max-age` を長くしたファイルを変更するとき、そのまま上書きすると古いキャッシュが残ります。やることはどちらかです:

- ファイル名を変える（`style.abc123.css`）
- クエリ文字列を変える（`style.css?v=abc123`）

```bash
# 参照側がファイル名かクエリで版を持っているか
grep -rn 'style.css\|app.js\|v=\|contenthash\|chunkhash' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -10
grep -rn 'stylesheet\|script src' /home/isucon/private_isu/webapp/public/*.html 2>/dev/null | head -10
find /home/isucon/private_isu/webapp/public -name '*.*.*.css' -o -name '*.chunk.js' | head
```

- ビルド時にハッシュ付き名が出る構成なら、その構成のままが正解です。手で `max-age` を延ばすだけでよいです
- ハッシュが無い素体で長期 `max-age`（1 年など）にするときは、更新手順（名変更かクエリ付与）を決めてから延ばします。決めずに延ばしません
- `/image/` の投稿画像は追記型（新規 id が増える）なので、既存 id の上書きが無ければ長期でも安全です。プロフィール画像のように上書きがある箇所だけ `max-age` を短めに残します

### 観察すること

- 長期化する対象に更新手順があるか言えます（名変更 / クエリ / 追記型で安全です）
- 手順が無い箇所の `max-age` は `1d` のままです（延ばしません）
- 更新後に `curl -sI` の新旧 URL で別物として返ることを確認しました

## 8. 公式ベンチで判定する（点数＋転送量＋alp。s1 で打つ。slow 側は s3）

[§4](#4-expires-で保持期間を付ける1-箇所だけ)〜[§7](#7-更新時はファイル名変更かクエリ文字列で外す) のうち 1 手だけ残し、他は戻した状態にして公式ベンチを 1 本回します。bench-prep は [0030-measure.md](0030-measure.md) と同じです（s1 で `mv` + `reopen`、s3 で `flush-logs`）。

```bash
# s1 で打つ
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  bytes=  note=expires-image-1d" >> ~/bench-notes/scores.txt
```

```bash
# s3 で打つ
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysqladmin flush-logs 2>/dev/null || sudo mysql -e 'FLUSH SLOW LOGS'
```

残す変更は台ごとの `backup/s1`・`s2`・`s3` に積みます（[0030-measure.md](0030-measure.md) の「残す変更は backup/s1・s2・s3 に積む」の流儀）。

判定は 3 点セットです。点数だけ見ません:

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
# 対象パスの Sum が落ちたか。落ちないのに 304 だけ出るなら測る場所がずれている
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download}\n' http://127.0.0.1/image/1.jpg
```

- ベンチマーカーが条件付きに対応している構成では点数が動きます。対応しない構成では点数が動かなくてもよいので、そのときは §5 の `304 size=0` とログの到達の有無を判定にします
- `proxy_cache`（[0040-cache.md](0040-cache.md) の「nginx で 1 箇所だけキャッシュして HIT 率で評価する」）とは別々にやります。同時に入れて比べません

戻すときは作業機で付けた `expires` 1 行を消します:

```bash
# 作業機
git pull --ff-only
# expires 1 行を消す
git add -A && git commit -m "tune: expires を外す" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo nginx -t && sudo systemctl reload nginx
curl -sI http://127.0.0.1/image/1.jpg | grep -i -E 'cache-control|expires' || echo 'reverted (no cache headers)'
```

### 観察すること

- `scores.txt` に 1 行残しました（`fail` が 0 の回だけ比べました）
- 対象パスの alp Sum が落ちたか、落ちないなら画像層は詰まっていません（DB / アプリの仕事に戻ります）
- 点数が動かない回は `304` とログの到達の有無を根拠にしました（点数だけで捨てません）

## 9. トラブルシュート

### `Cache-Control` が出ない

- 対象 location を間違えています（`curl -sI` のパスと `location` を見比べます）。全体に付けず 1 箇所に絞りましたか
- アプリ配信のままです（DB から読んで返す経路です）。nginx の自動付与はファイル配信だけです。§3 に戻ります
- `sudo nginx -t && sudo systemctl reload nginx` を忘れていませんか

### ずっと `200` で `304` にならない

- ヘッダ値の写し間違いが一番多いです。`ETag` は `"` 付き全体、`Last-Modified` は曜日から GMT まで全体を写します。`Date:` と取り違えていませんか
- アプリ配信のまま条件付きを期待していませんか。`ETag` / `Last-Modified` 自体が出ていないなら §4 に戻ります
- 条件を付けずに `curl` を 2 回叩いてもログが減らないのは正常です（`curl` はキャッシュしません）。`304` は条件付きヘッダ付きのときだけ返ります

### ブラウザ 2 回目も毎回 `200` でログが増える

- DevTools の `Disable cache` が入ったままですか
- `private` / `no-store` / `max-age=0` が付いていませんか（`curl -sI` で確認します）
- 対象がログイン後の私用ページになっていませんか。匿名 GET に戻します

### `fail` や他人表示が出る

- `public` を私用ページに付けています。対象を匿名 GET（`/image`、静的）だけに戻します。`max-age` を延ばしません
- `proxy_cache` の HIT が消えたら `Cache-Control: private` / `no-store` との干渉を疑います。`proxy_ignore_headers` を足す前に、対象の狭さとヘッダの中身を見ます

### 複数台で `ETag` が割れる

- `rsync` に `-t`（または `-a`）が付いていますか。mtime がずれると `ETag` が割れます
- `stat -c '%y %s'` が各台で一致しますか。サイズ違いはファイル自体が違います
- 一致が取れないときは `etag off;` で `Last-Modified` に寄せる選択肢もあります（対象 location だけです）
