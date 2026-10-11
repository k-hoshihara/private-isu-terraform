# 0040 HTTPクライアントのコネクションを使い回す

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）は、手元の private-isu-terraform リポジトリで実行します。

[0030-cache.md](0030-cache.md) の続きです。

- 「達人が教える Web パフォーマンスチューニング」8-3「HTTPクライアントの使い方」の 3 点を扱います。
- 同じ相手（ホスト）への接続（コネクション）に絞って、数字で確認します。

- 同一ホストへのコネクションを使い回す（接続の取り決めを毎回やり直す手間を減らす。TCP + TLS のハンドシェイクと呼びます）
- 適切なタイムアウト（待ち切れの時間）を設定する（いつまでも待つ状態をなくす）
- 同一ホストに大量のリクエストを送る場合、コネクション数の上限を確認する

- ゴールは、「使い回した / タイムアウトを付けた / 上限を絞った」の前後を、数字で言えることです。
- 使う数字は、`ss` の `TIME-WAIT`（使い終わった接続の記録が残っている状態）と、`oha` / 公式ベンチです。
- 「入れた」だけでは、終わりません。

- 順番: 探す（1.）→ 合成再現で差を見る（2.〜3.）→ nginx の使い回し（4.）→ タイムアウト（5.）→ 上限（6.）→ 公式ベンチ判定（7.）。
- 1 つずつ進めます。
- `fail`（エラーの件数）が 0 でない点数は、比べません。
- 出口は [0050-timeout.md](0050-timeout.md) です。

- 点数は、`~/bench-notes/scores.txt` に 1 行書きます。`tw`（TIME-WAIT 数）も一緒に書きます。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  tw=  oha_p99=  note=http-baseline" >> ~/bench-notes/scores.txt
```

| 回 | score | tw | oha_p99 | メモ |
| --- | --- | --- | --- | --- |
| ベースライン | 23016 | - | - | 2026-10-13、`c7a.large` 1 台 / `ap-northeast-1` / 単一構成 / `nproc=2` / メモリ 3.7Gi。0030 の proxy_cache 残し・keepalive 前（今回 B3） |
| 1 つ目 | 22296 | 3740→9150（`:8080` は 6065） | 0.17s | nginx keepalive 32。B3 比 -720（ノイズ内。上流が `close` を返すため効果限定） |

<a id="1-どこで-http-を投げているか探す"></a>

## 1. どこで HTTP を投げているか探す

- 直す前に、自分のアプリがどこで外へ HTTP を投げているか確かめます。
- private-isu の素体（配布された元の状態）は、DB / memcached 直結です。外への HTTP は、無いことが多いです。
- そのときは、[§2](#2-合成再現で差を見る使い回す-vs-使い捨て)〜[§3](#3-body-を読み切って-close-する) の合成再現（本物の代わりの小さなプログラムで動きを再現すること）で型を掴みます。
- そして、[§4](#4-nginx-の-upstream-を使い回す1-台でも効く) の nginx upstream（nginx がつなぐ相手先の指定）を 1 つにします。

```bash
grep -rn 'requests\.\(get\|post\|Session\)\|urllib\|http\.client\|HTTPConnection' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
sudo nginx -T 2>/dev/null | grep -E 'proxy_pass|upstream' | head -20
```

読み方:

- Python で `requests.get` を毎回呼ぶ → `Session` にまとめます（[§2](#2-合成再現で差を見る使い回す-vs-使い捨て)）
- `proxy_pass http://...` → [§4](#4-nginx-の-upstream-を使い回す1-台でも効く) の nginx upstream が HTTP クライアントの役割をしています。アプリ側に HTTP 呼び出しが無くてもここは必ずあります
- どこにも無い → §2〜§3 は合成スクリプトで練習し、§4 を 1 つにします

### 観察すること

