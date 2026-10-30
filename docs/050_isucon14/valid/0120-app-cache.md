# 0120 キャッシュと「列で持つ」で通知を軽くする

[0090-app-notification.md](0090-app-notification.md) で通知の往復と `COMMIT` を減らしました。この章は**その上に載っているクエリ**を消します。2 つあります。

| 回数 | 合計 | 平均 | SQL |
| --- | --- | --- | --- |
| 11,004 | **48.7 s** | 4.42 ms | `SELECT * FROM chairs WHERE access_token = ?` |
| 5,082 | **89.4 s** | 17.6 ms | 椅子の成績の集計（`COUNT` + `AVG`） |

**どちらも「毎回同じ答えを引き直している」**のが問題です。1 つめは**全リクエストの前に 1 回**必ず走ります（`app/middlewares.py` の認証）。2 つめは**通知のたびに**走ります。

## 1. 認証の引き当てをプロセス内に持つ

`app/middlewares.py` の 3 つの認証を、**モジュールレベルの辞書**でキャッシュします。

```python
# app/middlewares.py
_app_sessions: dict[str, User] = {}
_owner_sessions: dict[str, Owner] = {}
_chair_sessions: dict[str, Chair] = {}


def chair_auth_middleware(
    chair_session: Annotated[str | None, Cookie()] = None,
) -> Chair:
    if not chair_session:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail="chair_session cookie is required",
        )

    cached = _chair_sessions.get(chair_session)
    if cached is not None:
        return cached

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM chairs WHERE access_token = :access_token"),
            {"access_token": chair_session},
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED, detail="invalid access token"
        )
    chair = Chair.model_validate(row)
    _chair_sessions[chair_session] = chair
    return chair
```

`app` / `owner` も同じ形にします。**`begin()` ではなく `connect()`** にしておくと、キャッシュに当たったときに接続すら借りません。

?> **なぜ壊れないか。**
>
> - `access_token` は `secure_random_str` で作られ、**あとから変わりません**。同じトークンなら同じ行です
> - ベンチの `initialize` で DB は作り直されますが、**古いトークンは二度と来ません**。残ったキャッシュは使われないだけです
> - ハンドラが使うのは **`id` だけ**です（`chair_post_activity` も `chair.id` しか見ません）。他の列が古くても結果は変わりません
> - 認証をキャッシュにすると、**「認証で弾かれるはずのリクエスト」も通る**余地があります。トークンは乱数なので、総当たりは起きません
> - キャッシュは **gunicorn の worker ごと**です（プロセスをまたがない）。worker を増やすと 1 台あたりのメモリはそのぶん増えますが、行数は「そのプロセスが触ったトークンの数」だけです

## 2. 椅子の成績を列で持つ

`app_get_notification` は、ライドに椅子が付いているとき `get_chair_stats` を呼びます。AMI は**毎回ライドを集計**していました（[0080](0080-sql-n1.md) で 1 クエリにはしましたが、それでも **17.6 ms × 5,082 回 = 89.4 秒**です）。

[0070](0070-sql-columns.md) と同じで、**集計をやめて列で持ちます。**

```sql
-- webapp/sql/1-index.sql の末尾
-- 0120: 椅子の成績（完了数と評価の合計）を列で持つ
ALTER TABLE chairs ADD COLUMN completed_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chairs ADD COLUMN evaluation_sum INTEGER NOT NULL DEFAULT 0;

UPDATE chairs c
LEFT JOIN (
    SELECT rides.chair_id, COUNT(*) AS cnt, SUM(rides.evaluation) AS evalsum
    FROM rides
    WHERE rides.chair_id IS NOT NULL
      AND rides.evaluation IS NOT NULL
      AND (SELECT COUNT(*) FROM ride_statuses
           WHERE ride_statuses.ride_id = rides.id
             AND ride_statuses.status IN ('ARRIVED', 'CARRYING', 'COMPLETED')) = 3
    GROUP BY rides.chair_id
) s ON s.chair_id = c.id
SET c.completed_count = IFNULL(s.cnt, 0),
    c.evaluation_sum = IFNULL(s.evalsum, 0);
```

```python
# app/app_handlers.py の get_chair_stats を差し替え
def get_chair_stats(conn, chair_id):
    # 成績は chairs の列に持たせてあるので、集計せずに読むだけ
    row = conn.execute(
        text("SELECT completed_count, evaluation_sum FROM chairs WHERE id = :chair_id"),
        {"chair_id": chair_id},
    ).fetchone()
    count = int(row.completed_count)
    return AppGetNotificationResponseChairStats(
        total_rides_count=count,
        total_evaluation_avg=(float(row.evaluation_sum) / count) if count > 0 else 0.0,
    )
```

加算は**評価を書くのと同じトランザクション**で行います。

