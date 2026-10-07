# 0050 HTTPクライアントのコネクションを使い回す

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）はローカル環境の private-isu-terraform リポジトリにて実行します。

本の 8-3「HTTPクライアントの使い方」の 3 点を、同じホストへのコネクションに絞って数字で確認します。

- 同一ホストへのコネクションを使い回す（TCP + TLS のハンドシェイクを減らす）
- 適切なタイムアウトを設定する（無期限待ちをなくす）
- 同一ホストに大量のリクエストを送る場合、コネクション数の制限を確認する

ゴールは「使い回した / タイムアウトを付けた / 上限を絞った」の前後を、`ss` の `TIME-WAIT` と `oha` / 公式ベンチで言えることです。「入れた」だけでは終わりません。

順番: 探す（1.）→ 合成再現で差を見る（2.〜3.）→ nginx の使い回し（4.）→ タイムアウト（5.）→ 上限（6.）→ 公式ベンチ判定（7.）。1 手ずつ進めます。`fail` が 0 でない点数は比べません。出口は [0060-timeout.md](0060-timeout.md) です。

点数は `~/bench-notes/scores.txt` に 1 行。`tw`（TIME-WAIT 数）も一緒に書く:

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  tw=  oha_p99=  note=http-baseline" >> ~/bench-notes/scores.txt
```

| 回 | score | tw | oha_p99 | メモ |
| --- | --- | --- | --- | --- |
| ベースライン |  |  |  | [0030-measure.md](0030-measure.md) までの状態。HTTP クライアント無調整 |
| 1 手目 |  | 例: 12 | 例: 0.08s | 例: nginx keepalive 32 |

## 1. どこで HTTP を投げているか探す

直す前に、自分のアプリがどこで外へ HTTP を投げているか確かめます。private-isu の素体は DB / memcached 直結で、外への HTTP は無いことが多いです。そのときは [§2](#2-合成再現で差を見る使い回す-vs-使い捨て)〜[§3](#3-body-を読み切って-close-する) の合成再現で型を掴み、[§4](#4-nginx-の-upstream-を使い回す1-台でも効く) の nginx upstream を 1 手にします。

```bash
grep -rn 'requests\.\(get\|post\|Session\)\|urllib\|http\.client\|HTTPConnection' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
sudo nginx -T 2>/dev/null | grep -E 'proxy_pass|upstream' | head -20
```

読み方:

- Python で `requests.get` を毎回呼ぶ → `Session` にまとめます（[§2](#2-合成再現で差を見る使い回す-vs-使い捨て)）
- `proxy_pass http://...` → [§4](#4-nginx-の-upstream-を使い回す1-台でも効く) の nginx upstream が HTTP クライアントの役割をしています。アプリ側に HTTP 呼び出しが無くてもここは必ずあります
- どこにも無い → §2〜§3 は合成スクリプトで練習し、§4 を 1 手にします

### 観察すること

- 自分の 1 手が「アプリの client」「nginx の upstream」のどちらかを言えます
- 対象のホスト（`127.0.0.1:8080` なのか `s2:8080` なのか）を言えます。分割後は [0020-split-2.md](0020-split-2.md) の `APP_PRIV` です
- 見つからなければ合成再現から入ります（無理にアプリをいじりません）

## 2. 合成再現で差を見る（使い回す vs 使い捨て）

アプリを変える前に、`/tmp` の小さなスクリプトで差を出します。重いアプリだと応答時間が支配的で差が見えないので、まず軽い静的配信（s1 の `:80` `/css/style.css`）で型を掴みます。アプリ直結（s2 の `:8080` `/`）でも回せますが、200 本で数分かかります。

### 2a. コネクション数の見方を固定する

```bash
ss -tan 'state time-wait' | wc -l
ss -tan state established '( dport = :8080 or sport = :8080 )' | head -20
cat /proc/sys/net/ipv4/ip_local_port_range
ulimit -n
```

- `time-wait` が毎回数百増える → 使い捨てています
- `established` が張りっぱなしで増えない → 使い回しています
- `ip_local_port_range` は既定 `32768 60999`（約 28k）です。TIME-WAIT（60 秒）が溜まると、使い捨てでは数百 rps で頭打ちになります

### 2b. Python で比べる（標準のみ）

`requests` が無くても動く `http.client` 版です。使い回す方は `HTTPConnection` を 1 本作って使い回し、使い捨てる方は毎回作り直します（最悪形の再現です）。

