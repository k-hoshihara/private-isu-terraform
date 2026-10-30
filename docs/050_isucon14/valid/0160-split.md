# 0160 3 台に分割する

[0150](0150-mw-lib.md) の負荷試験で、**2 vCPU が限界**と分かりました（同時接続を 8 倍にしても RPS は ~800 で横ばい、`iowait` 0.4%、CPU 87.9%）。この章は**3 台に分けて、MySQL に専用の CPU を渡します**。

## 役割

| 台 | 役割 | 動かすもの |
| --- | --- | --- |
| `app-1` | 入口 + DB | nginx、MySQL、matcher、payment_mock |
| `app-2` | アプリ | Python 実装（`0.0.0.0:8080`） |
| `app-3` | アプリ | Python 実装（`0.0.0.0:8080`） |

- ベンチの `--addr` は入口の `app-1` です
- `app-2` / `app-3` のアプリは **`app-1` の MySQL** を見ます（DB を 1 つに寄せないとデータが食い違います）
- `app-2` / `app-3` の nginx・matcher・payment_mock は**止めます**（CPU をアプリに回すため）
- `app-2` / `app-3` の MySQL は**残します**（`isuride-python.service` が `Requires=mysql.service` のため）。アプリは使いません

## 1. 調整済みのアプリを app-2 / app-3 に配ります

**ここがこの章の最大の関門です。**`app-2` / `app-3` は AMI の直後の状態なので、**0050〜0150 の直しが入っていません**。配らずに振ると、リクエストの 3 分の 2 を**直っていないアプリ**が処理します。

```bash
# app-2 / app-3 で（`s1` のスナップショットを送ったあと）
sudo bash /tmp/rb/backup/isucon14/restore.sh s1
```

**3 つの関門**を確かめます。

```bash
md5sum app/app_handlers.py app/sql.py          # s1 と同じ md5 か
grep -c 'NOT EXISTS' app/internal_handlers.py  # マッチングの直し
grep -c 'CREATE INDEX' ../sql/1-index.sql      # 索引
grep -c 'Idempotency-Key' app/payment_gateway.py
```

成功の見え方: `1` / `9` / `1` です（この環境の実測）。**1 つでも 0 なら配布が失敗しています。**

?> **`restore.sh` は `env.sh` と `gunicorn.conf.py` も上書きします。**`s1` の `env.sh` は `ISUCON_DB_HOST="127.0.0.1"`、`gunicorn.conf.py` は `bind = "127.0.0.1:8080"` です。**次の 2 つを必ず実行してください。**

```bash
sudo sed -i 's|^ISUCON_DB_HOST=.*|ISUCON_DB_HOST="10.0.1.240"|' /home/isucon/env.sh   # app-1 のプライベート IP
sudo sed -i 's|^bind = .*|bind = "0.0.0.0:8080"|' /home/isucon/webapp/python/gunicorn.conf.py
```

?> **`s1` は git にコミットしていません。**`git pull` で配ろうとすると**古い版**が入ります。**手元のスナップショットをそのまま送る**のが正解です（この教材では `base64` で分割して SSM で送りました）。

## 2. 使わないサービスを止めます

```bash
# app-2 / app-3 で
sudo systemctl disable --now nginx isuride-matcher isuride-payment_mock isuride-go
sudo systemctl is-active isuride-python mysql nginx isuride-matcher
```

成功の見え方: `isuride-python` と `mysql` が `active`、`nginx` が `inactive` です。`isuride-matcher` は `failed` と出ますが、**`ExecStop` の作りで「異常終了」と記録されるだけ**です（`disable` 済みなので再起動でも戻りません）。

## 3. app-1 の MySQL を外に開きます

```bash
# app-1 で
sudo sed -i 's|^bind-address.*|bind-address = 0.0.0.0|' /etc/mysql/mysql.conf.d/mysqld.cnf
sudo systemctl restart mysql
ss -ltnp | grep 3306
```

成功の見え方: `0.0.0.0:3306` で `LISTEN` します。

?> **`isucon` ユーザーは `'isucon'@'%'` で作られています**（AMI の `webapp/sql/0-init.sql`）。追加の `GRANT` は要りません。`app-2` から `mysql -h 10.0.1.240 -uisucon -pisucon isuride -e 'SELECT COUNT(*) FROM users'` で確かめます。

