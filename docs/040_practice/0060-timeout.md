# 0060 タイムアウトとプール上限を Python で検証する

[0050-http-client.md](0050-http-client.md) の続き。0050 が使い回し全体の話なら、ここはタイムアウトとプール上限を Python で数字で確認する。

- `timeout` を必ず付ける（付けないと無期限に待つ）
- 上限（`pool_connections` / `pool_maxsize` / `pool_block`）を確認する
- `timeout=(connect, read)` で接続と読み取りを分けて指定する
- GET は短め、POST は長めに分ける

ゴールは「タイムアウト無しが固まる / 付けたら例外で返る / 上限を絞ると詰まる」の 3 つを、`/tmp` の合成スクリプトで言えること。「付けた」だけでは終わらない。

順番: 対応表（1.）→ 遅延サーバ（2.）→ 固まること（3.）→ 付けて返すこと（4.）→ GET / POST 分離（5.）→ 上限（6.）→ アプリへの足し方（7.）→ 判定（8.）。1 手ずつ。`fail` が 0 でない点数は比べない。出口は [0070-static-cache.md](0070-static-cache.md)。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  note=timeout-baseline" >> ~/bench-notes/scores.txt
```

## 1. タイムアウトとプールの対応表

設定項目を Python の書き方に読み替える。この表がこのドリルの対応基準。

| 設定項目 | Python の書き方 | 出発点 |
| --- | --- | --- |
| 全体タイムアウト | `requests.get(url, timeout=5)`。単数値は connect + read の全体 | まず 5 秒。必ず付ける。付けないのが既定 |
| タイムアウト無し | `requests.get(url)` の timeout 無し。無限に待つ | `grep` で無しを 0 にする（4.） |
| connect / read の分離指定 | `timeout=(connect, read)` のタプル。`urllib3.Timeout(connect=, read=)` も同じ | `(3, 5)` から入る。connect 3 秒 / read 5 秒 |
| GET 短め / POST 長めに分ける | GET 用と POST 用で値を分ける（5.） | GET `(2, 3)`、POST `(3, 10)` の例から振る |
| プール数（ホスト種別数） | `HTTPAdapter(pool_connections=10)`。`requests.Session` 既定 10 | まず 10〜20。増やす前に効果を見る |
| プール数（ホスト毎の保持数） | `HTTPAdapter(pool_maxsize=10)`。`requests` 既定 10 | まず 10〜20。絞りすぎない（6.） |
| アイドル維持 | `urllib3` 側の keepalive 維持。`requests` 単体では直接の項目名は無い | アプリ側で触らず、nginx の `keepalive_timeout` と対で見る |

`requests` の既定は `pool_connections=10, pool_maxsize=10, pool_block=False`。`pool_block=False` のとき上限を超えても待たずに新規接続を作る（超えた分はプールに戻さない）。待ち行列にしたいときだけ `pool_block=True` にする（6. で振る舞いの差を見る）。最初から絞らない。

今のコードを洗い出す:

```bash
grep -rn 'requests\.\(get\|post\)\|Session\|HTTPAdapter\|urlopen\|http\.client\|urllib' \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
grep -rn 'timeout' /home/isucon/private_isu/webapp/python --include='*.py' | head -20
grep -rn 'proxy_.*_timeout\|keepalive_timeout' /etc/nginx/sites-enabled/ | head
```

- `requests.get` があちこちにあって `Session` が無い → 7. で 1 個にまとめる
- `timeout=` が無い行がある → 4. で付ける
- どこにも HTTP 呼び出しが無い → 2.〜6. は合成で練習し、7. の nginx upstream を本番の 1 手にする（[0050](0050-http-client.md#4-nginx-の-upstream-を使い回す1-台でも効く)）

### 観察すること

- 自分の 1 手が「アプリの `Session`」「nginx の `proxy_*_timeout`」のどちらかを言える
- `timeout` 無しの行が残っているか `grep` で言える
- 見つからなければ合成再現から入る（無理にアプリをいじらない）

## 2. 遅延サーバを用意する（相手役）

外の障害を待たずに、自分の `/tmp` で遅い相手を作る。`/slow` は 10 秒固まる GET、POST は 5 秒固まる。素体の `:8080` を壊さないよう、ポートは `18080` にする。

```bash
mkdir -p /tmp/timeout
cat > /tmp/timeout/delay.py <<'PY'
from http.server import BaseHTTPRequestHandler, HTTPServer
import time

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/slow"):
            time.sleep(10)
        body = b"ok\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        time.sleep(5)
        body = b"posted\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass

