# 0040 キャッシュのヒット率を見る

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）はローカル環境の private-isu-terraform リポジトリにて実行します。

[0030-measure.md](0030-measure.md) の続きです。同じ構成でキャッシュを 1 手ずつ入れ、**何%当たったか**を数字で言います。「入れた」だけでは終わりません。

順番: 本体データの線引き → 測り方の固定（2. / 3.）→ アプリ層 1 本 → nginx 1 本 → Cache-Control（6.）。複数まとめては入れません。1 手ごとに公式ベンチを回します。`fail` が 0 でない点数は比べません。出口は [0050-http-client.md](0050-http-client.md) です。条件付きの深掘りは [0070-static-cache.md](0070-static-cache.md) です。

点数は `~/bench-notes/scores.txt` に 1 行。ヒット率も一緒に書く:

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  memc_hit=  nginx_hit=  note=cache-baseline" >> ~/bench-notes/scores.txt
```

| 回 | score | memc_hit | nginx_hit | メモ |
| --- | --- | --- | --- | --- |
| ベースライン |  | - | - | [0030-measure.md](0030-measure.md) までの状態。キャッシュ追加前 |
| 1 手目 |  | 例: 82% | - | 例: timeline を memcached、TTL=60 |

## 1. 投稿の本体データは MySQL と EBS

キャッシュを入れる前に、投稿の本体データと複製の線を引きます。投稿の本体データを消して複製だけにすると失格に近くなります。

- 投稿の本体データ（`posts` / `users` / `comments`）は MySQL（InnoDB）にあります。投稿の唯一のコピーは memcached に置きません
- `/image` をファイルにしたら **EBS 上**（`/home/isucon/...`）に置きます。`tmpfs`、`/dev/shm`、`/tmp` には置きません
- nginx の `proxy_cache` だけを投稿の本体データにしません
- ベンチ中に書いたデータは再起動後も読めることです

再起動試験は [0020-split-3.md](0020-split-3.md#10-ベンチ中の書き込みは再起動後も残す) と同じ。1 台でもやる:

```bash
mysql -uisuconp -pisuconp isuconp -e "SELECT COUNT(*) FROM posts;"
# マーカーを 1 行入れてから sudo reboot、もう一度 COUNT とマーカー
# キャッシュを入れたあとも同じ。消えたら投稿の本体データをキャッシュだけにしている
```

### 観察すること

- 投稿の本体データが MySQL / EBS に残っています。再起動試験で消えません
- 点差がその 1 手のコストです。まとめて入れた点数は比べません

## 2. memcached の `stats` を読む

アプリ層の判定材料は `stats` の出力です。共有 memcached（s2、`backup/common/ips.sh` の `MC_PRIV`）に `echo stats` を投げて全文を見ます。s1 の `127.0.0.1:11211` は分割後は使っていない別物なので見ません。見るのは 6 行です。

```bash
# s2 で打つ（共有 memcached のホスト）
systemctl is-active memcached.service isu-python.service
echo stats | nc -w 1 127.0.0.1 11211
# s1 から打つ例（MC_PRIV は backup/common/ips.sh）
echo stats | nc -w 1 "$MC_PRIV" 11211
```

出力は `STAT` で始まる行が並びます。その中で見る行はこれだけです:

| 行 | 意味 | 目安 |
| --- | --- | --- |
| `STAT get_hits` | 当たりの累積 | `hits / (hits + misses)` がヒット率 |
| `STAT get_misses` | 外れの累積 | 同上 |
| `STAT curr_items` | 入っているキー数 | 0 のままならアプリが書いていない |
| `STAT evictions` | メモリ不足で追い出された数 | 増え続けるならキーか TTL を減らす |
| `STAT bytes` / `STAT limit_maxbytes` | 使用量 / 上限（既定 `-m 64`） | 上限に張り付く前に evictions が動く |

ヒット率は手で割ります。例: `hits=8231`、`misses=1823` なら `8231 / (8231 + 1823) = 81.9%` です。

アプリがどこを見ているか確認します（アドレス違いのミスが一番多いです）:

```bash
cat /home/isucon/env.sh
# ISUCONP_MEMCACHED_ADDRESS が無ければ既定は 127.0.0.1:11211
# 分割済みなら s2 の IP:11211 を書いています
```

gunicorn のワーカーは別プロセスです。プロセスメモリ（dict、global）に置いたものはワーカー間で見えません。**アプリ層の共有は memcached にします**。プロセス内キャッシュはこのドリルでは使いません。

累積値だけでは今回のベンチのヒット率は分かりません。ベンチ前後で `stats` を保存して差分で比べます。やり方は [§4](#4-アプリ層-memcached-に-1-本入れてヒット率で評価する) のベンチで扱います。

カウンタを 0 から見たいときだけ（データは消さない）:

```bash
# 共有 memcached（s2）に投げる。s1 からは "$MC_PRIV" を使う
printf 'stats reset\nquit\n' | nc -w 1 127.0.0.1 11211
# キャッシュの中身を消すときは別物: printf 'flush_all\nquit\n' | nc -w 1 127.0.0.1 11211
```

`stats reset` はカウンタだけを、`flush_all` は中身だけを消します。ベンチ前のリセットを忘れても、前後差分でヒット率は出せます。

### 観察すること

- `curr_items` が 0 でない。0 のままなら get/set の配線ミス
- slp で狙った 1 本の Count が落ちた（ヒット率と DB 側の両方で確認）

## 3. nginx の HIT / MISS を読む

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
- `cache:-` → キャッシュの対象外です。`X-Cache` 配線（次の節）へ進んで切り分けます

### 1 リクエストの HIT / MISS を確認できるようにする（X-Cache）

一番小さい出し方は、nginx 側で `add_header X-Cache $upstream_cache_status always` を足すことです（site conf の対象 location へ。全体に足しません）。

`backup/s1/etc/nginx/sites-enabled/isucon.conf` の対象 location に次を足します:

```nginx
location @app {
  proxy_set_header Host $host;
  proxy_pass http://heavy;
  add_header X-Cache $upstream_cache_status always;
}
```

```bash
# 作業機
git pull --ff-only
# 上の内容を backup/s1/etc/nginx/sites-enabled/isucon.conf に編集する
git add -A && git commit -m "tune: X-Cache を出す" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo nginx -t && sudo systemctl reload nginx
curl -sI http://127.0.0.1/image/1.jpg
curl -sI http://127.0.0.1/image/1.jpg
```

応答ヘッダの中の `X-Cache:` 行を読みます。1 回目 `MISS` → 2 回目 `HIT` なら配線は合っています。ずっと `MISS` ならキーが毎回違います（クエリ、Cookie）。ずっと `BYPASS` なら `proxy_no_cache` / `bypass` が広すぎます。

`$upstream_cache_status` の主な値:

| 値 | 意味 | 次の一手 |
| --- | --- | --- |
| `HIT` | キャッシュから返した | このままベンチで全体の傾向を見る |
| `MISS` | アプリまで行って保存した | 2 回目も MISS ならキーを疑う |
| `BYPASS` | 条件でキャッシュを避けた | `proxy_no_cache` / `bypass` が広すぎないか |
| `EXPIRED` | 期限切れで取り直した | TTL を延ばす前に破棄条件を見る |

注意: ログの `cache` 列は `$upstream_cache_status`（nginx 自身の判定）です。`$upstream_http_x_cache` ではありません。`curl -sI` の `X-Cache` とログの `cache:` が違うときは `etc/nginx/ltsv.conf` の `log_format` を見ます。

### ベンチ後のログの見方

ベンチ後の `access.log` を数行見て、`cache:HIT` が出ていることを確認します。全体の判定は HIT 数ではなく alp の Sum と点数でやります（§5 のベンチでやります）。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH"
# /image が Sum 上位のまま HIT が増えない → nginx の前で落ちていない。対象かキーを見直す
```

