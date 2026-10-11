# 0070 nearby-chairs の N+1 を潰す

`GET /api/app/nearby-chairs` は 1 リクエストの中でクエリをループで発行します。クエリ数を測り、1 手ずつ直します。初期データだけでも椅子が 500 台あります。[0060-sql.md](0060-sql.md) の続きです。出口は [0080-notification.md](0080-notification.md) です。

## 1. 実装を読みます

`~/webapp/python/app/app_handlers.py` の `app_get_nearby_chairs` が対象です。関数名で行を絞ります。

```bash
cd ~/webapp/python
grep -n 'def app_get_nearby_chairs' -A 80 app/app_handlers.py
```

Doing されていることは次の 3 つです。

1. `SELECT * FROM chairs` で椅子を全件読む
2. 椅子ごとに `SELECT * FROM rides WHERE chair_id = ?` を読み、その ride ごとに `get_latest_ride_status` で `ride_statuses` をもう 1 本読む
3. 椅子ごとに `SELECT * FROM chair_locations WHERE chair_id = ? ORDER BY created_at DESC LIMIT 1` を読む

つまりクエリ数は「1 + 配車受け付け中の椅子の数 × (2 + その椅子の走っているリード数)」になります。初期データでも椅子は 500 台、`rides` も 750 件あるので、インデックスの無いままだと 1 回のリクエストが `rides` の全表スキャンを 500 回繰り返します。

## 2. 1 リクエストあたりのクエリ数を測ります

MySQL の `Questions` カウンタは累積値なので、前後の差を取ります。

セッションを作ってから、10 回叩きます。

```bash
cd /tmp
curl -sS -c /tmp/app.jar -X POST https://isuride.xiv.isucon.net/api/app/users \
  -H 'content-type: application/json' \
  -d '{"username":"measure-user","firstname":"Measure","lastname":"User","date_of_birth":"2000-01-01"}'
echo
grep app_session /tmp/app.jar
```

`app_session` の行が出たら、続けて測ります。

```bash
mysql -uisucon -pisucon -e "SHOW GLOBAL STATUS LIKE 'Questions';"
for i in $(seq 10); do
  curl -sS -o /dev/null -b /tmp/app.jar \
    'https://isuride.xiv.isucon.net/api/app/nearby-chairs?latitude=0&longitude=0&distance=50'
done
mysql -uisucon -pisucon -e "SHOW GLOBAL STATUS LIKE 'Questions';"
```

成功の見え方: 2 つの `Questions` の差が 10 回分のクエリ数になります。今のコードなら 1 リクエストあたり数十から数百のはずです。差を 10 で割ると「1 リクエストあたりのクエリ数」になります。

?> 初期状態では 500 台の椅子の `is_active` がすべて `0` なので、`if not chair.is_active: continue` でループが skips され、クエリは `SELECT * FROM chairs` の 1 本だけです。ベンチを 1 本回ると椅子が配車受け付け状態になるので、必ず 1 本回してから測ってください。

## 3. 1 手目、走る椅子を 1 クエリでまとめます

`SELECT * FROM chairs` で椅子を全件読み、椅子ごとに ride と状態を読み分けていた部分を、`is_active = TRUE` の 1 クエリに置き換えます。走っている椅子（最新状態が `COMPLETED` でない）がどれかを 1 本のクエリで先に確定します。

対象の関数を変える前にバックアップを取ります。

```bash
cd ~/webapp/python
cp -a app/app_handlers.py app/app_handlers.py.orig
```

`app_get_nearby_chairs` の本体を、次の実装に置き換えます（`vi app/app_handlers.py` で関数の範囲を置き換えます）。2 点だけ注意してください。

- `ORDER BY chairs.id` を付けます。参照実装は主キー順に返すので、順序を揃えます
- `retrieved_at` は元の実装と同じく `CURRENT_TIMESTAMP(6)` を使います

