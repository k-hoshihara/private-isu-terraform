# 0100 マッチングを速くする（スコアの本体）

ISUCon 14 のスコアは「完了したライドの数」と「移動距離」で決まります。完了数を増やす入口はマッチングの速さです。`GET /api/internal/matching` の周期と中身を変えます。[0090-payment.md](0090-payment.md) の続きです。出口は [0110-result.md](0110-result.md) です。

## スコアの式を先に確かめます

公式 `docs/manual.md` の式です。

```text
スコア = 椅子がライドとマッチした位置から乗車位置までの移動距離の合計 * 0.1
       + 椅子の乗車位置から目的地までの移動距離の合計
       + ライド完了数 * 5
```

- 「マッチした位置から乗車位置までの距離」には 0.1 が掛かる → 1 単位あたり 0.1 点
- 「乗車位置から目的地までの距離」には 1 が掛かる → 1 単位あたり 1 点
- 「完了数」には 5 が掛かる → 1 件あたり 5 点

`ライド完了数 * 5` を上げるには、ライドを `COMPLETED` まで到達させる速度を速くする必要があります。マッチングが遅いと、ライドが `COMPLETED` に到達する件数が増えません。

## 1. 現状を把握します

マッチングは systemd サービスが回します。`isuride-matcher.service` は `while true` ループで `GET /api/internal/matching` を叩き、`ISUCON_MATCHING_INTERVAL` 秒 待って繰り返します（`env.sh` の既定は `0.5`）。

```bash
cat /home/isucon/env.sh
systemctl cat isuride-matcher.service | grep ExecStart
sudo systemctl status isuride-matcher --no-pager | head -n 8
```

成功の見え方: `ExecStart` に `/api/internal/matching` と `sleep $ISUCON_MATCHING_INTERVAL` が見えます。

1 回のマッチングは「未割り当てのリード 1 件 → 空いている椅子 1 台 → `rides.chair_id` を更新する」だけです。1 回につき 1 件しか処理しないので、`0.5` 秒間隔だと 1 秒あたり 2 件しかマッチできません。

## 2. 1 手目、周期を 0.5 秒から縮めます

一番手数の少ない調整です。`env.sh` の `ISUCON_MATCHING_INTERVAL` を書き換え、matcher サービスを再起動します。

```bash
sudo cp -a /home/isucon/env.sh /home/isucon/env.sh.orig
sudo sed -i 's/^ISUCON_MATCHING_INTERVAL=0.5$/ISUCON_MATCHING_INTERVAL=0.05/' /home/isucon/env.sh
grep ISUCON_MATCHING_INTERVAL /home/isucon/env.sh
sudo systemctl restart isuride-matcher.service
sudo systemctl status isuride-matcher --no-pager | head -n 8
```

成功の見え方: `grep` が `ISUCON_MATCHING_INTERVAL=0.05` を返し、`isuride-matcher` が `active (running)` になります。

ベンチを 1 本回して、スコアを `~/bench-notes/scores.txt` に 1 行足します。

```bash
# ベンチ用インスタンスで
source ~/bench.env
cd ~
./bench run --target https://isuride.xiv.isucon.net \
  --addr ${APP_IP}:443 --payment-url http://${BENCH_IP}:12345 -t 60
```

?> 周期を縮めても curl のプロセス起動コストがあるので、`0.02` 以下にしても効果は出ません。`0.05` と `0.02` の 2 通りを試して比べます。

## 3. 2 手目、1 リクエストで複数をさばきます

`isuride-matcher` は 500 ms（変更後は 0.05 秒）ごとに 1 回だけ `GET /api/internal/matching` を叩き、1 件だけさばきます。`internal_get_matching` を「1 リクエストで N 件さばく」に変えると、curl の起動コストも減ります。

`app/internal_handlers.py` の `internal_get_matching` を次に置き換えます。

```python
@router.get("/matching", status_code=HTTPStatus.NO_CONTENT)
def internal_get_matching() -> None:
    # 1 リクエストで最大 10 件さばく
    for _ in range(10):
        if not match_one():
            return
```

さばく 1 件を関数に分けます。

```python
def match_one() -> bool:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT * FROM rides WHERE chair_id IS NULL ORDER BY created_at LIMIT 1"
            )
        ).fetchone()
    if row is None:
        return False
    ride = Ride.model_validate(row)

    matched: Chair | None = None
    empty = False
    for _ in range(10):
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT * FROM chairs INNER JOIN (SELECT id FROM chairs WHERE is_active = TRUE ORDER BY RAND() LIMIT 1) AS tmp ON chairs.id = tmp.id LIMIT 1"
                )
            ).fetchone()
        if row is None:
            return False
        matched = Chair.model_validate(row)

        with engine.begin() as conn:
            empty = bool(
                conn.execute(
                    text(
                        "SELECT COUNT(*) = 0 FROM (SELECT COUNT(chair_sent_at) = 6 AS completed FROM ride_statuses WHERE ride_id IN (SELECT id FROM rides WHERE chair_id = :chair_id) GROUP BY ride_id) is_completed WHERE completed = FALSE"
                    ),
                    {"chair_id": matched.id},
                ).scalar()
            )
        if empty:
            break

    if not empty:
        return False

    assert matched is not None
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE rides SET chair_id = :chair_id WHERE id = :id"),
            {"chair_id": matched.id, "id": ride.id},
        )
    return True
```

