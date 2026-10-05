# 0060 タイムアウトとプール上限を Python で検証する

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）はローカル環境の private-isu-terraform リポジトリにて実行します。

[0050-http-client.md](0050-http-client.md) の続きです。0050 は使い回し全体の話。ここではタイムアウトとプール上限を Python で数字で確認します。

- `timeout` を必ず付けます（付けないと無期限に待ちます）
- 上限（`pool_connections` / `pool_maxsize` / `pool_block`）を確認します
- `timeout=(connect, read)` で接続と読み取りを分けて指定します
- GET は短め、POST は長めに分けます

ゴールは次の 3 つを `/tmp` の合成スクリプトで数字で言えることです。「付けた」だけでは終わりません。

- `timeout` 無しでは 10 秒待たされます
- `timeout=(3, 5)` を付けると約 2 秒で `ReadTimeout` になります
- `pool_maxsize=1` + `block` では順番待ちになります

順番: 対応表（1.）→ 遅延サーバ（2.）→ 固まること（3.）→ 付けて返すこと（4.）→ GET / POST 分離（5.）→ 上限（6.）→ アプリへの足し方（7.）→ 判定（8.）。1 手ずつ進めます。`fail` が 0 でない点数は比べません。出口は [0070-static-cache.md](0070-static-cache.md) です。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  note=timeout-baseline" >> ~/bench-notes/scores.txt
```

## 1. タイムアウトとプールの対応表

設定項目を Python の書き方に読み替えます。この表がこのドリルの対応基準です。

| 設定項目 | Python の書き方 | 出発点 |
| --- | --- | --- |
| 全体タイムアウト | `requests.get(url, timeout=5)`。単数値は connect + read の全体 | まず 5 秒。必ず付ける。付けないのが既定 |
| タイムアウト無し | `requests.get(url)` の timeout 無し。無限に待つ | `grep` で無しを 0 にする（4.） |
| connect / read の分離指定 | `timeout=(connect, read)` のタプル。`urllib3.Timeout(connect=, read=)` も同じ | `(3, 5)` から入る。connect 3 秒 / read 5 秒 |
| GET 短め / POST 長めに分ける | GET 用と POST 用で値を分ける（5.） | GET `(2, 3)`、POST `(3, 10)` の例から始める |
| プール数（ホスト種別数） | `HTTPAdapter(pool_connections=10)`。`requests.Session` 既定 10 | まず 10〜20。増やす前に効果を見る |
| プール数（ホスト毎の保持数） | `HTTPAdapter(pool_maxsize=10)`。`requests` 既定 10 | まず 10〜20。絞りすぎない（6.） |
| アイドル維持 | `urllib3` 側の keepalive 維持。`requests` 単体では直接の項目名は無い | アプリ側で触らず、nginx の `keepalive_timeout` と対で見る |

`requests` の既定は `pool_connections=10, pool_maxsize=10, pool_block=False` です。`pool_block=False` のとき上限を超えても待たずに新規接続を作ります（超えた分はプールに戻しません）。待ち行列にしたいときだけ `pool_block=True` にします（6. で振る舞いの差を見ます）。

今のコードを洗い出します:

```bash
grep -rn 'requests\.\(get\|post\)\|Session\|HTTPAdapter\|urlopen\|http\.client\|urllib' \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  /home/isucon/private_isu/webapp/python --include='*.py' | head -20
grep -rn 'timeout' /home/isucon/private_isu/webapp/python --include='*.py' --exclude-dir=.venv | head -20
sudo nginx -T 2>/dev/null | grep -E 'proxy_.*_timeout|keepalive_timeout' | head
```

- `requests.get` があちこちにあって `Session` が無い → 7. で 1 個にまとめます
- `timeout=` が無い行がある → 4. で付けます
- どこにも HTTP 呼び出しが無い → 2.〜6. は合成で練習し、7. の nginx upstream を 1 手にします（[0050-http-client.md §4](0050-http-client.md#4-nginx-の-upstream-を使い回す1-台でも効く)）

### 観察すること

- 自分の 1 手が「アプリの `Session`」「nginx の `proxy_*_timeout`」のどちらかを言えます
- `timeout` 無しの行が残っているか `grep` で言えます
- 見つからなければ合成再現から入ります（無理にアプリをいじりません）

## 2. 遅延サーバを用意する（相手役）

外の障害を待たずに、自分の `/tmp` で遅い相手を作ります。`/slow` は GET で 10 秒待たされる応答、POST は 5 秒待たされる応答です。既設の `:8080` を壊さないよう、ポートは `18080` にします。

`/tmp/timeout/delay.py`:

```python
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

