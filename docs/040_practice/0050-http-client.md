# 0050 HTTPクライアントのコネクションを使い回す

本の 8-3「HTTPクライアントの使い方」の 3 点を、同じホストへのコネクションに絞って数字で確認する。

- 同一ホストへのコネクションを使い回す（TCP + TLS のハンドシェイクを減らす）
- 適切なタイムアウトを設定する（無期限待ちをなくす）
- 同一ホストに大量のリクエストを送る場合、コネクション数の制限を確認する

ゴールは「使い回した / タイムアウトを付けた / 上限を絞った」の前後を、`ss` の `TIME-WAIT` と `oha` / 公式ベンチで言えること。「入れた」だけでは終わらない。

順番: 探す（1.）→ 合成再現で差を見る（2.〜3.）→ nginx の使い回し（4.）→ タイムアウト（5.）→ 上限（6.）→ 公式ベンチ判定（7.）。1 手ずつ。`fail` が 0 でない点数は比べない。出口は [0060-timeout.md](0060-timeout.md)。

点数は `~/bench-notes/scores.txt` に 1 行。`tw`（TIME-WAIT 数）も一緒に書く:

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  tw=  oha_p99=  note=http-baseline" >> ~/bench-notes/scores.txt
```

| 回 | score | tw | oha_p99 | メモ |
| --- | --- | --- | --- | --- |
| ベースライン |  |  |  | 0030 までの状態。HTTP クライアント無調整 |
| 1 手目 |  | 例: 12 | 例: 0.08s | 例: nginx keepalive 32 |

## 1. どこで HTTP を投げているか探す

直す前に、自分のアプリがどこで外へ HTTP を投げているか確かめる。private-isu の素体は DB / memcached 直結で、外への HTTP は無いことが多い。そのときは 2.〜3. の合成再現で型を掴み、4. の nginx upstream を本番の 1 手にする。

```bash
grep -rn 'requests\.\(get\|post\|Session\)\|urllib\|http\.client\|HTTPConnection' \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
grep -rn 'proxy_pass' /etc/nginx/sites-enabled/ | head -20
```

読み方:

- Python で `requests.get` を毎回呼ぶ → `Session` にまとめる（2.）
- `proxy_pass http://...` → 4. の nginx upstream が HTTP クライアントの役割をしている。アプリ側に HTTP 呼び出しが無くてもここは必ずある
- どこにも無い → 2.〜3. は合成スクリプトで練習し、4. を本番の 1 手にする

### 観察すること

- 自分の 1 手が「アプリの client」「nginx の upstream」のどちらかを言える
- 対象のホスト（`127.0.0.1:8080` なのか `s2:8080` なのか）を言える。分割後は [0020-split-1.md](0020-split-1.md) の `APP_PRIV`
- 見つからなければ合成再現から入る（無理にアプリをいじらない）

## 2. 合成再現で差を見る（使い回す vs 使い捨て）

アプリを変える前に、`/tmp` の小さなスクリプトで差を出す。相手は自分のアプリ（`:8080` に直結）でよい。ベンチマーカーは使わない。

### 2a. コネクション数の見方を固定する

```bash
ss -tan 'state time-wait' | wc -l
ss -tan state established '( dport = :8080 or sport = :8080 )' | head -20
cat /proc/sys/net/ipv4/ip_local_port_range
ulimit -n
```

- `time-wait` が毎回数百増える → 使い捨てている
- `established` が張りっぱなしで増えない → 使い回している
- `ip_local_port_range` は既定 `32768 60999`（約 28k）。TIME-WAIT（60 秒）が溜まると、使い捨てでは単純計算で数百 rps で頭打ちになる

### 2b. Python で比べる（標準のみ）

`requests` が無くても動く `http.client` 版。使い回す方は `HTTPConnection` を 1 本作って使い回し、使い捨てる方は毎回作り直す（最悪形の再現）。`/tmp/reuse_py.py` に置く。

```python
# /tmp/reuse_py.py
import http.client, sys, time
reuse = sys.argv[1] == "reuse" if len(sys.argv) > 1 else True
n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
url_host, url_port, url_path = "127.0.0.1", 8080, "/"
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
echo "--- before ---"; ss -tan 'state time-wait' | wc -l
python3 /tmp/reuse_py.py reuse 200
echo "--- after reuse ---"; ss -tan 'state time-wait' | wc -l
python3 /tmp/reuse_py.py noreuse 200
echo "--- after noreuse ---"; ss -tan 'state time-wait' | wc -l
```