HTTPServer(("127.0.0.1", 18080), H).serve_forever()
PY
python3 /tmp/timeout/delay.py &
echo $! | tee /tmp/timeout/delay.pid
sleep 1
curl -s -m 2 http://127.0.0.1:18080/slow -o /dev/null -w 'curl exit=%{exitcode} http=%{http_code}\n' || true
```

- `curl -m 2` が exit 28（timeout）で返ること。これで相手が 10 秒固まることを確認したことになる
- 終わったら必ず殺す: `kill $(cat /tmp/timeout/delay.pid)`
- `/tmp` 配下なので再起動で消える。本番コードに置かない

`/tmp` 用の `requests` が無ければ入れる（アプリの `.venv` には足さない。合成用のみ）:

```bash
python3 -c 'import requests' 2>/dev/null || sudo python3 -m pip install --break-system-packages -q requests
python3 -c 'import requests; print(requests.__version__)'
```

アプリ本体に `requests` を足すときは `uv add` + `uv sync` が要る（7.）。ここでは `/tmp` 用に system の python を使えばよい。

### 観察すること

- `curl -m 2` が timeout する（相手の遅さが再現できた）
- `delay.py` のプロセスが `ps` で見える。殺すと `curl` が connection refused に変わる
- `:8080` のアプリは壊れていない（`curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/`）

## 3. タイムアウト無しが固まることを確認する

引用の「レスポンスが返ってくるまで無限に待つ」を手で再現する。`timeout` コマンドはプロセスを殺すものであり、アプリのタイムアウトではない。違いを意識する。

```bash
# 1. timeout 無しは 10 秒固まる。shell 側の timeout 4 で殺す（exit 124 が殺された証拠）
timeout 4 python3 -c "import requests; print(requests.get('http://127.0.0.1:18080/slow').text)" || echo "killed by outer timeout (exit $?)"
# 2. 標準ライブラリも同じ。timeout 無しは既定でブロックする
timeout 4 python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:18080/slow').read())" || echo "killed by outer timeout (exit $?)"
```

期待:

- どちらも本文が出ず、`timeout` コマンドに殺される（exit 124）
- これが gunicorn のワーカーで起きると、そのワーカーは 10 秒まるごと塞がる。同時に来たリクエストが溜まって高負荷になる。引用の「処理中のリクエストが大量に溜まる」はこのこと

gunicorn の `--timeout`（ワーカーを殺す設定）と混同しない。`--timeout` は固まった後に殺すためのものであり、HTTP の待ちそのものを短くしない。先に HTTP 側の `timeout` を付ける。

### 観察すること

- `timeout` 無しが固まることを 2 系統（`requests` / `urllib`）のどちらかで言える
- 外側の `timeout` コマンドと内側の `timeout=` 引数の違いを言える
- ワーカーが塞がると何が溜まるか（gunicorn の worker 数と nginx の待ち）を言える

## 4. タイムアウトを付けて例外で返す

全体タイムアウトに相当する。付けると固まらず例外で返る。まず単数値、次にタプル。

```bash
# 単数値 2 秒。ReadTimeout で返る（約 2 秒で終わる）
time python3 -c "import requests; requests.get('http://127.0.0.1:18080/slow', timeout=2)" || echo "raised (exit $?)"
# タプル (connect, read)。接続は速いので read 側で切れる
time python3 -c "import requests; requests.get('http://127.0.0.1:18080/slow', timeout=(1, 2))" || echo "raised (exit $?)"
# 標準ライブラリ
time python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18080/slow', timeout=2)" || echo "raised (exit $?)"
```

例外の読み方:

- `ReadTimeout: Read timed out. (read timeout=2)` → 接続はできたが本文が来ない。`/slow` 型
- `ConnectTimeout` / `Connection refused` → 相手が居ない・届かない。停止後の `delay.py` や閉じたポート（例: `http://127.0.0.1:9/`）で出る。`timeout=(1, 5)` の connect 側が効く
- `timeout=2` と `timeout=(1, 2)` の差が出るのは connect が遅いときだけ。localhost 再現ではどちらも read 側で切れる。差を見たいときは閉じたポートと `/slow` を両方叩く

