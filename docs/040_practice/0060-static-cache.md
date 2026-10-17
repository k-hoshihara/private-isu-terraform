# 0060 静的・画像を条件付きリクエストで速くする

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）は、手元の private-isu-terraform リポジトリで実行します。

- 「達人が教える Web パフォーマンスチューニング」8-5「HTTPヘッダーを活用してクライアント側にキャッシュさせる」を扱います。
- 画像・静的ファイルの高速表示に絞って、数字で確認します。

- `expires` で `Cache-Control: max-age` を返します（「この応答はどれだけの時間保存してよい」という保持期間の指定です）
- `Last-Modified` / `ETag`（ファイルの版を示す目印）による HTTP 条件付きリクエスト（前回と同じ物なら本文を送り直さないやり取り）で `304 NOT MODIFIED` を返します（転送量の削減です）
- 複数台では `ETag` の台間一致を確認します（`rsync -a` でファイルの更新日時（mtime）を揃えるか `etag off` です）
- 更新時はファイル名変更かクエリ文字列で古いキャッシュを外します

- ゴールは、次を言えることです。
- `expires` 付与の前後で、`curl -sI` の `Cache-Control` と `304`（`size=0`）、転送量差、`access.log` の行数増加の有無、公式ベンチの点数。
- 「付けた」だけでは、終わりません。

- [0050-timeout.md](0050-timeout.md) の続きです。
- [0030-cache.md](0030-cache.md) §6 までやった状態なら、先に戻してから入ります。
- 戻し方は、付けた 1 行だけ消して restart / reload です（[0030-cache.md](0030-cache.md) の「Cache-Control を付ける前後で挙動を比べる」 の戻し方です）。
- 未実施なら、§1〜§2 の「付ける前」から入ります。

- 順番: 対象を決める（1.）→ 付ける前（2.）→ 静的配信に寄せる（3.）→ `expires`（4.）→ `304` 再現（5.）→ 台間一致（6.、1 台なら読みだけ）→ 更新時の外し方（7.）→ ベンチ判定（8.）。
- 1 つずつ進めます。
- `fail`（エラーの件数）が 0 でない点数は、比べません。
- 出口は [0070-infra-params.md](0070-infra-params.md) です。

- 点数は、`~/bench-notes/scores.txt` に 1 行書きます。転送量の目安も一緒に書きます。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  bytes=  note=static-baseline" >> ~/bench-notes/scores.txt
```

| 回 | score | bytes（目安） | メモ |
| --- | --- | --- | --- |
| ベースライン | 22468 | image 79316 / css 1549 | 0050 §8 の状態。静的はアプリ経由、`Cache-Control: no-cache`（Python の ETag） |
| 1 つ目 | 28660 | 304 size=0（5724 件） | 静的 4 パスに `expires 1h`＋nginx 直接配信。fail=0、+6192 |

<a id="1-対象を決めるget-の読み物だけ"></a>

## 1. 対象を決める（GET の読み物だけ）

- 狙うのは、更新されにくく何度も参照される GET だけです。
- POST と、ログイン後の個人用ページは外します。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
# /image が Sum 上位なら筆頭候補。/css /js /img があれば次点
find /home/isucon/private_isu/webapp/public -type f | head -20
ls -l /home/isucon/private_isu/webapp/public/
```

- `public/` 配下（`css/style.css` など）は最初からファイルです。対象にしやすいです
- `/image/<id>` の素体（配布された元の状態）は MySQL の `posts.imgdata`（ファイルではありません）。ファイル化済みならこの演習の対象です。未済なら [0030-cache.md](0030-cache.md) の「nginx で 1 箇所だけキャッシュして HIT 率で評価する」 を先に済ませるか、今回は `public/` 静的だけにします
- 公開タイムラインの GET は認証不要部分だけです。迷ったら `/image` の匿名 GET か `public/` 静的のどちらか 1 つに絞ります

### 観察すること

- 自分の 1 つが「`/image/`」「`public/` 静的」のどちらかを言えます
- alp の Sum 上位と対象が一致しています（上位に無いパスを触りません）
- `/image` が未ファイル化なら対象を `public/` 静的に変えました（無理に広げません）

## 2. 付ける前: ヘッダが無くて毎回 200 であることを確認する

