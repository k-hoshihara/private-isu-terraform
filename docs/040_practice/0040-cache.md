# 0040 キャッシュのヒット率を見る

[0030-measure.md](0030-measure.md) の続き。同じ 1 台でキャッシュを 1 手ずつ入れ、**何%当たったか**を数字で言うのがゴール。「入れた」だけでは終わらない。

順番: 本体データの線引き → 測り方の固定（2. memcached / 3. nginx）→ アプリ層 1 本 → nginx 1 本 → Cache-Control（6.）。まとめて入れない。1 手ごとに公式ベンチ。`fail` が 0 でない点数は比べない。出口は [0050-http-client.md](0050-http-client.md)。条件付きの深掘りは [0070-static-cache.md](0070-static-cache.md)。

点数は `~/bench-notes/scores.txt` に 1 行。ヒット率も一緒に書く:

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  memc_hit=  nginx_hit=  note=cache-baseline" >> ~/bench-notes/scores.txt
```

| 回 | score | memc_hit | nginx_hit | メモ |
| --- | --- | --- | --- | --- |
| ベースライン |  | - | - | 0030 までの状態。キャッシュ追加前 |
| 1 手目 |  | 例: 82% | - | 例: timeline を memcached、TTL=60 |

## 1. 投稿の本体データは MySQL と EBS

キャッシュを入れる前に、投稿の本体データと複製の線を引く。投稿の本体データを消して複製だけにすると失格に近い。

- 投稿の本体データ（`posts` / `users` / `comments`）は MySQL（InnoDB）にある。投稿の唯一のコピーを memcached にしない
- `/image` をファイルにしたら **EBS 上**（`/home/isucon/...`）。`tmpfs`、`/dev/shm`、`/tmp` にしない
- nginx の `proxy_cache` だけを投稿の本体データにしない
- ベンチ中に書いたデータは再起動後も読めること。ISUCON14 ではメモリ上のキャッシュが原因で上位（トップ 8）のチームが失格になった

再起動試験は [0020-split-3.md](0020-split-3.md#10-ベンチ中の書き込みは再起動後も残す) と同じ。1 台でもやる:

```bash
mysql -uisuconp -pisuconp isuconp -e "SELECT COUNT(*) FROM posts;"
# マーカーを 1 行入れてから sudo reboot、もう一度 COUNT とマーカー
# キャッシュを入れたあとも同じ。消えたら投稿の本体データをキャッシュだけにしている
```

### 観察すること

- 投稿の本体データが MySQL / EBS に残っている。再起動試験で消えない
- 点差がその 1 手のコスト。まとめて入れた点数は比べない

## 2. memcached: `stats` を読む

アプリ層の判定材料は `stats` の出力だ。`echo stats` を投げて全文を見る。見るのは 6 行。

```bash
systemctl is-active memcached.service isu-python.service
echo stats | nc -w 1 127.0.0.1 11211
```

出力は `STAT` で始まる行が並ぶ。その中で見る行はこれだけ:

| 行 | 意味 | 目安 |
| --- | --- | --- |
| `STAT get_hits` | 当たりの累積 | `hits / (hits + misses)` がヒット率 |
| `STAT get_misses` | 外れの累積 | 同上 |
| `STAT curr_items` | 入っているキー数 | 0 のままならアプリが書いていない |
| `STAT evictions` | メモリ不足で追い出された数 | 増え続けるならキーか TTL を減らす |
| `STAT bytes` / `STAT limit_maxbytes` | 使用量 / 上限（既定 `-m 64`） | 上限に張り付く前に evictions が動く |

ヒット率は手で割る。例: `hits=8231`、`misses=1823` なら `8231 / (8231 + 1823) = 81.9%`。

アプリがどこを見ているか（アドレス違いのミスが一番多い）:

```bash
cat /home/isucon/env.sh
# ISUCONP_MEMCACHED_ADDRESS が無ければ既定は 127.0.0.1:11211（分割後は s2。ここでは 1 台なので localhost）
```

gunicorn はワーカーが別プロセス。セッションや読み物をプロセスメモリ（dict、global）に置くとワーカー間で見えない。**アプリ層の共有は memcached** にする。プロセス内キャッシュはこのドリルでは使わない。

累積値だけ見ても「今回のベンチで当たったか」は分からない。ベンチ前後で保存して見比べる。やり方は 4. のベンチでやる。

カウンタを 0 から見たいときだけ（データは消さない）:

```bash
printf 'stats reset\nquit\n' | nc -w 1 127.0.0.1 11211
# キャッシュの中身を消すときは別物: printf 'flush_all\nquit\n' | nc -w 1 127.0.0.1 11211
```

`stats reset` はカウンタ、`flush_all` は中身。混ぜない。ベンチ前のリセットを忘れても、前後差分があればヒット率は出せる。

### 観察すること

- `curr_items` が 0 でない。0 のままなら get/set の配線ミス
- slp で狙った 1 本の Count が落ちた（ヒット率と DB 側の両方で確認）

## 3. nginx: HIT / MISS を読む

1 リクエストの成否は `curl -sI` の `X-Cache` 行で見る。全体の傾向は `access.log` の `cache:` 列で見る。

### まず今の `cache` 列を見る

このリポジトリの LTSV は `cache` 列を持っている（`etc/nginx/ltsv.conf`）。ログを数行見て、`cache:` の後ろを読む。

```bash
head -3 /var/log/nginx/access.log
# 行の中の cache:HIT / cache:MISS / cache:- を見る
```

値の意味:

- `cache:HIT` → キャッシュから返した
- `cache:MISS` → アプリまで行って保存した
- `cache:-` → キャッシュの対象外。アプリが `X-Cache` を返していないか、nginx が書いていない。どちらか切り分ける。次の X-Cache 配線へ進む

### 1 リクエストの HIT / MISS を見える化（X-Cache）

一番小さい出し方は、nginx 側で `add_header X-Cache $upstream_cache_status always` を足すこと（site conf の対象 location へ。全体に足さない）:

```nginx
# nginx 側で足す例（site conf の location へ）
add_header X-Cache $upstream_cache_status always;
```

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -sI http://127.0.0.1/image/1
curl -sI http://127.0.0.1/image/1
```

