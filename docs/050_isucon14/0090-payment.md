# 0090 決済の冪等と遅延

`POST /api/app/rides/{ride_id}/evaluation` は、ライドを完了させると同時に決済サーバーへ HTTP を送ります。決済が遅いと評価リクエストがタイムアウトし、決済の重複は点数の不一致になります。仕様に沿って直します。[0080-notification.md](0080-notification.md) の続きです。出口は [0100-matching.md](0100-matching.md) です。

## 1. 仕組みを読みます

```bash
cd ~/webapp/python
grep -n 'def app_post_ride_evaluation' -A 110 app/app_handlers.py
grep -n 'def request_payment_gateway_post_payment' -A 85 app/payment_gateway.py
```

`app_post_ride_evaluation` は次の順に処理します。

1. `rides` の状態を `COMPLETED` に更新し、`ride_statuses` に 1 行追加する
2. `payment_tokens` から user's token を読む
3. 運賃を計算する
4. `settings.payment_gateway_url` を読む
5. `request_payment_gateway_post_payment` を呼ぶ

`request_payment_gateway_post_payment` は `POST {payment_gateway_url}/payments` を送り、204 以外が返ると `GET {payment_gateway_url}/payments` で支払履歴と照合します。失敗すると 0.1 秒 待って最大 5 回まで再試行します。照合に使う `retrieve_rides_order_by_created_at_asc` は、そのユーザーの全リードを読み直す関数です。

## 2. 失敗する条件（ベンチ側の判定）

公式 `docs/manual.md` の「負荷走行の打ち切り」から読みます。

- `POST /api/app/rides/{id}/evaluation` のタイムアウトは 10 秒
- 負荷走行終了時点で行われていた決済は、走行終了後 5 秒以内に完了する必要があります
- 5 秒以内に完了しないと、ベンチが期待する決済金額と一致せず FAIL になります

つまり「決済を速くする」だけでなく「決済を必ず 1 回だけ送达する」必要があります。

## 3. 1 手目、Idempotency-Key を付けます

決済マイクロサービスは `Idempotency-Key` ヘッダを受け付け、同じキーを持つリクエストを冪等に処理します（公式 `docs/ISURIDE.md` の「Idempotency-Key ヘッダを利用したリクエストの重複防止」）。同じ `ride_id` をキーにすると、再試行しても 1 回しか計上されません。

変更前にバックアップを取ってから、`payment_gateway.py` の `urllib3.request` にヘッダを足します。差分は 1 行です。

```bash
cd ~/webapp/python
cp -a app/payment_gateway.py app/payment_gateway.py.orig
```

ヘッダを追加します。

```python
res = urllib3.request(
    "POST",
    payment_gateway_url + "/payments",
    json=param.model_dump(),
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idempotency_key,
    },
    retries=False,
)
```

`idempotency_key` を関数に渡す形に変えます。呼び出し側の `app_post_ride_evaluation` は 1 つの ride につき 1 回しか評価されないので、`ride_id` をそのままキーに使えます。

```python
def request_payment_gateway_post_payment(
    payment_gateway_url: str,
    token: str,
    param: PaymentGatewayPostPaymentRequest,
    idempotency_key: str,
    retrieve_rides_order_by_created_at_asc: Callable[[], list[Ride]],
) -> None:
```

呼び出し側は引数を 1 つ増やします。

```python
request_payment_gateway_post_payment(
    payment_gateway_url,
    payment_token.token,
    payment_gateway_request,
    ride_id,
    retrieve_rides_order_by_created_at_asc,
)
```

反映して確認します。

```bash
python3 -c "import ast;[ast.parse(open(f).read()) for f in ['app/app_handlers.py','app/payment_gateway.py']];print('syntax ok')"
sudo systemctl restart isuride-python.service
sudo journalctl -u isuride-python -n 20 --no-pager
grep -n 'Idempotency-Key' app/payment_gateway.py
```

成功の見え方: `syntax ok` が出て、`grep` が 1 行返ります。

## 4. 手元の決済モックで動きを確認します

アプリ用インスタンスには 12345 番で `isuride-payment_mock.service` が動いています（[0040-bench.md](0040-bench.md) で見たものです）。POST が通ることと、履歴が GET で取れることを確認します。

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  http://localhost:12345/payments \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer test-token' \
  -d '{"amount":1500}'
