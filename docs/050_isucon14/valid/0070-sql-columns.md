# 0070 集計をやめて列で持つ

[0060-sql-index.md](0060-sql-index.md) で索引は 10 本になりました。それでも `performance_schema` の時間の 1 位は **`owner/chairs` の窓関数**でした。

| 回数 | 合計 | 1 回 | 読んだ行 | SQL |
| --- | --- | --- | --- | --- |
| 39 | **296.0 s** | **7,590 ms** | 487 | `SELECT id, owner_id, name, ... FROM chairs LEFT JOIN (... LAG ...)` |

**1 回 7.6 秒**です。`chair_locations` 全体に `LAG` をかけて、椅子ごとの移動距離を毎回足し直しています。索引では取れません。**集計をやめて、書き込みのときに足しておく**ようにします。

## 1. `chairs` に列を足します

```bash
# app-1 で。1-index.sql のいちばん後ろに足す
cd ~/webapp/sql
cat >> 1-index.sql <<'SQL'

-- 0070: 椅子ごとの累積移動距離と、最後に報告された座標
ALTER TABLE chairs ADD COLUMN total_distance INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chairs ADD COLUMN last_latitude INTEGER NULL;
ALTER TABLE chairs ADD COLUMN last_longitude INTEGER NULL;
SQL
```

?> **足す位置が大事です。**これを `1-index.sql` の**先頭**に置くと、まだ索引が無い `chair_locations` に窓関数をかけることになり、**`initialize` が 24 秒**かかりました。索引を張った**後**に置いたら **4.0 秒**です（実測）。

## 2. 初期データぶんを 1 回だけ積みます

**AMI の初期データには `chair_locations` が 21,209 行入っています**（`chairs` は 500 行）。列を足しただけでは初期値が 0 のままなので、`initialize` のときに 1 回だけ積みます。

```bash
cat >> 1-index.sql <<'SQL'

-- 0070: 初期データぶんを 1 回だけ積む（以降は chair_post_coordinate が足していく）
UPDATE chairs c
LEFT JOIN (
    SELECT chair_id, SUM(IFNULL(distance, 0)) AS d
    FROM (
        SELECT chair_id,
               ABS(latitude - LAG(latitude) OVER (PARTITION BY chair_id ORDER BY created_at)) +
               ABS(longitude - LAG(longitude) OVER (PARTITION BY chair_id ORDER BY created_at)) AS distance
        FROM chair_locations
    ) t
    GROUP BY chair_id
) s ON s.chair_id = c.id
SET c.total_distance = IFNULL(s.d, 0),
    c.last_latitude  = (SELECT cl.latitude  FROM chair_locations cl
                         WHERE cl.chair_id = c.id ORDER BY cl.created_at DESC LIMIT 1),
    c.last_longitude = (SELECT cl.longitude FROM chair_locations cl
                         WHERE cl.chair_id = c.id ORDER BY cl.created_at DESC LIMIT 1);
SQL
```

**値が合っているかを確かめます。**列の値と、窓関数で出した値を突き合わせます。

```bash
mysql -uisucon -pisucon isuride -e "
SELECT COUNT(*) AS mismatches FROM (
  SELECT c.id, c.total_distance AS col_value,
         (SELECT SUM(IFNULL(distance,0)) FROM (
            SELECT chair_id, created_at,
                   ABS(latitude - LAG(latitude) OVER (PARTITION BY chair_id ORDER BY created_at)) +
                   ABS(longitude - LAG(longitude) OVER (PARTITION BY chair_id ORDER BY created_at)) AS distance
            FROM chair_locations WHERE chair_id = c.id) x) AS win_value
  FROM chairs c) t
WHERE IFNULL(col_value,0) <> IFNULL(win_value,0);"
```

成功の見え方: `0` です。実測でも `0` で、合計も **`68,668 = 68,668`** で一致しました。`initialize` は **4.0 秒**（窓関数の 1 回ぶんと同じくらいです）。

## 3. 座標が届いたら積みます

`chair_post_coordinate` の `INSERT` のすぐあとに 1 文足します。

```python
# app/chair_handlers.py
        # 累積移動距離と最後の座標を 1 文で更新する。
        # 前の座標を別の SELECT で読むと、同じ椅子に 2 つ同時に届いたときに差分を取りこぼす。
        conn.execute(
            text(
                "UPDATE chairs SET total_distance = total_distance + IFNULL(ABS(:latitude - last_latitude) + ABS(:longitude - last_longitude), 0), last_latitude = :latitude, last_longitude = :longitude WHERE id = :id"
            ),
            {"latitude": req.latitude, "longitude": req.longitude, "id": chair.id},
        )
```

?> **1 文で書くのが大事です。**前の座標を別の `SELECT` で読んでから `UPDATE` すると、**同じ椅子に 2 つの座標が同時に届いたときに差分を取りこぼします**（3 台構成で測ったときに `種別エラー数=map[26:6]` が出ました）。1 文なら `chairs` の行ロックで直列化されます。

## 4. `owner/chairs` を列から引きます

```python
# app/owner_handlers.py の owner_get_chairs の SQL
                SELECT id, owner_id, name, access_token, model, is_active, created_at, updated_at,
                       total_distance,
                       (SELECT MAX(cl.created_at)
                        FROM chair_locations cl
                        WHERE cl.chair_id = chairs.id) AS total_distance_updated_at
                FROM chairs
                WHERE owner_id = :owner_id
```

`total_distance_updated_at` は `MAX(created_at)` のままです。`idx_chair_locations_chair_created` で 1 行引くだけなので、相関サブクエリでも軽いです。

## 5. 実測（単一インスタンス、2026-10-18）

- `initialize` の所要時間: 3.7 s → **4.0 s**（backfill が 1 回ぶん増えただけ）
- 値の一致: **不一致 0 件**（全 500 台）
- **窓関数の実行回数: `performance_schema` で 39 回のまま増えていない**（この手を入れたあと 7 本ベンチを回しても増えていません＝もう呼ばれていない）。合計 296 秒が消えました
- `CODE=25`（オーナーの椅子一覧のタイムアウト）は**出なくなりました**

| 構成 | 1 本目 | 2 本目 | 3 本目 | 4 本目 | 5 本目 | いちばん悪い回 |
| --- | --- | --- | --- | --- | --- | --- |
| 0060 の後（10 本の索引） | 1901 | 1697 | 1591 | 1115 | — | 1115 |
| 0070（この手） | 1212 | 1551 | 1284 | 1568 | 1638 | 1212 |

?> **スコアは「同じくらい」です。**いちばん悪い回は 1115 → 1212 とわずかに上、平均はほぼ同じです。この環境のスコアは ±40% ぶれるので、**1 手ぶんの差は埋もれます**。それでも、**DB がやっていた仕事（1 回 7.6 秒 × 39 回）が消えて、`CODE=25` も消えた**ので採用します。判断は `performance_schema` の実行回数で取りました。

## 6. 戻すには

`1-index.sql` から `-- 0070` の 3 つの `ALTER` と `UPDATE` を消し、`owner_handlers.py` / `chair_handlers.py` を `*.pre0070` に戻します。

## 出口

この列は [0080-sql-n1.md](0080-sql-n1.md) でも使います（**椅子の最新座標を `chair_locations` に引きに行かなくて済む**）。