応答ヘッダの中の `X-Cache:` 行を読む。1 回目 `MISS` → 2 回目 `HIT` なら配線は合っている。ずっと `MISS` ならキーが毎回違う（クエリ、Cookie）。ずっと `BYPASS` なら `proxy_no_cache` / `bypass` が広すぎる。

`$upstream_cache_status` の主な値:

| 値 | 意味 | 次の一手 |
| --- | --- | --- |
| `HIT` | キャッシュから返した | このままベンチで全体の傾向を見る |
| `MISS` | アプリまで行って保存した | 2 回目も MISS ならキーを疑う |
| `BYPASS` | 条件でキャッシュを避けた | `proxy_no_cache` / `bypass` が広すぎないか |
| `EXPIRED` | 期限切れで取り直した | TTL を延ばす前に破棄条件を見る |

注意: `etc/nginx/ltsv.conf` の `cache` は `$upstream_http_x_cache`（上流アプリの返すヘッダ）を見ている。上の `add_header` はクライアントへの応答ヘッダなので、ログの `cache:` に載らないことがある。そのときは `curl -sI` の `X-Cache` を正にする。ログでも毎回数えたいなら `log_format` の `cache` を `$upstream_cache_status` に変える。

### ベンチ後のログの見方

ベンチ後の `access.log` を数行見て、`cache:HIT` が出ていることを確認する。全体の判定は HIT 数ではなく alp の Sum と点数でやる（5. のベンチでやる）。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH"
# /image が Sum 上位のまま HIT が増えない → nginx の前で落ちていない。対象かキーを見直す
```

### 観察すること

- 2 回目の `curl -sI` の `X-Cache` が HIT
- `/image` の alp Sum が落ちた。落ちないのに HIT だけ増えたら測る場所がずれている

## 4. アプリ層 memcached に 1 本入れてヒット率で評価する

2. の測り方を使って、ホットな読み 1 本だけをキャッシュに寄せる。

順番は 1 クエリ選ぶ → get/set → 破棄 → ベンチ → ヒット率判定 → 戻せること、の順。

### 1 つ選ぶ

alp の **Count も Sum も大きい** パスに紐づく SQL を 1 本だけ選ぶ:

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH"
sudo slp my --file /var/log/mysql/mysql-slow.log
# Count が大きく、examined/sent が数倍以内のものを 1 本。タイムラインやユーザー参照になりやすい
# examined >> sent のままなら先にインデックス（[0100-sql-tuning.md](0100-sql-tuning.md#3-インデックスを1本貼る)）
```

