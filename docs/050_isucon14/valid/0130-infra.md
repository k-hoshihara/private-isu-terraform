# 0130 インフラ（分割しても効くもの）

ここまでは**アプリと SQL の中身**を削ってきました。この章は**外側**です。**分割しても効くもの**だけを扱います。

?> **このインスタンスは 2 vCPU です。**アプリ・MySQL・nginx が**同じ 2 コアを奪い合っています**。この章の数字は、その前提で読んでください。

## 1. アプリへの接続を keepalive します

`/etc/nginx/sites-available/isuride.conf` は `proxy_pass http://localhost:8080;` でした。**nginx の既定は HTTP/1.0 で、上流への接続を使い捨て**にします。60 秒で 5 万リクエストなら、そのぶん**接続の作成と後片付け**が起きます。

```nginx
# /etc/nginx/sites-available/isuride.conf の先頭（server の外）
upstream isuride_app {
    server 127.0.0.1:8080;
    keepalive 64;
}

server {
  ...
    location / {
        proxy_pass http://isuride_app;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        ...
    }
}
```

?> **3 行セットで意味があります。**`proxy_http_version 1.1` だけでは keepalive になりません。**`Connection: ""` で「閉じる」ヘッダを消す**必要があります。`upstream` に `keepalive 64`（プールする本数）も書きます。

```bash
sudo nginx -t && sudo systemctl reload nginx
```

?> **`worker_processes` は AMI のまま `auto`、`worker_connections` は 768 のままにしました。**同時接続は実測で 130 程度（5 万リクエスト ÷ 60 秒 ÷ 平均 0.17 秒）なので、768 × 2 worker で足ります。**上げる前に、必要かを計算します。**

## 2. アクセスログを止めます（実走行時）

0050 で入れた LTSV のアクセスログは**計測のため**のものです。実走行では止めます。

```bash
sudo sed -i 's|^  access_log /var/log/nginx/access.ltsv ltsv;|  access_log off;|' /etc/nginx/sites-available/isuride.conf
sudo nginx -t && sudo systemctl reload nginx
```

?> **実測ではスコアはほとんど変わりませんでした**（ログ OFF: 7242 / 6359、ログ ON: 7300 / 6502）。**この LTSV は 1 リクエストあたりのコストが小さい**ので、**計測のためなら付けっぱなしでよい**という判断です。ただし**ディスクを食う**ので、長く回すときは注意します。

## 3. gunicorn の worker を増やします

設定は `/home/isucon/webapp/python/gunicorn.conf.py` にあります。

```python
wsgi_app = "app.main:app"
bind = "127.0.0.1:8080"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"
accesslog = "-"
```

**`workers = 6` にします。**理由は「リクエスト 1 本あたりの DB 実時間」と「alp の AVG」の比です。

| 見たもの | 値 | 意味 |
| --- | --- | --- |
| alp の AVG（通知） | 0.17〜0.21 s | アプリが返すまでの平均 |
| 通知 1 本の DB 実時間（digest の合計 ÷ 回数） | 約 0.06 s | DB が実際に使っている時間 |
| 比 | **約 3 倍** | **順番待ちが起きている** |

**uvicorn worker は async ですが、このアプリの DB 呼び出しは同期**です。同期呼び出しは**イベントループごと止める**ので、1 worker が同時に DB に投げられるのは 1 本だけです。**worker の数が、そのまま DB 並列度の上限**になります。

```bash
sudo python3 - <<'PY'
import re
p = '/home/isucon/webapp/python/gunicorn.conf.py'
s = open(p).read()
open(p, 'w').write(re.sub(r'(?m)^(\s*)workers\s*=\s*\d+', r'\g<1>workers = 6', s))
PY
sudo systemctl restart isuride-python.service
pgrep -cf gunicorn    # master 1 + worker 6 = 7
```

?> **`pgrep -cf gunicorn` は 7 です**（master が 1 つ増えます）。4 worker のときは 5 でした。

## 4. gunicorn のアクセスログも止めます

