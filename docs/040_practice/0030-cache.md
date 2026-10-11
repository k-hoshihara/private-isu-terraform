# 0030 キャッシュのヒット率を見る

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）は、手元の private-isu-terraform リポジトリで実行します。

[0020-measure.md](0020-measure.md) の続きです。

- 同じ構成で、キャッシュ（一度見た物を覚えておく仕組み）を 1 つずつ入れます。
- **何% 当たったか**（覚えていた物が使われた割合。ヒット率）を、数字で言います。
- 「入れた」だけでは、終わりません。

- 順番: 本体データの線引き → 測り方の固定（2. / 3.）→ アプリ層 1 本 → nginx 1 本 → Cache-Control（6.）。
- まとめては、入れません。
- 1 つごとに、公式ベンチを回します。
- `fail`（エラーの件数）が 0 でない点数は、比べません。
- 出口は [0040-http-client.md](0040-http-client.md) です。
- 条件付きの深掘りは、[0060-static-cache.md](0060-static-cache.md) です。

- 点数は、`~/bench-notes/scores.txt` に 1 行書きます。ヒット率も一緒に書きます。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  memc_hit=  nginx_hit=  note=cache-baseline" >> ~/bench-notes/scores.txt
```

| 回 | score | memc_hit | nginx_hit | メモ |
| --- | --- | --- | --- | --- |
| ベースライン | 18153 | - | -（`cache:-` のみ 15147 行） | 2026-10-13、`c7a.large` 1 台 / `ap-northeast-1` / 単一構成 / `nproc=2` / メモリ 3.7Gi。[0020-measure.md](0020-measure.md) までの状態 + §3 の X-Cache 配線のみ（挙動不変） |
| §5 nginx `/image` proxy_cache | 23016 | - | 89.8%（HIT 8824 / (HIT+MISS 9826)。全体では 44.6%、BYPASS 1397） | 同条件。`/image` を `proxy_cache` 60s。ベースライン比 +4863 |

<a id="1-投稿の本体データは-mysql-と-ebs"></a>

## 1. 投稿の本体データは MySQL と EBS

- キャッシュを入れる前に、投稿の本体データと、それを写した複製の線を引きます。
- 投稿の本体データを消して複製だけにすると、失格に近くなります。

- 投稿の本体データ（`posts` / `users` / `comments`）は MySQL（InnoDB）にあります。投稿の唯一のコピーは memcached に置きません
- `/image` をファイルにしたら **EBS 上**（`/home/isucon/...`）に置きます。`tmpfs`、`/dev/shm`、`/tmp` には置きません（これらは再起動で消えます）
- nginx の `proxy_cache`（nginx が覚えるキャッシュ）だけを投稿の本体データにしません
- ベンチ中に書いたデータは再起動後も読めることです

- 再起動試験は、[0110-split-3.md](0110-split-3.md#10-ベンチ中の書き込みは再起動後も残す) と同じです。1 台でもやります。

```bash
mysql -uisuconp -pisuconp isuconp -e "SELECT COUNT(*) FROM posts;"
# マーカーを 1 行入れてから sudo reboot、もう一度 COUNT とマーカー
# キャッシュを入れたあとも同じ。消えたら投稿の本体データをキャッシュだけにしている
```

### 観察すること

- 投稿の本体データが MySQL / EBS に残っています。再起動試験で消えません
- 点差がその 1 つぶんの効果です。まとめて入れた点数は比べません

<a id="2-memcached-stats-を読む"></a>

## 2. memcached の `stats` を読む

- アプリ層の判定材料は、`stats`（memcached の状態表示）の出力です。
- memcached（`127.0.0.1:11211`）に `echo stats` を投げて、全文を見ます。
- 見るのは、6 行です。

```bash
# 1 台で打つ
systemctl is-active memcached.service isu-python.service
echo stats | nc -w 1 127.0.0.1 11211
```

- 出力は、`STAT` で始まる行が並びます。その中で見る行は、これだけです。

| 行 | 意味 | 目安 |
| --- | --- | --- |
| `STAT get_hits` | 当たりの累積 | `hits / (hits + misses)` がヒット率 |
| `STAT get_misses` | 外れの累積 | 同上 |
| `STAT curr_items` | 入っているキー数 | 0 のままならアプリが書いていない |
| `STAT evictions` | メモリ不足で追い出された数 | 増え続けるならキーか TTL を減らす |
| `STAT bytes` / `STAT limit_maxbytes` | 使用量 / 上限（既定 `-m 64`） | 上限に張り付く前に evictions が動く |

- ヒット率は、手で割ります。
- 例: `hits=8231`、`misses=1823` なら `8231 / (8231 + 1823) = 81.9%` です。

- アプリがどこを見ているか確認します。アドレス違いのミスが、いちばん多いです。

```bash
cat /home/isucon/env.sh
# ISUCONP_MEMCACHED_ADDRESS が無ければ既定は 127.0.0.1:11211
```

- gunicorn のワーカー（処理を受け持つ子プロセス）は、別プロセスです。
- プロセスの中のメモリ（dict、global）に置いたものは、ワーカー間で見えません。
- **アプリ層の共有は、memcached にします。**
- プロセス内キャッシュは、この演習では使いません。

- 累積値だけでは、今回のベンチのヒット率が分かりません。
- ベンチ前後で `stats` を保存して、差分で比べます。
- やり方は [§4](#4-アプリ層-memcached-に-1-本入れてヒット率で評価する) のベンチで扱います。

- カウンタを 0 から見たいときだけ、使います（データは消しません）。

```bash
# memcached に投げる
printf 'stats reset\nquit\n' | nc -w 1 127.0.0.1 11211
# キャッシュの中身を消すときは別物: printf 'flush_all\nquit\n' | nc -w 1 127.0.0.1 11211
```

- `stats reset` は、カウンタだけを消します。`flush_all` は、中身だけを消します。
- ベンチ前のリセットを忘れても、前後差分でヒット率は出せます。

### 観察すること

- `curr_items` が 0 でない。0 のままなら get/set の配線ミス
- slp で狙った 1 本の Count が落ちた（ヒット率と DB 側の両方で確認）

<a id="3-nginx-hit-miss-を読む"></a>

## 3. nginx の HIT / MISS を読む

- 1 リクエストの成否は、`curl -sI` の `X-Cache` 行で見ます。
- 全体の傾向は、`access.log` の `cache:` 列で見ます。

### まず今の `cache` 列を見る

- このリポジトリの LTSV は、`cache` 列を持っています（`etc/nginx/ltsv.conf`）。
- ログを数行見て、`cache:` の後ろを読みます。

```bash
head -3 /var/log/nginx/access.log
# 行の中の cache:HIT / cache:MISS / cache:- を見る
```

値の意味:

- `cache:HIT` → キャッシュから返した
- `cache:MISS` → アプリまで行って保存した
- `cache:-` → キャッシュの対象外です。`X-Cache` 配線（次の節）へ進んで切り分けます

### 1 リクエストの HIT / MISS を確認できるようにする（X-Cache）

- いちばん簡単な出し方は、nginx 側で `add_header X-Cache $upstream_cache_status always` を足すことです。
- site conf の対象 location に足します。全体には足しません。
- `proxy_cache` を入れる location は、§5 で増えます。この時点では、全部 `proxy_pass` している `location /` に足します。

- `backup/s1/etc/nginx/sites-enabled/isucon.conf` の `location /` に、`add_header` の 1 行を足します。
- 素体（配布された元の状態）は、`root` + `proxy_pass` の 1 箇所だけです。
- 足した結果は、次のとおりです。

```nginx
location / {
  proxy_set_header Host $host;
  proxy_pass http://localhost:8080;
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

?> `sites-enabled/isucon.conf` が symlink なら、コピー先はリンク先の実体にします。コピー元のスナップショットも symlink の場合は、`backup/s1/etc/nginx/sites-available/isucon.conf` を使います。root 所有の設定への配置は sudo で行います。isucon へ所有者を変える必要はありません。

```bash
git pull --ff-only
SOURCE=backup/s1/etc/nginx/sites-enabled/isucon.conf
test ! -L "$SOURCE" || SOURCE=backup/s1/etc/nginx/sites-available/isucon.conf
TARGET=$(readlink -f /etc/nginx/sites-enabled/isucon.conf)
test -s "$SOURCE" && test -n "$TARGET" || exit 1
sudo cp -a "$SOURCE" "$TARGET"
sudo nginx -t && sudo systemctl reload nginx
curl -sI http://127.0.0.1/image/1.jpg
curl -sI http://127.0.0.1/image/1.jpg
```

- 応答ヘッダの中の `X-Cache:` 行を読みます。
- 1 回目 `MISS` → 2 回目 `HIT` なら、配線は合っています。
- ずっと `MISS` なら、キーが毎回違います（クエリ文字列、Cookie）。
- ずっと `BYPASS` なら、`proxy_no_cache` / `bypass`（キャッシュを使わせる／避けさせる指定）が広すぎます。

`$upstream_cache_status` の主な値:

| 値 | 意味 | 次の一手 |
| --- | --- | --- |
| `HIT` | キャッシュから返した | このままベンチで全体の傾向を見る |
| `MISS` | アプリまで行って保存した | 2 回目も MISS ならキーを疑う |
| `BYPASS` | 条件でキャッシュを避けた | `proxy_no_cache` / `bypass` が広すぎないか |
| `EXPIRED` | 有効期限切れで取り直した | TTL（有効期限）を延ばす前に破棄条件を見る |

- 注意: ログの `cache` 列は、`$upstream_cache_status`（nginx 自身の判定）です。`$upstream_http_x_cache` ではありません。
- `curl -sI` の `X-Cache` とログの `cache:` が違うときは、`etc/nginx/ltsv.conf` の `log_format` を見ます。

### ベンチ後のログの見方

- ベンチ後の `access.log` を数行見て、`cache:HIT` が出ていることを確認します。
- 全体の判定は、HIT 数ではありません。alp の Sum（合計時間）と点数でやります（§5 のベンチでやります）。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH"
# /image が Sum 上位のまま HIT が増えない → nginx の前で落ちていない。対象かキーを見直す
```

#### 実行例：

2026-10-13 の実測（`c7a.large` 1 台 / `ap-northeast-1` / 単一構成 / `nproc=2` / メモリ 3.7Gi。X-Cache 配線直後、proxy_cache 導入前の状態）:

```text
$ sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
    -u /home/isucon/private_isu/benchmarker/userdata \
    -t http://localhost
{"pass":true,"score":18153,"success":15264,"fail":0,"messages":[]}
```

cache 分布（ベンチ後。`proxy_cache` 未設定なので全行対象外）:

```text
   15147 cache:-
```

`curl -sI` 2 回（配線直後。`proxy_cache` 未設定のため `X-Cache` 行は出ない）:

```text
1回目: X-Cache 行なし（HTTP/1.1 200 OK）
2回目: X-Cache 行なし（HTTP/1.1 200 OK）
```

- `$upstream_cache_status` は、キャッシュ未設定時は空です。`add_header` は、空値のヘッダを出しません。
- 配線自体は有効です。同じ location に `add_header X-Diag static-ok always;` を一時追加すると、出ることを確認済みです（確認後に除去）。
- `MISS → HIT` の確認は、§5 の導入後に行います。

| 状態 | score | fail | 備考 |
| --- | --- | --- | --- |
| 0020 bench-prep 後（2026-10-13） | 15125 | 0 | 単一構成・キャッシュ追加前 |
| **0030 X-Cache 配線のみ（今回）** | **18153** | **0** | **+3028。配線は挙動を変えないので差はベンチの再現性の幅と読む** |

### 観察すること

- 2 回目の `curl -sI` の `X-Cache` が HIT
- `/image` の alp Sum が落ちました。落ちないのに HIT だけ増えたら測る場所がずれています

<a id="4-アプリ層-memcached-に-1-本入れてヒット率で評価する"></a>

## 4. アプリ層 memcached に 1 本入れてヒット率で評価する

- [§2](#2-memcached-stats-を読む) の測り方を使います。
- アクセスが多い読みクエリを、1 本だけキャッシュします。

- 順番は、1 本選ぶ → get/set → 破棄（必要なときに消す）→ ベンチ → ヒット率判定 → 戻せること、です。

### 1 つ選ぶ

- alp の **Count も Sum も大きい** パスに紐づく SQL を、1 本だけ選びます。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH"
sudo slp my --file /var/log/mysql/mysql-slow.log
# Count が大きく、examined/sent が数倍以内のものを 1 本。タイムラインやユーザー参照になりやすい
# examined >> sent のままなら先にインデックス（[0015-sql-tuning.md](0015-sql-tuning.md#6-インデックスを-1-本貼る)）
```

- コードの場所は、`grep` で探します。関数名は決め打ちしません。

```bash
grep -rn 'def .*timeline\|def .*post\|SELECT.*FROM posts\|SELECT.*FROM users' \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
```

### get/set を足す

- 方針は、次のとおりです（コピペ用の完成品は置きません。自分の 1 本に当てます）。

1. キーに引数を含めます（例: `timeline:<page>`、`user:<id>`）。引数なしの固定キーは混ざります
2. `get` してあれば返します。無ければ DB から取って `set` します（TTL＝有効期限付き）。`pymemcache` は既定で bytes しか運ばないので、dict 等は `pickle.dumps` / `pickle.loads`（Python の保存形式）で包みます（素のまま `set` すると読み側が bytes で壊れます）
3. 書き込み経路（投稿・コメント・プロフィール更新）でそのキーを `delete` します。TTL 任せだけにしません

- TTL の目安は、まず 30〜60 秒です。
- 長くすると、キャッシュの残る時間が長くなります。ベンチの書き込みとずれて、`fail` になりやすくなります。
- 短くすると安全ですが、効きません。ベンチで比べます。

- コードは、作業機で変えます。
- 編集前の状態は、git が持っています。サーバー上では、バックアップを取りません。

```bash
# 作業機。対象ファイルは自分の grep 結果に置き換える
git pull --ff-only
# アプリのソースは backup/s1/home/private_isu/webapp/python/<対象>.py を編集する
git add -A && git commit -m "tune: <対象> を memcached 化" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s1/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
```

```bash
sudo systemctl restart isu-python.service
systemctl is-active isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
sudo journalctl -u isu-python.service -n 40 --no-pager
```

?> gunicorn は `:8080` を、nginx は 80 番を開いています。`:8080` 直叩き（アプリ）と `http://127.0.0.1/`（nginx 経由）の両方を、同じ台で打てます。

- ベンチ前後で、ヒット率を判定します。
- `stats` は累積値です。ベンチ前後で保存して、見比べます。

```bash
# 1. ベンチ前（1 台で打つ）
echo stats | nc -w 1 127.0.0.1 11211 | tee /tmp/memc-before.txt
# 2. ログを回してから公式ベンチ 1 本（同じ台で打つ）
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
fi
sudo nginx -s reopen
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
# 3. ベンチ後
echo stats | nc -w 1 127.0.0.1 11211 | tee /tmp/memc-after.txt
diff -u /tmp/memc-before.txt /tmp/memc-after.txt || true
echo "$(date -Iseconds)  score=  pass=  fail=  memc_hit=  note=memcached-timeline-ttl60" >> ~/bench-notes/scores.txt
```

- 残す変更は、`backup/s1` に積みます（[0020-measure.md](0020-measure.md) の「残す変更は backup/s1 に積む」の流儀）。

- `get_hits` と `get_misses` の増え方を読みます（[§2](#2-memcached-stats-を読む) の表の行を見ます）。

- 実測（2026-10-13、`c7a.large` 1 台 / `ap-northeast-1` / 単一構成 / `nproc=2` / メモリ 3.7Gi。`get_session_user` の users 行をキー `user:<id>` / TTL 60 で cache 化）。
- 結果: `{"pass":true,"score":18194,"success":15283,"fail":0,"messages":[]}`
- ベンチ前後差分: `get_hits` +6197・`get_misses` +866 → ヒット率 87.7%
- `evictions` 0。
- ベースライン比 +41（ノイズ内。主キーでの参照 1 本では点数は動かない）。
- §5 とは別々に評価するため、この演習では戻した。

- `get_hits` だけ増えた → 当たっています。そのまま TTL を変えて比べます
- `get_misses` だけ増えた → 書いていません、またはキーが毎回違います（引数なしの固定キー、再起動忘れ、アドレス違い）
- 両方増えない → アプリが memcached を見に行っていません
- `evictions` が増える → メモリ不足です。この演習では `-m`（容量の上限）を上げず、キーか TTL を減らします

- 効かなければ、残しません。作業機で戻して、次の 1 本へ進みます。

```bash
# 作業機。未 push なら HEAD に戻ります。push 済みなら代わりに git revert します
git pull --ff-only
git checkout -- backup/s1/home/private_isu/webapp/python/<対象>.py
git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s1/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
```

### 観察すること

- `get_hits / (hits + misses)` をベンチ前後差分で言える
- slp で選んだ 1 本の Count が落ち、alp の Sum が落ちた（ヒット率と両方）
- 再起動試験で投稿が消えない（1. の線引き）。消えたら投稿の本体データを memcached だけにしている
- `fail` が 0。破棄の忘れは `fail` で出る。TTL を延ばす前に破棄を直す

<a id="5-nginx-で-1-箇所だけキャッシュして-hit-率で評価する"></a>

## 5. nginx で 1 箇所だけキャッシュして HIT 率で評価する

- nginx 側で返せるものは、nginx で返します。アプリまで行かせません。
- ただし、投稿の本体データにはしません。
- §4 とは、別々にやります。同時に入れて比べません。

- 順番: 対象を絞ります → [§3](#3-nginx-hit-miss-を読む) で HIT / MISS を確認します → 1 箇所だけキャッシュします → ベンチします → HIT 率を判定します → 本体データが残ることを確認します。

### 対象を絞る

- 狙うのは、GET の読み物です。
- POST と、ログイン後の個人用ページは外します。

- 静的（`public/` 配下。`css/style.css` など）
- `/image`（`posts.imgdata` を読む回。画像の大きなデータ（BLOB）を毎回 MySQL から読むと重い）
- 公開タイムラインの GET（ログイン不要部分だけ。認証が必要ならこの演習では外す）

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH"
# /image が Sum 上位なら候補。POST や /login が上位なら nginx の仕事ではない
```

- 今の nginx を見ます。site conf のパスは、自分の環境に合わせて書き換えます。

```bash
sudo nginx -t
{ sudo cat /etc/nginx/sites-enabled/isucon.conf 2>/dev/null || sudo cat /etc/nginx/conf.d/*.conf; } | head -80
```

### 1 箇所だけキャッシュする

- 例として、`/image` だけやります（静的とタイムラインは次回です）。
- `proxy_cache_path` は、1 行だけ足します。

`backup/s1/etc/nginx/conf.d/cache-image.conf`:

```nginx
proxy_cache_path /var/cache/nginx/image levels=1:2 keys_zone=image:10m max_size=1g inactive=60m use_temp_path=off;
```

- `backup/s1/etc/nginx/sites-enabled/isucon.conf` に、`location /image/` を 1 箇所だけ足します。
- 素体の `location /` は、そのまま残します。

```nginx
location /image/ {
  proxy_set_header Host $host;
  proxy_pass http://localhost:8080;
  proxy_cache image;
  proxy_cache_valid 200 60s;
  proxy_cache_key "$request_method$host$request_uri";
  # POST や Cookie 付きを巻き込まない
  proxy_cache_methods GET HEAD;
  proxy_no_cache $cookie_session $http_authorization;
  proxy_cache_bypass $cookie_session $http_authorization;
  # §3 の X-Cache もここに足す（location が分かれると上の location / の
  # add_header は引き継がれません）
  add_header X-Cache $upstream_cache_status always;
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
SOURCE=backup/s1/etc/nginx/sites-enabled/isucon.conf
test ! -L "$SOURCE" || SOURCE=backup/s1/etc/nginx/sites-available/isucon.conf
TARGET=$(readlink -f /etc/nginx/sites-enabled/isucon.conf)
test -s "$SOURCE" && test -n "$TARGET" || exit 1
sudo cp -a "$SOURCE" "$TARGET"
sudo mkdir -p /var/cache/nginx/image
sudo chown www-data:www-data /var/cache/nginx/image
sudo nginx -t && sudo systemctl reload nginx
# 3. の curl 2 回で HIT を確認してからベンチへ
curl -sI http://127.0.0.1/image/1.jpg | grep -i 'x-cache'
curl -sI http://127.0.0.1/image/1.jpg | grep -i 'x-cache'
```

- `proxy_cache_valid` は、まず 60 秒です。長くする前に、`proxy_no_cache` / `bypass` が効いているか見ます
- `sites-enabled/isucon.conf` は、`sites-available` への symlink です。
- `cp` で上書きすると、実体（`sites-available` 側）が変わります。`.orig` も同じ実体を指すので、退避になりません。
- 変える前に、実ファイルの内容を別名で保存します
- reload 直後は、少し待ってから打ちます（旧 worker の応答が混ざります）
- `/image` をファイル化したら、EBS 上に置きます。`proxy_cache_path` を `/tmp` や `/dev/shm` にしません（[§1](#1-投稿の本体データは-mysql-と-ebs) の線引きです）
- DB の `imgdata` を消しません。キャッシュは複製です。投稿の本体データは MySQL のまま残します

- ベンチ前後で、HIT 率を数えます（[§3](#3-nginx-hit-miss-を読む) の型）。
- `cache:-` は、proxy を経由しない行です（nginx が直接返す静的ファイル等）。失敗ではありません。

```bash
# bench-prep で access.log を回してから
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
sudo cat /var/log/nginx/access.log | grep -o 'cache:[A-Z-]*' | sort | uniq -c | sort -nr
sudo cat /var/log/nginx/access.log | grep '/image' | grep -o 'cache:[A-Z-]*' | sort | uniq -c | sort -nr
echo "$(date -Iseconds)  score=  pass=  fail=  nginx_hit=  note=nginx-image-60s" >> ~/bench-notes/scores.txt
```

#### 出力例

2026-10-13 の実測（`c7a.large` 1 台 / `ap-northeast-1` / 単一構成 / `nproc=2` / メモリ 3.7Gi。`/image` を `proxy_cache` 60s にした状態）:

`{"pass":true,"score":23016,"success":19919,"fail":0,"messages":[]}`

`curl -sI` 3 回（[§3](#3-nginx-hit-miss-を読む) の確認）:

```text
1回目: X-Cache: MISS
2回目: X-Cache: HIT
3回目: X-Cache: HIT
```

`/image` のキャッシュ分布:

```text
   8824 cache:HIT
   1397 cache:BYPASS
   1002 cache:MISS
```

- HIT 率 = `8824 / 9826 = 89.8%`（`/image` の HIT+MISS 分母。BYPASS 1397 は session 付きで対象外）。
- 全体では、`8824 / 19804 = 44.6%` です。

`alp ltsv --sort sum` の `/image` 変化:

| | COUNT | SUM | AVG | P90 | P99 | SUM(BODY) |
| --- | --- | --- | --- | --- | --- | --- |
| ベースライン（キャッシュ前。今回 B1） | 7699 | 319.447 | 0.041 | 0.072 | 0.124 | 2363563900 |
| 0030（キャッシュ後。今回 B3） | 11223 | 94.136 | **0.008** | **0.036** | 0.080 | 3998083215 |

- AVG が、0.041 秒から 0.008 秒へ落ちました。
- P90 が、0.072 秒から 0.036 秒へ落ちました。
- 点数も、18153 から 23016 へ上がりました（+4863）。

- `SUM(BODY)` が増えても、問題ありません。
- キャッシュが効くと、同じ画像を返す応答が増えているためです。

#### この節でつまずいた点

1. **`sites-enabled/isucon.conf` は symlink で、実体は `sites-available/isucon.conf`**

   ```text
   /etc/nginx/sites-enabled/isucon.conf -> /etc/nginx/sites-available/isucon.conf
   ```

   - 当時は、設定への書き込みで `Permission denied` が出ました。
   - root 所有の設定でも、`sudo` で置くだけなら `chown` は要りません。
   - 今は、コピー元の symlink を確認します。コピー先を `readlink -f` で実体に合わせて、`sudo` で置きます。
   - 所有者を `isucon` に変える前提は、外しました。

2. **§6 の `If-Modified-Since` の例の日付が 304 になりません**

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -H 'If-Modified-Since: Wed, 01 Jan 2025 00:00:00 GMT' http://127.0.0.1/image/1.jpg
   # 304 が返れば再検証 OK。ずっと 200 ならアプリが条件付き GET に対応していない
   ```

   - 固定の日付が、実際の mtime（ファイルの更新日時）より古ければ、変更済みと扱われ 200 になります。
   - この結果だけで、条件付き GET に未対応とは判断できません。
   - 今は §6 を、実際の `Last-Modified` を取る手順に直しています。

   ```bash
   LM=$(curl -sI http://127.0.0.1/css/style.css | grep -i '^Last-Modified:' | sed 's/^[Ll]ast-[Mm]odified: //' | tr -d '\r')
   curl -s -o /dev/null -w '%{http_code}\n' -H "If-Modified-Since: $LM" http://127.0.0.1/css/style.css
   ```

- 残す変更は、`backup/s1` に積みます（[0020-measure.md](0020-measure.md) の「残す変更は backup/s1 に積む」の流儀）。

### 本体データ確認と戻し

```bash
# キャッシュを消して再起動しても画像と投稿が残ること
sudo rm -rf /var/cache/nginx/image/*
sudo reboot
# 起きたあと
curl -fsS -o /dev/null -m 5 http://127.0.0.1/image/1.jpg
mysql -uisuconp -pisuconp isuconp -e "SELECT COUNT(*) FROM posts;"
```

- 戻すときは、作業機で足した行を消します。
- `proxy_cache_path` のディレクトリは、残してよいです。

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
SOURCE=backup/s1/etc/nginx/sites-enabled/isucon.conf
test ! -L "$SOURCE" || SOURCE=backup/s1/etc/nginx/sites-available/isucon.conf
TARGET=$(readlink -f /etc/nginx/sites-enabled/isucon.conf)
test -s "$SOURCE" && test -n "$TARGET" || exit 1
sudo cp -a "$SOURCE" "$TARGET"
sudo rm -f /etc/nginx/conf.d/cache-image.conf
sudo nginx -t && sudo systemctl reload nginx
```

### 観察すること

- `/image` の HIT 率を全体とパス別で言えます（`grep -o 'cache:...'` の 2 本です）
- `/image` の alp Sum が落ちました。HIT だけ増えて Sum が落ちないなら測る場所がずれています
- `fail` が 0 です。POST やログイン後を巻き込むと `fail` か別人表示になります。そのときは対象を狭めます（TTL を延ばしません）
- キャッシュ全消し + 再起動でデータが残ります。消えたら投稿の本体データを nginx だけにしています

## 6. Cache-Control を付ける前後で挙動を比べる

- 深掘り（`304` の再現・転送量・`ETag` の台間一致・点数判定）は、[0060-static-cache.md](0060-static-cache.md) です。
- ここは入口です。1 箇所だけ付けて、前後を見ます。

- [§2](#2-memcached-stats-を読む)〜[§5](#5-nginx-で-1-箇所だけキャッシュして-hit-率で評価する) は、サーバが応答を覚えるキャッシュです。
- ここは、ブラウザが覚えるキャッシュです（`Cache-Control` は「この応答はこれだけの時間保存してよい」という指定）。覚える主体が違います。
- §5 までと、同時に入れて比べません。
- 1 箇所だけ付けて、`curl` とログで前後を見ます。

- 対象は、GET の読み物だけです（`/image` か `public/` 静的のどちらか 1 つ）。
- POST と、ログイン後の個人用ページには付けません。

### 付ける前: ヘッダが無いことと、毎回サーバまで来ることを確認する

```bash
curl -sI http://127.0.0.1/image/1.jpg | grep -i -E 'cache-control|etag|last-modified' || echo 'no cache headers (before)'
curl -sI http://127.0.0.1/css/style.css | grep -i -E 'cache-control|etag|expires' || echo 'no cache headers (before)'
```

- `curl` は、既定ではキャッシュしません。
- 2 回叩いた分だけ行が増えます（= 毎回サーバまで来ています）。

```bash
sudo cat /var/log/nginx/access.log | tail -3
curl -s -o /dev/null http://127.0.0.1/image/1.jpg
curl -s -o /dev/null http://127.0.0.1/image/1.jpg
sudo cat /var/log/nginx/access.log | tail -3
# 2 回叩いた分だけ行が増える = 毎回サーバまで来ている
```

### 付ける: Flask か nginx のどちらか 1 つ

- 全体の `after_request` には、付けません。対象の 1 箇所だけです。
- Flask と nginx の両方には、付けません。

- Flask 側で付ける例です。まず対象の関数を探します。

```bash
grep -rn 'def .*image\|send_file\|make_response\|Response' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
```

`backup/s1/home/private_isu/webapp/python/<対象>.py` の対象の `return` の直前:

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

- nginx 側で付ける例です。
- `backup/s1/etc/nginx/sites-enabled/isucon.conf` の対象 location だけに付けます（全体には付けません）。

```nginx
location /css/ {
  # expires だけで Cache-Control: max-age と Expires が付く。add_header の重ね書きは要らない
  expires 60s;
}
```

- `max-age` は、まず 60 です。長くする前に、前後の挙動差を見ます。
- 個人用ページに `public`（誰のブラウザでも保存してよい）を付けると、他人に見えます。
- 迷ったら、対象を `/image` の匿名 GET だけにします。

```bash
# Flask を変えたら（同じ台で反映）
git pull --ff-only
sudo cp -a backup/s1/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
# nginx を変えたら（同じ台で反映）
git pull --ff-only
SOURCE=backup/s1/etc/nginx/sites-enabled/isucon.conf
test ! -L "$SOURCE" || SOURCE=backup/s1/etc/nginx/sites-available/isucon.conf
TARGET=$(readlink -f /etc/nginx/sites-enabled/isucon.conf)
test -s "$SOURCE" && test -n "$TARGET" || exit 1
sudo cp -a "$SOURCE" "$TARGET"
sudo nginx -t && sudo systemctl reload nginx
```

### 付けた後: ヘッダと 2 回目の到達を比べます

```bash
curl -sI http://127.0.0.1/image/1.jpg | grep -i 'cache-control'
# Cache-Control: public, max-age=60 が見えること
curl -sD - -o /dev/null http://127.0.0.1/image/1.jpg | grep -i -E 'HTTP/|cache-control|etag|last-modified'
```

- ブラウザがやる再検証（「まだ同じ物か」だけを確かめる軽い通信）を、`curl` で再現します（`ETag` / `Last-Modified` が出ているときだけです）。

```bash
URL=http://127.0.0.1/css/style.css
LM=$(curl -fsSI "$URL" | grep -i '^last-modified:' | tr -d '\r' | cut -d' ' -f2-)
test -n "$LM" || exit 1
curl -s -o /dev/null -w 'http=%{http_code} size=%{size_download}\n' \
  -H "If-Modified-Since: $LM" "$URL"
# 304かつsize=0なら再検証OK。固定日付ではなく取得した値を使う
```

- `curl` はキャッシュしません。サーバへの到達の有無は、ブラウザで確認します。

1. DevTools（ブラウザの開発者ツール）→ Network を開き、`Disable cache` のチェックを外します
2. `/image/1.jpg` を2回開きます
3. 1 回目 `200`、2 回目 `200 (memory cache)` / `200 (disk cache)` か `304` ならブラウザが覚えています
4. memory / disk cache ならサーバへの到達は減ります。304 なら再検証がサーバへ届くので `access.log` は増えますが、本文は返りません

```bash
# bench-prep で access.log を回してから公式ベンチ 1 本。ブラウザ cache はベンチマーカーが再現しないことがある
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=cache-control-image-60" >> ~/bench-notes/scores.txt
```

- 実測（2026-10-13、`c7a.large` 1 台 / `ap-northeast-1` / 単一構成 / `nproc=2` / メモリ 3.7Gi。Flask `get_image` に `Cache-Control: public, max-age=60` を付与）。
- 結果: `{"pass":true,"score":21963,"success":19004,"fail":0,"messages":[]}`
- B3 比 -1053（ノイズ内。ベンチマーカーはブラウザ cache を再現しないため、点数は動かなくてよい）。
- 付けた行は、戻した（`curl` でヘッダ消滅を確認）。

- 注意: ベンチマーカーは、ブラウザキャッシュを再現しないことが多いです。点数は動かなくてもよく、判定は `curl` + ログの前後差です。
- `Cache-Control: private` / `no-store` を付けると、`proxy_cache` が効かなくなることがあります。

- 戻すときは、作業機で付けた箇所を消します（Flask の 1 行か nginx の `expires` 1 行）。

```bash
# 作業機。付けた行を消す
git pull --ff-only
git add -A && git commit -m "tune: Cache-Control を外す" && git push
```

サーバーで反映します:

```bash
# Flask を戻したら（同じ台で打つ）
git pull --ff-only
sudo cp -a backup/s1/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
# nginx を戻したら
git pull --ff-only
SOURCE=backup/s1/etc/nginx/sites-enabled/isucon.conf
test ! -L "$SOURCE" || SOURCE=backup/s1/etc/nginx/sites-available/isucon.conf
TARGET=$(readlink -f /etc/nginx/sites-enabled/isucon.conf)
test -s "$SOURCE" && test -n "$TARGET" || exit 1
sudo cp -a "$SOURCE" "$TARGET"
sudo nginx -t && sudo systemctl reload nginx
curl -sI http://127.0.0.1/image/1.jpg | grep -i 'cache-control' || echo 'reverted (no header)'
```

### 観察すること

- 付ける前: ヘッダ無し + `curl` 2 回でログ 2 行です。付けた後: `Cache-Control: public, max-age=60` が見えます
- ブラウザキャッシュならサーバのログが増えず、304 ならログは増えますが本文サイズが 0 になることを見分けます
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
- `fail` や他人表示: `public` を個人用ページに付けています。対象を匿名 GET（`/image`、静的）だけに戻します
- `proxy_cache` の HIT が消えた: `Cache-Control: private` / `no-store` と干渉しています。`proxy_ignore_headers` を足す前に、対象の狭さとヘッダの中身を見ます
