# 0190 分割前提のアプリ

[0180](0180-split-sql.md) で **DB は全部 1 ms 以下**になりました。それでも `chair/notification` は **0.117〜0.137 s** かかります。この章は**その差**を削ります。

```text
DB が返すまで     0.39 ms
アプリが返すまで  117 ms
```

**差の 99% はアプリ側**（往復の回数・Python の処理・worker の順番待ち）です。

## 1. 通知のたびに `FOR SHARE` を打っていました

`chair_get_notification` は、応答に**ユーザーの表示名**を入れるために 1 行読みます。AMI のコードはこうでした。

```python
row = conn.execute(
    text("SELECT * FROM users WHERE id = :id FOR SHARE"), {"id": ride.user_id}
).fetchone()
user = User.model_validate(row)
```

**`FOR SHARE` は共有ロック**です。読み取り専用の通知なのに、**通知のたびにユーザーの行をロック**していました。窓で **43,462 回**です。

?> **`FOR SHARE` が要るのは「読んだ内容が変わる前に、他の書き込みを止めたい」とき**です。通知は表示名を読むだけで、そのあと何も書きません。**ロックは不要**です。

## 2. 表示名をプロセス内に持ちます

**ユーザーの名前は実行中に変わりません**（変わるのは `POST /api/app/users` のときだけで、それは新しい行です）。

```python
# app/chair_handlers.py（モジュールレベル）
# 0190: 椅子の通知で使うユーザーの表示名（実行中は変わらない）
_chair_notification_users: dict[str, SimpleUser] = {}
```

```python
# app/chair_handlers.py の chair_get_notification
        # 0190: 通知のたびに FOR SHARE（共有ロック）で 1 行読むのをやめる
        user = _chair_notification_users.get(ride.user_id)
        if user is None:
            row = conn.execute(
                text("SELECT id, firstname, lastname FROM users WHERE id = :id"),
                {"id": ride.user_id},
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR)
            user = SimpleUser(id=row.id, name=f"{row.firstname} {row.lastname}")
            _chair_notification_users[ride.user_id] = user
```

応答も、作った `SimpleUser` をそのまま使います（`user=user,`）。

### 実測（2026-10-20）

| | 変更前 | 変更後 |
| --- | --- | --- |
| `chair/notification` AVG | 0.130 s | **0.117 s** |
| `chair/notification` P99 | 0.533 s | **0.528 s** |
| `SELECT * FROM users WHERE id = ? FOR SHARE` | 43,462 回 / 窓 | **消えた** |
| スコア | 21025 / 18961 | 16722 / 16349 |

?> スコアは帯の中です。**効いたのはロックが消えたこと**です。`FOR SHARE` は**同じユーザーの書き込み（評価など）を待たせる**ので、負荷が上がるほど**詰まりの原因**になります。

## 3. やらかしたこと: デコレータと `def` の間に書いてアプリを落としました

最初、モジュールレベルの辞書を `def chair_get_notification(` の**直前**に入れました。ところが実際のファイルはこうでした。

```python
@router.get("/notification", response_model_exclude_none=True)
# 0190: ...                       ← ここに入ってしまった
_chair_notification_users: dict[str, SimpleUser] = {}


def chair_get_notification(
```

**デコレータと `def` の間に文が入った**ので、`SyntaxError` でアプリが起動しません。`systemctl is-active` は **`activating`**、`curl` は **`000`**（接続できない）でした。

```bash
# 気づき方
systemctl is-active isuride-python        # activating のまま
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/app/rides   # 000
cd ~/webapp/python
sudo -u isucon .venv/bin/python -c "import app.chair_handlers; print('ok')"     # SyntaxError
```

?> **`python -c "import ..."` を 1 回打つのが最短です。**ファイルを直したら、**`ast.parse` だけでなく import まで**通します（import は依存や実行時のエラーも見つけます）。

?> 2 台のうち 1 台だけ直したときは、**もう 1 台も同じ状態か**を必ず確かめます。片方が落ちていると、nginx がそちらに振ったぶんだけ 500 になり、**スコアが半分近くまで落ちます**（実測: 12974）。

## 4. ここから先（0190 の残り）

- **アプリの CPU が上限**です（2 vCPU × 2 台）。 alp の AVG 0.117 s に対して DB は 1 ms なので、残りは**順番待ち**（`6 worker` に 8 リクエストが並ぶ）
- **app-3（DB 専用）には CPU の余裕があります**。ここに**worker 2 つだけ**アプリを置く案は残っています（DB の CPU を食わない範囲で）。本教材では測っていません
- **往復を減らす**余地もあります（`chair/coordinate` の `chair_locations` の読み直しなど）。**削る前に alp で「どのエンドポイントか」を確かめます**

## 5. 戻すには

```bash
cp app/chair_handlers.py.pre0190a app/chair_handlers.py
sudo systemctl restart isuride-python.service
```

## 出口

次は [0200-timeout.md](0200-timeout.md) です。**決済の呼び出しにタイムアウトが無い**のを直します。