コードの場所は `grep` で探す。関数名は決め打ちしない:

```bash
grep -rn 'def .*timeline\|def .*post\|SELECT.*FROM posts\|SELECT.*FROM users' \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
```

### get/set を足す

方針（コピペ用の完成品は置かない。自分の 1 本に当てる）:

1. キーに引数を含める（例: `timeline:<page>`、`user:<id>`）。引数なしの固定キーは混ざる
2. `get` してあれば返す。無ければ DB から取って `set`（TTL 付き）
3. 書き込み経路（投稿・コメント・プロフィール更新）でそのキーを `delete` する。TTL 任せだけにしない

TTL の目安: まず 30〜60 秒。長くすれば当たるが、ベンチの書き込みとずれて `fail` になりやすい。短くすれば安全だが効かない。ベンチで振る。

```bash
# 変える前のバックアップ（サーバ上で直すとき）
cp -a /home/isucon/private_isu/webapp/python ~/kit-backup/app.before-cache 2>/dev/null || \
  mkdir -p ~/kit-backup && cp -a /home/isucon/private_isu/webapp/python ~/kit-backup/app.before-cache
```

```bash
sudo systemctl restart isu-python.service
systemctl is-active isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
sudo journalctl -u isu-python.service -n 40 --no-pager
```

ベンチ前後でヒット率を判定する。`stats` は累積値なので、ベンチ前後で保存して見比べる:

```bash
# 1. ベンチ前
echo stats | nc -w 1 127.0.0.1 11211 | tee /tmp/memc-before.txt
# 2. ログを回してから公式ベンチ 1 本
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
# 3. ベンチ後
echo stats | nc -w 1 127.0.0.1 11211 | tee /tmp/memc-after.txt
diff -u /tmp/memc-before.txt /tmp/memc-after.txt || true
echo "$(date -Iseconds)  score=  pass=  fail=  memc_hit=  note=memcached-timeline-ttl60" >> ~/bench-notes/scores.txt
```