```bash
# connect 側の確認（相手が居ない）
python3 -c "import requests; requests.get('http://127.0.0.1:9/', timeout=(1, 5))" || echo "connect fail (exit $?)"
```

方針（コピペ用の完成品ではなく、自分の 1 箇所に当てる）:

```python
# まず全部に付ける。値は 4.〜5. で振る
requests.get(url, timeout=5)
requests.get(url, timeout=(3, 5))  # (connect, read)
urllib.request.urlopen(url, timeout=5)
```

受け側は例外を握りつぶさない。500 で返すか、フォールバック（キャッシュ・既定値）に逃がすか決める。握りつぶして `None` を返すと後の `fail` が読みにくくなる。

```python
import requests

try:
    r = session.get(url, timeout=(3, 5))
    r.raise_for_status()
    body = r.content
except (requests.ConnectTimeout, requests.ReadTimeout, requests.ConnectionError) as e:
    # ここでフォールバックか 502 系に寄せる。pass だけにしない
    raise
```

### 観察すること

- `time` が約 2 秒で終わり、`ReadTimeout` / `TimeoutError` が出た
- connect 失敗と read 失敗の例外が違うと言える（閉じたポート vs `/slow`）
- `grep -rn 'requests.get\|urlopen' ... | grep -v timeout` が 0 になった（付け忘れが無い）

## 5. GET は短め、POST は長めに分ける

引用の「データの更新をしない GET は短め、更新する POST は長め」。GET の方が数が多いので、短くする効果が大きい。

出発点（ベンチで振る前の仮値）:

| 用途 | 値 | 理由 |
| --- | --- | --- |
| GET（参照） | `timeout=(2, 3)` | 数が多い。遅い参照は捨ててフォールバックか再試行に回す |
| POST（更新） | `timeout=(3, 10)` | 再試行すると二重書き込みになるので、長めに待って 1 回で決める |

```bash
# GET 用は 3 秒で切れること
time python3 -c "import requests; requests.get('http://127.0.0.1:18080/slow', timeout=(2, 3))" || echo "GET cut (exit $?)"
# POST 用は 5 秒固まる相手に 10 秒なら通ること
time python3 -c "import requests; print(requests.post('http://127.0.0.1:18080/post', data=b'x', timeout=(3, 10)).text)"
```

方針:

```python
GET_TIMEOUT = (2, 3)
POST_TIMEOUT = (3, 10)

session.get(url, timeout=GET_TIMEOUT)
session.post(url, data=..., timeout=POST_TIMEOUT)
```

- POST のリトライはここでは足さない。二重 POST になる。まず 1 回の値を決める
- 短くしすぎて `fail` になったら値を戻す。リトライや上限を同時にいじらない

### 観察すること

- GET と POST で値が違うと言える（同じ 5 秒を使い回さない）
- 短くした GET が `ReadTimeout` で切れ、長くした POST が通った
- `fail` が出たらどちらの値を戻すか言える

## 6. 同一ホストへの上限を確認する（絞りすぎない）

プール上限の話。Python では `HTTPAdapter` の 3 点を見る。

```bash
grep -rn 'pool_maxsize\|pool_connections\|HTTPAdapter\|pool_block' \
  /home/isucon/private_isu/webapp/python --include='*.py' | head
python3 -c "from requests.adapters import HTTPAdapter; help(HTTPAdapter.__init__)" | head -5
```

既定の確認（打つだけ）:

```bash
python3 -c "from requests.adapters import HTTPAdapter; import inspect; print(inspect.signature(HTTPAdapter.__init__))"
# (pool_connections=10, pool_maxsize=10, max_retries=0, pool_block=False)
```

各項目の意味:

- `pool_connections=10` … ホスト種別数。まず触らない
- `pool_maxsize=10` … ホスト毎の保持数。まず 10〜20
- `pool_block=False` … 上限を超えたら待たずに新規接続。`True` にすると上限で待つ行列になる

振る舞いの差はスレッド 10 本で見る。相手は速いサーバ（0.5 秒）でよい:

```bash
cat > /tmp/timeout/fast.py <<'PY'
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(0.5)
        body = b"ok\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
ThreadingHTTPServer(("127.0.0.1", 18081), H).serve_forever()
PY
python3 /tmp/timeout/fast.py &
echo $! | tee /tmp/timeout/fast.pid
sleep 1
cat > /tmp/timeout/pool.py <<'PY'
import concurrent.futures, sys, time, requests
from requests.adapters import HTTPAdapter
maxsize = int(sys.argv[1]) if len(sys.argv) > 1 else 1
block = sys.argv[2] == "block" if len(sys.argv) > 2 else False
s = requests.Session()
s.mount("http://", HTTPAdapter(pool_connections=10, pool_maxsize=maxsize, max_retries=0, pool_block=block))
def one(_):
    return s.get("http://127.0.0.1:18081/", timeout=10).text.strip()
t0 = time.time()
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
    list(ex.map(one, range(10)))
print(f"maxsize={maxsize} block={block} elapsed={time.time()-t0:.2f}s")
PY
python3 /tmp/timeout/pool.py 1 noblock
python3 /tmp/timeout/pool.py 1 block
python3 /tmp/timeout/pool.py 10 noblock
kill $(cat /tmp/timeout/fast.pid)
```

期待（実測例）:

- `maxsize=1 noblock` … 約 0.5 秒。待たずに新規接続するので速いが、使い回しは効かない
- `maxsize=1 block` … 約 5.0 秒。1 本ずつ順番待ちになる
- `maxsize=10 noblock` … 約 0.5 秒。10 本並列で使い回す

読み方:

- 最初から `maxsize=1` や `pool_block=True` に絞らない。p99 が跳ねたら上限が犯人。戻してから次へ
- `too many open files` が出たらプールの数が原因と決めつけない。`ulimit -n` / `ss -s` を見る（[0050 §6](0050-http-client.md#6-同一ホストへの上限を確認する絞りすぎない) と同じ）
- 方針は `pool_maxsize=20` から始める。`oha -c 50` で p99 が壊れない範囲だけ振る

```python
# 方針: モジュール先頭で 1 個。リクエスト毎に Session() を作らない
import requests
from requests.adapters import HTTPAdapter

session = requests.Session()
session.mount("http://", HTTPAdapter(pool_connections=20, pool_maxsize=20))
session.mount("https://", HTTPAdapter(pool_connections=20, pool_maxsize=20))
# 使う側は毎回 session.get(url, timeout=(3, 5))
```

### 観察すること

- 3 本の `elapsed` を数字で言える（速い / 遅い / 速い）
- `block=True` の遅さが「上限の順番待ち」だと説明できる
- 自分の値は 1 箇所だけ。その値と理由（`oha -c 50` の p99）を言える

## 7. アプリに足すときの形（1 箇所だけ）

合成で掴んだら、自分の 1 箇所に当てる。まとめて全部は変えない。

1. `Session` はモジュール先頭で 1 個作る（gunicorn はワーカー別プロセスなのでワーカー毎に 1 個になる）。リクエスト毎に `Session()` を作らない
2. `timeout` は全部に付ける。GET / POST で値を分ける（5.）
3. 受けたら `content` を読んでから閉じる（[0050 §3](0050-http-client.md#3-body-を読み切って-close-する)）。読まずに捨てるとプールに戻らない

```python
# app.py の先頭寄りに 1 回だけ。関数の中に置かない
import requests
from requests.adapters import HTTPAdapter

session = requests.Session()
session.mount("http://", HTTPAdapter(pool_connections=20, pool_maxsize=20))
session.mount("https://", HTTPAdapter(pool_connections=20, pool_maxsize=20))

GET_TIMEOUT = (2, 3)
POST_TIMEOUT = (3, 10)
```

```python
# 使う側（例）
with session.get(url, timeout=GET_TIMEOUT) as r:
    r.raise_for_status()
    body = r.content
```

依存の足し方（`requests` が無いときだけ）:

```bash
sudo su - isucon
cd /home/isucon/private_isu/webapp/python
uv add requests
uv sync
sudo systemctl restart isu-python.service
systemctl is-active isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
sudo journalctl -u isu-python.service -n 40 --no-pager
```

依存を増やしたくないときは標準の `urllib.request.urlopen(url, timeout=5)` でよい。プール再利用は効きにくいが、タイムアウトの型は同じ。

nginx 側のタイムアウトは別物。アプリの `timeout` を先に付けてから、長すぎないか見る:

```bash
grep -rn 'proxy_.*_timeout' /etc/nginx/sites-enabled/ | head
# 例: proxy_connect_timeout 5s; proxy_read_timeout 60s;
# アプリの read 5 秒に対して nginx の read 60 秒なら、アプリ側が先に切れる。正しい順序
```

変えたら restart / reload して `curl` 2 本を確認する。短くしすぎると `fail` になる。そのときは値を戻す:

```bash
sudo systemctl restart isu-python.service
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- `Session` が 1 個か（`grep -c 'Session()'` が 1）
- `timeout` 無しの行が 0 か
- `curl` 2 本が 200 になること。`journalctl` に例外が出ていないこと

## 8. 公式ベンチで判定する

2.〜7. のどれか 1 手を残し、他は戻した状態にして公式ベンチを 1 本回す。bench-prep は [0030-measure.md](0030-measure.md#2-計測サイクルを回す) と同じ `mv` + `reopen` / `flush-logs`。タイムアウト自体は平常時の点数を上げない。判定は「固まらないこと」と「`fail` が 0」。

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
echo "$(date -Iseconds)  score=  pass=  fail=  note=timeout-get23-post310" >> ~/bench-notes/scores.txt
```

残す変更は `backup/s1` に積む（[0030 §2](0030-measure.md#残す変更は-backups1-に積む) の流儀）。

合成サーバはベンチ前に殺す。ベンチ中に `18080` / `18081` を残さない:

```bash
kill $(cat /tmp/timeout/delay.pid) 2>/dev/null || true
kill $(cat /tmp/timeout/fast.pid) 2>/dev/null || true
sudo ss -lntp | grep 1808 || echo 'synthetic servers stopped'
```

戻し方は変えた層だけやる:

```bash
# Python を戻すとき（Session / timeout の差分だけ消す）
sudo systemctl restart isu-python.service
# nginx を戻すとき
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- ベンチ前後で合成サーバが残っていない（`ss` で 18080 / 18081 が無い）
- `fail` が 0。`fail` が出たら 5. か 6. の締めすぎを疑う（タイムアウトを延ばす前に上限を戻す）
- alp の Sum が落ちないなら HTTP 層は詰まっていない（DB / キャッシュの仕事に戻る）

## 9. トラブルシュート

### タイムアウトで fail が出る

- 短くしすぎている。まず GET `(2, 3)` / POST `(3, 10)` に戻す
- `session.get(url, timeout=...)` と `urllib` の既定混在になっていないか。`grep -v timeout` で洗い出す
- 相手の p99 がタイムアウトに張り付いているなら、こちらの値を延ばさず相手（DB / 上流）を直す。alp と slp に戻る

### 上限を絞ったら遅くなる

- 正常な逆振り。`pool_maxsize` / `pool_block` を戻す。6. の 3 本の `elapsed` を取り直す
- `too many open files` は FD 不足。`ulimit -n` と `ss -s` を見る。プールを絞っても直らない

### requests が無い / uv sync 忘れ

```bash
sudo su - isucon
cd /home/isucon/private_isu/webapp/python
uv sync
sudo systemctl restart isu-python.service
sudo journalctl -u isu-python.service -n 40 --no-pager | grep -i -E 'ModuleNotFound|requests'
```

- `ModuleNotFoundError: requests` → `uv add requests` 漏れか `uv sync` 漏れ
- `/tmp` の合成では system python の `pip` でよい。`.venv` と混ぜない

### 合成サーバが残っている

```bash
ps -ef | grep -E 'delay\.py|fast\.py' | grep -v grep
kill $(cat /tmp/timeout/delay.pid) 2>/dev/null || true
kill $(cat /tmp/timeout/fast.pid) 2>/dev/null || true
sudo ss -lntp | grep 1808 || echo 'cleaned'
```

- ベンチ前に殺す。残すとポート枯れや `fail` の原因になる
- `/tmp/timeout` はドリル用。再起動で消える。本番に置かない