- 自分の 1 つが「アプリの client」「nginx の upstream」のどちらかを言えます
- 対象のホスト（`127.0.0.1:8080`）を言えます。複数台に分けたあとは [0110-split-2.md](0110-split-2.md) の `APP_PRIV` に変わります
- 見つからなければ合成再現から入ります（無理にアプリをいじりません）

<a id="2-合成再現で差を見る使い回す-vs-使い捨て"></a>

## 2. 合成再現で差を見る（使い回す vs 使い捨て）

- アプリを変える前に、`/tmp` の小さなスクリプトで差を出します。
- 重いアプリだと、応答時間が支配的で差が見えません。まず軽い静的配信（nginx の `:80` `/css/style.css`）で型を掴みます。
- アプリ直結（gunicorn の `:8080` `/`）でも回せます。ただし 200 本で数分かかります。

<a id="2a-コネクション数の見方を固定する"></a>

### 2a. コネクション数の見方を固定する

```bash
ss -tan state time-wait | wc -l
ss -tan state established '( dport = :8080 or sport = :8080 )' | head -20
cat /proc/sys/net/ipv4/ip_local_port_range
ulimit -n
```

?> `state` と `time-wait` は、別々の引数で渡します。`ss -tan 'state time-wait'` は 1 つの引数になり、この検証では解析エラーになりました。`wc -l` だけを見ると、エラーを 0 件と誤認します。stderr も確認します。件数を数えるときは `ss -Htan state time-wait | wc -l` とします。`-H` でヘッダを除きます。

- `time-wait` が毎回数百増える → 毎回接続を作り捨てています
- `established`（つながっている接続）が張りっぱなしで増えない → 使い回しています
- `ip_local_port_range` は既定 `32768 60999`（約 28k）です。TIME-WAIT（60 秒残ります）が溜まると、使い捨てでは数百 rps で頭打ちになります

### 2b. Python で比べる（標準のみ）

- `requests` が無くても動く、`http.client` 版です。
- 使い回す方は、`HTTPConnection` を 1 本作って使い回します。
- 使い捨てる方は、毎回作り直します（いちばん悪い形の再現です）。

- `/tmp/reuse_py.py` です。相手は引数で変えます。
- 静的なら nginx の `:80` `/css/style.css`。アプリ直結なら gunicorn の `:8080` `/` です。

```python
import http.client, sys, time
reuse = sys.argv[1] == "reuse" if len(sys.argv) > 1 else True
n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
url_host = sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1"
url_port = int(sys.argv[4]) if len(sys.argv) > 4 else 8080
url_path = sys.argv[5] if len(sys.argv) > 5 else "/"
conn = http.client.HTTPConnection(url_host, url_port, timeout=5) if reuse else None
t0 = time.time()
for _ in range(n):
    c = conn if reuse else http.client.HTTPConnection(url_host, url_port, timeout=5)
    c.request("GET", url_path)
    r = c.getresponse()
    r.read()
    if not reuse:
        c.close()
print(f"reuse={reuse} n={n} elapsed={time.time()-t0:.2f}s")
```

```bash
# 1 台で打つ（静的。速いので 200 本でも一瞬）
echo "--- before ---"
ss -tan state time-wait | wc -l
python3 /tmp/reuse_py.py reuse 200 127.0.0.1 80 /css/style.css
echo "--- after reuse ---"
ss -tan state time-wait | wc -l
python3 /tmp/reuse_py.py noreuse 200 127.0.0.1 80 /css/style.css
echo "--- after noreuse ---"
ss -tan state time-wait | wc -l
# 実測例: reuse は tw+201、noreuse はさらに tw+402。elapsed は 0.13s 対 0.15s（2026-10-13、`c7a.large` 1 台 / 単一構成。相手は nginx `:80` `/css/style.css` 200 本）
```

- `requests` を使っているアプリなら、対応は次のとおりです（コピペ用の完成品ではありません。方針です）。
- `Session` を、プロセス全体で 1 個作ります。
- gunicorn はワーカー別プロセスです。ワーカー毎に 1 個になります。
- リクエスト毎に、`Session()` を作りません。

