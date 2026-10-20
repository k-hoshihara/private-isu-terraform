# 0200 タイムアウト

[0190](0190-split-app.md) までで、**アプリが 0.12 s で返し、DB は 1 ms 以下**になりました。最後は**「返らない」を防ぐ**話です。スコアは上がりませんが、**FAIL を防ぐ**章です。

## 1. 決済の呼び出しにタイムアウトがありません

`POST /api/app/rides/{ride_id}/evaluation` は決済サーバーへ HTTP を送ります（[0100](0100-app-payment.md)）。AMI のコードはこうでした。

```python
# app/payment_gateway.py（AMI のまま）
            res = urllib3.request(
                "POST",
                payment_gateway_url + "/payments",
                json=param.model_dump(),
                headers={...},
                retries=False,
            )
```

**`timeout` がありません。**`urllib3` の既定は**無限に待つ**です。決済サーバーが詰まると、**評価リクエストが返らなくなります**。ベンチの評価タイムアウトは **10 秒**（`docs/manual.md`）なので、**そのまま FAIL** です。

## 2. 2 秒 / 5 秒のタイムアウトを入れます

```python
# app/payment_gateway.py（POST と GET の両方）
                retries=False,
                # 0200: 決済が詰まってもリクエストが返らなくならないように
                timeout=urllib3.Timeout(connect=2.0, read=5.0),
```

```bash
grep -c 'urllib3.Timeout' app/payment_gateway.py   # 2（POST と GET）
```

?> **予算を先に決めます。**「接続 2 秒 + 読み取り 5 秒 = 7 秒 < ベンチの評価 10 秒」です。**この 7 秒に再試行（`retry < 5`）が乗る**ので、**最悪では 10 秒を超えます**。評価のタイムアウトが出たら、**再試行の回数を落として予算に収めます**（例: 2 回）。

## 3. ほかのタイムアウトも、予算で決めます

| 場所 | 値 | 根拠 |
| --- | --- | --- |
| アプリ → DB の接続 | `connect_timeout=5`（[0150](0150-mw-lib.md)） | DB は同じ VPC。5 秒待つなら異常 |
| アプリ → DB の読み書き | `read/write_timeout=30`（[0150](0150-mw-lib.md)） | 長いクエリでも 30 秒。`slp` は 0 件 |
| アプリ → DB のプール待ち | `pool_timeout=10`（[0150](0150-mw-lib.md)） | プールが枯れたら待たずに失敗させる |
| アプリ → 決済 | `connect=2 / read=5`（この章） | ベンチの評価 10 秒に収める |
| nginx → アプリ | 既定の 60 秒のまま | アプリは 0.12 秒で返す。**短くすると正常な遅れを切る** |
| gunicorn の worker | 既定の 30 秒のまま | 同上 |

?> **nginx のタイムアウトを短くしないでください。**「遅いときは早く切る」は正しそうですが、**正常なリクエストを 502 にする**だけです。**切るのは外側（ベンチ）の予算に合わせるとき**だけです。

## 4. 実測（2026-10-20）

| 構成 | 1 本目 | 2 本目 |
| --- | --- | --- |
| 0190 の後 | 16722 | 16349 |
| **0200（タイムアウト）** | **21719** | **20366** |

**スコアは帯の中**です。この章の成果は、**決済が詰まってもアプリが返る**ことです。

?> **この手は「測れない」典型**です。決済サーバーが詰まる状況を作らないと差が出ません。**それでも入れる理由は、`docs/manual.md` の打ち切り条件（評価 10 秒）に直接ぶつかるから**です。**測れないものは仕様で決める**（[0100](0100-app-payment.md) と同じ判断）。

## 5. 戻すには

```bash
cp app/payment_gateway.py.pre0200 app/payment_gateway.py
sudo systemctl restart isuride-python.service
```

## 出口

次は [0210-result.md](0210-result.md) です。**ここまでの実測を 1 枚にまとめ、後片付けをします**。
