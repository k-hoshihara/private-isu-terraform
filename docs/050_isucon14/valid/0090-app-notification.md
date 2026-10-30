# 0090 通知を軽くする

alp の上位 2 本は通知でした（`pass=true 5097` の窓）。

| COUNT | URI | AVG | P99 | SUM |
| --- | --- | --- | --- | --- |
| 7,363 | `/api/chair/notification` | 0.224 | 0.879 | 1,651.4 |
| 5,229 | `/api/app/notification` | 0.294 | 0.951 | 1,537.5 |
| 3,492 | `/api/chair/coordinate` | 0.333 | 1.021 | 1,164.3 |

**上位 3 本で件数の 88%・合計時間の 92%**です。通知は**椅子とアプリが 0.5 秒ごとに叩く**ので、ここが重いと全体が重くなります。

digest を見ると、通知の中身はこうでした。

| 回数 | 合計 | SQL |
| --- | --- | --- |
| 14,068 | 177.2 s | `SELECT status FROM ride_statuses WHERE ride_id = ? ORDER BY created_at DESC LIMIT 1` |
| 5,229 | 66.4 s | `... AND app_sent_at IS NULL` |
| 6,086 | 72.9 s | `... AND chair_sent_at IS NULL` |

**「未送信の状態を探す」→「見つからなければもう一度、最新の状態を引く」の 2 往復**になっています。未送信が無いとき（＝たいていのとき）に、**同じ行をもう一度引き直しています**。

## 1. `UNION ALL` で 1 往復にします

```python
# app/app_handlers.py の app_get_notification
        # 「まだアプリに送っていない状態」と「最新の状態」を 1 往復で取る
        # （未送信が無いときに get_latest_ride_status をもう 1 回叩かないため）
        rows = conn.execute(
            text(
                "(SELECT 0 AS kind, ride_statuses.* FROM ride_statuses"
                " WHERE ride_id = :ride_id AND app_sent_at IS NULL ORDER BY created_at ASC LIMIT 1)"
                " UNION ALL "
                "(SELECT 1 AS kind, ride_statuses.* FROM ride_statuses"
                " WHERE ride_id = :ride_id ORDER BY created_at DESC LIMIT 1)"
            ),
            {"ride_id": ride.id},
        ).fetchall()
        yet_sent_ride_status: RideStatus | None = None
        unsent = [r for r in rows if r.kind == 0]
        if unsent:
            yet_sent_ride_status = RideStatus.model_validate(unsent[0])
            status = yet_sent_ride_status.status
        else:
            status = [r for r in rows if r.kind == 1][0].status
```

`kind` で「どちらの枝から来た行か」を見分けます。`chair_handlers.py` の `chair_get_notification` も同じ形にします（`app_sent_at` → `chair_sent_at`）。

?> **1 本の SQL に押し込むより、`UNION ALL` で 2 枝を並べるほうが読みやすいです。**オプティマイザにも「2 つの `LIMIT 1`」がそのまま伝わります。枝ごとに `LIMIT 1` が付いているので、返る行は最大 2 行です。

## 2. 読み取りは `connect()`、書き込みのときだけ `commit()` します

AMI の通知ハンドラは `with engine.begin() as conn:` で始まります。**`engine.begin()` は何も書かなくても必ず `COMMIT` を打ちます**。`performance_schema` でも `COMMIT` が **35,565 回・352.7 秒で SQL の 1 位**でした。

```python
# app/app_handlers.py / chair_handlers.py の通知ハンドラ
def app_get_notification(...):
    with engine.connect() as conn:          # begin → connect に変える
        ...
        if yet_sent_ride_status is not None:
            ...
            conn.execute(
                text("UPDATE ride_statuses SET app_sent_at = CURRENT_TIMESTAMP(6) WHERE id = :id"),
                {"id": yet_sent_ride_status.id},
            )
            conn.commit()                   # 書いたときだけ明示的に COMMIT
```

?> **`connect()` にすると、読み取りだけのリクエストは `ROLLBACK` で終わります。**`ROLLBACK` は `COMMIT` よりずっと安いです（実測: **`COMMIT` 13.14 ms / `ROLLBACK` 1.31 ms**）。この手のあと、digest には `ROLLBACK` が **35,927 回・47.0 秒**で出るようになりますが、これは**正常**です（`COMMIT` が 352.7 秒 → 312.5 秒に減ったほうが大きい）。

?> トランザクションを分けると**見えるものが変わる**ことに注意してください。読み取りと書き込みが別トランザクションになるので、**同じリクエストの中で読んだ行を書く**ときは、読んだ内容が変わっていないかを気にする必要があります。通知は「未送信の行を 1 回だけ更新する」だけなので、ここは問題になりません。

## 3. 実測（単一インスタンス、2026-10-18）

| 構成 | 1 本目 | 2 本目 | 3 本目 | いちばん悪い回 |
| --- | --- | --- | --- | --- |
| 0110 の後（この手の前） | 3934 | 4173 | **5097** | 3934 |
| **0090（この手）** | **4549** | **3924** | — | 3924 |
| 0090（alp/slp の計測窓） | **4045** | — | — | 4045 |

**スコアは同じ帯**です（この環境のぶれが ±40% あるので、1 手ぶんの差は埋もれます）。**証拠は構造のほうで取ります**。同じくらいの負荷（18,152 → 18,107 リクエスト）の 2 つの窓を並べます。

| | 0110 の窓 | 0090 の窓 | |
| --- | --- | --- | --- |
| `COMMIT` | 35,565 回 / 352.7 s | **23,786 回 / 312.5 s** | **−33%** |
| `get_latest_ride_status` | 14,068 回 / 177.2 s | **3,734 回 / 60.2 s** | **−73%** |
| `Questions` ÷ リクエスト | 10.0 | **8.8** | **−12%** |
| `ROLLBACK` | — | 35,927 回 / 47.0 s | 読み取りの分（1 回 1.31 ms） |
| `chair/notification` P99 | 0.879 s | **0.726 s** | **−17%** |
| `app/notification` P99 | 0.951 s | **0.789 s** | **−17%** |
| `chair/coordinate` P99 | 1.021 s | **0.856 s** | **−16%** |

?> **alp の AVG はほとんど動かず、P99 が 17% 下がりました。**「1 往復と 1 COMMIT を消す」手は、平均より**裾**に出ます。**alp を見るときは `-o count,uri,avg,p99,sum` のように P99 も並べて**ください。

## 4. 戻すには

```bash
cp app/app_handlers.py.pre0090 app/app_handlers.py
cp app/chair_handlers.py.pre0090 app/chair_handlers.py
sudo systemctl restart isuride-python.service
```

## 出口

通知はこれで打ち止めではありません。**`get_chair_stats` が 1 回 17.6 ms × 5,082 回**（通知のたびに毎回呼ばれています）残っています。これは **0120（キャッシュ）**で、**「同じ椅子の成績はそう変わらない」**ことを使って間引きます。