`accesslog = "-"` は **stdout（＝journald）に 1 リクエスト 1 行**書きます。nginx のログと同じ内容を**もう 1 回**書いていることになります。

```python
accesslog = None  # 0130: リクエストごとの journald 書き込みを止める
```

```bash
sudo systemctl restart isuride-python.service
```

## 5. 実測（単一インスタンス、2026-10-18）

| 構成 | 1 本目 | 2 本目 | 3 本目 | 4 本目 | いちばん悪い回 |
| --- | --- | --- | --- | --- | --- |
| 0140 + 0100 の後 | 4756 | 5475 | — | — | 4756 |
| nginx keepalive（ログ OFF） | **7242** | 6359 | — | — | 6359 |
| nginx keepalive（ログ ON） | **7300** | 6502 | — | — | 6502 |
| + gunicorn worker 6 | 6546 | **7403** | — | — | 6546 |
| + `accesslog = None` | 6928 | 6471 | — | — | 6471 |

**最高は 7403**（AMI は 607、0060 の後は 1591）。

### alp（上位 3 本の推移）

| 窓 | `chair/notification` AVG / P99 | `app/notification` AVG / P99 | `chair/coordinate` AVG / P99 | リクエスト数 |
| --- | --- | --- | --- | --- |
| 0110 | 0.224 / 0.879 | 0.294 / 0.951 | 0.333 / 1.021 | 18,152 |
| 0090 | 0.209 / 0.726 | 0.275 / 0.789 | 0.343 / 0.856 | 18,107 |
| 0120 | 0.166 / 0.528 | 0.214 / 0.605 | 0.276 / 0.678 | 22,093 |
| **0130** | **0.171 / 0.551** | **0.210 / 0.589** | **0.261 / 0.645** | **50,043** |

?> **0130 の窓は負荷が 2.3 倍**（22,093 → 50,043 リクエスト）です。**同じ AVG・P99 を 2.3 倍の負荷で出している**ので、**1 リクエストあたりでは大きく改善**しています。**マッチングが速くなると、完了するライドが増えて、ベンチが出すリクエストも増えます。**窓をまたいで比べるときは、**リクエスト数で正規化**してください。

### ここで単一インスタンスは頭打ちです

最後の窓の digest です（`Questions = 286,523`、50,043 リクエスト）。

| 回数 | 合計 | 1 回 | SQL |
| --- | --- | --- | --- |
| 16,032 | **740.5 s** | **46.19 ms** | `COMMIT` |
| 19,104 | 666.0 s | 34.86 ms | 通知の状態読み（アプリの枝） |
| 17,255 | 614.5 s | 35.61 ms | 通知の状態読み（椅子の枝） |
| 32,325 | 439.0 s | 13.58 ms | `SELECT * FROM rides WHERE chair_id = ? ORDER BY updated_at DESC` |
| 10,597 | 292.1 s | 27.56 ms | `SELECT status FROM ride_statuses WHERE ride_id = ? ... LIMIT 1` |
| 16,286 | 147.1 s | 9.03 ms | `SELECT * FROM chairs WHERE id = ?` |

**索引は効いています**（`slp` は最後まで 0 件、`chairs WHERE id` は 1 行読み）。遅いのは**1 本の SQL ではなく、2 コアの奪い合い**です。`COMMIT` が 46 ms なのは、コミットが**ディスク待ちではなく CPU 待ち**になっているからです。

?> **単一インスタンスでは、ここから先は増えません。**アプリを速くすると負荷が増え、MySQL と CPU を奪い合います。**次は 3 台に分けて、MySQL に専用の CPU を渡します**（**0160 分割**）。

## 6. 戻すには

```bash
# nginx
sudo cp -a /etc/nginx/sites-available/isuride.conf.pre0130 /etc/nginx/sites-available/isuride.conf
sudo nginx -t && sudo systemctl reload nginx
# gunicorn
sudo sed -i 's/^workers = 6/workers = 4/; s/^accesslog = None.*/accesslog = "-"/' /home/isucon/webapp/python/gunicorn.conf.py
sudo systemctl restart isuride-python.service
```

## 出口

次は **0160（3 台に分割）**です。
