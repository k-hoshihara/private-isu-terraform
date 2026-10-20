# 0100 決済の冪等化

`POST /api/app/rides/{ride_id}/evaluation` は、ライドを完了させると同時に**決済サーバーへ HTTP を送ります**。ここは**スコアの手ではなく、FAIL を防ぐ手**です。

## 1. 仕様を読みます

`docs/manual.md` の「負荷走行の打ち切り」から読みます。

- `POST /api/app/rides/{id}/evaluation` のタイムアウトは **10 秒**
- 負荷走行の終了時点で行われていた決済は、**終了後 5 秒以内に完了**している必要があります
- 完了しないと、ベンチが期待する決済金額と一致せず **FAIL** になります

`docs/ISURIDE.md` には次の 1 行があります。

> **Idempotency-Key ヘッダを利用したリクエストの重複防止**

**同じキーで再送しても 1 回しか計上されません。**AMI は再試行のたびに「キーなし」で POST するので、**障害時に二重計上**の可能性がありました。

## 2. AMI の実装

```python
# app/payment_gateway.py（AMI のまま、抜粋）
    retry = 0
    while True:
        try:
            res = urllib3.request(
                "POST",
                payment_gateway_url + "/payments",
                json=param.model_dump(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                retries=False,
            )
            if res.status != HTTPStatus.NO_CONTENT:
                # エラーが返ってきても成功している場合があるので問い合わせる
                get_res = urllib3.request("GET", payment_gateway_url + "/payments", ...)
                ...
        except Exception:
            if retry < 5:
                retry += 1
                time.sleep(0.1)
                continue
            raise
        else:
            break
```

**やっていること**は正しいです（204 以外なら一覧を照合し、失敗は 5 回まで再試行）。**足りないのはキーだけ**です。

## 3. `Idempotency-Key` を付けます

`ride_id` をそのままキーにします。**1 つのライドの決済は 1 回だけ**だからです。

```python
# app/payment_gateway.py
def request_payment_gateway_post_payment(
    payment_gateway_url: str,
    token: str,
    param: PaymentGatewayPostPaymentRequest,
    idempotency_key: str,
    retrieve_rides_order_by_created_at_asc: Callable[[], list[Ride]],
) -> None:
    ...
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                    # 0100: 同じキーの再送は 1 回だけ計上される
                    "Idempotency-Key": idempotency_key,
                },
```

```python
# app/app_handlers.py の app_post_ride_evaluation
            request_payment_gateway_post_payment(
                payment_gateway_url,
                payment_token.token,
                payment_gateway_request,
                ride_id,
                retrieve_rides_order_by_created_at_asc,
            )
```

## 4. 実測（単一インスタンス、2026-10-18）

| 構成 | 1 本目 | 2 本目 |
| --- | --- | --- |
| 0140 の後 | 5107 | 6076 |
| **0100（この手）** | **4756** | **5475** |

?> **この手はスコアでは見えません。**この環境では決済の FAIL を再現できなかったので、**仕様（`ISURIDE.md` と `manual.md`）を根拠に採用**しています。スコアの帯（4756〜5475）は前後と重なっています。**「測れないものは仕様で決める」**という判断です。

?> 決済は**ベンチの外**（`--payment-url` で指定したモック）に飛びます。**アプリの外に出す処理は、アプリのスコアを上げません。**だからここは**正しさ**だけを見ます。

## 5. 戻すには

```bash
cp app/payment_gateway.py.pre0100 app/payment_gateway.py
cp app/app_handlers.py.pre0100 app/app_handlers.py
sudo systemctl restart isuride-python.service
```

## 出口

次は **0130（インフラ）**です。ここからは**アプリの外側**（nginx・gunicorn・ログ）を見ます。