?> **MySQL を再起動したら、アプリも再起動します。**`pool_pre_ping=False`（[0150](0150-mw-lib.md)）なので、**死んだ接続を掴んだまま**になります。

## 4. app-1 の nginx から 3 台へ振ります

[0130](0130-infra.md) で入れた `upstream isuride_app` に 2 台を足すだけです。

```nginx
# /etc/nginx/sites-available/isuride.conf
upstream isuride_app {
    server 127.0.0.1:8080;      # app-1
    server 10.0.1.132:8080;     # app-2
    server 10.0.1.234:8080;     # app-3
    keepalive 64;
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

?> **`initialize` は 3 台のどこに来ても大丈夫です。**`init.sh` は `ISUCON_DB_HOST` を見て DB を初期化します。`app-2` に来ても `app-1` の DB を初期化します（`app-2` の DB は空のままです）。

## 5. 実測（2026-10-20）

**ベンチ 3 本（すべて `pass=true`）:**

| 構成 | 1 本目 | 2 本目 | 3 本目 | いちばん悪い回 |
| --- | --- | --- | --- | --- |
| 単一インスタンス（0150 の後） | 7053 | 6896 | — | 6896 |
| 単一の最高（0130） | **7403** | — | — | 7403 |
| **3 台に分割** | **14694** | **13064** | **12007** | **12007** |

**約 2 倍**です（7403 → 14694）。

### 振り分け（nginx の `upstream_addr` を数える）

| 台 | リクエスト数 | 割合 |
| --- | --- | --- |
| `app-2`（10.0.1.132） | 54,411 | 36% |
| `app-3`（10.0.1.234） | 51,995 | 34% |
| `app-1`（127.0.0.1） | 45,837 | 30% |
| 到達せず（`-`） | 424 | 0.3% |

**きれいに 3 等分**です。`app-1` が少ないのは、nginx と MySQL も持っているためです。

### alp（152,662 リクエストの窓）

| COUNT | URI | AVG | P99 |
| --- | --- | --- | --- |
| 67,985 | `/api/chair/notification` | **0.112** | **0.484** |
| 49,542 | `/api/app/notification` | **0.137** | **0.539** |
| 25,937 | `/api/chair/coordinate` | **0.173** | **0.588** |

**単一インスタンスの 3 倍の負荷（152,662 リクエスト）で、AVG は 0.171 → 0.112、P99 は 0.551 → 0.484 に下がりました。**データベースが専用の CPU を得たからです。

### ここから見える次のボトルネック

digest（3 本ぶんの合計）:

| 回数 | 合計 | 1 回 | SQL |
| --- | --- | --- | --- |
| 58,405 | **1,637 s** | 28.03 ms | 通知の状態読み（アプリの枝） |
| 49,402 | **1,572 s** | 31.82 ms | 通知の状態読み（椅子の枝） |
| 42,705 | 1,317 s | 30.84 ms | `COMMIT` |
| 93,725 | 794 s | 8.47 ms | `SELECT * FROM rides WHERE chair_id = ? ...` |

**上位 3 つが MySQL の 1 台に集中しています。**`Questions` は 787,209。**アプリは 3 台になったのに、DB は 1 台のまま**なので、次はここです。

?> **`map[1:...]` などのエラーが少し出ました**（152,662 リクエストに対して 1〜24 件）。`pass=true` は維持していますが、**分割前には出ていなかった種類**です。次の章で、接続とロックの競合を疑って追います。

## 6. 元に戻すには

```bash
# app-1
sudo python3 - <<'PY'
p = '/etc/nginx/sites-available/isuride.conf'
s = open(p).read()
s = s.replace('    server 10.0.1.132:8080;\n    server 10.0.1.234:8080;\n', '')
open(p, 'w').write(s)
PY
sudo nginx -t && sudo systemctl reload nginx
sudo sed -i 's|^bind-address.*|bind-address = 127.0.0.1|' /etc/mysql/mysql.conf.d/mysqld.cnf
sudo systemctl restart mysql isuride-python

# app-2 / app-3
sudo systemctl disable --now isuride-python
sudo sed -i 's|^bind = .*|bind = "127.0.0.1:8080"|' /home/isucon/webapp/python/gunicorn.conf.py
```

## 出口

次は **0170（分割前提のインフラ）**です。**MySQL を app-3 に移して、app-1 を「入口 + アプリ」にします**。
