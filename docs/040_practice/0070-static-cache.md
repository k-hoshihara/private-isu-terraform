# 0070 静的・画像を条件付きリクエストで速くする

本の 8-5「HTTPヘッダーを活用してクライアント側にキャッシュさせる」を、画像・静的の高速表示に絞って数字で確認する。

- `expires` で `Cache-Control: max-age` を返す（保持期間の指定）
- `Last-Modified` / `ETag` による HTTP 条件付きリクエストで `304 NOT MODIFIED` を返す（転送量の削減）
- 複数台では `ETag` の台間一致を確認する（`rsync -a` の mtime 同期か `etag off`）
- 更新時はファイル名変更かクエリ文字列で古いキャッシュを外す

ゴールは「付けた前後で `curl` のヘッダと `304`、転送量、`access.log` の来なさ、公式ベンチの点数」を言えること。「付けた」だけでは終わらない。

[0060-timeout.md](0060-timeout.md) の続き。[0040-cache.md](0040-cache.md) §6 までやった状態なら、先に戻してから入る（付けた 1 行だけ消して restart / reload。0040 §6 の戻し方）。未実施なら 1.〜2. の「付ける前」から入る。

順番: 対象を決める（1.）→ 付ける前（2.）→ 静的配信に寄せる（3.）→ `expires`（4.）→ `304` 再現（5.）→ 台間一致（6.、1 台なら読みだけ）→ 更新時の外し方（7.）→ ベンチ判定（8.）。1 手ずつ。`fail` が 0 でない点数は比べない。出口は [0080-infra-params.md](0080-infra-params.md)。

点数は `~/bench-notes/scores.txt` に 1 行。転送量の目安も一緒に書く:

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  bytes=  note=static-baseline" >> ~/bench-notes/scores.txt
```

| 回 | score | bytes（目安） | メモ |
| --- | --- | --- | --- |
| ベースライン |  | 例: 200 10KB | 0030 までの状態。条件付き未設定 |
| 1 手目 |  | 例: 304 0KB | 例: `/image/` に `expires 1d` |

## 1. 対象を決める（GET の読み物だけ）

狙うのは更新頻度が低く、何度も参照される GET だけ。POST、ログイン後の私用ページは外す。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
# /image が Sum 上位なら筆頭候補。/css /js /img があれば次点
find /home/isucon/private_isu/webapp/public -type f | head -20
ls -l /home/isucon/private_isu/webapp/public/
```