curl -sS http://localhost:12345/payments -H 'Authorization: Bearer test-token'
```

成功の見え方:

- 1 つめが `204`（`POST /payments` は成功時に 204 を返します）
- 2 つめが `[{"amount":1500,"status":"成功"}]` という JSON

`Idempotency-Key` を付けても結果は同じです。このモックはヘッダを見ない簡易版なので、冪等性の検証には使いません。実際の検証は手順 5. のベンチで行います（ベンチの決済サーバーが `Idempotency-Key` を解釈します）。

## 5. 2 手目、リトライの待ち時間を短くします

`payment_gateway.py` は失敗ごとに `time.sleep(0.1)` します。評価リクエストのタイムアウトは 10 秒なので、リトライが 5 回済むと 0.5 秒を消費します。

リトライの待ち時間を 0.02 秒に短縮し、試行回数を 20 回まで増やします。

```python
except Exception:
    if retry < 20:
        retry += 1
        time.sleep(0.02)
        continue
    else:
        raise
```

決済が誰も戻らない状態を 20 回待つと、かえって評価リクエストが 10 秒を超えて FAIL します。`payment_gateway_url` が途中で切れているときは長く待たない方が安全です。

反映してからベンチを 1 本回します。

```bash
# ベンチ用インスタンスで
source ~/bench.env
cd ~
./bench run --target https://isuride.xiv.isucon.net \
  --addr ${APP_IP}:443 --payment-url http://${BENCH_IP}:12345 -t 60
```

成功の見え方: ログに `クリティカルエラーが発生しました` が出ず、最終行が `msg=結果 pass=true` になります。スコアを `~/bench-notes/scores.txt` に 1 行足します。

?> `pass=false` のままなら、ベンチのログにある `決済 Amount が一致しない` 系のエラーを探します。1 回だけ決済したはずが 2 回分計上されている場合は、`Idempotency-Key` が効いていない可能性があります。

## 6. 3 手目、決済を DB へ積む（任意）

評価リクエストを待たせないには、決済を「記録 → ワーカーで送信」の 2 段にします。`payment_status` のような列を足すことになりますが、ベンチは `POST /api/initialize` で DB を初期化するため、**スキーマの変更は必ず `~/webapp/sql/1-schema.sql` に入れて初期化に追随させてください**（[0060-sql.md](0060-sql.md) の手順 3 と同じ考え方です）。

猶予は 5 秒までです。ワーカーの周期は 0.2 秒程度なら余裕があります。 実装では、`isuride-payment` という systemd サービスを 1 つ増やします。

```bash
sudo tee /etc/systemd/system/isuride-payment.service >/dev/null <<'EOF'
[Unit]
Description=isuride-payment worker
After=mysql.service
Requires=mysql.service

[Service]
WorkingDirectory=/home/isucon/webapp/python
User=isucon
Group=isucon
EnvironmentFile=/home/isucon/env.sh
ExecStart=/home/isucon/webapp/python/.venv/bin/python -m app.payment_worker

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
```

`app/payment_worker.py` は自分で作ります。作り終えたら起動します。

```bash
sudo systemctl enable --now isuride-payment.service
sudo systemctl status isuride-payment --no-pager | head -n 10
sudo journalctl -u isuride-payment -n 20 --no-pager
```

成功の見え方: `active (running)` が出て、`journalctl` にエラーが流れません。

?> ワーカーを作らない場合、この手順は飛ばして構いません。スコアは [0100-matching.md](0100-matching.md) で大きく変わります。決済は「点数を上げる」主角ではなく「落とさない」ための作業です。

## 7. 元に戻すには

```bash
cd ~/webapp/python
cp -a app/payment_gateway.py app/payment_gateway.py.orig
```

編集に入ります。

```bash
cd ~/webapp/python
cp -a app/payment_gateway.py.orig app/payment_gateway.py
sudo systemctl restart isuride-python.service
# ワーカーを止めた場合は
sudo systemctl disable --now isuride-payment.service
sudo rm /etc/systemd/system/isuride-payment.service
sudo systemctl daemon-reload
```

## 出口

決済が安定したら [0100-matching.md](0100-matching.md) へ進みます。次はスコア本体であるマッチングを速くします。