```python
# app/app_handlers.py の app_post_ride_evaluation（COMPLETED を入れたすぐあと）
        # 0120: 椅子の成績を列で持つ（集計しない）。
        # 評価が入るのと COMPLETED が入るのは同じトランザクションなので、ここで数えれば一致する
        conn.execute(
            text(
                "UPDATE chairs SET completed_count = completed_count + 1, evaluation_sum = evaluation_sum + :evaluation WHERE id = :chair_id"
            ),
            {"evaluation": req.evaluation, "chair_id": ride.chair_id},
        )
```

?> **なぜ一致するか。**集計の条件は「`ARRIVED` / `CARRYING` / `COMPLETED` の 3 つがそろったライド」でした。`COMPLETED` が入るのは**評価を送ったときだけ**で、そのときには `ARRIVED` と `CARRYING` がすでに入っています。だから**評価を書くときに数えれば、あとから数えたのと同じ**です。

?> `total_evaluation_avg` は `SUM / COUNT` で出します。MySQL の `AVG` も同じ割り算なので、**同じ値になります**。`COUNT = 0` のときは AMI の `IFNULL(AVG(...), 0)` と同じ `0.0` を返します。

**値が合っているかを確かめます**（生の DB にも列を入れてから、突き合わせます）。

```sql
SELECT
 (SELECT COUNT(*) FROM chairs c WHERE c.completed_count <> (SELECT COUNT(*) FROM rides r
    WHERE r.chair_id = c.id AND r.evaluation IS NOT NULL
      AND (SELECT COUNT(*) FROM ride_statuses rs WHERE rs.ride_id = r.id
           AND rs.status IN ('ARRIVED','CARRYING','COMPLETED')) = 3)) AS count_mismatch,
 (SELECT COUNT(*) FROM chairs c WHERE c.evaluation_sum <> IFNULL((SELECT SUM(r.evaluation) FROM rides r
    WHERE r.chair_id = c.id AND r.evaluation IS NOT NULL
      AND (SELECT COUNT(*) FROM ride_statuses rs WHERE rs.ride_id = r.id
           AND rs.status IN ('ARRIVED','CARRYING','COMPLETED')) = 3), 0)) AS sum_mismatch;
```

成功の見え方: 実測で `count_mismatch = 0` / `sum_mismatch = 0`（評価済み **829 ライド**）でした。

?> **`initialize` のたびに列は作り直されます。**`1-index.sql` は `0-SCHEMA.sql` のあとに走るので、`ALTER TABLE ... ADD COLUMN` が二重になることはありません。ただし、**アプリを先に再起動すると列が無くて落ちる**ので、生の DB にも同じ `ALTER` を流してから再起動します。

## 3. 実測（単一インスタンス、2026-10-18）

| 構成 | 1 本目 | 2 本目 | 3 本目 | 4 本目 | いちばん悪い回 |
| --- | --- | --- | --- | --- | --- |
| 0090 の後（この手の前） | 4549 | 3924 | 4045（計測窓） | — | 3924 |
| 0120 認証キャッシュ | **5571** | **5617** | — | — | 5571 |
| 0120 椅子の成績を列で | **5674** | **5191** | 5265（計測窓） | — | 5191 |

**いちばん悪い回が 3924 → 5191（+32%）**です。

### 構造の証拠（alp / slp / digest）

| | 0090 の窓 | 0120 の窓 | |
| --- | --- | --- | --- |
| `COMMIT` | 23,786 回 / 312.5 s | **6,996 回 / 306.1 s** | **−71%** |
| `chairs WHERE access_token` | 11,004 回 / 48.7 s | **上位 10 から消えた** | — |
| `users WHERE access_token` | （上位 10 の下） | **上位 10 から消えた** | — |
| 椅子の成績の集計 | 5,082 回 / 89.4 s | **消えた** | — |
| `chair/notification` AVG / P99 | 0.209 / 0.726 s | **0.166 / 0.528 s** | **−20% / −27%** |
| `app/notification` AVG / P99 | 0.275 / 0.789 s | **0.214 / 0.605 s** | **−22% / −23%** |
| `chair/coordinate` AVG / P99 | 0.343 / 0.856 s | **0.276 / 0.678 s** | **−20% / −21%** |
| slp（`long_query_time=0.1`） | 0 件 | 0 件 | — |

?> **`COMMIT` の 1 回あたりは 13.14 ms → 43.76 ms に増えました。**回数を 71% 減らしたのに、**合計は 312.5 → 306.1 秒であまり減っていません**。これは「`COMMIT` の時間が**回数ではなく 1 回あたりの fsync 待ち**で決まっている」という証拠です。**次は回数ではなく 1 回を削ります**（[0140-mysql-write.md](0140-mysql-write.md)）。

## 4. 戻すには

```bash
cp app/middlewares.py.pre0120a app/middlewares.py
cp app/app_handlers.py.pre0120b app/app_handlers.py
# 1-index.sql の末尾（-- 0120 のブロック）を消す
sudo systemctl restart isuride-python.service
```

## 出口

次は **0140（MySQL の書き込み設定）**です。`COMMIT` が 1 回 43.76 ms かかっているので、**fsync をやめます**。