`/tmp/reuse_py.py`（相手を引数で変えます。s1 静的なら s1 の `:80` `/css/style.css`、アプリ直結なら s2 の `:8080` `/`）:

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
# s1 で打つ（静的。速いので 200 本でも一瞬）
echo "--- before ---"
ss -tan 'state time-wait' | wc -l
python3 /tmp/reuse_py.py reuse 200 127.0.0.1 80 /css/style.css
echo "--- after reuse ---"
ss -tan 'state time-wait' | wc -l
python3 /tmp/reuse_py.py noreuse 200 127.0.0.1 80 /css/style.css
echo "--- after noreuse ---"
ss -tan 'state time-wait' | wc -l
# 実測例: reuse は tw+1、noreuse は tw+200。elapsed も 0.01s 対 0.03s と開く
```

`requests` を使っているアプリなら対応はこうなります（コピペ用の完成品ではなく方針です）。`Session` をプロセス全体で 1 個作ります（gunicorn はワーカー別プロセスなのでワーカー毎に 1 個になります）。リクエスト毎に `Session()` を作りません。

`backup/s2/home/private_isu/webapp/python/app.py` のモジュール先頭（リクエスト毎ではなく 1 回だけ）:

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

- reuse / noreuse の `elapsed` と `time-wait` 増分を 2 つの数字で言えます
- `established` が reuse では張りっぱなしで、noreuse では残りません

## 3. Body を読み切って Close する

`read()` / `content` を読まずに捨てると、そのコネクションはプールに戻らず切断されます。§2b の `r.read()` が読み切りの行です。

確認はコードの目視と `ss` の両方でやります。形だけ閉じても読んでいなければ `time-wait` は減りません。

```bash
# Python: content/read までやっているか
grep -rn '\.content\|resp\.read\|getresponse\|Session' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
```

`backup/s2/home/private_isu/webapp/python/<対象>.py` の 1 箇所だけ直します（`requests` の場合）:

```python
# 読んでから閉じる
with session.get(url, timeout=(3, 5)) as r:
    r.raise_for_status()
    body = r.content
```

§2b をもう一度回し、`time-wait` 増分が reuse 側に寄ったか見ます。寄らなければまだ読み切り漏れか、別箇所で使い捨て接続を作っています。

### 観察すること

- `Close` と読み切りがセットになっています（片方だけにしません）
- 直す前後で `time-wait` 増分が変わりました
- `fail` が出ません（読み切りを足しただけで挙動は変わらないはずです）

## 4. nginx の upstream を使い回す（1 台でも効く）

アプリに外への HTTP が無くても、nginx → アプリは毎リクエスト HTTP しています。分割後（s1 → s2:8080）は台をまたぐので必須です。

今の upstream を見ます:

```bash
sudo nginx -T 2>/dev/null | grep -E 'proxy_pass|upstream|keepalive|proxy_http_version' | head -30
sudo nginx -t
```

分割後は s1 に `upstream heavy`（s2:8080）が既にあります。site conf を丸ごと上書きしません（light / heavy の振り分けが消えます）。`keepalive` 1 行と、heavy へ向ける location への 2 行を足します。`upstream` は `server` ブロックの中に書けません（`nginx -t` が落ちます）。

`backup/s1/etc/nginx/conf.d/upstream.conf`:

```nginx
upstream heavy {
  server 10.42.0.181:8080;
  keepalive 32;
}
```

`backup/s1/etc/nginx/sites-enabled/isucon.conf` の heavy へ `proxy_pass` する 2 つの location（`/` と `@app`）:

```nginx
location / {
  proxy_http_version 1.1;
  proxy_set_header Host $host;
  proxy_set_header Connection "";
  proxy_pass http://heavy;
}