ThreadingHTTPServer(("127.0.0.1", 18080), H).serve_forever()
```

起動します:

```bash
mkdir -p /tmp/timeout
# 上の内容を /tmp/timeout/delay.py に配置する
python3 /tmp/timeout/delay.py &
echo $! | tee /tmp/timeout/delay.pid
sleep 1
curl -s -m 2 http://127.0.0.1:18080/slow -o /dev/null -w 'curl exit=%{exitcode} http=%{http_code}\n' || true
```

- `curl -m 2` が exit 28（timeout）で返ることです。これで相手が 10 秒応答しないことを再現できました
- 終わったら必ず殺します: `kill $(cat /tmp/timeout/delay.pid)`
- `/tmp` 配下なので再起動で消えます。本番コードに置きません
- 相手役はスレッド化（`ThreadingHTTPServer`）にします。単一スレッドだと、タイムアウトで切り上げたリクエストが 1 つ目で専有し、次の検証に影響します

`/tmp` 用の `requests` が無ければ入れます（アプリの `.venv` には足しません。合成用のみです）:

```bash
python3 -c 'import requests' 2>/dev/null || sudo python3 -m pip install --break-system-packages -q requests
python3 -c 'import requests; print(requests.__version__)'
```

アプリ本体に `requests` を足すときは §7 の手順を使います。ここでは `/tmp` 用に system の python を使います。

### 観察すること

- `curl -m 2` が timeout します（相手の遅さが再現できました）
- `delay.py` のプロセスが `ps` で見えます。殺すと `curl` が connection refused に変わります
- `:8080` のアプリは壊れていません（`curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/`）

## 3. タイムアウト無しが固まることを確認する

レスポンスが返るまで無期限に待つことを手で再現します。`timeout` コマンドはプロセスを殺すもので、アプリのタイムアウトとは別物です。

```bash
# 1. timeout 無しは 10 秒固まる。shell 側の timeout 4 で殺す（exit 124 が殺された証拠）
timeout 4 python3 -c "import requests; print(requests.get('http://127.0.0.1:18080/slow').text)" || echo "killed by outer timeout (exit $?)"
# 2. 標準ライブラリも同じ。timeout 無しは既定でブロックする
timeout 4 python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:18080/slow').read())" || echo "killed by outer timeout (exit $?)"
```

期待:

- どちらも本文が出ず、`timeout` コマンドに殺されます（exit 124 です）
- これが gunicorn のワーカーで起きると、そのワーカーは 10 秒まるごと塞がります。同時に来たリクエストが溜まって高負荷になります

gunicorn の `--timeout`（ワーカーを殺す設定）と混同しません。`--timeout` は固まった後に殺すためのもので、HTTP の待ちそのものを短くしません。先に HTTP 側の `timeout` を付けます。

### 観察すること

- `timeout` 無しが固まることを 2 系統（`requests` / `urllib`）のどちらかで言えます
- 外側の `timeout` コマンドと内側の `timeout=` 引数の違いを言えます
- ワーカーが塞がると何が溜まるか（gunicorn の worker 数と nginx の待ち）を言えます

## 4. タイムアウトを付けて例外で返す

全体タイムアウトに相当します。付けると固まらず例外で返ります。まず単数値、次にタプルです。

```bash
# 単数値 2 秒。ReadTimeout で返る（約 2 秒で終わる）
time python3 -c "import requests; requests.get('http://127.0.0.1:18080/slow', timeout=2)" || echo "raised (exit $?)"
# タプル (connect, read)。接続は速いので read 側で切れる
time python3 -c "import requests; requests.get('http://127.0.0.1:18080/slow', timeout=(1, 2))" || echo "raised (exit $?)"
# 標準ライブラリ
time python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18080/slow', timeout=2)" || echo "raised (exit $?)"
```

例外の読み方:

- `ReadTimeout: Read timed out. (read timeout=2)` → 接続はできましたが本文が来ません。`/slow` 型です
- `ConnectTimeout` / `Connection refused` → 相手が居ないか届きません。停止後の `delay.py` や閉じたポート（例: `http://127.0.0.1:9/`）で出ます。`timeout=(1, 5)` の connect 側が効きます
- `timeout=2` と `timeout=(1, 2)` の差が出るのは connect が遅いときだけです。localhost 再現ではどちらも read 側で切れます。差を見たいときは閉じたポートと `/slow` を両方叩きます