- `public/` 配下（`css/style.css` など）は最初からファイル。対象にしやすい
- `/image/<id>` の素体は MySQL の `posts.imgdata`（ファイルではない）。ファイル化済みならこのドリルの対象。未済なら [0040 §5](0040-cache.md#5-nginx-で-1-箇所だけキャッシュして-hit-率で評価する) を先に済ませるか、今回は `public/` 静的だけにする
- 公開タイムラインの GET は認証不要部分だけ。迷ったら `/image` の匿名 GET か `public/` 静的のどちらか 1 つに絞る

### 観察すること

- 自分の 1 手が「`/image/`」「`public/` 静的」のどちらかを言える
- alp の Sum 上位と対象が一致している（上位に無いパスを触らない）
- `/image` が未ファイル化なら対象を `public/` 静的に変えた（無理に広げない）

## 2. 付ける前: ヘッダ無し＋毎回 200 であることを確認する

```bash
curl -sI http://127.0.0.1/image/1 | grep -i -E 'cache-control|etag|last-modified|expires' || echo 'no cache headers (before)'
curl -sI http://127.0.0.1/css/style.css | grep -i -E 'cache-control|etag|last-modified|expires' || echo 'no cache headers (before)'
```

`curl` はデフォルトではキャッシュしない。2 回叩けば `access.log` に 2 行残る。これが「付ける前」の挙動:

```bash
sudo cat /var/log/nginx/access.log | tail -2
curl -s -o /dev/null http://127.0.0.1/image/1
curl -s -o /dev/null http://127.0.0.1/image/1
sudo cat /var/log/nginx/access.log | tail -2
# 2 回叩いた分だけ行が増える = 毎回サーバまで来て毎回 200 で本文を返している
```

転送量の目安を取る（`304` 応答後の比較原点にする）。`size_download` が本文サイズを表す:

```bash
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download} time=%{time_total}s\n' http://127.0.0.1/image/1
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download} time=%{time_total}s\n' http://127.0.0.1/css/style.css
```

### 観察すること

- 対象パスの応答ヘッダに `Cache-Control` / `ETag` / `Last-Modified` が無い（あるなら既設。4. へ進む）
- `curl` 2 回でログが 2 行増える（毎回サーバまで来ている）
- `size_download` を控えた（`304` 応答時の 0 バイトと比較するため）

## 3. nginx でファイルを配信する形に寄せる（条件付きの土台）

条件付きリクエスト（5.）は、nginx がファイルを配信する場合は自動で付く。アプリ（Flask）が DB から読んで返すときは自動では付かない。自前で `304` を実装するより、対象を nginx のファイル配信に寄せる方が早い。

今の配信経路を見る:

```bash
sudo nginx -t
cat /etc/nginx/sites-enabled/isucon 2>/dev/null || cat /etc/nginx/conf.d/*.conf 2>/dev/null | head -80
grep -rn 'proxy_pass\|root\|location /image\|location /css' /etc/nginx/sites-enabled/ | head -20
```

- `location /css/ { }` のように空で `root` 配下を返す形なら、そのままファイル配信になっている。この節（3.）は何もしないで 4. へ進む
- `/image/` が `proxy_pass` のまま（DB 配信）なら、ファイル化済みのときだけ `try_files` を前に足す。未ファイル化なら付けない（重いアプリへ proxy のまま。対象を `public/` 静的に変える）

```nginx
# /image をファイル化済みのときだけ。未出力なら付けない
# location /image/ {
#   try_files $uri @app;
# }
# location @app {
#   proxy_set_header Host $host;
#   proxy_pass http://heavy;
# }
```

ファイル化したら EBS 上に置く（`/home/isucon/...`）。`tmpfs`、`/dev/shm`、`/tmp` にしない（[0040 §1](0040-cache.md#1-投稿の本体データは-mysql-と-ebs) の線引き）。DB の `imgdata` は消さない。投稿の本体データは MySQL のまま残す。

```bash
sudo chmod o+x /home/isucon /home/isucon/private_isu /home/isucon/private_isu/webapp
sudo chmod -R a+rX /home/isucon/private_isu/webapp/public
curl -fsS -o /dev/null -m 5 http://127.0.0.1/css/style.css
```

### 観察すること

- 対象がファイル配信かアプリ配信か言える（`location` の中身で判定）
- DB 配信のまま無理に `expires` を付けていない（付けるのはファイル配信の location だけ）
- 権限 403 が出ない（`www-data` が読める）

## 4. `expires` で保持期間を付ける（1 箇所だけ）

本のリスト 11 そのままの設定を使う。対象の location だけに付ける。全体に付けない。

```nginx
server {
  # 省略
  location /image/ {
    root /home/isucon/private_isu/webapp/public/;
    expires 1d;
  }
}
```

`expires 1d;` で `Cache-Control: max-age=86400` と `Expires` が返る。各クライアントは 1 日間キャッシュできる。初回は `1d` から入る。いきなり 1 年にしない。`public/` 静的で試すなら対象を `/css/` などに変える（どちらか 1 つ）。

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -sI http://127.0.0.1/image/1 | grep -i -E 'cache-control|expires|etag|last-modified'
# Cache-Control: max-age=86400 が見えること
# Last-Modified（mtime 起源）と ETag（mtime + サイズ起源）が自動で付くこと
```

`max-age` の振り方:

- まず `1d`（86400）。静的は変更が入らないので大きめでよいが、最初から 1 年にしない
- 私用ページに `public` を付けると他人に見える。迷ったら匿名 GET だけ
- `Cache-Control: private` / `no-store` を付けると `proxy_cache` が効かなくなることがある（[0040 §6](0040-cache.md#6-cache-control-を付ける前後で挙動を比べる) と同じ）。対象の狭さとヘッダの中身を先に見る

### 観察すること

- `curl -sI` で `Cache-Control: max-age=86400` が見える
- `ETag` / `Last-Modified` のどちらか（または両方）が見える。どちらも無ければ対象 location を間違えているか、アプリ配信のまま
- `fail` が出ない（出たら対象を狭める。`max-age` を延ばさない）

## 5. 条件付きリクエストで `304` を再現する（転送量の差）

4. までやってヘッダが出たら、ブラウザの 2 回目を `curl` で再現する。保存した `Last-Modified` / `ETag` をリクエストに付けて送り、変化が無ければ `304`（本文空）が返る。

```bash
# 1. 値を保存する
curl -sI http://127.0.0.1/image/1 | grep -i -E 'etag|last-modified'
# 2. ETag で再検証（値全体を " 付きでそのまま返す）
ETAG=$(curl -sI http://127.0.0.1/image/1 | grep -i '^etag:' | tr -d '\r' | awk '{print $2}')
echo "etag=$ETAG"
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download}\n' -H "If-None-Match: $ETAG" http://127.0.0.1/image/1
# 304 size=0 なら当たり。本文を運んでいない
# 3. Last-Modified で再検証（値全体をそのまま返す）
LM=$(curl -sI http://127.0.0.1/image/1 | grep -i '^last-modified:' | tr -d '\r' | cut -d' ' -f2-)
echo "lm=$LM"
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download}\n' -H "If-Modified-Since: $LM" http://127.0.0.1/image/1
# 304 size=0 なら当たり
```

読み方:

- `304 size=0` → 条件付きが効いている。本文転送が消えた分が高速化の効果になる
- ずっと `200` → アプリ配信のまま（nginx の自動付与が効かない経路）か、ヘッダ値の写し間違い（`"` の脱落、`Date:` との取り違え）。4. の `curl -sI` に戻る
- `curl` で条件を付けずに 2 回叩いてもログは減らない（`curl` はキャッシュしないので正常）。「来なさ」はブラウザか次の DevTools 手順で見る

ブラウザで見る（`curl` はキャッシュしないので、来なさはここで確認する）:

1. DevTools → Network を開き、`Disable cache` のチェックを外す
2. 対象（`/image/1` か `/css/style.css`）をリロード 2 回
3. 1 回目 `200`、2 回目 `200 (memory cache)` / `200 (disk cache)` か `304` ならブラウザが覚えている
4. その間 `access.log` に行が増えない（サーバに来ていない分）

```bash
sudo cat /var/log/nginx/access.log | tail -2
# ブラウザで 2 回リロードしてからもう一度 tail。行が増えなければ来ていない
sudo cat /var/log/nginx/access.log | tail -2
```

### 観察すること

- `If-None-Match` か `If-Modified-Since` のどちらかで `304 size=0` を出した（両方出ればなおよい）
- `200` 時の `size_download` と `304` 時の `0` を 2 つの数字で言える（転送量差が高速化の根拠）
- ブラウザ 2 回目が cache / `304` で、サーバのログが増えない

## 6. 複数台では `ETag` の台間一致を確認する（1 台なら読みだけ）

配信サーバが複数台あるとき、台ごとに違う `Last-Modified` / `ETag` を返すとクライアントのキャッシュが効かない。nginx のファイル配信では、mtime が同じなら同じ `Last-Modified` が、mtime とサイズが同じなら同じ `ETag` が生成できる。同じファイルならサイズは同じはずなので、合わせるのは mtime。

1 台構成ならここは読みだけ。4.〜5. が通っていれば次へ進む。配信役（s1）が複数台ある構成の回だけ実施する（[0020-split-1.md](0020-split-1.md) の分割後）。

```bash
# 各配信台で同じファイルの mtime と応答ヘッダを比べる
stat -c '%y %s %n' /home/isucon/private_isu/webapp/public/css/style.css
curl -sI http://127.0.0.1/css/style.css | grep -i -E 'etag|last-modified'
# s1 が複数台なら各台で同じ 2 本を取る。ETag / Last-Modified が一致すること
```

ずれたときの直し方:

```bash
# 配信元から mtime 保持で配る。-a に -t が含まれるので -a でよい
rsync -av /home/isucon/private_isu/webapp/public/ isucon@${WEB_PRIV}:/home/isucon/private_isu/webapp/public/
stat -c '%y %s %n' /home/isucon/private_isu/webapp/public/css/style.css
curl -sI http://${WEB_PRIV}/css/style.css | grep -i -E 'etag|last-modified'
```

- `rsync` には `-t`（mtime 保持）を付ける。`-a` に含まれているので `-a` で足りる。`-t` 無しの転送は mtime がずれて `ETag` が割れる
- どちらか片方があれば十分なので、紛らわしい場合は `Last-Modified` だけに寄せる考え方もある。nginx では `etag off;` で無効化できる（既定は有効）。台間一致が取れないときの選択肢として覚える。両方を消すのは安易にやらない

```nginx
# ETag を切るときだけ（対象の location へ。全体に付けない）
etag off;
```

### 観察すること

- 1 台なら「台間一致は対象外」と言える（無理に rsync しない）
- 複数台なら各台の `ETag` / `Last-Modified` が一致した（`curl -sI` の 2 行で判定）
- 一致しないまま `max-age` を延ばしていない（延ばす前に mtime 同期か `etag off` を決めた）

## 7. 更新時はファイル名変更かクエリ文字列で外す

`max-age` を長くしたファイルを変更するとき、そのまま上書きすると古いキャッシュが残る。やることはどちらか:

- ファイル名を変える（`style.abc123.css`）
- クエリ文字列を変える（`style.css?v=abc123`）

```bash
# 参照側がファイル名かクエリで版を持っているか
grep -rn 'style.css\|app.js\|v=\|contenthash\|chunkhash' \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -10
grep -rn 'stylesheet\|script src' /home/isucon/private_isu/webapp/public/*.html 2>/dev/null | head -10
find /home/isucon/private_isu/webapp/public -name '*.*.*.css' -o -name '*.chunk.js' | head
```

- ビルド時にハッシュ付き名が出る構成なら、その構成のままが正解。手で `max-age` を延ばすだけでよい
- ハッシュが無い素体で長期 `max-age`（1 年など）にするときは、更新手順（名変更かクエリ付与）を決めてから延ばす。決めずに延ばさない
- `/image/` の投稿画像は追記型（新規 id が増える）なので、既存 id の上書きが無ければ長期でも安全。プロフィール画像のように上書きがある箇所だけ `max-age` を短めに残す

### 観察すること

- 長期化する対象に更新手順があるか言える（名変更 / クエリ / 追記型で安全）
- 手順が無い箇所の `max-age` は `1d` のまま（延ばさない）
- 更新後に `curl -sI` の新旧 URL で別物として返ることを確認した

## 8. 公式ベンチで判定する（点数＋転送量＋alp）

4.〜7. のうち 1 手だけ残し、他は戻した状態にして公式ベンチを 1 本回す。bench-prep は [0030-measure.md](0030-measure.md#2-計測サイクルを回す) と同じ `mv` + `reopen` / `flush-logs`。

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
echo "$(date -Iseconds)  score=  pass=  fail=  bytes=  note=expires-image-1d" >> ~/bench-notes/scores.txt
```

残す変更は `backup/s1` に積む（[0030 §2](0030-measure.md#残す変更は-backups1-に積む) の流儀）。

判定は 3 点セット。点数だけ見ない:

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
# 対象パスの Sum が落ちたか。落ちないのに 304 だけ出るなら測る場所がずれている
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download}\n' http://127.0.0.1/image/1
```

- ベンチマーカーが条件付きに対応している構成では点数が動く。対応しない構成では点数が動かなくてもよい。そのときは 5. の `304 size=0` とログの来なさを本体の判定にする（[0040 §6](0040-cache.md#6-cache-control-を付ける前後で挙動を比べる) と同じ）
- `proxy_cache`（[0040 §5](0040-cache.md#5-nginx-で-1-箇所だけキャッシュして-hit-率で評価する)）とは別々にやる。同時に入れて比べない

戻すときは付けた location の `expires` 1 行だけ消して reload する:

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -sI http://127.0.0.1/image/1 | grep -i -E 'cache-control|expires' || echo 'reverted (no cache headers)'
```

### 観察すること

- `scores.txt` に 1 行残した（`fail` が 0 の回だけ比べた）
- 対象パスの alp Sum が落ちたか、落ちないなら画像層は詰まっていない（DB / アプリの仕事に戻る）
- 点数が動かない回は `304` とログの来なさを根拠にした（点数だけで捨てない）

## 9. トラブルシュート

### `Cache-Control` が出ない

- 対象 location を間違えている（`curl -sI` のパスと `location` を見比べる）。全体に付けず 1 箇所に絞ったか
- アプリ配信のまま（DB から読んで返す経路）。nginx の自動付与はファイル配信だけ。3. に戻る
- `sudo nginx -t && sudo systemctl reload nginx` を忘れていないか

### ずっと `200` で `304` にならない

- ヘッダ値の写し間違いが一番多い。`ETag` は `"` 付き全体、`Last-Modified` は曜日から GMT まで全体を写す。`Date:` と取り違えていないか
- アプリ配信のまま条件付きを期待していないか。`ETag` / `Last-Modified` 自体が出ていないなら 4. に戻る
- 条件を付けずに `curl` を 2 回叩いてもログが減らないのは正常（`curl` はキャッシュしない）。`304` は条件付きヘッダ付きのときだけ返る

### ブラウザ 2 回目も毎回 `200` でログが増える

- DevTools の `Disable cache` が入ったままか
- `private` / `no-store` / `max-age=0` が付いていないか（`curl -sI` で確認）
- 対象がログイン後の私用ページになっていないか。匿名 GET に戻す

### `fail` や他人表示が出る

- `public` を私用ページに付けている。対象を匿名 GET（`/image`、静的）だけに戻す。`max-age` を延ばさない
- `proxy_cache` の HIT が消えたら `Cache-Control: private` / `no-store` との干渉を疑う。`proxy_ignore_headers` を足す前に、対象の狭さとヘッダの中身を見る

### 複数台で `ETag` が割れる

- `rsync` に `-t`（または `-a`）が付いているか。mtime がずれると `ETag` が割れる
- `stat -c '%y %s'` が各台で一致するか。サイズ違いはファイル自体が違う
- 一致が取れないときは `etag off;` で `Last-Modified` に寄せる選択肢もある（対象 location だけ）