```bash
curl -sI http://127.0.0.1/image/1.jpg | grep -i -E 'cache-control|etag|last-modified|expires' || echo 'no cache headers (before)'
curl -sI http://127.0.0.1/css/style.css | grep -i -E 'cache-control|etag|last-modified|expires' || echo 'no cache headers (before)'
```

- `curl` は、既定ではキャッシュしません。
- 2 回叩いた分だけ行が増えます（= 毎回サーバまで来ています）。

```bash
sudo cat /var/log/nginx/access.log | tail -2
curl -s -o /dev/null http://127.0.0.1/image/1.jpg
curl -s -o /dev/null http://127.0.0.1/image/1.jpg
sudo cat /var/log/nginx/access.log | tail -2
# 2 回叩いた分だけ行が増える = 毎回サーバまで来て毎回 200 で本文を返している
```

- 転送量の目安を取ります（`304` 応答後の比較原点にします）。
- `size_download` が、本文サイズを表します。

```bash
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download} time=%{time_total}s\n' http://127.0.0.1/image/1.jpg
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download} time=%{time_total}s\n' http://127.0.0.1/css/style.css
```

### 観察すること

- 対象パスの応答ヘッダに `Cache-Control` / `ETag` / `Last-Modified` がありません（あるなら既設。§4 へ進みます）
- `curl` 2 回でログが 2 行増えます（毎回サーバまで来ています）
- `size_download` を控えました（`304` 応答時の 0 バイトと比較するためです）

<a id="3-nginx-でファイルを配信する形に寄せる条件付きの土台"></a>

## 3. nginx でファイルを配信する形に寄せる（条件付きの土台）

- 条件付きリクエスト（§5）は、nginx がファイルを配信する場合は、自動で付きます。
- アプリ（Flask）が DB から読んで返すときは、自動では付きません。

- 今の配信経路を見ます。

```bash
sudo nginx -t
sudo nginx -T 2>/dev/null | grep -E 'proxy_pass|root|location' | head -30
sudo nginx -T 2>/dev/null | grep -E 'proxy_pass|location /image|location /css' | head -20
```

- `location /css/ { }` のように空で `root` 配下を返す形なら、そのままファイル配信になっています。この節（3.）は何もしないで §4 へ進みます
- `/image/` が `proxy_pass` のまま（DB 配信）なら、ファイル化済みのときだけ `try_files` を前に足します。未ファイル化なら付けません（重いアプリへ proxy のままです。対象を `public/` 静的に変えます）

- 足すときは、`backup/s1/etc/nginx/sites-enabled/isucon.conf` に書きます（§4 と同じ流し方です）。
- ファイル化済みで、素体に出力が無いときだけ追加します。

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

- `@app` は、ファイルが無いときのフォールバック（代替の応答先）です。[0040-http-client.md](0040-http-client.md) で足した upstream `app` へ向けます。
- [0030-cache.md](0030-cache.md) §5 で `location /image/` に `proxy_cache` の行を入れていれば、この location に移します。

- ファイル化したら、EBS 上に置きます（`/home/isucon/...`）。
- `tmpfs`、`/dev/shm`、`/tmp` には置きません（[0030-cache.md](0030-cache.md) の「投稿の本体データは MySQL と EBS」 の線引きです）。
- DB の `imgdata` は、消しません。投稿の本体データは MySQL のまま残します。

```bash
sudo chmod o+x /home/isucon /home/isucon/private_isu /home/isucon/private_isu/webapp
sudo chmod -R a+rX /home/isucon/private_isu/webapp/public
curl -fsS -o /dev/null -m 5 http://127.0.0.1/css/style.css
```

### 観察すること

- 対象がファイル配信かアプリ配信か言えます（`location` の中身で判定します）
- DB 配信のまま無理に `expires` を付けていません（付けるのはファイル配信の location だけです）
- 権限 403 が出ません（`www-data` が読めます）

<a id="4-expires-で保持期間を付ける1-箇所だけ"></a>

## 4. `expires` で保持期間を付ける（1 箇所だけ）

- `backup/s1/etc/nginx/sites-enabled/isucon.conf` の対象 location に、`expires` 1 行を足します。
- push → サーバーで pull → cp で反映 → reload します。
- 全体には、付けません。

- `/image` を対象にする場合（ファイル配信の location）は、次のとおりです。

```nginx
location /image/ {
  root /home/isucon/private_isu/webapp/public/;
  expires 1d;
}
```

- `public/` 静的なら、対象を `/css/` などにします（どちらか 1 つです）。

