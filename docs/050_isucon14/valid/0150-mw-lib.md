# 0150 ミドルウェアとライブラリ

この章は**接続プールとタイムアウト**を決めます。あわせて ****0160** の前に「この箱はどこで飽和するのか」を負荷試験で確かめます**。ここが、いまのスコアが**実力なのか、箱の限界なのか**を分けるところです。

## 1. AMI の接続設定

```python
# app/sql.py（AMI のまま）
engine = sqlalchemy.create_engine(
    f"mysql+pymysql://{user}:{password}@{host}:{port}/{dbname}"
)
```

**プールの指定がありません。**SQLAlchemy の既定は `pool_size=5` / `max_overflow=10` / `pool_timeout=30` / `pool_recycle=-1` / `pool_pre_ping=False` です。既定でも**大きくは外していません**が、**意図を書いておきます**。

## 2. プールとタイムアウトを設定します

```python
# app/sql.py
engine = sqlalchemy.create_engine(
    f"mysql+pymysql://{user}:{password}@{host}:{port}/{dbname}",
    # 0150: worker ごとの接続プール。6 worker x 5 = 30 接続まで（max_connections = 151 に対して余裕）
    pool_size=5,
    max_overflow=5,
    pool_timeout=10,
    pool_recycle=300,
    # 0150: 接続の生存確認クエリは打たない（1 リクエストあたり 1 往復ぶん増える）
    pool_pre_ping=False,
    # 0150: DB が詰まってもリクエストが無限に待たないようにする
    connect_args={
        "connect_timeout": 5,
        "read_timeout": 30,
        "write_timeout": 30,
    },
)
```

| 設定 | 値 | 理由 |
| --- | --- | --- |
| `pool_size` | 5 | **worker 1 つが同時に DB に投げられるのは 1 本**（同期呼び出しがイベントループを止める）。5 あれば十分で、6 worker × 5 = 30 接続は `max_connections=151` に収まる |
| `pool_pre_ping` | `False` | **有効にすると接続を取り出すたびに `SELECT 1` が飛ぶ**。1 リクエスト 1 往復ぶんの純増 |
| `pool_recycle` | 300 | MySQL の `wait_timeout`（既定 28800 秒）より十分短くして、死んだ接続を掴まない |
| `pool_timeout` | 10 | プールが枯れたときに**無限に待たない** |
| `connect_timeout` / `read_timeout` / `write_timeout` | 5 / 30 / 30 | **DB が詰まったときにリクエストが返らなくなる**のを防ぐ |

?> **`pool_pre_ping` は「安全のため」に入れたくなりますが、この構成では純損です。**DB は同じホストにいて、接続が切れるのは MySQL の再起動のときだけです。**再起動するときはアプリも再起動する**（[0140](0140-mysql-write.md) でそうしました）ので、生存確認は要りません。

### ドライバを替えたかったが、入れられませんでした

`pymysql` は**純 Python** のドライバです。C 実装の `mysqlclient` に替えると、**クエリ 1 本あたりの CPU が下がります**。この箱は CPU が限界（下の §3）なので、効くはずの手です。

```bash
.venv/bin/python -m pip install mysqlclient
# ERROR: Failed to build installable wheels for some pyproject.toml based projects (mysqlclient)
```

**この AMI の Python 3.13 では wheel が無く、ビルドも通りませんでした。**`pyproject.toml` は `pymysql[rsa]` のままです。**「効きそう」で終わらせず、入れて測るところまでやって落とす**のがこの章の記録です。

## 3. 負荷試験: この箱はどこで飽和するか

ベンチは**負荷を上げるオプションを持っていません**（`bench run --help` にあるのは `-t` だけ）。そこで**同時接続を変えた負荷試験**を `ab` で回します。**ベンチのスコアは「箱がさばける量」で頭打ちになる**ので、その天井を先に測ります。

準備（ベンチ側のインスタンスで）:

```bash
sudo apt-get install -y apache2-utils
# ベンチは --addr で IP を指定するので hosts は不要だが、ab は名前で引くので向ける
sudo sed -i 's|^127\.0\.0\.1\(\s*\)isuride\.xiv\.isucon\.net|10.0.1.240\1isuride.xiv.isucon.net|' /etc/hosts
```

?> トークンは**ベンチ実行後の DB** から取ります（`initialize` のたびに作り直されるため、**ベンチの後に取る**のがコツです）。

```bash
mysql -uisucon -pisucon -N isuride -e "SELECT access_token FROM users ORDER BY created_at LIMIT 2"
```

```bash
ab -k -q -n 3000 -c 200 -H "Cookie: app_session=$U" \
   https://isuride.xiv.isucon.net/api/app/notification
```

### 結果（`n=3000`、`-k` あり）

**`GET /api/app/notification`**（ログイン済みユーザー）:

| 同時接続 | RPS | 1 リクエスト平均 | P50 | P99 |
| --- | --- | --- | --- | --- |
| 50 | **811.7** | 61.6 ms | 46 ms | 277 ms |
| 100 | **810.1** | 123.4 ms | 118 ms | 284 ms |
| 200 | **768.9** | 260.1 ms | 222 ms | **1,213 ms** |
| 400 | **728.3** | 549.3 ms | 469 ms | **1,571 ms** |

**`GET /api/app/rides`**:

| 同時接続 | RPS | 1 リクエスト平均 | P99 |
| --- | --- | --- | --- |
| 50 | 767.2 | 65.2 ms | 222 ms |
| 100 | 749.5 | 133.4 ms | 366 ms |
| 200 | 724.1 | 276.2 ms | 905 ms |
| 400 | 696.2 | 574.6 ms | 1,385 ms |

**`GET /api/chair/notification`**（同時接続 200）: **442.8 RPS** / 451.6 ms。

### 読み方

**同時接続を 8 倍にしても、RPS は 810 → 728 とほぼ横ばい**です。**P50 は同時接続に比例して伸びる**（46 → 469 ms）ので、これは**閉じた待ち行列**です。**これ以上は箱が捌けません。**

そのあいだの**アプリの台の CPU**:

```text
cpu_total_ticks=9,141  busy=8,039  util=87.9%
user=73.1%  sys=12.2%  iowait=0.4%
```

- **`iowait` は 0.4%** → **ディスクは待っていません**（0140 の `flush=2` / `sync_binlog=0` が効いています）
- **`user` 73% + `sys` 12%** → **CPU が限界**です
- 27,000 リクエストで `Questions` 77,055 → **1 リクエストあたり 2.85 クエリ**。`slp` は 0 件

?> **つまり、いまのスコアは「SQL が遅い」のではなく「2 コアが足りない」で止まっています。**索引も N+1 も効いていて、`slp` に 100 ms 超えのクエリは 1 本もありません。**これ以上アプリや SQL を削っても、この箱ではスコアは増えません。**

?> **逆に言うと、分割したあとに効く手は残っています。**`chair/notification` は 442 RPS で、`app/notification` の 769 RPS の**6 割**しか出ていません。**椅子側の通知はまだ重い**（**0190** で削ります）。

## 4. 実測（単一インスタンス、2026-10-20）

| 構成 | 1 本目 | 2 本目 |
| --- | --- | --- |
| 0130 の後 | 6928 | 6471 |
| **0150（この手）** | **7053** | **6896** |

**プールとタイムアウトはスコアを動かしません**（動かすのは `pool_pre_ping` の有無のような**往復を増やす設定**だけです）。この章の成果は**天井を測ったこと**のほうです。

## 5. 戻すには

```bash
cp app/sql.py.pre0150 app/sql.py
sudo systemctl restart isuride-python.service
```

## 出口

**2 コアが限界**と分かったので、次は **0160-split** で **3 台に分けて、MySQL に専用の CPU を渡します**。