```python
@router.get("/nearby-chairs", status_code=HTTPStatus.OK)
def app_get_nearby_chairs(
    _user: Annotated[User, Depends(app_auth_middleware)],
    latitude: int,
    longitude: int,
    distance: int = 50,
) -> AppGetNearByChairsResponse:
    with engine.begin() as conn:
        # 最新状態が COMPLETED でない ride を持つ椅子を 1 クエリで集める
        busy_chair_ids = set(
            conn.execute(
                text(
                    """
                    SELECT DISTINCT rides.chair_id
                    FROM rides
                    INNER JOIN (
                        SELECT ride_id,
                               SUBSTRING_INDEX(GROUP_CONCAT(status ORDER BY created_at DESC), ',', 1) AS latest_status
                        FROM ride_statuses
                        GROUP BY ride_id
                    ) AS latest ON latest.ride_id = rides.id
                    WHERE rides.chair_id IS NOT NULL
                      AND latest.latest_status <> 'COMPLETED'
                    """
                )
            ).scalars()
        )

        rows = conn.execute(
            text(
                """
                SELECT chairs.id, chairs.name, chairs.model,
                       chair_locations.latitude, chair_locations.longitude
                FROM chairs
                INNER JOIN chair_locations ON chair_locations.id = (
                    SELECT id FROM chair_locations
                    WHERE chair_id = chairs.id
                    ORDER BY created_at DESC LIMIT 1
                )
                WHERE chairs.is_active = TRUE
                ORDER BY chairs.id
                """
            )
        ).fetchall()

        near_by_chairs = []
        for row in rows:
            if row.id in busy_chair_ids:
                continue
            if (
                calculate_distance(latitude, longitude, row.latitude, row.longitude)
                <= distance
            ):
                near_by_chairs.append(
                    AppGetNearbyChairsResponseChair(
                        id=row.id,
                        name=row.name,
                        model=row.model,
                        current_coordinate=Coordinate(
                            latitude=row.latitude, longitude=row.longitude
                        ),
                    )
                )
        retrieved_at = conn.execute(text("SELECT CURRENT_TIMESTAMP(6)")).scalar()
        assert retrieved_at is not None

    return AppGetNearByChairsResponse(
        chairs=near_by_chairs,
        retrieved_at=timestamp_millis(retrieved_at),
    )
```

文末の `row` は SQLAlchemy の `Row` なので、`row.id` のようにキーで引けます。

反映して測り直します。

```bash
python3 -c "import ast;ast.parse(open('app/app_handlers.py').read());print('syntax ok')"
sudo systemctl restart isuride-python.service
sudo journalctl -u isuride-python -n 20 --no-pager
```

成功の見え方: `syntax ok` が出て、`journalctl` に例外がありません。

クエリ数を測り直します。

```bash
mysql -uisucon -pisucon -e "SHOW GLOBAL STATUS LIKE 'Questions';"
for i in $(seq 10); do
  curl -sS -o /dev/null -b /tmp/app.jar \
    'https://isuride.xiv.isucon.net/api/app/nearby-chairs?latitude=0&longitude=0&distance=50'
done
mysql -uisucon -pisucon -e "SHOW GLOBAL STATUS LIKE 'Questions';"
```

成功の見え方: 差が 2〜3 本 × 10 に落ちます（認証 1 本 + busy 判定 1 本 + 椅子取得 1 本 + 時刻 1 本）。

レスポンスの中身が変わっていないことも確認します。

```bash
curl -sS -b /tmp/app.jar \
  'https://isuride.xiv.isucon.net/api/app/nearby-chairs?latitude=0&longitude=0&distance=50' | head -c 400
echo
```

`chairs` が配列で、`retrieved_at` が Unix ミリ秒で出ます。

## 4. 2 手目、位置の取得もまとめます

手順 3 で入れたクエリは `chair_locations` の相関サブクエリです。`chair_locations` は `POST /api/chair/coordinate` のたびに 1 行増えるテーブルなので、`idx_chair_locations_chair_created`（[0060-sql.md](0060-sql.md) の手順 8）が入っていればインデックス参照になります。入っているか `EXPLAIN` で確認します。

```bash
mysql -uisucon -pisucon isuride -e "
  EXPLAIN SELECT chairs.id FROM chairs
  INNER JOIN chair_locations ON chair_locations.id = (
    SELECT id FROM chair_locations WHERE chair_id = chairs.id
    ORDER BY created_at DESC LIMIT 1)
  WHERE chairs.is_active = TRUE\G"
```

`key` が `idx_chair_locations_chair_created` を指していることを確認します。`type` が `ALL` のままなら 0060 のインデックスが読み込まれていません（`POST /api/initialize` を 1 回呼んでから測り直します）。

## 5. ベンチを 1 本回します

```bash
# ベンチ用インスタンスで
source ~/bench.env
cd ~
./bench run --target https://isuride.xiv.isucon.net \
  --addr ${APP_IP}:443 --payment-url http://${BENCH_IP}:12345 -t 60
```

成功の見え方: `msg=結果 pass=true` が出たら、スコアを `~/bench-notes/scores.txt` に 1 行足します。

?> ベンチの整合性チェックは `nearby-chairs` の中身も検証します（椅子の名前・モデル・座標・現在の状態）。座標が古いと `ErrorCodeTooOldNearbyChairsResponse` のソフトエラーになります。`pass=false` になったら、直したクエリが最新位置を返しているかを確認します。

## 6. 戻すには

```bash
cd ~/webapp/python
cp -a app/app_handlers.py.orig app/app_handlers.py
sudo systemctl restart isuride-python.service
```

## 出口

クエリ数が 1 リクエストあたり定数になったところで [0080-notification.md](0080-notification.md) へ進みます。次は通知エンドポイントを同じように直します。