```nginx
location /css/ {
  expires 1d;
}
```

- `expires 1d;` で、`Cache-Control: max-age=86400` と `Expires` が返ります。
- 最初は `1d` から入れます。いきなり 1 年にはしません。
- `public/` 静的で試すなら、対象を `/css/` などに変えます（どちらか 1 つです）。

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
- 個人用ページに `public`（誰のブラウザでも保存してよい）を付けると他人に見えます。迷ったら匿名 GET だけにします
- `Cache-Control: private` / `no-store` を付けると `proxy_cache` が効かなくなることがあります（[0030-cache.md](0030-cache.md) の「Cache-Control を付ける前後で挙動を比べる」 と同じです）。対象の狭さとヘッダの中身を先に見ます

### 観察すること

- `curl -sI` で `Cache-Control: max-age=86400` が見えます
- `ETag` / `Last-Modified` のどちらか（または両方）が見えます。どちらも無ければ対象 location を間違えているか、アプリ配信のままです
- `fail` が出ません（出たら対象を狭めます。`max-age` を延ばしません）

#### 出力例

2026-10-13 の実測（`c7a.large` 1 台 / `ap-northeast-1` / 単一構成 / `nproc=2` / メモリ 3.7Gi / avail 2.7Gi）。

§1 の alp（Sum 上位）と対象の対応（変更前の access.log、約 2.1 万リクエスト）:

| COUNT | URI | SUM | AVG | SUM(BODY) |
| --- | --- | --- | --- | --- |
| 10733 | `/image/[0-9]+` | 96.188 | 0.009 | 3676742681 |
| 1558 | `/` | 69.253 | 0.044 | 4539799 |
| 768 | `/favicon.ico` | 31.261 | 0.041 | 33024 |
| 769 | `/css/style.css` | 29.798 | 0.039 | 1191181 |
| 769 | `/js/main.js` | 29.044 | 0.038 | 1402656 |
| 768 | `/js/timeago.min.js` | 28.834 | 0.038 | 1470720 |