### 観察すること

- 2 回目の `curl -sI` の `X-Cache` が HIT
- `/image` の alp Sum が落ちました。落ちないのに HIT だけ増えたら測る場所がずれています

## 4. アプリ層 memcached に 1 本入れてヒット率で評価する

[§2](#2-memcached-stats-を読む) の測り方を使い、アクセスが多い読みクエリ 1 本だけをキャッシュします。

順番は 1 クエリ選ぶ → get/set → 破棄 → ベンチ → ヒット率判定 → 戻せること、の順です。

### 1 つ選ぶ

alp の **Count も Sum も大きい** パスに紐づく SQL を 1 本だけ選びます:

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

方針（コピペ用の完成品は置きません。自分の 1 本に当てます）:

1. キーに引数を含めます（例: `timeline:<page>`、`user:<id>`）。引数なしの固定キーは混ざります
2. `get` してあれば返します。無ければ DB から取って `set` します（TTL 付き）。`pymemcache` は既定で bytes しか運ばないので、dict 等は `pickle.dumps` / `pickle.loads` で包みます（素のまま `set` すると読み側が bytes で壊れます）
3. 書き込み経路（投稿・コメント・プロフィール更新）でそのキーを `delete` します。TTL 任せだけにしません

TTL の目安はまず 30〜60 秒です。長くすると当たりますが、ベンチの書き込みとずれて `fail` になりやすくなります。短くすると安全ですが効きません。ベンチで比べます。

コードは作業機で変えます。編集前の状態は git が持っているので、サーバー上でバックアップは取りません:

```bash
# 作業機。対象ファイルは自分の grep 結果に置き換える
git pull --ff-only
# 重い処理（`/`・`/posts`・`/image`）は s2 の gunicorn が捌くので backup/s2、
# 軽い処理（unix socket の s1）は backup/s1 の <対象>.py を編集する
git add -A && git commit -m "tune: <対象> を memcached 化" && git push
```

サーバーで反映します（s2 の例）:

```bash
git pull --ff-only
sudo cp -a backup/s2/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
```

```bash
sudo systemctl restart isu-python.service
systemctl is-active isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
sudo journalctl -u isu-python.service -n 40 --no-pager
```

?> s1 の gunicorn は unix socket 専用で `:8080` を開いていません。s1 の疎通は `http://127.0.0.1/`（nginx）で見ます。`:8080` 直叩きは s2 で打ちます。

ベンチ前後でヒット率を判定します。`stats` は累積値なので、ベンチ前後で保存して見比べます（共有 memcached = s2 を見ます）:

```bash
# 1. ベンチ前（s1 で打つ。MC_PRIV は backup/common/ips.sh）
echo stats | nc -w 1 "$MC_PRIV" 11211 | tee /tmp/memc-before.txt
# 2. ログを回してから公式ベンチ 1 本（s1 で打つ）
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
# 3. ベンチ後
echo stats | nc -w 1 "$MC_PRIV" 11211 | tee /tmp/memc-after.txt
diff -u /tmp/memc-before.txt /tmp/memc-after.txt || true
echo "$(date -Iseconds)  score=  pass=  fail=  memc_hit=  note=memcached-timeline-ttl60" >> ~/bench-notes/scores.txt
```

残す変更は `backup/s2`（重い処理）か `backup/s1`（軽い処理）に積みます（[0030-measure.md](0030-measure.md) の「残す変更は backup/s1・s2・s3 に積む」の流儀）。

`get_hits` と `get_misses` の増え方を読みます（[§2](#2-memcached-stats-を読む) の表の行を見ます）:

- `get_hits` だけ増えた → 当たっています。そのまま TTL を変えて比べます
- `get_misses` だけ増えた → 書いていません、またはキーが毎回違います（引数なしの固定キー、再起動忘れ、アドレス違い）
- 両方増えない → アプリが memcached を見に行っていません
- `evictions` が増える → メモリ不足です。このドリルでは `-m` を上げず、キーか TTL を減らします

効かなければ残しません。作業機で戻して次の 1 本へ進みます:

```bash
# 作業機。未 push なら HEAD に戻ります。push 済みなら代わりに git revert します
git pull --ff-only
git checkout -- backup/s2/home/private_isu/webapp/python/<対象>.py
git push
```

サーバーで反映します（s2 で打つ）:

```bash
git pull --ff-only
sudo cp -a backup/s2/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
```

### 観察すること

- `get_hits / (hits + misses)` をベンチ前後差分で言える
- slp で選んだ 1 本の Count が落ち、alp の Sum が落ちた（ヒット率と両方）
- 再起動試験で投稿が消えない（1. の線引き）。消えたら投稿の本体データを memcached だけにしている
- `fail` が 0。破棄忘れは `fail` で出る。TTL を延ばす前に破棄を直す

## 5. nginx で 1 箇所だけキャッシュして HIT 率で評価する

nginx 側で返せるものは nginx で返します。アプリまで行かせません。ただし投稿の本体データにはしません。§4 とは別々にやります。同時に入れて比べません。

順番: 対象を絞ります → [§3](#3-nginx-hit-miss-を読む) で HIT / MISS を確認します → 1 箇所だけキャッシュします → ベンチします → HIT 率を判定します → 本体データが残ることを確認します。

### 対象を絞る

狙うのは GET の読み物です。POST、ログイン後の私用ページは外します:

- 静的（`public/` 配下。`css/style.css` など）
- `/image`（`posts.imgdata` を読む回。BLOB を毎回 MySQL から読むと重い）
- 公開タイムラインの GET（ログイン不要部分だけ。認証が必要ならこのドリルでは外す）

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH"
# /image が Sum 上位なら候補。POST や /login が上位なら nginx の仕事ではない
```

今の nginx を見ます（site conf のパスは自分の環境に合わせて書き換えます）:

```bash
sudo nginx -t
{ sudo cat /etc/nginx/sites-enabled/isucon.conf 2>/dev/null || sudo cat /etc/nginx/conf.d/*.conf; } | head -80
```

### 1 箇所だけキャッシュする

例として `/image` だけやります（静的とタイムラインは次回です）。`proxy_cache_path` は 1 行だけ足します。

`backup/s1/etc/nginx/conf.d/cache-image.conf`:

```nginx
proxy_cache_path /var/cache/nginx/image levels=1:2 keys_zone=image:10m max_size=1g inactive=60m use_temp_path=off;
```

`backup/s1/etc/nginx/sites-enabled/isucon.conf` の `location @app`（`try_files` で流れる先の応答を作る場所）:

```nginx
location @app {
  proxy_set_header Host $host;
  proxy_pass http://heavy;
  proxy_cache image;
  proxy_cache_valid 200 60s;
  proxy_cache_key "$request_method$host$request_uri";
  # POST や Cookie 付きを巻き込まない
  proxy_cache_methods GET HEAD;
  proxy_no_cache $cookie_session $http_authorization;
  proxy_cache_bypass $cookie_session $http_authorization;
}
```

```bash
# 作業機
git pull --ff-only
mkdir -p backup/s1/etc/nginx/conf.d
# 上の 2 つの内容を各ファイルに配置する
git add -A && git commit -m "tune: /image を proxy_cache する" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s1/etc/nginx/conf.d/cache-image.conf /etc/nginx/conf.d/
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo mkdir -p /var/cache/nginx/image
sudo chown www-data:www-data /var/cache/nginx/image
sudo nginx -t && sudo systemctl reload nginx
# 3. の curl 2 回で HIT を確認してからベンチへ
curl -sI http://127.0.0.1/image/1.jpg | grep -i 'x-cache'
curl -sI http://127.0.0.1/image/1.jpg | grep -i 'x-cache'
```

- `proxy_cache_valid` はまず 60 秒です。長くする前に `proxy_no_cache` / `bypass` が効いているか見ます
- `sites-enabled/isucon.conf` は `sites-available` への symlink です。`cp` で上書きすると実体（`sites-available` 側）が変わり、`.orig` も同じ実体を指すので退避になりません。変える前に実ファイルの内容を別名で保存します
- reload 直後は少し待ってから打ちます（旧 worker の応答が混ざります）
- `/image` をファイル化したら EBS 上に置きます。`proxy_cache_path` を `/tmp` や `/dev/shm` にしません（[§1](#1-投稿の本体データは-mysql-と-ebs) の線引きです）
- DB の `imgdata` を消しません。キャッシュは複製で、投稿の本体データは MySQL のまま残します

ベンチ前後で HIT 率を数えます（[§3](#3-nginx-hit-miss-を読む) の型）。`cache:-` は proxy を経由しない行（nginx が直接返す静的ファイル等）で、失敗ではありません:

```bash
# bench-prep で access.log を回してから
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
sudo cat /var/log/nginx/access.log | grep -o 'cache:[A-Z-]*' | sort | uniq -c | sort -nr
sudo cat /var/log/nginx/access.log | grep '/image' | grep -o 'cache:[A-Z-]*' | sort | uniq -c | sort -nr
echo "$(date -Iseconds)  score=  pass=  fail=  nginx_hit=  note=nginx-image-60s" >> ~/bench-notes/scores.txt
```

残す変更は `backup/s1` に積みます（[0030-measure.md](0030-measure.md) の「残す変更は backup/s1・s2・s3 に積む」の流儀）。

### 本体データ確認と戻し

```bash
# キャッシュを消して再起動しても画像と投稿が残ること
sudo rm -rf /var/cache/nginx/image/*
sudo reboot
# 起きたあと
curl -fsS -o /dev/null -m 5 http://127.0.0.1/image/1.jpg
mysql -uisuconp -pisuconp isuconp -e "SELECT COUNT(*) FROM posts;"
```

戻すときは作業機で足した行を消します。`proxy_cache_path` のディレクトリは残してよいです:

```bash
# 作業機。backup/s1/etc/nginx/sites-enabled/isucon.conf の location から足した行を消す
# cache-image.conf ごと消すなら git rm する
git pull --ff-only
git rm backup/s1/etc/nginx/conf.d/cache-image.conf
git add -A && git commit -m "tune: /image の proxy_cache を外す" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo rm -f /etc/nginx/conf.d/cache-image.conf
sudo nginx -t && sudo systemctl reload nginx
```

### 観察すること

- `/image` の HIT 率を全体とパス別で言えます（`grep -o 'cache:...'` の 2 本です）
- `/image` の alp Sum が落ちました。HIT だけ増えて Sum が落ちないなら測る場所がずれています
- `fail` が 0 です。POST やログイン後を巻き込むと `fail` か別人表示になります。そのときは対象を狭めます（TTL を延ばしません）
- キャッシュ全消し + 再起動でデータが残ります。消えたら投稿の本体データを nginx だけにしています

## 6. Cache-Control を付ける前後で挙動を比べる

深掘り（`304` 再現・転送量・`ETag` 台間一致・点数判定）は [0070-static-cache.md](0070-static-cache.md) です。ここは入口として 1 箇所だけ付けて前後を見ます。

[§2](#2-memcached-stats-を読む)〜[§5](#5-nginx-で-1-箇所だけキャッシュして-hit-率で評価する) はサーバが応答を覚えるキャッシュです。ここはブラウザが覚えるキャッシュです。覚える主体が違います。§5 までと同時に入れて比べません。1 箇所だけ付けて、`curl` とログで前後を見ます。

対象は GET の読み物だけです（`/image` か `public/` 静的のどちらか 1 つ）。POST、ログイン後の私用ページには付けません。

### 付ける前: ヘッダが無いことと、毎回サーバまで来ることを確認する

```bash
curl -sI http://127.0.0.1/image/1.jpg | grep -i -E 'cache-control|etag|last-modified' || echo 'no cache headers (before)'
curl -sI http://127.0.0.1/css/style.css | grep -i -E 'cache-control|etag|expires' || echo 'no cache headers (before)'
```

`curl` はデフォルトではキャッシュしません。2 回叩いた分だけ行が増えます（= 毎回サーバまで来ています）:

```bash
sudo cat /var/log/nginx/access.log | tail -3
curl -s -o /dev/null http://127.0.0.1/image/1.jpg
curl -s -o /dev/null http://127.0.0.1/image/1.jpg
sudo cat /var/log/nginx/access.log | tail -3
# 2 回叩いた分だけ行が増える = 毎回サーバまで来ている
```

### 付ける: Flask か nginx のどちらか 1 つ

全体の `after_request` に付けません。対象の 1 箇所だけです。Flask と nginx の両方に付けません。

Flask 側で付ける例です。まず対象の関数を探します:

```bash
grep -rn 'def .*image\|send_file\|make_response\|Response' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
```

`backup/s2/home/private_isu/webapp/python/<対象>.py`（重い処理なら s2、軽い処理なら s1）の対象の `return` の直前:

```python
resp = make_response(image_bytes)
resp.headers["Content-Type"] = "image/jpeg"  # 既存の型に合わせる
resp.headers["Cache-Control"] = "public, max-age=60"
return resp
```

```bash
# 作業機
git pull --ff-only
# 上の内容を配置する
git add -A && git commit -m "tune: <対象> に Cache-Control を付ける" && git push
```

nginx 側で付ける例です。`backup/s1/etc/nginx/sites-enabled/isucon.conf` の対象 location だけ（全体には付けません）:

```nginx
location /css/ {
  # expires だけで Cache-Control: max-age と Expires が付く。add_header の重ね書きは要らない
  expires 60s;
}
```

`max-age` はまず 60 です。長くする前に前後の挙動差を見ます。私用ページに `public` を付けると他人に見えます。迷ったら対象を `/image` の匿名 GET だけにします。

```bash
# Flask を変えたら（対象の台で反映。重い処理なら s2）
git pull --ff-only
sudo cp -a backup/s2/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
# nginx を変えたら（s1 で反映）
git pull --ff-only
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo nginx -t && sudo systemctl reload nginx
```

### 付けた後: ヘッダと 2 回目の到達を比べます

```bash
curl -sI http://127.0.0.1/image/1.jpg | grep -i 'cache-control'
# Cache-Control: public, max-age=60 が見えること
curl -sD - -o /dev/null http://127.0.0.1/image/1.jpg | grep -i -E 'HTTP/|cache-control|etag|last-modified'
```

ブラウザがやる再検証を `curl` で再現します（`ETag` / `Last-Modified` が出ているときだけです）:

```bash
curl -sI http://127.0.0.1/image/1.jpg | grep -i -E 'etag|last-modified'
curl -s -o /dev/null -w '%{http_code}\n' -H 'If-Modified-Since: Wed, 01 Jan 2025 00:00:00 GMT' http://127.0.0.1/image/1.jpg
# 304 が返れば再検証 OK。ずっと 200 ならアプリが条件付き GET に対応していない
```

ブラウザで見ます（`curl` はキャッシュしないので、サーバへの到達の有無はこちらで確認します）:

1. DevTools → Network を開き、`Disable cache` のチェックを外します
2. `/image/1` を 2 回リロードします
3. 1 回目 `200`、2 回目 `200 (memory cache)` / `200 (disk cache)` か `304` ならブラウザが覚えています
4. その間 `access.log` に行が増えません（サーバに来ていない分です）

```bash
# bench-prep で access.log を回してから公式ベンチ 1 本。ブラウザ cache はベンチマーカーが再現しないことがある
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=cache-control-image-60" >> ~/bench-notes/scores.txt
```

注意: ベンチマーカーはブラウザキャッシュを再現しないことが多いです。点数は動かなくてもよく、判定は `curl` + ログの前後差です。`Cache-Control: private` / `no-store` を付けると `proxy_cache` が効かなくなることがあります。

戻すときは作業機で付けた箇所（Flask の 1 行か nginx の `expires` 1 行）を消します:

```bash
# 作業機。付けた行を消す
git pull --ff-only
git add -A && git commit -m "tune: Cache-Control を外す" && git push
```

サーバーで反映します:

```bash
# Flask を戻したら（変えた台で打つ）
git pull --ff-only
sudo cp -a backup/s2/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
# nginx を戻したら
git pull --ff-only
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo nginx -t && sudo systemctl reload nginx
curl -sI http://127.0.0.1/image/1.jpg | grep -i 'cache-control' || echo 'reverted (no header)'
```

### 観察すること

- 付ける前: ヘッダ無し + `curl` 2 回でログ 2 行です。付けた後: `Cache-Control: public, max-age=60` が見えます
- ブラウザ 2 回目が cache / `304` で、サーバのログが増えません（`curl` だけでは到達の有無は見えません）
- `fail` が 0 です。他人表示や更新漏れが出たら対象を狭めます（`max-age` を延ばしません）
- ベンチ点数も `scores.txt` に残します。ベンチマーカーがブラウザ cache を再現しない場合は点数が動かなくてもよいです。そのときは `curl` + ログの前後差を本体の判定にします

## 7. トラブルシュート

### memcached が HIT しない

- `ISUCONP_MEMCACHED_ADDRESS` を確認します。`echo stats` が届くか確認します。unit を restart しましたか
- `curr_items` が 0 のまま: `set` まで届いていません。キー生成と restart を見ます
- `get_misses` だけ増える: キーが毎回違います。引数なし固定キー、クエリ文字列、ページ番号の扱いを見ます
- ログイン後に別人になる: セッションと読み物を同じキー設計にしています。キーを分けます。ログイン必須パスは対象外にします
- ベンチ中に `evictions` が跳ねる: キーが多すぎるか TTL が長すぎます。対象を 1 本に戻します

### nginx がずっと MISS / fail

- ずっと MISS: キーに時刻や Cookie が入っています。`proxy_cache_key` を固定形に戻します。`proxy_no_cache` が広すぎないか見ます
- ずっと BYPASS: session 系 Cookie を全部避けています。対象を匿名 GET だけに狭めたか見ます
- `cache:-` しか出ない: `X-Cache` 配線か `log_format` の問題です。3. の節で `curl -sI` が HIT することを先に確認します
- `fail` が出る: 認証付きをキャッシュしています。`proxy_no_cache` / `bypass` に session 系 Cookie を足して、対象を匿名 GET だけにします
- ディスクが膨らむ: `max_size` と `inactive` を見ます。`du -sh /var/cache/nginx` と `df -h /` を見ます

### Cache-Control を付けても変わらない / fail

- ヘッダが出ない: Flask なら restart 忘れ、nginx なら対象 location を間違えています。`curl -sI` の対象パスと location を見比べます
- `curl` 2 回でログが減らない: 正常です。`curl` はキャッシュしないので毎回来ます。到達の有無はブラウザの 2 回目 + `access.log` で見ます
- ブラウザ 2 回目も毎回 `200` でログが増える: `private` / `no-store` が付いていないか、`max-age=0` になっていないか見ます。DevTools の `Disable cache` が入ったままか見ます
- `fail` や他人表示: `public` を私用ページに付けています。対象を匿名 GET（`/image`、静的）だけに戻します
- `proxy_cache` の HIT が消えた: `Cache-Control: private` / `no-store` と干渉しています。`proxy_ignore_headers` を足す前に、対象の狭さとヘッダの中身を見ます