バックアップを取ってから編集します。

```bash
cd ~/webapp/python
cp -a app/internal_handlers.py app/internal_handlers.py.orig
python3 -c "import ast;ast.parse(open('app/internal_handlers.py').read());print('syntax ok')"
sudo systemctl restart isuride-python.service
sudo journalctl -u isuride-python -n 20 --no-pager
```

成功の見え方: `syntax ok` が出て、`journalctl` に例外がありません。`isuride-matcher` は `isuride-python.service` を `After` で待っているので、アプリ再起動後は matcher も追従します。

ベンチを 1 本回して、スコアを `~/bench-notes/scores.txt` に 1 行足します。

## 4. 3 手目、割り当て方を変えます

スコアの第 1 項は「マッチした位置 → 乗車位置」の距離です。`ORDER BY RAND()` で完全ランダムに椅子を選んでいるので、乗車位置から遠い椅子に当たることもあります。近い椅子を優先すると第 1 項が下がります（0.1 倍なので効果はやや小さいです）。

`match_one` の椅子選択を、乗車位置に近い順に 1 台取る形にします。`chair_locations` の最新座標を使うため、`ride.pickup_latitude` / `ride.pickup_longitude` を条件にします。

```sql
SELECT chairs.* FROM chairs
INNER JOIN chair_locations ON chair_locations.id = (
  SELECT id FROM chair_locations WHERE chair_id = chairs.id
  ORDER BY created_at DESC LIMIT 1)
WHERE chairs.is_active = TRUE
ORDER BY ABS(chair_locations.latitude - :pickup_latitude)
       + ABS(chair_locations.longitude - :pickup_longitude)
LIMIT 1
```

この 1 クエリで「空いている椅子」の候補を 1 台選べます。`empty` の判定（その椅子で 6 回の通知を送りきっていないか）は別のクエリなので、そのまま残します。

```python
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT chairs.id, chairs.owner_id, chairs.name, chairs.model,
                           chairs.is_active, chairs.access_token,
                           chairs.created_at, chairs.updated_at
                    FROM chairs
                    INNER JOIN chair_locations ON chair_locations.id = (
                      SELECT id FROM chair_locations
                      WHERE chair_id = chairs.id
                      ORDER BY created_at DESC LIMIT 1)
                    WHERE chairs.is_active = TRUE
                    ORDER BY ABS(chair_locations.latitude - :pickup_latitude)
                           + ABS(chair_locations.longitude - :pickup_longitude)
                    LIMIT 1
                    """
                ),
                {
                    "pickup_latitude": ride.pickup_latitude,
                    "pickup_longitude": ride.pickup_longitude,
                },
            ).fetchone()
```

反映してベンチを 1 本回します。

```bash
python3 -c "import ast;ast.parse(open('app/internal_handlers.py').read());print('syntax ok')"
sudo systemctl restart isuride-python.service
sudo journalctl -u isuride-python -n 20 --no-pager
```

成功の見え方: `syntax ok` が出て、ベンチの最終行が `msg=結果 pass=true` になります。

?> `ORDER BY` で椅子を並べる処理は、椅子数が増えると重くなります。`chair_locations` のインデックス（[0060-sql.md](0060-sql.md) の手順 8）が入っていないと全表スキャンになります。先にインデックスが入っているか `EXPLAIN` で確認してください。

## 5. 4 手目、マッチングをアプリ内で行う（任意）

`isuride-matcher` はループのたびに curl プロセスを立ち上げます。周期を 0.01 秒程度まで縮めると、curl の起動コストが処理時間を上回ります。

マッチングをアプリ内のバックグラウンドタスクに移すには、`app/main.py` に起動時処理と終了時処理を足します。FastAPI では `lifespan` を使います。

```python
import asyncio
import contextlib
import os

from contextlib import asynccontextmanager

from .internal_handlers import match_one


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(matching_loop())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def matching_loop():
    interval = float(os.getenv("ISUCON_MATCHING_INTERVAL", "0.5"))
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(match_one)
```

`app = FastAPI()` を `app = FastAPI(lifespan=lifespan)` に置き換えます。`match_one` は手順 3 で `app/internal_handlers.py` に移した関数です。`main.py` と同じパッケージなので相対 import で読めます。

ベンチが `GET /api/internal/matching` を直接叩くことは無いので、`isuride-matcher.service` は止めて構いません。

```bash
sudo systemctl disable --now isuride-matcher.service
sudo systemctl restart isuride-python.service
sudo journalctl -u isuride-python -n 20 --no-pager
```

成功の見え方: `journalctl` にエラーがなく、`isuride-python` が `active (running)` 続けます。

## 6. 元に戻すには

```bash
# env.sh を戻す
sudo cp -a /home/isucon/env.sh.orig /home/isucon/env.sh
sudo systemctl restart isuride-matcher.service

# コードを戻す
cd ~/webapp/python
cp -a app/internal_handlers.py.orig app/internal_handlers.py
sudo systemctl restart isuride-python.service
```

## 出口

スコアの増減が読めたら [0110-result.md](0110-result.md) へ進みます。次は結果を記録する形と、手を戻す手順をまとめます。