- `/image` は Sum 上位です。ただし、画像が MySQL の `posts.imgdata` のままで、ファイル化していません。
- [§1](#1-対象を決めるget-の読み物だけ) の判断どおり、対象を `public/` 静的（`/css/` `/js/` `/img/` `/favicon.ico`）に絞りました。

§2 の「付ける前」の原点:

```text
/css/style.css     200 size=1549  Content-Disposition: inline; filename=style.css（アプリ経由）
                   Cache-Control: no-cache / ETag: "1779606055.2279437-1549-317723678"（Python 形式）
/js/main.js        200 size=1824  同上（アプリ経由）
/image/1.jpg       200 size=79316 X-Cache: EXPIRED（proxy_cache 経由）
curl 2 回でログ増分 = 2（毎回サーバまで来ている）
```

- `location /` が `proxy_pass` のみで、静的も gunicorn 経由でした（§3）。
- `location /css/ /js/ /img/` と `location = /favicon.ico` を足して、nginx 直接配信に寄せました（`root` はサーバ共通値を継承）。
- そして、`expires 1h` を付けました。

§4 の `expires 1h` 付与後（4 パスとも）:

```text
/css/style.css        Cache-Control: max-age=3600 / Expires: +1h
                      Last-Modified: Sun, 24 May 2026 07:00:55 GMT
                      ETag: "6a12a227-60d"（nginx 形式 mtime_hex-size_hex に変化）
/js/main.js           Cache-Control: max-age=3600 / ETag: "6a12a227-720"
/img/ajax-loader.gif  Cache-Control: max-age=3600 / ETag: "6a12a227-2a1"
/favicon.ico          Cache-Control: max-age=3600 / ETag: "6a12a227-2b"
```

§5 の `304` 再現（`curl` で実測）:

| パス | If-None-Match | If-Modified-Since | 通常 GET |
| --- | --- | --- | --- |
| `/css/style.css` | 304 size=0 | 304 size=0 | 200 size=1549 |
| `/js/main.js` | 304 size=0 | —（ETag 側で確認） | 200 size=1824 |

- §6 の ETag 台間一致: 1 台構成のため、読みだけです。
- `stat` は `2026-05-24 16:00:55 +0900 / 1549 bytes` で、応答 ETag と整合しています。

- §7 の更新時の外し方: `grep` で、アプリ内の版管理（`v=` / contenthash 等）は見つかりません。`public/*.html` も、存在しません（素体はテンプレート参照）。
- `/image/` の投稿画像は、追記型（新規 id 増加）です。長期でも安全です。
- 今回は、`max-age` を `1h` のまま据え置きました。長期化しませんでした。

§8 のベンチ: `{"pass":true,"score":28660,"success":25760,"fail":0,"messages":[]}`（[0050-timeout.md](0050-timeout.md) の 22468 から +6192、fail=0）。

ベンチ中の静的 4 パス（alp、条件付きが効いている証拠）:

```text
1432 GET /js/timeago.min.js   1 回 200 + 1431 回 304（3XX）  SUM(BODY)=1915（初回分のみ）
1432 GET /js/main.js           1 回 200 + 1431 回 304          SUM(BODY)=1824
1432 GET /css/style.css        1 回 200 + 1431 回 304          SUM(BODY)=1549
1432 GET /favicon.ico          1 回 200 + 1431 回 304          SUM(BODY)=43
```

- 304 応答は、計 5724 件でした（`grep -c 'status:304'` 一致）。割合は 99.93% です。
- 本文転送は、初回 1 回分だけです。
- `access.log` の cache 分布は `HIT 11173 / BYPASS 1524 / EXPIRED 565 / MISS 892` です。`/image` の `proxy_cache`（[0030-cache.md §5](0030-cache.md#5-nginx-で-1-箇所だけキャッシュして-hit-率で評価する)）も、継続しています。

| 回 | score | fail | メモ |
| --- | --- | --- | --- |
| 0050 §8（変更なしで判定） | 22468 | 0 | |
| **0060 静的 expires 1h＋直接配信** | **28660** | **0** | **+6192（2026-10-13、`c7a.large` 1 台 / 単一構成 / `nproc=2`）** |

- **点数が大きく動きました（+6192）。**
- 単一構成では、静的 4 パスが `proxy_pass` でアプリ（gunicorn、worker 1）を経由していました。
- nginx の直接配信＋`expires` に寄せると、gunicorn は動的処理に専念できます。
- 判定は、`Cache-Control` の出現・304 の件数（5724 件）・点数で行います。

#### この節でつまずいた点

1. **§5 の例の日付が `304` になりません**

   [0030-cache.md](0030-cache.md) §6 には次のようにあります。

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -H 'If-Modified-Since: Wed, 01 Jan 2025 00:00:00 GMT' http://127.0.0.1/image/1.jpg
   # 304 が返れば再検証 OK。ずっと 200 ならアプリが条件付き GET に対応していない
   ```

   - 2025-01-01 は、AMI のファイル mtime（`Sun, 24 May 2026 07:00:55 GMT`）より前です。
   - **常に 200 が返ります。**実測でも、同じでした。

   ```text
   If-Modified-Since: 2025-01-01（手順書の例）  -> 200
   If-Modified-Since: 実際の mtime（2026-05-24） -> 304
   ```

   - [§5](#5-条件付きリクエストで-304-を再現する転送量の差) の手順どおり、実際の `Last-Modified` を先に取得します。
   - 固定日付の例は、条件付き GET の対応有無を判断する根拠にはなりません。

2. **§4 の `location` の形が 1 つだけ変わると `expires` が漏れます**

   ```nginx
   location /css/ { }              # 元の形
   ```

   - この章 §4 の `expires` をあとから消すと、`location /css/ { }` が改行付きの形に変わります。`{ }` のまま書いたパターンに、一致しなくなります。
   - 実測では、**`/css/` だけ `Cache-Control` が付かず、残り 3 パスには付いた**という状態になりました。

   - 4 パスを 1 つずつ `curl -sI` で確認しておけば、検出できます。
   - **「3/4 でしか入っていない」なら、テンプレートがズレています。**
   - 戻す時は、`{ }` を保ったまま書くか、ブロックごと書き換えます。

<a id="5-条件付きリクエストで-304-を再現する転送量の差"></a>

## 5. 条件付きリクエストで `304` を再現する（転送量の差）

- §4 までやってヘッダが出たら、ブラウザの 2 回目を `curl` で再現します。
- 保存した `Last-Modified` / `ETag` をリクエストに付けて送ります。
- 変化が無ければ、`304`（本文空）が返ります。

```bash
# 1. 値を保存する
curl -sI http://127.0.0.1/image/1.jpg | grep -i -E 'etag|last-modified'
# 2. ETag で再検証（値全体を " 付きでそのまま返す）
ETAG=$(curl -sI http://127.0.0.1/image/1.jpg | grep -i '^etag:' | tr -d '\r' | awk '{print $2}')
echo "etag=$ETAG"
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download}\n' -H "If-None-Match: $ETAG" http://127.0.0.1/image/1.jpg
# 304 size=0 なら成功。本文を運んでいない
# 3. Last-Modified で再検証（値全体をそのまま返す）
LM=$(curl -sI http://127.0.0.1/image/1.jpg | grep -i '^last-modified:' | tr -d '\r' | cut -d' ' -f2-)
echo "lm=$LM"
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download}\n' -H "If-Modified-Since: $LM" http://127.0.0.1/image/1.jpg
# 304 size=0 なら成功
```

読み方:

- `304 size=0` → 条件付きが効いています。本文転送が消えた分が高速化の効果になります
- ずっと `200` → アプリ配信のまま（nginx の自動付与が効かない経路）か、ヘッダ値の写し間違いです（`"` の脱落、`Date:` との取り違え）。§4 の `curl -sI` に戻ります
- `curl` で条件を付けずに 2 回叩いてもログは減りません（`curl` はキャッシュしないので正常です）。サーバへの到達の有無はブラウザの DevTools（開発者ツール）手順で確認します

- `curl` はキャッシュしません。サーバへの到達の有無は、ブラウザで確認します。

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

- 配信サーバが複数台あるとき、台ごとに違う `Last-Modified` / `ETag` を返すと、クライアントのキャッシュが効きません。
- 合わせるのは、mtime（ファイルの更新日時）です。

- 1 台構成なら、ここは読みだけです。§4〜§5 が通っていれば、次へ進みます。
- 配信役（s1）が複数台ある構成の回だけ、実施します（[0110-split-1.md](0110-split-1.md) の分割後です）。

```bash
# 各配信台で同じファイルの mtime と応答ヘッダを比べる
stat -c '%y %s %n' /home/isucon/private_isu/webapp/public/css/style.css
curl -sI http://127.0.0.1/css/style.css | grep -i -E 'etag|last-modified'
# s1 が複数台なら各台で同じ 2 本を取る。ETag / Last-Modified が一致すること
```

- ずれたときの直し方は、次のとおりです（配信元で打ち、`WEB_PRIV` の配信先へ配ります）。

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

- ETag を切るときだけ、`backup/s1/etc/nginx/sites-enabled/isucon.conf` の対象 location に足します。
- 全体には、付けません。

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

<a id="7-更新時はファイル名変更かクエリ文字列で外す"></a>

## 7. 更新時はファイル名変更かクエリ文字列で外す

- `max-age` を長くしたファイルを変更するとき、そのまま上書きすると、古いキャッシュが残ります。
- やることは、どちらかです。

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

## 8. 公式ベンチで判定する（点数＋転送量＋alp）

- [§4](#4-expires-で保持期間を付ける1-箇所だけ)〜[§7](#7-更新時はファイル名変更かクエリ文字列で外す) のうち、1 つだけ残します。他は、戻した状態にします。
- その状態で、公式ベンチを 1 本回します。
- bench-prep は [0020-measure.md](0020-measure.md) と同じです（nginx のログは `mv` + `reopen`、slow log は `flush-logs`。同じ台です）。

- 先に slow log を回転してから、ベンチを実行します。

```bash
# ベンチ前（slow log の回転）
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysql -e 'FLUSH SLOW LOGS'
```

```bash
# 同じ台で打つ
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
fi
sudo nginx -s reopen
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  bytes=  note=expires-image-1d" >> ~/bench-notes/scores.txt
```

実測サンプル: `{"pass":true,"score":28660,"success":25760,"fail":0,"messages":[]}`（[0050-timeout.md](0050-timeout.md) の 22468 から +6192、`fail=0`）。

- 残す変更は、`backup/s1` に積みます（[0020-measure.md](0020-measure.md) の「残す変更は backup/s1 に積む」の流儀）。

- 判定は、3 点セットです。点数だけを見ません。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
# 対象パスの Sum が落ちたか。落ちないのに 304 だけ出るなら測る場所がずれている
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download}\n' http://127.0.0.1/image/1.jpg
```

- ベンチマーカーが条件付きに対応している構成では点数が動きます。対応しない構成では点数が動かなくてもよいので、そのときは §5 の `304 size=0` とログの到達の有無を判定にします
- `proxy_cache`（[0030-cache.md](0030-cache.md) の「nginx で 1 箇所だけキャッシュして HIT 率で評価する」）とは別々にやります。同時に入れて比べません

- 戻すときは、作業機で付けた `expires` 1 行を消します。

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

#### 実行例：

- 2026-10-13 の実測（`c7a.large` 1 台 / `ap-northeast-1` / 単一構成 / `nproc=2` / メモリ 3.7Gi）。
- 条件（日付、c7a.large 1 台、ap-northeast-1、nproc=2、メモリ 3.7Gi、単一構成）を明記します。
- 静的 4 パス（`/css/` `/js/` `/img/` `/favicon.ico`）を、nginx 直接配信に寄せました。
- `expires 1h` を付けました。
- ベンチ: `{"pass":true,"score":28660,"success":25760,"fail":0,"messages":[]}`（[0050-timeout.md](0050-timeout.md) の 22468 から +6192、fail=0）。

ヘッダの変化:

```text
# 変更前（アプリ経由）
$ curl -sI http://127.0.0.1/css/style.css
HTTP/1.1 200 OK
Content-Type: text/css; charset=utf-8
Content-Length: 1549
Content-Disposition: inline; filename=style.css
Last-Modified: Sun, 24 May 2026 07:00:55 GMT
Cache-Control: no-cache
ETag: "1779606055.2279437-1549-317723678"

# 変更後（nginx 直接配信＋expires 1h）
$ curl -sI http://127.0.0.1/css/style.css
HTTP/1.1 200 OK
Content-Type: text/css
Content-Length: 1549
Last-Modified: Sun, 24 May 2026 07:00:55 GMT
ETag: "6a12a227-60d"
Expires: Tue, 13 Oct 2026 02:51:28 GMT
Cache-Control: max-age=3600
Accept-Ranges: bytes
```

- 4 パスとも、`Cache-Control: max-age=3600` が付きました。
- `ETag` は、Python 形式から nginx 形式（`mtime_hex-size_hex`）に変わりました。
- `Content-Disposition` は、消えました。

条件付きリクエストの再現:

```text
Last-Modified: Sun, 24 May 2026 07:00:55 GMT
If-Modified-Since -> http=304 size=0
ETag: "6a12a227-60d"
If-None-Match -> http=304 size=0
```

ベンチ:

```text
{"pass":true,"score":28660,"success":25760,"fail":0,"messages":[]}
```

- ベンチ中に、**5724 件の `status:304`**（`grep -c` で実測）が出ました。
- ベンチマーカーは、条件付きリクエストを使っています。

| 状態 | score | fail | 備考 |
| --- | --- | --- | --- |
| 0050 §8（変更なしで判定） | 22468 | 0 | |
| **0060 静的 expires 1h＋直接配信** | **28660** | **0** | **+6192**（2026-10-13、`c7a.large` 1 台 / 単一構成 / `nproc=2`） |

- **点数が大きく動きました（+6192）。**
- 単一構成では、静的ファイル（`/css/` `/js/` `/img/` `/favicon.ico`）が `proxy_pass` でアプリ（gunicorn）を経由していました。
- nginx の直接配信＋`expires` に寄せると、gunicorn は動的処理に専念できます。
- `/css/style.css` は、`Cache-Control: max-age=3600` と `Expires` を返します。条件付きリクエストは、`304`（0 バイト）になりました。
- 判定は、`Cache-Control` の出現・304 の件数（5724 件）・点数で行います。

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
- 対象がログイン後の個人用ページになっていませんか。匿名 GET に戻します

### `fail` や他人表示が出る

- `public` を個人用ページに付けています。対象を匿名 GET（`/image`、静的）だけに戻します。`max-age` を延ばしません
- `proxy_cache` の HIT が消えたら `Cache-Control: private` / `no-store` との干渉を疑います。`proxy_ignore_headers` を足す前に、対象の狭さとヘッダの中身を見ます

### 複数台で `ETag` が割れる

- `rsync` に `-t`（または `-a`）が付いていますか。mtime がずれると `ETag` が割れます
- `stat -c '%y %s'` が各台で一致しますか。サイズ違いはファイル自体が違います
- 一致が取れないときは `etag off;` で `Last-Modified` に寄せる選択肢もあります（対象 location だけです）
