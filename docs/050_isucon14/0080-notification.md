# 0080 通知エンドポイントを直す

`GET /api/app/notification` と `GET /api/chair/notification` は 30 ms ごとに叩かれるため、リクエスト数がいちばん多いエンドポイントです。重いクエリを 1 手ずつ潰し、最後に SSE へ切り替えます。[0070-nearby-chairs.md](0070-nearby-chairs.md) の続きです。出口は [0090-payment.md](0090-payment.md) です。

## 1. 実装を読みます

```bash
cd ~/webapp/python
grep -n 'def app_get_notification' -A 90 app/app_handlers.py
grep -n 'def get_chair_stats' -A 50 app/app_handlers.py
```

`app_get_notification` は次のようにクエリを並べます。

1. `SELECT * FROM rides WHERE user_id = ? ORDER BY created_at DESC LIMIT 1`
2. `SELECT * FROM ride_statuses WHERE ride_id = ? AND app_sent_at IS NULL ORDER BY created_at ASC LIMIT 1`
3. 2 が空のときだけ `get_latest_ride_status` で最新状態を取りに行く
4. 運賃計算で `SELECT * FROM coupons WHERE used_by = ?`
5. `SELECT * FROM chairs WHERE id = ?`、そして `get_chair_stats`

重いのは 5 の `get_chair_stats` です。`SELECT * FROM rides WHERE chair_id = ?` でその椅子の全リードを読み、**ride 1 件ごとに** `ride_statuses` を読んで `ARRIVED` / `CARRYING` / `COMPLETED` の有無を集計します。ride は増えるので、これ，请求 1 回あたりのクエリ数も一緒に増えます。

## 2. 1 手目、椅子の統計を 1 クエリにします

`get_chair_stats` の本体を、次の SQL に置き換えます。元は「その椅子の ride を全部読んで 1 件ずつ状態を見ていた」ので、同じ結果を集合クエリ 1 本で求めます。

```sql
SELECT COUNT(*) AS total_rides_count,
       IFNULL(AVG(rides.evaluation), 0) AS total_evaluation_avg
FROM rides
INNER JOIN (
  SELECT ride_id,
         MAX(status = 'ARRIVED')   AS arrived,
         MAX(status = 'CARRYING')  AS carrying,
         MAX(status = 'COMPLETED') AS completed
  FROM ride_statuses
  GROUP BY ride_id
) AS st ON st.ride_id = rides.id
WHERE rides.chair_id = :chair_id
  AND st.arrived = 1
重いのは 5 の `get_chair_stats` です。`SELECT * FROM rides WHERE chair_id = ?` でその椅子の全リードを読み、**ride 1 件ごとに** `ride_statuses` を読んで `ARRIVED` / `CARRYING` / `COMPLETED` の有無を集計します。ride は増えるので、リクエスト 1 回あたりのクエリ数も一緒に増えます。
  AND st.completed = 1
```

関数ごと差し替えます。既存の `get_chair_stats` は `conn` と `chair_id` を受け取り、`AppGetNotificationResponseChairStats` を返すので、戻り値の型はそのままです。

```python
def get_chair_stats(
    conn: Connection, chair_id: str
) -> AppGetNotificationResponseChairStats:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) AS total_rides_count,
                   IFNULL(AVG(rides.evaluation), 0) AS total_evaluation_avg
            FROM rides
            INNER JOIN (
              SELECT ride_id,
                     MAX(status = 'ARRIVED')   AS arrived,
                     MAX(status = 'CARRYING')  AS carrying,
                     MAX(status = 'COMPLETED') AS completed
              FROM ride_statuses
              GROUP BY ride_id
            ) AS st ON st.ride_id = rides.id
            WHERE rides.chair_id = :chair_id
              AND st.arrived = 1
              AND st.carrying = 1
              AND st.completed = 1
            """
        ),
        {"chair_id": chair_id},
    ).one()

    return AppGetNotificationResponseChairStats(
        total_rides_count=int(row.total_rides_count),
        total_evaluation_avg=float(row.total_evaluation_avg),
    )
```

`Row` はキーで引けるので `row.total_rides_count` で取り出せます。

反映して測ります。

```bash
cd ~/webapp/python
python3 -c "import ast;ast.parse(open('app/app_handlers.py').read());print('syntax ok')"
sudo systemctl restart isuride-python.service
sudo journalctl -u isuride-python -n 20 --no-pager
```

成功の見え方: `syntax ok` が出て、`journalctl` に例外がありません。

クエリ数を測ります。[0070-nearby-chairs.md](0070-nearby-chairs.md) の手順 2 と同じ測り方をします。`/tmp/app.jar`（セッション cookie）もそこで作っています。

```bash
mysql -uisucon -pisucon -e "SHOW GLOBAL STATUS LIKE 'Questions';"
for i in $(seq 10); do
  curl -sS -o /dev/null -b /tmp/app.jar https://isuride.xiv.isucon.net/api/app/notification
done
mysql -uisucon -pisucon -e "SHOW GLOBAL STATUS LIKE 'Questions';"
```