`requests` を使っているアプリなら対応はこうなる（コピペ用の完成品ではなく方針）。`Session` をプロセス全体で 1 個作る（gunicorn はワーカー別プロセスなのでワーカー毎に 1 個になる）。リクエスト毎に `Session()` を作らない:

```python
# 方針: モジュール先頭で 1 個。リクエスト毎に作らない
import requests
from requests.adapters import HTTPAdapter
session = requests.Session()
session.mount("http://", HTTPAdapter(pool_connections=20, pool_maxsize=20))
session.mount("https://", HTTPAdapter(pool_connections=20, pool_maxsize=20))
# 使う側は毎回 session.get(url, timeout=(3, 5))
# 受けたら resp.content を読んでから閉じる（3. と同じ）
```

### 観察すること

- reuse / noreuse の `elapsed` と `time-wait` 増分を 2 つの数字で言える
- `established` が reuse では張りっぱなし、noreuse では残らない

## 3. Body を読み切って Close する

`read()` / `content` を読まずに捨てると、そのコネクションはプールに戻らず切断される。2b. の `r.read()` が読み切りの行に当たる。

確認はコードの目視と `ss` の両方でやる。形だけ閉じても読んでいなければ `time-wait` は減らない。

```bash
# Python: content/read までやっているか
grep -rn '\.content\|resp\.read\|getresponse\|Session' \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
```

直し方（自分の 1 箇所だけ）:

```python
# Python requests: 読んでから閉じる
with session.get(url, timeout=(3, 5)) as r:
    r.raise_for_status()
    body = r.content
```

2b. をもう一度回し、`time-wait` 増分が reuse 側に寄ったか見る。寄らなければまだ読み切り漏れか、別箇所で使い捨て接続を作っている。

### 観察すること

- `Close` と読み切りがセットになっている（片方だけにしない）
- 直す前後で `time-wait` 増分が変わった
- `fail` が出ない（読み切りを足しただけで挙動は変わらないはず）

## 4. nginx の upstream を使い回す（1 台でも効く）

アプリに外への HTTP が無くても、nginx → アプリは毎リクエスト HTTP している。ここが使い捨てだと、2. と同じことが本番経路で起きる。分割後（s1 → s2:8080）は台をまたぐので必須。

今の upstream を見る:

```bash
grep -rn 'proxy_pass\|upstream\|keepalive\|proxy_http_version' /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | head -30
sudo nginx -t
```

既定の素体は `proxy_pass http://localhost:8080;` のみで keepalive が無い。`upstream` 定義を http 直下の別ファイルに分け、site conf の `location /` を書き換える。`upstream` は `server` ブロックの中に書けない（`nginx -t` が落ちる）。

```bash
SITE=/etc/nginx/sites-enabled/isucon.conf
sudo test -f "${SITE}.orig" || sudo cp -a "$SITE" "${SITE}.orig"

sudo tee /etc/nginx/conf.d/upstream.conf >/dev/null <<'EOF'
upstream heavy {
  server 127.0.0.1:8080;
  keepalive 32;
}
EOF

sudo tee "$SITE" >/dev/null <<'EOF'
server {
  listen 80;
  client_max_body_size 10m;
  root /home/isucon/private_isu/webapp/public/;

  location / {
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Connection "";
    proxy_pass http://heavy;
  }
}
EOF
sudo nginx -t && sudo systemctl reload nginx
```

注意:

- `proxy_http_version 1.1;` と `proxy_set_header Connection "";` が無いと keepalive にならない（1.0 のまま閉じる）
- `keepalive` はまず 32 にする。大きくする前に効果を見る。分割後は `server ${APP_PRIV}:8080;` に変える（[0020-split-1.md](0020-split-1.md) の変数）
- s1 が unix socket（`0020-split` の light）の箇所は対象外。TCP の heavy 側だけ

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/login
sudo journalctl -u nginx -n 20 --no-pager || sudo tail -20 /var/log/nginx/error.log
```

効果は `oha` と `ss` で見る（bench-prep でログを回してから）:

```bash
echo "--- before keepalive ---"; ss -tan 'state time-wait' | wc -l
oha -n 1000 -c 20 --no-tui http://127.0.0.1/
echo "--- after ---"; ss -tan 'state time-wait' | wc -l
sudo cat /var/log/nginx/access.log | tail -3
```

`time-wait` が減り、`oha` の p99 が下がれば成功。変わらなければ `proxy_http_version` / `Connection` の付け場所（`location /` の内側か）を見る。

### 観察すること

- `nginx -t` が通り、`curl` 2 本が 200 になること
- `oha` の前後で `time-wait` 増分と p99 を言える
- 分割構成なら `APP_PRIV` 側の `:8080` への `established` が張りっぱなしになる

## 5. タイムアウトを付ける（無期限待ちをなくす）

使い回しができたら、次は固まらないようにすること。`requests` の `timeout` 無しは無期限に待つ。相手が詰まるとこちらのワーカーまで固まる。本番では必ず付ける。

Python の出発点（使う箇所の 1 箇所ずつ）:

```python
session.get(url, timeout=(3, 5))  # (connect, read)
```

- `timeout` 無しが既定。必ず付ける。まず connect 3 秒 / read 5 秒
- 単数値（例: `timeout=5`）は connect + read の全体に効く。分けて書くならタプルにする
- gunicorn の `--timeout`（固まったワーカーを殺す設定）と nginx の `proxy_connect_timeout 5s;` / `proxy_read_timeout 60s;` は別物。アプリの `timeout` を先に付けてから、nginx 側が長すぎないか見る
- 受け側は例外を握りつぶさない。`try` / `except requests.Timeout` でフォールバックか 502 系に寄せる。握りつぶして `None` を返すと後の `fail` が読みにくくなる

```bash
# 今のタイムアウトを洗い出す
grep -rn 'timeout' /home/isucon/private_isu/webapp/python --include='*.py' | head -20
grep -rn 'proxy_.*_timeout\|keepalive_timeout' /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | head
```

変えたら restart / reload し、`curl` と `oha` で `fail` が出ないことを確認する。タイムアウトを短くしすぎると `fail` になる。そのときは値を戻す（リトライや上限を足さない）:

```bash
# Python を変えたら
sudo systemctl restart isu-python.service
# nginx を変えたら
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- `timeout` 無しの箇所が残っていない（`grep -v timeout` で 0）
- 短くしても `oha` が `fail` しない値に落ち着いた
- p99 が read timeout に張り付いていない（張り付いたら相手側の問題で、こちらの値は延ばさない）

## 6. 同一ホストへの上限を確認する（絞りすぎない）

大量リクエストでは「使い回す数」の上限が効く。多すぎると相手に負荷をかけ、少なすぎると自分が詰まる。変えるのは 1 箇所だけ。

目安（まずここから。ベンチで振る）:

| 層 | 項目 | 出発点 | 絞りすぎの症状 |
| --- | --- | --- | --- |
| Python `HTTPAdapter` | `pool_connections` | 10（ホスト種別数）。まず触らない | 種別が増えたら足りなくなる |
| Python `HTTPAdapter` | `pool_maxsize` | 20 | プール待ちで遅くなる |
| nginx upstream | `keepalive` | 32 | `time-wait` が減らない |
| OS | `ulimit -n` / local port | 触らない。見るだけ | `too many open files` / port 枯渇 |

```bash
# 上限の今を見る
grep -rn 'pool_maxsize\|pool_connections\|HTTPAdapter' \
  /home/isucon/private_isu/webapp/python --include='*.py' | head
grep -rn 'keepalive' /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | head
ulimit -n; ss -s | head -10
```

負荷をかけて確認する（`oha -c` を上げる。ベンチマーカーの前にやる）:

```bash
oha -n 2000 -c 50 --no-tui http://127.0.0.1/
ss -tan 'state time-wait' | wc -l
ss -tan state established '( dport = :8080 or sport = :8080 )' | wc -l
dmesg | tail -5
```

- `pool_maxsize` をいきなり 4 などまで絞らない。p99 が跳ねたら上限が犯人。戻してから次へ
- `too many open files` が出たらアプリの上限ではなく `ulimit -n` / `worker_rlimit_nofile`。アプリの値を絞る前に `ss -s` と `dmesg` を見る
- TLS（HTTPS）の相手なら、コネクション数＝ハンドシェイク数。使い回し（2.）と上限（6.）はセットで見る