location @app {
  proxy_http_version 1.1;
  proxy_set_header Host $host;
  proxy_set_header Connection "";
  proxy_pass http://heavy;
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

- `proxy_http_version 1.1;` と `proxy_set_header Connection "";` が無いと keepalive になりません（1.0 のまま閉じます）。`location` の内側に書きます
- `keepalive` はまず 32 にします。大きくする前に効果を見ます
- s1 の unix socket（[0020-split-2.md §6](0020-split-2.md#6-s1-の-gunicornunix-socket)）は対象外です。TCP の heavy 側だけに残します
- 上流（gunicorn）が毎回 `Connection: close` を返す構成では keepalive は効きません。`curl -s -D - -o /dev/null http://<app>:8080/login | grep -i connection` で `close` が返るか先に見ます。`close` のまま `time-wait` が減らなければ、この 1 手は戻します（上流を変えずに先に進めません）

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/login
sudo journalctl -u nginx -n 20 --no-pager || sudo tail -20 /var/log/nginx/error.log
```

効果は `oha` と `ss` で見ます（bench-prep でログを回してから）:

```bash
echo "--- before keepalive ---"
ss -tan 'state time-wait' | wc -l
oha -n 1000 -c 20 --no-tui http://127.0.0.1/
echo "--- after ---"
ss -tan 'state time-wait' | wc -l
sudo cat /var/log/nginx/access.log | tail -3
```

`time-wait` が減り、`oha` の p99 が下がれば成功です。変わらなければ `proxy_http_version` / `Connection` の付け場所（`location` の内側か）と、上流が `Connection: close` を返していないか（上の注意）を見ます。使い回しが張り付いた証拠は `ss -tan state established` の相手先 `:8080` 行が残ることです。

### 観察すること

- `nginx -t` が通り、`curl` 2 本が 200 になることです
- `oha` の前後で `time-wait` 増分と p99 を言えます
- 分割構成なら `APP_PRIV` 側の `:8080` への `established` が張りっぱなしになります

## 5. タイムアウトを付ける（無期限待ちをなくす）

使い回しができたら、次はワーカーが固まらないように `timeout` を付けます。`requests` の `timeout` 無しは無期限に待ちます。相手が詰まるとこちらの gunicorn ワーカーまで固まります。まず `timeout=(3, 5)` を付けます。

今のタイムアウトを洗い出します:

```bash
grep -rn 'timeout' --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
grep -rn 'proxy_.*_timeout\|keepalive_timeout' /etc/nginx/sites-enabled/* /etc/nginx/conf.d/ 2>/dev/null | head
```

`backup/s2/home/private_isu/webapp/python/<対象>.py`（重い処理なら s2、軽い処理なら s1）の使う箇所 1 箇所ずつ:

```python
session.get(url, timeout=(3, 5))  # (connect, read)
```

- `timeout` 無しが既定です。必ず付けます。まず connect 3 秒 / read 5 秒にします
- 単数値（例: `timeout=5`）は connect + read の全体に効きます。分けて書くならタプルにします
- gunicorn の `--timeout`（固まったワーカーを殺す設定）と nginx の `proxy_connect_timeout 5s;` / `proxy_read_timeout 60s;` は別物です。アプリの `timeout` を先に付けてから、nginx 側が長すぎないか見ます
- 受け側は例外を握りつぶしません。`try` / `except requests.Timeout` でフォールバックか 502 系に寄せます

```bash
# 作業機
git pull --ff-only
# 上の内容を配置する
git add -A && git commit -m "tune: <対象> に timeout を付ける" && git push
```

変えたら restart / reload し、`curl` と `oha` で `fail` が出ないことを確認します。タイムアウトを短くしすぎると `fail` になります。そのときは値を戻します（リトライや上限を足しません）:

```bash
# Python を変えたら（サーバーで反映）
git pull --ff-only
sudo cp -a backup/s2/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
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

## 6. 同一ホストへの上限を確認する（絞りすぎない）

大量リクエストでは「使い回す数」の上限が効きます。多すぎると相手に負荷をかけ、少なすぎると自分が詰まります。変えるのは 1 箇所だけです。変更は作業機で入れ、サーバーでは pull + cp + restart / reload します（§5 と同じ流し方です）。

目安（まずここから。ベンチで比べます）:

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

負荷をかけて確認します（`oha -c` を上げます。ベンチマーカーの前にやります）:

```bash
oha -n 2000 -c 50 --no-tui http://127.0.0.1/
ss -tan 'state time-wait' | wc -l
ss -tan state established '( dport = :8080 or sport = :8080 )' | wc -l
dmesg | tail -5
```

- `pool_maxsize` をいきなり 4 まで絞りません。p99 が跳ねたら上限が原因の可能性が高いです。元の値に戻してから次へ進みます
- `too many open files` が出たらアプリの上限ではなく `ulimit -n` / `worker_rlimit_nofile` を見ます。アプリの値を絞る前に `ss -s` と `dmesg` を見ます
- TLS（HTTPS）の相手なら、コネクション数＝ハンドシェイク数です。使い回し（2.）と上限（6.）はセットで見ます

### 観察すること

- 変えた上限は 1 箇所だけです。その値と理由（`oha -c 50` の p99 / `time-wait`）を言えます
- 絞ったら p99 が上がり、戻したら下がることを確認しました
- `fail` が 0 です。`fail` が出たら上限を緩めます（タイムアウトを延ばしません）

## 7. 公式ベンチで判定する（s1 で打つ。slow 側は s3）

[§2](#2-合成再現で差を見る使い回す-vs-使い捨て)〜[§6](#6-同一ホストへの上限を確認する絞りすぎない) のどれか 1 手を残し、他は戻した状態にして公式ベンチを 1 本回します。bench-prep は [0030-measure.md](0030-measure.md) と同じです（s1 で `mv` + `reopen`、s3 で `flush-logs`）。

```bash
# s1 で打つ
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
TW_BEFORE=$(ss -tan 'state time-wait' | wc -l)
echo "tw_before=$TW_BEFORE"
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
TW_AFTER=$(ss -tan 'state time-wait' | wc -l)
echo "tw_before=$TW_BEFORE tw_after=$TW_AFTER"
echo "$(date -Iseconds)  score=  pass=  fail=  tw=${TW_BEFORE}-${TW_AFTER}  note=http-keepalive-32" >> ~/bench-notes/scores.txt
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

判定は点数＋`tw` 増分＋alp です。点だけ見ません:

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
```

効かなければ残しません。`scores.txt` に 1 行残して戻します。戻し方は変えた層だけやります:

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
sudo cp -a backup/s2/home/private_isu/webapp/python/<対象>.py /home/isucon/private_isu/webapp/python/<対象>.py
sudo systemctl restart isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- `tw_before → tw_after` の増分がベースラインより小さいです
- alp の Sum が落ちましたか。落ちないなら HTTP 層は詰まっていません（DB / キャッシュの仕事に戻ります）
- `fail` が 0 です。`fail` が出たら §5〜§6 の締めすぎを疑います

## 8. トラブルシュート

### TIME-WAIT が減らない

- `proxy_http_version 1.1` と `proxy_set_header Connection "";` が `location` の内側にあるか確認します。外側や `server` 直下では効かないことがあります
- Python でリクエスト毎に `Session()` を作っていないか確認します。モジュール先頭の 1 個ですか
- 読み切り漏れ（§3）です。読まずに捨てるとプールに戻りません
- 相手が `Connection: close` を返していないか確認します（s2 で打つ。s1 に `:8080` はありません）。`curl -sD - -o /dev/null http://127.0.0.1:8080/ | grep -i connection`

### keepalive を入れたら 502 / fail

- upstream 名と `proxy_pass` がずれています（`http://heavy;` の `;` 忘れ、`server` のポート違い）
- s1 が unix、s2 が `:8080` の分割では、TCP 側にだけ keepalive を付けましたか（unix 側は対象外です）
- `keepalive` の数が原因と決めつけません。まず `sudo nginx -t` と `journalctl -u isu-python.service` の直近 40 行を見ます

### タイムアウトで fail が出る

- 短くしすぎています。まず §5 の出発点（connect 3 秒 / read 5 秒）に戻します
- タイムアウトの二重指定になっていないか確認します（`timeout=` と `urllib3.Timeout` の混在など）
- 相手の p99 がタイムアウトに張り付いているなら、こちらの値を延ばさず相手（DB / 上流）を直します。alp と slp に戻ります

### 上限を絞ったら遅くなる

- 正常です（悪化を確認する逆向きの試験です）。`pool_maxsize` / `keepalive` を戻します。絞るのは `oha -c 50` で p99 が壊れない範囲だけです
- `too many open files` は上限の絞りすぎではなく FD 不足です。`ulimit -n` と nginx の `worker_rlimit_nofile` を見ます。アプリのプールを絞っても直りません

### TLS（HTTPS）の場合

- VPC 内の素体は HTTP なので、ここでの `time-wait` 差が TLS の差にそのまま当てはまるわけではありません。HTTPS ではハンドシェイクが数往復＋CPU になるので、§2 の差が拡大する方向に読みます
- 証明書の検証は外しません（`requests` の `verify=False` は入れません）