残す変更は `backup/s1` に積む（[0030 §2](0030-measure.md#残す変更は-backups1-に積む) の流儀）。

`get_hits` と `get_misses` の増え方を読む（2. の表の行を見る）:

- `get_hits` だけ増えた → 当たっている。そのまま TTL を振る
- `get_misses` だけ増えた → 書いていないかキーが毎回違う（引数なしの固定キー、再起動忘れ、アドレス違い）
- 両方増えない → アプリが memcached を見に行っていない
- `evictions` が増える → メモリ不足。このドリルでは `-m` を上げず、キーか TTL を減らす

効かなければ残さない。戻して次の 1 本へ:

```bash
# バックアップから戻すとき
cp -a ~/kit-backup/app.before-cache/. /home/isucon/private_isu/webapp/python/
sudo systemctl restart isu-python.service
```

### 観察すること

- `get_hits / (hits + misses)` をベンチ前後差分で言える
- slp で選んだ 1 本の Count が落ち、alp の Sum が落ちた（ヒット率と両方）
- 再起動試験で投稿が消えない（1. の線引き）。消えたら投稿の本体データを memcached だけにしている
- `fail` が 0。破棄忘れは `fail` で出る。TTL を延ばす前に破棄を直す

## 5. nginx で 1 箇所だけキャッシュして HIT 率で評価する

nginx 側で返せるものは nginx で返す。アプリまで行かせない。ただし投稿の本体データにはしない。4. とは別々にやる。同時に入れて比べない。

順番: 対象を絞る → HIT/MISS を見える化（3.） → 1 箇所だけキャッシュ → ベンチ → HIT 率判定 → 本体データ確認。

### 対象を絞る

狙うのは GET の読み物。POST、ログイン後の私用ページは外す:

- 静的（`public/` 配下。`css/style.css` など）
- `/image`（`posts.imgdata` を読む回。BLOB を毎回 MySQL から読むと重い）
- 公開タイムラインの GET（ログイン不要部分だけ。認証が必要ならこのドリルでは外す）

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH"
# /image が Sum 上位なら候補。POST や /login が上位なら nginx の仕事ではない
```

今の nginx を見る（site conf のパスは自分の `nginx -t` 結果に置き換える）:

```bash
sudo nginx -t
cat /etc/nginx/sites-enabled/isucon.conf 2>/dev/null || cat /etc/nginx/conf.d/*.conf 2>/dev/null | head -80
```

### 1 箇所だけキャッシュする

例として `/image` だけやる（静的とタイムラインは次回）。`proxy_cache_path` は 1 行だけ足す:

```nginx
proxy_cache_path /var/cache/nginx/image levels=1:2 keys_zone=image:10m max_size=1g inactive=60m use_temp_path=off;
```

```nginx
# /image の location だけ
proxy_cache image;
proxy_cache_valid 200 60s;
proxy_cache_key "$request_method$host$request_uri";
# POST や Cookie 付きを巻き込まない
proxy_cache_methods GET HEAD;
proxy_no_cache $cookie_session $http_authorization;
proxy_cache_bypass $cookie_session $http_authorization;
```

```bash
sudo mkdir -p /var/cache/nginx/image
sudo chown www-data:www-data /var/cache/nginx/image
sudo nginx -t && sudo systemctl reload nginx
# 3. の curl 2 回で HIT を確認してからベンチへ
curl -sI http://127.0.0.1/image/1 | grep -i 'x-cache'
curl -sI http://127.0.0.1/image/1 | grep -i 'x-cache'
```

- `proxy_cache_valid` はまず 60 秒。長くする前に `proxy_no_cache` / `bypass` が効いているか見る
- `/image` をファイル化したら EBS 上に置く。`proxy_cache_path` を `/tmp` や `/dev/shm` にしない（1. の線引き）
- DB の `imgdata` を消さない。キャッシュは複製で、投稿の本体データは MySQL のまま残す

ベンチ前後で HIT 率を数える（3. の型）:

```bash
# bench-prep で access.log を回してから
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
sudo cat /var/log/nginx/access.log | grep -o 'cache:[A-Z-]*' | sort | uniq -c | sort -nr
sudo cat /var/log/nginx/access.log | grep '/image' | grep -o 'cache:[A-Z-]*' | sort | uniq -c | sort -nr
echo "$(date -Iseconds)  score=  pass=  fail=  nginx_hit=  note=nginx-image-60s" >> ~/bench-notes/scores.txt
```

残す変更は `backup/s1` に積む（[0030 §2](0030-measure.md#残す変更は-backups1-に積む) の流儀）。

### 本体データ確認と戻し

```bash
# キャッシュを消して再起動しても画像と投稿が残ること
sudo rm -rf /var/cache/nginx/image/*
sudo reboot
# 起きたあと
curl -fsS -o /dev/null -m 5 http://127.0.0.1/image/1
mysql -uisuconp -pisuconp isuconp -e "SELECT COUNT(*) FROM posts;"
```

戻すときは location に足した数行だけ消して reload する。`proxy_cache_path` のディレクトリは残してよい:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 観察すること

- `/image` の HIT 率を全体とパス別で言える（`grep -o 'cache:...'` の 2 本）
- `/image` の alp Sum が落ちた。HIT だけ増えて Sum が落ちないなら測る場所がずれている
- `fail` が 0。POST やログイン後を巻き込むと `fail` か別人表示になる。そのときは対象を狭める（TTL を延ばさない）
- キャッシュ全消し + 再起動でデータが残る。消えたら投稿の本体データを nginx だけにしている

## 6. Cache-Control を付ける前後で挙動を比べる

深掘り（`304` 再現・転送量・`ETag` 台間一致・点数判定）は [0070-static-cache.md](0070-static-cache.md)。ここは入口として 1 箇所だけ付けて前後を見る。

2.〜5. はサーバ側のキャッシュ（memcached / `proxy_cache` はサーバが覚える）。ここはクライアント側（ブラウザに覚えさせる）。誰が守るかが違う。5. までと同時に入れて比べない。1 箇所だけ付けて、`curl` とログで前後を見る。

対象は GET の読み物だけ（`/image` か `public/` 静的のどちらか 1 つ）。POST、ログイン後の私用ページには付けない。

### 付ける前: ヘッダが無いことと、毎回サーバまで来ることを確認する

```bash
curl -sI http://127.0.0.1/image/1 | grep -i -E 'cache-control|etag|last-modified' || echo 'no cache headers (before)'
curl -sI http://127.0.0.1/css/style.css | grep -i -E 'cache-control|etag|expires' || echo 'no cache headers (before)'
```

`curl` はデフォルトではキャッシュしない。だから 2 回叩けば `access.log` に 2 行残る。これが「付ける前」の挙動:

```bash
sudo cat /var/log/nginx/access.log | tail -3
curl -s -o /dev/null http://127.0.0.1/image/1
curl -s -o /dev/null http://127.0.0.1/image/1
sudo cat /var/log/nginx/access.log | tail -3
# 2 回叩いた分だけ行が増える = 毎回サーバまで来ている
```

### 付ける: Flask か nginx のどちらか 1 つ

全体の `after_request` に付けない。対象の 1 箇所だけ。Flask と nginx の両方に付けない。

Flask 側で付ける例（対象の `return` の直前。関数名は自分の `grep` 結果に置き換える）:

```bash
grep -rn 'def .*image\|send_file\|make_response\|Response' \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
```

```python
# 対象の return の直前で 1 行
resp = make_response(image_bytes)
resp.headers["Content-Type"] = "image/jpeg"  # 既存の型に合わせる
resp.headers["Cache-Control"] = "public, max-age=60"
return resp
```

nginx 側で付ける例（対象の location だけ）:

```nginx
# /image だけ、または /css/ だけ。全体には付けない
expires 60s;
add_header Cache-Control "public, max-age=60" always;
```

`max-age` はまず 60。長くする前に前後の挙動差を見る。私用ページに `public` を付けると他人に見える。迷ったら対象を `/image` の匿名 GET だけにする。

```bash
# Flask を変えたら
sudo systemctl restart isu-python.service
# nginx を変えたら
sudo nginx -t && sudo systemctl reload nginx
```

### 付けた後: ヘッダと、2 回目の来なさを比べる

```bash
curl -sI http://127.0.0.1/image/1 | grep -i 'cache-control'
# Cache-Control: public, max-age=60 が見えること
curl -sD - -o /dev/null http://127.0.0.1/image/1 | grep -i -E 'HTTP/|cache-control|etag|last-modified'
```

ブラウザがやる再検証を `curl` で再現する（`ETag` / `Last-Modified` が出ているときだけ）:

```bash
curl -sI http://127.0.0.1/image/1 | grep -i -E 'etag|last-modified'
curl -s -o /dev/null -w '%{http_code}\n' -H 'If-Modified-Since: Wed, 01 Jan 2025 00:00:00 GMT' http://127.0.0.1/image/1
# 304 が返れば再検証 OK。ずっと 200 ならアプリが条件付き GET に対応していない
```

ブラウザで見る（`curl` はキャッシュしないので、来なさはこちらで確認）:

1. DevTools → Network を開き、`Disable cache` のチェックを外す
2. `/image/1` をリロード 2 回
3. 1 回目 `200`、2 回目 `200 (memory cache)` / `200 (disk cache)` か `304` ならブラウザが覚えている
4. その間 `access.log` に行が増えない（サーバに来ていない分）

```bash
# bench-prep で access.log を回してから公式ベンチ 1 本。ブラウザ cache はベンチマーカーが再現しないことがある
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=cache-control-image-60" >> ~/bench-notes/scores.txt
```

注意: ベンチマーカーはブラウザキャッシュをエミュレートしないことが多い。点数ではなく `curl` + ログの前後差で判定する。`proxy_cache` との関係も独立で、`Cache-Control: private` / `no-store` を付けると `proxy_cache` が効かなくなることがある（`proxy_ignore_headers` を触る前に、対象を匿名 GET だけに狭める）。

戻すときは付けた箇所（Flask の 1 行か nginx の 2 行）だけ消して restart / reload する:

```bash
# Flask を戻したら
sudo systemctl restart isu-python.service
# nginx を戻したら
sudo nginx -t && sudo systemctl reload nginx
curl -sI http://127.0.0.1/image/1 | grep -i 'cache-control' || echo 'reverted (no header)'
```

### 観察すること

- 付ける前: ヘッダ無し + `curl` 2 回でログ 2 行。付けた後: `Cache-Control: public, max-age=60` が見える
- ブラウザ 2 回目が cache / `304` で、サーバのログが増えない（`curl` だけでは「来なさ」は見えない）
- `fail` が 0。他人表示や更新漏れが出たら対象を狭める（`max-age` を延ばさない）
- ベンチ点数も `scores.txt` に残す。ベンチマーカーがブラウザ cache を再現しない場合は点数が動かなくてもよい。そのときは `curl` + ログの前後差を本体の判定にする

## 7. トラブルシュート

### memcached が HIT しない

- `ISUCONP_MEMCACHED_ADDRESS` が localhost か。`echo stats` が届くか。unit を restart したか
- `curr_items` が 0 のまま: `set` まで届いていない。キー生成と restart を見る
- `get_misses` だけ増える: キーが毎回違う。引数なし固定キー、クエリ文字列、ページ番号の扱いを見る
- ログイン後に別人になる: セッションと読み物を同じキー設計にしている。キーを分ける。ログイン必須パスは対象外にする
- ベンチ中に `evictions` が跳ねる: キーが多すぎるか TTL が長すぎる。対象を 1 本に戻す

### nginx がずっと MISS / fail

- ずっと MISS: キーに時刻や Cookie が入っている。`proxy_cache_key` を固定形に戻す。`proxy_no_cache` が広すぎないか
- ずっと BYPASS: session 系 Cookie を全部避けている。対象を匿名 GET だけに狭めたか見る
- `cache:-` しか出ない: `X-Cache` 配線か `log_format` の問題。3. の手順で `curl -sI` が HIT することを先に確認する
- `fail` が出る: 認証付きをキャッシュしている。`proxy_no_cache` / `bypass` に session 系 Cookie を足して、対象を匿名 GET だけにする
- ディスクが膨らむ: `max_size` と `inactive` を見る。`du -sh /var/cache/nginx` と `df -h /`

### Cache-Control を付けても変わらない / fail

- ヘッダが出ない: Flask なら restart 忘れ、nginx なら対象 location を間違えている。`curl -sI` の対象パスと location を見比べる
- `curl` 2 回でログが減らない: 正常。`curl` はキャッシュしないので毎回来る。「来なさ」はブラウザの 2 回目 + `access.log` で見る
- ブラウザ 2 回目も毎回 `200` でログが増える: `private` / `no-store` が付いていないか、`max-age=0` になっていないか。DevTools の `Disable cache` が入ったままか
- `fail` や他人表示: `public` を私用ページに付けている。対象を匿名 GET（`/image`、静的）だけに戻す
- `proxy_cache` の HIT が消えた: `Cache-Control: private` / `no-store` と干渉している。`proxy_ignore_headers` を足す前に、対象の狭さとヘッダの中身を見る