### 観察すること

- 変えた上限は 1 箇所だけ。その値と理由（`oha -c 50` の p99 / `time-wait`）を言える
- 絞ったら p99 が上がり、戻したら下がることを確認した（逆振り）
- `fail` が 0。`fail` が出たら上限を緩める（タイムアウトを延ばさない）

## 7. 公式ベンチで判定する

2.〜6. のどれか 1 手を残し、他は戻した状態にして公式ベンチを 1 本回す。bench-prep は [0030-measure.md](0030-measure.md#2-計測サイクルを回す) と同じ `mv` + `reopen` / `flush-logs`。

```bash
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysqladmin flush-logs 2>/dev/null || sudo mysql -e 'FLUSH SLOW LOGS'
TW_BEFORE=$(ss -tan 'state time-wait' | wc -l)
echo "tw_before=$TW_BEFORE"
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
TW_AFTER=$(ss -tan 'state time-wait' | wc -l)
echo "tw_before=$TW_BEFORE tw_after=$TW_AFTER"
echo "$(date -Iseconds)  score=  pass=  fail=  tw=${TW_BEFORE}-${TW_AFTER}  note=http-keepalive-32" >> ~/bench-notes/scores.txt
```

残す変更は `backup/s1` に積む（[0030 §2](0030-measure.md#残す変更は-backups1-に積む) の流儀）。

判定は点数＋`tw` 増分＋alp。点だけ見ない:

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
```

効かなければ残さない。`scores.txt` に 1 行残して戻す。戻し方は変えた層だけやる:

```bash
# nginx を戻すとき（drop-in や site conf の keepalive 3 行だけ消す）
sudo nginx -t && sudo systemctl reload nginx
# Python を戻すとき
sudo systemctl restart isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- `tw_before → tw_after` の増分がベースラインより小さい
- alp の Sum が落ちたか。落ちないなら HTTP 層は詰まっていない（DB / キャッシュの仕事に戻る）
- `fail` が 0。`fail` が出たら 5.〜6. の締めすぎを疑う

## 8. トラブルシュート

### TIME-WAIT が減らない

- `proxy_http_version 1.1` と `proxy_set_header Connection "";` が `location` の内側にあるか。外側や `server` 直下では効かないことがある
- Python でリクエスト毎に `Session()` を作っていないか。モジュール先頭の 1 個か
- 読み切り漏れ（3.）。読まずに捨てるとプールに戻らない
- 相手が `Connection: close` を返していないか。`curl -sD - -o /dev/null http://127.0.0.1:8080/ | grep -i connection`

### keepalive を入れたら 502 / fail

- upstream 名と `proxy_pass` がずれている（`http://heavy;` の `;` 忘れ、`server` のポートが `APP_PORT` と違う）
- s1 が unix、s2 が `:8080` の分割で、TCP 側にだけ keepalive を付けたか（unix 側は対象外）
- `keepalive` の数が原因と決めつけない。まず `sudo nginx -t` と `journalctl -u isu-python.service` の直近 40 行を見る

### タイムアウトで fail が出る

- 短くしすぎている。まず 5. の出発点（全体 5 秒 / connect 3 秒 / read 5 秒）に戻す
- タイムアウトの二重指定になっていないか（`timeout=` と `urllib3.Timeout` の混在など）
- 相手の p99 がタイムアウトに張り付いているなら、こちらの値を延ばさず相手（DB / 上流）を直す。alp と slp に戻る

### 上限を絞ったら遅くなる

- 正常な逆振り。`pool_maxsize` / `keepalive` を戻す。絞るのは `oha -c 50` で p99 が壊れない範囲だけ
- `too many open files` は上限の絞りすぎではなく FD 不足。`ulimit -n` と nginx の `worker_rlimit_nofile` を見る。アプリのプールを絞っても直らない

### TLS の話だけしたい

- VPC 内の素体は HTTP なので、ここでの `time-wait` 差が TLS の差にそのまま当てはまるわけではない。HTTPS ではハンドシェイクが数往復＋CPU になるので、2. の差が拡大する方向に読む
- 証明書の検証は外さない（`requests` の `verify=False` は入れない）