?> `/api/app/notification` は 30 ms ごとに 1 行だけ返すので、`curl -N` を付けずに 10 回叩けば十分です。ride が無いときは `{"retry_after_ms":30}` だけが返ります。

## 3. 2 手目、椅子の通知も直します

`GET /api/chair/notification` は `chair_get_notification` で、1 リクエストあたり 4〜5 クエリです。ここに重いループは無いので、`ride_statuses` のインデックス（[0060-sql.md](0060-sql.md) の手順 8）が入っていることだけ確認します。

```bash
mysql -uisucon -pisucon isuride -e "
  EXPLAIN SELECT id, status FROM ride_statuses
  WHERE ride_id = '01JEG4X2TZSE169T99XERS990M'
    AND chair_sent_at IS NULL
  ORDER BY created_at ASC LIMIT 1\G"
```

`key` が `idx_ride_statuses_ride_chair_sent` を指していることを確認します。

## 4. ベンチを 1 本回します

```bash
# ベンチ用インスタンスで
source ~/bench.env
cd ~
./bench run --target https://isuride.xiv.isucon.net \
  --addr ${APP_IP}:443 --payment-url http://${BENCH_IP}:12345 -t 60
```

成功の見え方: `msg=結果 pass=true` が出たら、スコアを `~/bench-notes/scores.txt` に 1 行足します。

## 5. 3 手目、SSE にする（任意）

ポーリングは 30 ms 間隔なので、通知のたびにリクエストが成立します。SSE（`text/event-stream`）に変えると、ベンチは 1 ユーザーにつき 1 本の接続を開いたまま状態を読み続けます。仕様は公式 `docs/ISURIDE.md` の「通知エンドポイント」にあります。

ベンチ側の判定は次の 2 点だけです（`bench/benchmarker/webapp/client_user.go`）。

- レスポンスの `Content-Type` に `text/event-stream` が含まれること
- 本文の `data:` で始まる行ごとに 1 件として読まれること

`data: ` の後ろに JSON を 1 行で並べ、末尾に空行を 1 つ置きます。続けて既存の本文を関数に切り出します。

```bash
cd ~/webapp/python
grep -n 'def app_get_notification' -A 90 app/app_handlers.py
```

`app_get_notification` の本文をそのまま `build_notification(user_id)` という関数に移し、`AppGetNotificationResponse` を返すようにします（`conn` は中で `engine.begin()` で開き、`user_id` だけ受け取る形に変えます）。その上で、ハンドラーを差し替えます。`app_sent_at` を更新する処理も関数の中に残してください（状態遷移を 1 回以上返すために必要です）。

```python
@router.get("/notification", status_code=HTTPStatus.OK)
async def app_get_notification(
    user: Annotated[User, Depends(app_auth_middleware)],
) -> StreamingResponse:
    async def event_stream():
        while True:
            response = await asyncio.to_thread(build_notification, user.id)
            if response.data is not None:
                payload = response.model_dump(mode="json", exclude_none=True)
                yield "data: " + json.dumps(payload, separators=(",", ":")) + "\n\n"
            else:
                await asyncio.sleep(0.05)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

import を 3 つ足します（`app/app_handlers.py` の先頭）。

```python
import asyncio
import json

from fastapi.responses import StreamingResponse
```

確認します。

```bash
python3 -c "import ast;ast.parse(open('app/app_handlers.py').read());print('syntax ok')"
sudo systemctl restart isuride-python.service
curl -sS -N -b /tmp/app.jar https://isuride.xiv.isucon.net/api/app/notification --max-time 3 | head -4
```

成功の見え方: `data: {"ride_id": ...}` の行が数秒間流れ続けます。`Content-Type` が `text/event-stream` になっていることも確認します。

```bash
curl -sS -N -o /dev/null -D - -b /tmp/app.jar \
  https://isuride.xiv.isucon.net/api/app/notification --max-time 3 | grep -i content-type
```

`content-type: text/event-stream; charset=utf-8` が返ります。

> `to_thread` で定義同期関数を別スレッドで走らせています。DB アクセスは `pymysql` の同期ドライバなので、`to_thread` 無しだとイベントループを塞ぎます。
>
> SSE の接続はベンチが 60 秒間持ちます（ベンチのクライアントは 60 秒でタイムアウトします）。状態が変化しない実装だと 60 秒間何も返らないのでタイムアウトします。上の実装は 50 ms ごとに確認するため、変化が無いあいだは `sleep` を回し続けるだけです。負荷は小さいままです。

?> 椅子側の `GET /api/chair/notification` も同じ形で作れます。ベンチも同じ判定をしています（`bench/benchmarker/webapp/client_chair.go`）。

## 6. 元に戻すには

```bash
cd ~/webapp/python
cp -a app/app_handlers.py.orig app/app_handlers.py
sudo systemctl restart isuride-python.service
```

## 出口

通知が軽くなったら [0090-payment.md](0090-payment.md) へ進みます。次は決済の重複リクエストを止めます。