- `backup/s1/home/private_isu/webapp/python/app.py` のモジュール先頭に書きます（リクエスト毎ではありません。1 回だけです）。

```python
import requests
from requests.adapters import HTTPAdapter

session = requests.Session()
session.mount("http://", HTTPAdapter(pool_connections=20, pool_maxsize=20))
session.mount("https://", HTTPAdapter(pool_connections=20, pool_maxsize=20))
# 使う側は毎回 session.get(url, timeout=(3, 5))
# 受けたら resp.content を読んでから閉じる（3. と同じ）
```

### 観察すること

- reuse / noreuse の `elapsed`（かかった時間）と `time-wait` 増分を 2 つの数字で言えます
- `established` が reuse では張りっぱなしで、noreuse では残りません

<a id="3-body-を読み切って-close-する"></a>

## 3. Body を読み切って Close する

- `read()` / `content` を読まずに捨てると、そのコネクションは使い回しの棚（プール）に戻りません。切断されます。
- §2b の `r.read()` が、読み切りの行です。

- 確認は、コードの目視と `ss` の両方でやります。
- 形だけ閉じても、読んでいなければ `time-wait` は減りません。

```bash
# Python: content/read までやっているか
grep -rn '\.content\|resp\.read\|getresponse\|Session' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
```

- `backup/s1/home/private_isu/webapp/python/<対象>.py` の 1 箇所だけ直します（`requests` の場合）。

```python
# 読んでから閉じる
with session.get(url, timeout=(3, 5)) as r:
    r.raise_for_status()
    body = r.content
```

- §2b をもう一度回します。`time-wait` 増分が reuse 側に寄ったか見ます。
- 寄らなければ、まだ読み切り漏れがあります。または、別箇所で使い捨て接続を作っています。

### 観察すること

- `Close` と読み切りがセットになっています（片方だけにしません）
- 直す前後で `time-wait` 増分が変わりました
- `fail` が出ません（読み切りを足しただけで挙動は変わらないはずです）

<a id="4-nginx-の-upstream-を使い回す1-台でも効く"></a>

## 4. nginx の upstream を使い回す（1 台でも効く）

- アプリに外への HTTP が無くても、nginx → アプリは毎リクエスト HTTP しています。
- 1 台でも、効きます。
- 複数台に分けたあとは（s1 → s2:8080）、台をまたぎます。効きが大きくなります。

- 今の upstream を見ます。

```bash
sudo nginx -T 2>/dev/null | grep -E 'proxy_pass|upstream|keepalive|proxy_http_version' | head -30
sudo nginx -t
```

- 素体の site conf は、`location /` が `proxy_pass http://localhost:8080;` です。
- 丸ごと上書きしません。`upstream` 定義を 1 つ足して、`location /` から向ける形に変えます。
- `upstream` は、`server` ブロックの中に書けません（`nginx -t` が落ちます）。

`backup/s1/etc/nginx/conf.d/upstream.conf`:

```nginx
upstream app {
  server 127.0.0.1:8080;
  keepalive 32;
}
```

- `backup/s1/etc/nginx/sites-enabled/isucon.conf` の `location /` を、次の内容に変えます。
- [0030-cache.md](0030-cache.md) §5 で `location /image/` を足していれば、そこも同じ 3 行に揃えます。
- `proxy_cache` の行は、残します。

```nginx
location / {
  proxy_http_version 1.1;
  proxy_set_header Host $host;
  proxy_set_header Connection "";
  proxy_pass http://app;
}
```