```bash
# connect 側の確認（相手が居ない）
python3 -c "import requests; requests.get('http://127.0.0.1:9/', timeout=(1, 5))" || echo "connect fail (exit $?)"
```

`backup/s2/home/private_isu/webapp/python/app.py`（重い処理なら s2）の HTTP 呼び出し 1 箇所に `timeout` を付けます。コピペ用の完成品ではなく、次の形から自分の 1 箇所に当てはめます:

```python
# まず全部に付ける。値は 4.〜5. で振る
requests.get(url, timeout=5)
requests.get(url, timeout=(3, 5))  # (connect, read)
urllib.request.urlopen(url, timeout=5)
```

受け側は例外を握りつぶしません。500 で返すか、フォールバック（キャッシュ・既定値）に逃がすか決めます。握りつぶして `None` を返すと後の `fail` が読みにくくなります。

同じファイルの該当箇所:

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

- `time` が約 2 秒で終わり、`ReadTimeout` / `TimeoutError` が出ました
- connect 失敗と read 失敗の例外が違うと言えます（閉じたポート vs `/slow`）
- `grep -rn 'requests.get\|urlopen' ... | grep -v timeout` が 0 行になりました（付け忘れがありません）

## 5. GET は短め、POST は長めに分ける

GET の方が数が多いので、先に GET を短くします。

出発点（ベンチで比べる前の仮値）:

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

`backup/s2/home/private_isu/webapp/python/app.py` の定数と呼び出し側:

```python
GET_TIMEOUT = (2, 3)
POST_TIMEOUT = (3, 10)

session.get(url, timeout=GET_TIMEOUT)
session.post(url, data=..., timeout=POST_TIMEOUT)
```

- POST のリトライはここでは足しません。二重 POST になります。まず 1 回の値を決めます
- 短くしすぎて `fail` になったら値を戻します。リトライや上限を同時にいじりません

### 観察すること

- GET と POST で値が違うと言えます（同じ 5 秒を使い回しません）
- 短くした GET が `ReadTimeout` で切れ、長くした POST が通りました
- `fail` が出たらどちらの値を戻すか言えます

## 6. 同一ホストへの上限を確認する（絞りすぎない）

プール上限の話です。Python では `HTTPAdapter` の 3 点を見ます。

```bash
grep -rn 'pool_maxsize\|pool_connections\|HTTPAdapter\|pool_block' \
  /home/isucon/private_isu/webapp/python --include='*.py' | head
python3 -c "from requests.adapters import HTTPAdapter; help(HTTPAdapter.__init__)" | head -5
```

既定の確認です（変更せず値を読むだけです。`signature` で pool 既定を確認できれば成功です）:

```bash
python3 -c "from requests.adapters import HTTPAdapter; import inspect; print(inspect.signature(HTTPAdapter.__init__))"
# (pool_connections=10, pool_maxsize=10, max_retries=0, pool_block=False)
```

各項目の意味:

- `pool_connections=10` … ホスト種別数。まず触らない
- `pool_maxsize=10` … ホスト毎の保持数。まず 10〜20
- `pool_block=False` … 上限を超えたら待たずに新規接続。`True` にすると上限で待つ行列になる

振る舞いの差はスレッド 10 本で見ます。相手は速いサーバ（0.5 秒）でよいです。

`/tmp/timeout/fast.py`:

```python
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
```

`/tmp/timeout/pool.py`:

```python
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
```

回します:

```bash
python3 /tmp/timeout/fast.py &
echo $! | tee /tmp/timeout/fast.pid
sleep 1
python3 /tmp/timeout/pool.py 1 noblock
python3 /tmp/timeout/pool.py 1 block
python3 /tmp/timeout/pool.py 10 noblock
kill $(cat /tmp/timeout/fast.pid)
```

期待（実測例）:

- `maxsize=1 noblock` … 約 0.5 秒です。待たずに新規接続するので速いですが、使い回しは効きません
- `maxsize=1 block` … 約 5.0 秒です。1 本ずつ順番待ちになります
- `maxsize=10 noblock` … 約 0.5 秒です。10 本並列で使い回します

読み方:

- 最初から `maxsize=1` や `pool_block=True` に絞りません。p99 が跳ねたら上限が犯人です。戻してから次へ進みます
- `too many open files` が出たらプールの数が原因と決めつけません。`ulimit -n` / `ss -s` を見ます（[0050-http-client.md §6](0050-http-client.md#6-同一ホストへの上限を確認する絞りすぎない) と同じです）
- 方針は `pool_maxsize=20` から始めます。`oha -c 50` で p99 が壊れない範囲だけ変えます

方針の例（`/tmp` の合成スクリプトと同じ形です）:

```python
# モジュール先頭で 1 個。リクエスト毎に Session() を作らない
import requests
from requests.adapters import HTTPAdapter

session = requests.Session()
session.mount("http://", HTTPAdapter(pool_connections=20, pool_maxsize=20))
session.mount("https://", HTTPAdapter(pool_connections=20, pool_maxsize=20))
# 使う側は毎回 session.get(url, timeout=(3, 5))
```

### 観察すること

- 3 本の `elapsed` を数字で言えます（速い / 遅い / 速い）
- `block=True` の遅さが「上限の順番待ち」だと説明できます
- 自分の値は 1 箇所だけです。その値と理由（`oha -c 50` の p99）を言えます

## 7. アプリに足すときの形（1 箇所だけ）

合成で掴んだら、自分の 1 箇所に当てます。まとめて全部は変えません。

1. `Session` はモジュール先頭で 1 個作ります（gunicorn はワーカー別プロセスなのでワーカー毎に 1 個になります）。リクエスト毎に `Session()` を作りません
2. `timeout` は全部に付けます。GET / POST で値を分けます（§5）
3. 受けたら `content` を読んでから閉じます（[0050-http-client.md §3](0050-http-client.md#3-body-を読み切って-close-する)）。読まずに捨てるとプールに戻りません

`backup/s2/home/private_isu/webapp/python/app.py` の先頭寄り（関数の中ではなく 1 回だけ）:

```python
import requests
from requests.adapters import HTTPAdapter

session = requests.Session()
session.mount("http://", HTTPAdapter(pool_connections=20, pool_maxsize=20))
session.mount("https://", HTTPAdapter(pool_connections=20, pool_maxsize=20))

GET_TIMEOUT = (2, 3)
POST_TIMEOUT = (3, 10)
```

同じファイルの呼び出し側（例）:

```python
with session.get(url, timeout=GET_TIMEOUT) as r:
    r.raise_for_status()
    body = r.content
```

アプリの変更は作業機で入れます:

```bash
# 作業機
git pull --ff-only
# 上の内容を配置する
git add -A && git commit -m "tune: Session と timeout を入れる" && git push
```

依存の足し方（`requests` が無いときだけ）。`pyproject.toml` の変更も作業機で入れます:

```bash
# 作業機（ローカル環境の private-isu-terraform リポジトリにて実行）
git pull --ff-only
cd backup/s2/home/private_isu/webapp/python
uv add requests
cd - >/dev/null
git add -A && git commit -m "tune: requests を足す" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s2/home/private_isu/webapp/python/app.py /home/isucon/private_isu/webapp/python/app.py
sudo cp -a backup/s2/home/private_isu/webapp/python/pyproject.toml /home/isucon/private_isu/webapp/python/pyproject.toml
sudo su - isucon
cd /home/isucon/private_isu/webapp/python
uv sync
sudo systemctl restart isu-python.service
systemctl is-active isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/  # s2 で打つ。s1 は unix socket なので :80 で見る
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
sudo journalctl -u isu-python.service -n 40 --no-pager
```

依存を増やしたくないときは標準の `urllib.request.urlopen(url, timeout=5)` でよいです。プール再利用は効きにくいですが、タイムアウトの型は同じです。

nginx 側のタイムアウトは別物です。アプリの `timeout` を先に付けてから、長すぎないか見ます:

```bash
grep -rn 'proxy_.*_timeout' /etc/nginx/sites-enabled/* /etc/nginx/conf.d/ 2>/dev/null | head
# 例: proxy_connect_timeout 5s; proxy_read_timeout 60s;
# アプリの read 5 秒に対して nginx の read 60 秒なら、アプリ側が先に切れる。正しい順序
```

変えたら restart / reload して `curl` 2 本を確認します。短くしすぎると `fail` になります。そのときは値を戻します:

```bash
git pull --ff-only
sudo cp -a backup/s2/home/private_isu/webapp/python/app.py /home/isucon/private_isu/webapp/python/app.py
# nginx も変えた場合だけ次の 2 行を打つ
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl restart isu-python.service
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/  # 変えたアプリの台で打つ（s1 は :80 のみ）
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- `Session` が 1 個ですか（`grep -c 'Session()'` が 1 です）
- `timeout` 無しの行が 0 行ですか
- `curl` 2 本が 200 になります。`journalctl` に例外が出ていません

## 8. 公式ベンチで判定する（s1 で打つ。slow 側は s3）

[§2](#2-遅延サーバを用意する相手役)〜[§7](#7-アプリに足すときの形1-箇所だけ) のどれか 1 手を残し、他は戻した状態にして公式ベンチを 1 本回します。bench-prep は [0030-measure.md](0030-measure.md) と同じです（s1 で `mv` + `reopen`、s3 で `flush-logs`）。タイムアウト自体は平常時の点数を上げません。判定は「固まらないこと」と「`fail` が 0」です。

```bash
# s1 で打つ
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=timeout-get23-post310" >> ~/bench-notes/scores.txt
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

合成サーバはベンチ前に殺します。ベンチ中に `18080` / `18081` を残しません:

```bash
kill $(cat /tmp/timeout/delay.pid) 2>/dev/null || true
kill $(cat /tmp/timeout/fast.pid) 2>/dev/null || true
sudo ss -lntp | grep 1808 || echo 'synthetic servers stopped'
```

戻し方は変えた層だけやります。作業機で戻してからサーバーに反映します:

```bash
# 作業機。Python を戻すとき（Session / timeout の差分だけ消す）
git pull --ff-only
# backup/s2/home/private_isu/webapp/python/app.py を戻す
git add -A && git commit -m "tune: timeout を外す" && git push
```

```bash
# サーバーで反映
git pull --ff-only
sudo cp -a backup/s2/home/private_isu/webapp/python/app.py /home/isucon/private_isu/webapp/python/app.py
sudo systemctl restart isu-python.service
# nginx を戻すとき
sudo cp -a backup/s1/etc/nginx/sites-enabled/isucon.conf /etc/nginx/sites-enabled/isucon.conf
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- ベンチ前後で合成サーバが残っていません（`ss` で 18080 / 18081 がありません）
- `fail` が 0 です。`fail` が出たら §5 か §6 の締めすぎを疑います（タイムアウトを延ばす前に上限を戻します）
- alp の Sum が落ちないなら HTTP 層は詰まっていません（DB / キャッシュの仕事に戻ります）

## 9. トラブルシュート

### タイムアウトで fail が出る

- 短くしすぎています。まず GET `(2, 3)` / POST `(3, 10)` に戻します
- `session.get(url, timeout=...)` と `urllib` の既定混在になっていないか確認します。`grep -v timeout` で洗い出します
- 相手の p99 がタイムアウトに張り付いているなら、こちらの値を延ばさず相手（DB / 上流）を直します。alp と slp に戻ります

### 上限を絞ったら遅くなる

- 正常です（悪化を確認する逆向きの試験です）。`pool_maxsize` / `pool_block` を戻します。§6 の 3 本の `elapsed` を取り直します
- `too many open files` は FD 不足です。`ulimit -n` と `ss -s` を見ます。プールを絞っても直りません

### requests が無い / uv sync 忘れ

```bash
sudo su - isucon
cd /home/isucon/private_isu/webapp/python
uv sync
sudo systemctl restart isu-python.service
sudo journalctl -u isu-python.service -n 40 --no-pager | grep -i -E 'ModuleNotFound|requests'
```

- `ModuleNotFoundError: requests` → `uv add requests` 漏れか `uv sync` 漏れです
- `/tmp` の合成では system python の `pip` でよいです。`.venv` と混ぜません

### 合成サーバが残っている

```bash
ps -ef | grep -E 'delay\.py|fast\.py' | grep -v grep
kill $(cat /tmp/timeout/delay.pid) 2>/dev/null || true
kill $(cat /tmp/timeout/fast.pid) 2>/dev/null || true
sudo ss -lntp | grep 1808 || echo 'cleaned'
```

- ベンチ前に殺します。残すとポート枯れや `fail` の原因になります
- `/tmp/timeout` はドリル用です。再起動で消えます。本番に置きません
