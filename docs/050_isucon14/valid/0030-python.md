# 0030 Python 実装を起動する

AMI の既定で起動しているのは Go 実装です。Python 実装はビルド済みですが停止した状態で入っています。Go を止めて Python を起動したら [0040-bench.md](0040-bench.md) へ進みます。

## 1. 起動しているものを見ます

```bash
sudo systemctl list-units 'isuride*' --no-pager
```

```text
isuride-go.service          loaded active running
isuride-matcher.service     loaded active running
isuride-payment_mock.service loaded active running
```

Python 実装はビルド済みですが停止した状態です。`isuride-go` は AMI 作成時に `enabled: true` で起動され、`isuride-python` は `enabled: false` で停止されています（公式 `provisioning/ansible/roles/webapp/tasks/` の `go.yaml` と `python.yaml` を読んだ値です）。

```bash
sudo systemctl status isuride-python --no-pager | head -n 10
ls ~/webapp/python
ls ~/webapp/python/.venv/bin | head
```

`gunicorn` が `.venv/bin` にあることだけ確認します。無い場合は AMI 作成時のビルドが済んでいないので、`0010-env.md` の「9. AMI が使えないとき」に沿って AMI を作り直します。

## 2. Go 実装を止めます

`stop` と `disable` を同時に行います。

```bash
sudo systemctl disable --now isuride-go.service
systemctl is-active isuride-go     # inactive であること
```

## 3. Python 実装を起動します

```bash
sudo systemctl enable --now isuride-python.service
systemctl is-active isuride-python # active
sudo ss -ltnp | grep 8080
```

成功の見え方:

- `systemctl is-active isuride-python` が `active` を返す
- `ss` の行が `127.0.0.1:8080` で `gunicorn` が `LISTEN`

Python 実装は `~/webapp/python` を作業ディレクトリにして、`/home/isucon/env.sh` を読んで `gunicorn` を起動します。`gunicorn.conf.py` の設定は次のとおりです。

```python
wsgi_app = "app.main:app"
bind = "127.0.0.1:8080"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"
accesslog = "-"
```

`bind` は loopback 固定で、nginx だけが外から届くように制限されています。`accesslog = "-"` なのでアクセスログは journal に入ります。

## 4. アプリまで届いているか確認します

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://isuride.xiv.isucon.net/api/app/rides
```

成功の見え方: `401` が返ります。認証 cookie がないため `app_auth_middleware` が拒否します。`200` や `404` ではなく `401` が返ることが、nginx からアプリまで届いた証拠です。

## 5. 初期化して言語が `python` になっているか見ます

`POST /api/initialize` は 2 つの働きをします。DB をベンチマーカーが想定する初期状態に戻すことと、`settings.payment_gateway_url` を決済サーバーの URL に設定することです。レスポンスの `language` は言語賞の対象判定に使われます。

```bash
curl -sS -X POST https://isuride.xiv.isucon.net/api/initialize \
  -H 'content-type: application/json' \
  -d '{"payment_server":"http://localhost:12345"}'
echo
```

成功の見え方: `{"language":"python"}` が返ります。

?> この呼び出しでデータはすべて消えます。ベンチは毎回この処理を行うので、データは作り直される前提で考えます。0050 以降の確認は、データがそろってから見てください。

初期化されたかを見ます。

```bash
mysql -uisucon -pisucon isuride -e '
  SELECT COUNT(*) AS users FROM users;
  SELECT COUNT(*) AS chairs FROM chairs;
  SELECT name, value FROM settings WHERE name = "payment_gateway_url";'
```

初期データの規模は次のとおりです（公式の `webapp/sql/3-initial-data.sql.gz` に入っていた値です）。利用者が 500 人超、椅子が 500 台あるため、椅子を全部読む実装は最初から重くなります。

```text
users 559 / chairs 500 / owners 5
chair_locations 10850 / rides 750 / ride_statuses 4496
payment_tokens 559 / coupons 518
```

`payment_gateway_url` は `http://localhost:12345` です。

## 6. 編集するファイルを見ます

```bash
cd ~/webapp/python
ls app
wc -l app/*.py
```

| ファイル | 中身 |
| --- | --- |
| `app/main.py` | `POST /api/initialize` と共通のエラー処理 |
| `app/app_handlers.py` | 利用者側の `/api/app/*`。リクエスト数が一番多い |
| `app/middlewares.py` | cookie の token から user / owner / chair を読む認証 |
| `app/chair_handlers.py` | 椅子側の `/api/chair/*` |
| `app/owner_handlers.py` | オーナー側の `/api/owner/*` |
| `app/internal_handlers.py` | `GET /api/internal/matching`（マッチング） |
| `app/sql.py` | DB 接続（`ISUCON_DB_*` を読む） |
| `app/models.py` | 入出力の形 |
| `app/utils.py` | 運賃とマンハッタン距離の計算 |
| `app/payment_gateway.py` | 決済マイクロサービスへの送信とリトライ |
| `gunicorn.conf.py` | bind アドレスと worker 数 |

DB は `~/webapp/sql/init.sh` で初期化されます。`isuride` データベースに `isucon` / `isucon` で繋ぎます。

## 7. 変更を反映します

コードを直したら再起動します。

```bash
sudo systemctl restart isuride-python.service
sudo journalctl -u isuride-python -n 30 --no-pager
```

起動に失敗したときは `journalctl` に出ます。`ImportError` や `ModuleNotFoundError` なら依存ライブラリが不足しています。

```bash
cd ~/webapp/python
~/.local/bin/uv sync --locked --no-dev
sudo systemctl restart isuride-python.service
```

`uv` は `/home/isucon/.local/bin/uv` にあります（AMI 作成時にインストール済み）。`pyproject.toml` と `uv.lock` を直したときだけ実行します。

## 8. ブラウザで動作を確認します

手元 PC のブラウザで `https://isuride.xiv.isucon.net` を開き、`Simulator Application` を選びます。

1. 椅子の `activity` をオンにします（`QC-L13-8361`）
2. `Client Application` 側で乗車位置と目的地を決めて `ISURIDE` を押します
3. シミュレーターが椅子の動きを再現します

成功の見え方: ライドを要求すると `MATCHING` → `ENROUTE` → `PICKUP` → `CARRYING` → `ARRIVED` → `COMPLETED` と進みます。Python 実装が動いていないと `MATCHING` で止まります（マッチングは `isuride-matcher.service` が 500 ms ごとに `GET /api/internal/matching` を叩きます）。

?> シミュレーターは特定の 1 台の椅子に固定されています。他の椅子が配車受け付け状態だと正常に動きません。動作確認は初期状態のまま行い、挙動が怪しくなってきたら `POST /api/initialize` で初期化してから試します。

## 出口

`{"language":"python"}` が返ったところで [0040-bench.md](0040-bench.md) へ進みます。次はベンチマーカーをビルドして 1 本回します。

?> この章の変更を残すなら、作業機で `backup/isucon14/s1` に積みます（[0015-backup.md](0015-backup.md) の手順 4）。