```bash
# 作業機
git pull --ff-only
# 上の内容を各ファイルに配置する
git add -A && git commit -m "tune: nginx upstream に keepalive を付ける" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
SITE=/etc/nginx/sites-enabled/isucon.conf
sudo test -f "${SITE}.orig" || sudo cp -a "$SITE" "${SITE}.orig"
sudo cp -a backup/s1/etc/nginx/conf.d/upstream.conf /etc/nginx/conf.d/upstream.conf
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf "$SITE"
sudo nginx -t && sudo systemctl reload nginx
```

注意:

- `proxy_http_version 1.1;` と `proxy_set_header Connection "";` が無いと keepalive（接続の使い回し）になりません（1.0 のまま閉じます）。`location` の内側に書きます
- `keepalive` はまず 32 にします。大きくする前に効果を見ます
- keepalive は TCP の相手にだけ効きます。unix socket では意味がありません（[0110-split-2.md](0110-split-2.md) の s1 は unix socket を使うので、この 1 つは効きません）
- 上流（gunicorn）が毎回 `Connection: close` を返す構成では keepalive は効きません。`curl -s -D - -o /dev/null http://127.0.0.1:8080/login | grep -i connection` で `close` が返るか先に見ます。`close` のまま `time-wait` が減らなければ、この 1 つは戻します（上流を変えずに先に進めません）

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/login
sudo journalctl -u nginx -n 20 --no-pager || sudo tail -20 /var/log/nginx/error.log
```

- 効果は `oha`（簡易的な負荷検査ツール）と `ss` で見ます（bench-prep でログを回してから）。

```bash
echo "--- before keepalive ---"
ss -tan state time-wait | wc -l
oha -n 1000 -c 20 --no-tui http://127.0.0.1/
echo "--- after ---"
ss -tan state time-wait | wc -l
sudo cat /var/log/nginx/access.log | tail -3
```

- `time-wait` が減り、`oha` の p99（遅い順に並べて 99 番目の応答時間）が下がれば、成功です。
- 変わらないときは、`proxy_http_version` / `Connection` の付け場所を見ます（`location` の内側か）。
- あわせて、上流が `Connection: close` を返していないかも見ます（上の注意）。
- 使い回しが張り付いた証拠は、`ss -tan state established` の相手先 `:8080` 行が残ることです。

### 観察すること

- `nginx -t` が通り、`curl` 2 本が 200 になることです
- `oha` の前後で `time-wait` 増分と p99 を言えます
- 使い回しが張り付いた証拠は `:8080` への `established` が残ることです（複数台構成なら `APP_PRIV` 側）

<a id="5-タイムアウトを付ける無期限待ちをなくす"></a>

## 5. タイムアウトを付ける（無期限待ちをなくす）

- 使い回しができたら、次は `timeout` を付けます。ワーカーが固まらないようにするためです。
- `requests` の `timeout` 無しは、無期限に待ちます。
- 相手が詰まると、こちらの gunicorn ワーカーまで固まります。
- まず `timeout=(3, 5)` を付けます。

- 今のタイムアウトを洗い出します。

```bash
grep -rn 'timeout' --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
grep -rn 'proxy_.*_timeout\|keepalive_timeout' /etc/nginx/sites-enabled/* /etc/nginx/conf.d/ 2>/dev/null | head
```

- `backup/s1/home/private_isu/webapp/python/<対象>.py` の、使う箇所を 1 箇所ずつ直します。

```python
session.get(url, timeout=(3, 5))  # (connect, read)
```

- `timeout` 無しが既定です。必ず付けます。まず接続の待ち 3 秒 / データの待ち 5 秒にします
- 単数値（例: `timeout=5`）はconnectとreadのそれぞれに5秒を指定します。リクエスト全体の締め切りではありません。別々の値にするならタプルにします
- gunicorn の `--timeout`（固まったワーカーを殺す設定）と nginx の `proxy_connect_timeout 5s;` / `proxy_read_timeout 60s;` は別物です。アプリの `timeout` を先に付けてから、nginx 側が長すぎないか見ます
- 受け側は例外を握りつぶしません。`try` / `except requests.Timeout` で代替の応答（フォールバック）か 502 系に寄せます

```bash
# 作業機
git pull --ff-only
# 上の内容を配置する
git add -A && git commit -m "tune: <対象> に timeout を付ける" && git push
```

- 変えたら、restart / reload します。
- `curl` と `oha` で、`fail` が出ないことを確認します。
- タイムアウトを短くしすぎると、`fail` になります。そのときは、値を戻します（やり直しや上限を足しません）。

```bash
# Python を変えたら（サーバーで反映）
git pull --ff-only
sudo cp -a backup/s1/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
# nginx を変えたら（サーバーで反映）
git pull --ff-only
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- `timeout` 無しの箇所が残っていません（`grep -v timeout` が 0 行です）
- 短くしても `oha` が `fail` しない値に落ち着きました
- p99 が read timeout に張り付いていません（張り付いたら相手側の問題で、こちらの値は延ばしません）

<a id="6-同一ホストへの上限を確認する絞りすぎない"></a>

## 6. 同一ホストへの上限を確認する（絞りすぎない）

- 大量リクエストでは、「使い回す数」の上限が効きます。
- 多すぎると、相手に負荷をかけます。少なすぎると、自分が詰まります。
- 変えるのは、1 箇所だけです。
- 変更は作業機で入れます。サーバーでは pull + cp + restart / reload をします（§5 と同じ流し方です）。

- 目安は、次のとおりです（まずここから始めます。ベンチで比べます）。

| 層 | 項目 | 出発点 | 絞りすぎの症状 |
| --- | --- | --- | --- |
| Python `HTTPAdapter` | `pool_connections` | 10（ホスト種別数）。まず触らない | 種別が増えたら足りなくなる |
| Python `HTTPAdapter` | `pool_maxsize` | 20 | プール待ちで遅くなる |
| nginx upstream | `keepalive` | 32 | `time-wait` が減らない |
| OS | `ulimit -n` / local port | 触らない。見るだけ | `too many open files` / port 枯渇 |

```bash
# 上限の今を見る
grep -rn 'pool_maxsize\|pool_connections\|HTTPAdapter' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head
grep -rn 'keepalive' /etc/nginx/sites-enabled/* /etc/nginx/conf.d/ 2>/dev/null | head
ulimit -n
ss -s | head -10
```

- 負荷をかけて確認します。`oha -c` を上げます。ベンチマーカーの前にやります。

```bash
oha -n 2000 -c 50 --no-tui http://127.0.0.1/
ss -tan state time-wait | wc -l
ss -tan state established '( dport = :8080 or sport = :8080 )' | wc -l
dmesg | tail -5
```

- `pool_maxsize` をいきなり 4 まで絞りません。p99 が跳ねたら上限が原因の可能性が高いです。元の値に戻してから次へ進みます
- `too many open files` が出たらアプリの上限ではなく `ulimit -n` / `worker_rlimit_nofile`（同時に開けるファイル数の上限）を見ます。アプリの値を絞る前に `ss -s` と `dmesg` を見ます
- TLS（HTTPS）の相手なら、接続を作る回数＝取り決めのやり直し（ハンドシェイク）の回数です。使い回し（2.）と上限（6.）はセットで見ます

### 観察すること

- 変えた上限は 1 箇所だけです。その値と理由（`oha -c 50` の p99 / `time-wait`）を言えます
- 絞ったら p99 が上がり、戻したら下がることを確認しました
- `fail` が 0 です。`fail` が出たら上限を緩めます（タイムアウトを延ばしません）

## 7. 公式ベンチで判定する

- [§2](#2-合成再現で差を見る使い回す-vs-使い捨て)〜[§6](#6-同一ホストへの上限を確認する絞りすぎない) のどれか 1 つを残します。他は、戻した状態にします。
- その状態で、公式ベンチを 1 本回します。
- bench-prep は [0020-measure.md](0020-measure.md) と同じです（nginx のログは `mv` + `reopen`、slow log は `flush-logs`。同じ台です）。

```bash
# 1 台で打つ
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
fi
sudo nginx -s reopen
TW_BEFORE=$(ss -tan state time-wait | wc -l)
echo "tw_before=$TW_BEFORE"
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
TW_AFTER=$(ss -tan state time-wait | wc -l)
echo "tw_before=$TW_BEFORE tw_after=$TW_AFTER"
echo "$(date -Iseconds)  score=  pass=  fail=  tw=${TW_BEFORE}-${TW_AFTER}  note=http-keepalive-32" >> ~/bench-notes/scores.txt
```

実測サンプル（今回 B5）: `{"pass":true,"score":22296,"success":19213,"fail":0,"messages":[]}`（`tw` 3740→9150。[0030-cache.md](0030-cache.md) の 23016 から -720 でノイズ内。upstream が `Connection: close` を返すため効果は限定的でした。下の「実行例」参照）。

```bash
# 同じ台で打つ（slow log の回転）
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysqladmin flush-logs 2>/dev/null || sudo mysql -e 'FLUSH SLOW LOGS'
```

- 残す変更は、`backup/s1` に積みます（[0020-measure.md](0020-measure.md) の「残す変更は backup/s1 に積む」の流儀）。

- 判定は、点数＋`tw` 増分＋alp です。点だけを見ません。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
```

- 効かなければ、残しません。`scores.txt` に 1 行残して、戻します。
- 戻し方は、変えた層だけやります。

```bash
# nginx を戻すとき（作業機で drop-in や site conf の keepalive 関係 3 行を消す）
git pull --ff-only
# `proxy_http_version`・`Connection`・`keepalive` を消して push する
git add -A && git commit -m "tune: nginx の keepalive を外す" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s1/etc/nginx/conf.d/upstream.conf /etc/nginx/conf.d/upstream.conf
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo nginx -t && sudo systemctl reload nginx
# Python を戻すとき
git pull --ff-only
sudo cp -a backup/s1/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- `tw_before → tw_after` の増分がベースラインより小さいです
- alp の Sum が落ちましたか。落ちないなら HTTP 層は詰まっていません（DB / キャッシュの仕事に戻ります）
- `fail` が 0 です。`fail` が出たら §5〜§6 の締めすぎを疑います

#### 出力例

2026-10-13 の実測（`c7a.large` 1 台 / `ap-northeast-1` / 単一構成 / `nproc=2` / メモリ 3.7Gi）。

§2b の使い回しの型（相手は nginx `:80` `/css/style.css` 200 本）:

```text
--- reuse（使い回す）200 本 ---
reuse=True n=200 elapsed=0.13s
  before = 3 → after reuse = 204（増分 201）
--- noreuse（毎回作り直し）200 本 ---
reuse=False n=200 elapsed=0.15s
  after reuse = 204 → after noreuse = 606（増分 402）
```

- reuse 側の +201 は、nginx → gunicorn の上流コネクションです（`proxy_http_version` 1.0 のまま・keepalive 前なので毎回張り直し）。
- noreuse 側は、それに client → nginx の 200 本が上乗せされます。
- 使い回しは、client 側の TW を消します。上流の接続の作り直しは、§4 の keepalive の仕事になります。

§4 の keepalive（単一構成なので upstream `app` が 1 つ。`location /` と `location /image/` の両方に同じ 3 行を入れた。`proxy_cache` の行は残す）:

```nginx
# /etc/nginx/conf.d/upstream.conf
upstream app {
  server 127.0.0.1:8080;
  keepalive 32;
}
```

```nginx
# location / と location /image/（各 location の内側）
proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header Connection "";
proxy_pass http://app;
```

§4 適用後の `oha -n 1000 -c 20 --no-tui http://127.0.0.1/`（`location /` は `cache:-` で毎回アプリまで行く）:

```text
Success rate:  100.00%
Average:       154.1985 ms / Slowest: 166.3886 ms
99.00% in 165.2933 ms（p99 ≒ 0.17s）
tw_before=612 → tw_after=1530（増分 918）
established (:8080) = 0
```

§6 の `oha -n 2000 -c 50 --no-tui http://127.0.0.1/`:

```text
Success rate:  100.00%
Average:       376.5707 ms / Slowest: 392.7459 ms
99.00% in 391.7388 ms（p99 ≒ 0.39s）
tw = 4236 / established (:8080) = 0
```

- `dmesg` に `too many open files` は出ません。
- 上限は変えず、`keepalive 32` のまま残します。

#### 実行例：

- [0030-cache.md](0030-cache.md) の proxy_cache を残した状態で、nginx upstream に `keepalive 32` を足しました。
- `proxy_http_version 1.1` + `proxy_set_header Connection ""` を、両 location に追加しました。
- §5 は、アプリに HTTP クライアントが無い（`grep` 0 件）ため、スキップしました。
- §6 も、上限変更なしで判定しました。

```text
$ sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
    -u /home/isucon/private_isu/benchmarker/userdata \
    -t http://localhost
{"pass":true,"score":22296,"success":19213,"fail":0,"messages":[]}
tw_before=3740 tw_after=9150（増分 5410）
ss -Htan state time-wait | grep -c ':8080'  ->  6065（ベンチ直後）
```

| 状態 | score | fail | 備考 |
| --- | --- | --- | --- |
| 0030 proxy_cache（今回 B3） | 23016 | 0 | keepalive 前 |
| **0040 keepalive 32（今回 B5）** | **22296** | **0** | **-720（ノイズ内）** |

- **点数の差は、再現性が確認できていません。効果を確定できません。**
- `curl -sD -` で、upstream の gunicorn は `Connection: close` を返します（`http://127.0.0.1:8080/login`）。
- ベンチ後の `time-wait :8080` は、6065 本と減っていません。
- `ss` で `established :8080` は、ベンチ後に 0 本です。upstream の使い回しは、効いていない可能性が高いです。
- [§4](#4-nginx-の-upstream-を使い回す1-台でも効く) の注意どおり、上流が `Connection: close` のままでは、keepalive は効きません。

§7 の公式ベンチ:

`{"pass":true,"score":22296,"success":19213,"fail":0,"messages":[]}`

| 回 | score | tw | メモ |
| --- | --- | --- | --- |
| 0020 ベースライン（2026-10-13） | 15125 | - | HTTP クライアント無調整 |
| 0030 `/image` proxy_cache（今回 B3） | 23016 | - | +7891（構成同一だがベンチの再現性の幅を含む） |
| 0040 nginx keepalive 32（今回 B5） | 22296 | 3740→9150 | -720（ノイズ内、upstream が close を返すため効果限定的） |

- 点数の改善は、わずかです。`:8080` の `time-wait` は、減っていません。
- 上流（gunicorn）が `Connection: close` を返す構成です。keepalive は、この 1 つでは効いていません。
- 効かせるには、上流側の対応が必要です。次の改善点は、[0070-infra-params.md §5](0070-infra-params.md#5-gunicorn-の-worker-プロセス数少なすぎも多すぎも遅い) の worker 増加です。

#### この節でつまずいた点

1. **`ss` のフィルタが引用符内で動きません**（[§2a](#2a-コネクション数の見方を固定する) :57、[§4](#4-nginx-の-upstream-を使い回す1-台でも効く) :231、[§6](#6-同一ホストへの上限を確認する絞りすぎない) :323）

   ```bash
   $ ss -tan 'state time-wait' | wc -l
   Error: an inet prefix is expected rather than "state".
   Cannot parse dst/src address.
   ```

   引用符の中のスペースは引数を分割しません。`state time-wait` が 1 つの引数になり、`ss` が読めなくなります。次の形に直しました:

   ```bash
   ss -Htan state time-wait | wc -l
   ```

   同じ誤用をしていた §2・§4・§6 も引用符なしに直しました。条件式の括弧を引用符で囲むのは別の話です。

2. **§2 のアプリ直結の測定時間が読めません**（:52）

   > アプリ直結（s2 の `:8080` `/`）でも回せますが、200 本で数分かかります。

   - 1 つずつ順に実行する合成スクリプトでは、応答が 2.3〜3.9 秒なら 200 本で 8〜13 分かかります。
   - `oha -c 50` は並列です。この計算は使えません。
   - 終わらない負荷を避けるため、nginx の接続確認には軽い経路を使います。
   - 動的経路の負荷は、`oha -z 10s -c 20 --timeout 5s --no-tui http://127.0.0.1/` のように、時間と待ち時間に上限を付けます。

3. **§5（タイムアウト）の変更対象が存在しません**

   - [§5](#5-タイムアウトを付ける無期限待ちをなくす) は、`session.get(url, timeout=(3, 5))` を入れます。
   - ただし、private-isu の Python には HTTP クライアントがありません（[§1](#1-どこで-http-を投げているか探す) の `grep` で 0 件）。
   - `grep` で見ると、`app.py` の `connect_timeout = 5` だけあります。これは [0110-split-3.md](0110-split-3.md) §9 で入れた MySQL 接続用です。

   - [§1](#1-どこで-http-を投げているか探す) :42 の「どこにも無い → §2〜§3 は合成スクリプトで練習し、§4 を 1 つにします」の方が、正しい実装です。
   - §5 は、スキップしてください。

## 8. トラブルシュート

### TIME-WAIT が減らない

- `proxy_http_version 1.1` と `proxy_set_header Connection "";` が `location` の内側にあるか確認します。外側や `server` 直下では効かないことがあります
- Python でリクエスト毎に `Session()` を作っていないか確認します。モジュール先頭の 1 個ですか
- 読み切り漏れ（§3）です。読まずに捨てるとプールに戻りません
- 相手が `Connection: close` を返していないか確認します（gunicorn 直の `:8080` で打つ）。`curl -sD - -o /dev/null http://127.0.0.1:8080/ | grep -i connection`

### keepalive を入れたら 502 / fail

- upstream 名と `proxy_pass` がずれています（`http://app;` の `;` 忘れ、`server` のポート違い）
- unix socket の相手に keepalive は効きません。TCP の相手にだけ付けましたか
- `keepalive` の数が原因と決めつけません。まず `sudo nginx -t` と `journalctl -u isu-python.service` の直近 40 行を見ます

### タイムアウトで fail が出る

- 短くしすぎています。まず §5 の出発点（接続 3 秒 / データ 5 秒）に戻します
- タイムアウトの二重指定になっていないか確認します（`timeout=` と `urllib3.Timeout` の混在など）
- 相手の p99 がタイムアウトに張り付いているなら、こちらの値は延ばさず相手（DB / 上流）を直します。alp と slp に戻ります

### 上限を絞ったら遅くなる

- 正常です（絞って悪化を確かめる試験なので、遅くなるのが正解です）。`pool_maxsize` / `keepalive` を戻します。絞るのは `oha -c 50` で p99 が壊れない範囲だけです
- `too many open files` は上限の絞りすぎではなく FD（開いているファイルの数）不足です。`ulimit -n` と nginx の `worker_rlimit_nofile` を見ます。アプリのプールを絞っても直りません

### TLS（HTTPS）の場合

- VPC 内の素体は HTTP なので、ここでの `time-wait` 差が TLS の差にそのまま当てはまるわけではありません。HTTPS では接続の取り決めに数往復＋CPU がかかるので、§2 の差が拡大する方向に読みます
- 証明書の検証は外しません（`requests` の `verify=False` は入れません）
