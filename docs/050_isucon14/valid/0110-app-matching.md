# 0110 マッチングを直す（いちばん効く手）

ここまでで **607 → 1811** まで来ました。ここからは**アプリ側**です。最初に**いちばん効く手**を打ちます。

`ISUCON_MATCHING_INTERVAL` ごとに叩かれる `GET /api/internal/matching` が、ライドと椅子を結びつけます。**この選び方がスコアに直結します**。スコアは「乗車位置とマッチ位置の距離 × 0.1」と「完了したライド × 5」で決まるので、**遠い椅子を当てると、そのぶんスコアが減ります**。

alp で見ると、この手の前は上位 3 本がこうでした（`internal/matching` は 493 回・合計 32.5 秒）。

| COUNT | URI | AVG | SUM |
| --- | --- | --- | --- |
| 7,363 | `/api/chair/notification` | 0.224 | 1,651.4 |
| 5,229 | `/api/app/notification` | 0.294 | 1,537.5 |
| 3,492 | `/api/chair/coordinate` | 0.333 | 1,164.3 |

## AMI の実装

```python
# app/internal_handlers.py（AMI のまま、抜粋）
def internal_get_matching() -> None:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM rides WHERE chair_id IS NULL ORDER BY created_at LIMIT 1")
        ).fetchone()
    if row is None:
        return
    ride = Ride.model_validate(row)

    matched: Chair | None = None
    empty = False
    for _ in range(10):
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT * FROM chairs INNER JOIN (SELECT id FROM chairs "
                    "WHERE is_active = TRUE ORDER BY RAND() LIMIT 1) AS tmp ON chairs.id = tmp.id"
                )
            ).fetchone()
            ...
            # 「この椅子が今ライド中でないか」を ride_statuses で数える
            ...
```

**悪いところが 3 つあります。**

1. **`ORDER BY RAND()`** で椅子を選んでいる ＝ 乗車位置からの距離を**まったく見ていない**（スコアの 0.1 点/m を捨てている）
2. 1 リクエストで **1 件しかマッチしない**（椅子の抽選を 10 回試すだけ）
3. 「空いているか」を**別クエリ**で数えている

## 1. 近い空き椅子を 1 クエリで選びます

「空いている椅子のうち、乗車位置にいちばん近い 1 台」を**1 つの SQL** で取ります。

```python
# app/internal_handlers.py を書き換え（match_one を切り出す）
def match_one() -> bool:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM rides WHERE chair_id IS NULL ORDER BY created_at LIMIT 1")
        ).fetchone()
        if row is None:
            return False
        ride = Ride.model_validate(row)

        row = conn.execute(
            text(
                """
                SELECT * FROM chairs
                WHERE is_active = TRUE
                  AND last_latitude IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM rides
                    WHERE rides.chair_id = chairs.id
                      AND (SELECT COUNT(chair_sent_at)
                           FROM ride_statuses
                           WHERE ride_id = rides.id) <> 6)
                ORDER BY ABS(last_latitude - :pickup_latitude)
                       + ABS(last_longitude - :pickup_longitude)
                LIMIT 1
                """
            ),
            {
                "pickup_latitude": ride.pickup_latitude,
                "pickup_longitude": ride.pickup_longitude,
            },
        ).fetchone()
        if row is None:
            return False
        chair = Chair.model_validate(row)

        conn.execute(
            text("UPDATE rides SET chair_id = :chair_id WHERE id = :id"),
            {"chair_id": chair.id, "id": ride.id},
        )
    return True
```

**「空いている」の判定がミソです。**`chair_sent_at` が 6 個そろっていないライドがある椅子 ＝ やりかけのライドを持っている椅子です。`NOT EXISTS` で**最初から除外**します。

?> **「候補を取ってから、別のクエリで空きを確かめて、埋まっていたらやり直す」形にしないでください。**3 台構成で試したときは、空き確認を別クエリに分けた版が **3120 → 1637 まで落ちました**（空き確認のたびに往復が増え、マッチングが遅れて乗車が完了しない）。**1 クエリで「空き」と「近さ」を同時に決める**のが正解です。

?> `last_latitude` / `last_longitude` は [0070-sql-columns.md](0070-sql-columns.md) で `chairs` に持たせた列です。**`chair_locations` を引きに行かなくて済みます**（AMI は椅子ごとに最新座標を引いていました）。

## 2. 1 リクエストで 10 件さばきます

```python
# app/internal_handlers.py
@router.get("/matching", status_code=HTTPStatus.NO_CONTENT)
def internal_get_matching() -> None:
    # 1 リクエストで最大 10 件さばく（マッチャは 0.05 秒ごとに叩く）
    for _ in range(10):
        if not match_one():
            return
```

マッチャは **0.05 秒ごと**に叩くようにします（AMI は 0.5 秒）。

```bash
sudo sed -i 's/^ISUCON_MATCHING_INTERVAL=.*/ISUCON_MATCHING_INTERVAL=0.05/' /home/isucon/env.sh
sudo systemctl restart isuride-python.service isuride-matcher.service
```

?> **間隔を詰めるだけでは効きません。**1 リクエスト 1 件のままだと、0.05 秒ごとに叩いてもマッチ数は 20 件/秒が上限です。**間隔とループはセット**で入れます。

## 3. 実測（単一インスタンス、2026-10-18）

| 構成 | 1 本目 | 2 本目 | 3 本目 | いちばん悪い回 |
| --- | --- | --- | --- | --- |
| 0080 の後（この手の前） | 1244 | 1637 | 1505 / **1811** | 1244 |
| **0110（この手）** | **3934** | **4173** | — | 3934 |
| 0110（alp/slp の計測窓） | **5097** | — | — | 5097 |

**約 2.6 倍**です。同じ負荷（18,152 リクエスト）で見ると、**上位 3 本の合計時間は 4,353 秒のまま**ですが、**完了したライドの数が増えています**（ベンチのスコア式は「完了数 × 5」なので、ここが伸びています）。

?> **この手はスコアの式に直接効きます。**「`ORDER BY RAND()` をやめて近い椅子を選ぶ」＝ 1 ライドあたり平均 0.1 点/m × 距離の差がそのままプラスになります。**900 m 遠い椅子を当てると 90 点**を捨てることになります。

## 4. 戻すには

```bash
cp app/internal_handlers.py.pre0110 app/internal_handlers.py
sudo sed -i 's/^ISUCON_MATCHING_INTERVAL=.*/ISUCON_MATCHING_INTERVAL=0.5/' /home/isucon/env.sh
sudo systemctl restart isuride-python.service isuride-matcher.service
```

## 出口

次は [0090-app-notification.md](0090-app-notification.md) で、alp の上位 2 本（通知）を軽くします。
