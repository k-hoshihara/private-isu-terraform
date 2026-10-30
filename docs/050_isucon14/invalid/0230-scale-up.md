# 0230 スケールアップ（スコープ外の検証）

?> **制約: 3 台 + ベンチ 1 台。**この章の app-4 / app-5 の検証は**制約の外**なので、測ったあとで**削除して 3 台に戻しました**。数字は「もし台が増やせたら」の参考として残します。**採用するのは 3 台の範囲の手だけ**です。

?> **この章は競技の範囲外です。**ISUCON 本番は**台数もインスタンスタイプも固定**です。ここは「**もし予算と台数の制約が無かったら**」の検証で、教材の流れ（0010〜0210）には含めません。

[0220](0220-more.md) の最後で、**アプリの CPU（2 vCPU × 2 台）が上限**と分かりました。DB は全クエリ 1 ms 未満で余裕がありました。この章は**その上限を外して**、どこまで伸びるかと、**次のボトルネックがどこに移るか**を見ます。

## 1. アプリを `c5.xlarge` に（2 vCPU → 4 vCPU）

Terraform は**台ごとのスペック**を書けます（`docs/050_isucon14/terraform/terraform.tfvars`）。

```hcl
# 0230: アプリ 2 台だけ 4 vCPU に（DB と nginx 側はそのまま）
instance_types = ["c5.xlarge", "c5.xlarge", "c5.large"]
```

**`terraform apply` の前に `plan` で「置き換え」かどうかを確かめます。**

```bash
terraform plan -no-color | grep -E '^  # |must be replaced|will be updated in-place|Plan:'
# aws_instance.app[0] will be updated in-place
# aws_instance.app[1] will be updated in-place
# Plan: 0 to add, 2 to change, 0 to destroy
```

**`0 to destroy` ならインスタンスは作り直されません**（停止 → 変更 → 起動）。`must be replaced` が出たら**そのまま進めない**でください。

?> **`stop/start` でパブリック IP は変わります。**プライベート IP（`10.0.1.x`）は残ります。ベンチは `--addr` で IP を指定するので、**`bench.env` の `APP_IP` をプライベート IP に**しておくと、以後の再起動で壊れません。

```bash
# ベンチ機で
sudo sed -i "s|^export APP_IP=.*|export APP_IP='10.0.1.240'|" /home/isucon/bench.env
grep APP_IP /home/isucon/bench.env
```

?> **`bench.env` は `export APP_IP='...'` の形**です。`s|^APP_IP=.*|` では当たりません（実測で 1 回やらかしました）。

**worker もコア数に合わせます**（2 vCPU で 4 だったので、4 vCPU なら 8）。

```bash
sudo sed -i 's|^workers = .*|workers = 8|' ~/webapp/python/gunicorn.conf.py
sudo systemctl restart isuride-python.service
```

### 実測

| 構成 | 1 本目 | 2 本目 | 3 本目 | いちばん悪い回 |
| --- | --- | --- | --- | --- |
| 0210 の最終（2 vCPU × 2、workers 4） | 24376 | 24530 | — | 24376 |
| **アプリ 4 vCPU × 2、workers 8** | **27828** | **27171** | **28267** | **27171** |

**+11〜15%**。そして**1 ベンチあたりのリクエストが 59,000 → 126,000（2.1 倍）**になりました。alp の通知は **0.13〜0.15 s → 0.054〜0.066 s** です。

### その代わり、ボトルネックが DB に移りました

同じ窓の digest です。

| 回数 | 1 回 | SQL |
| --- | --- | --- |
| 98,968 | **10.91 ms** | `COMMIT` |
| 152,557 | **6.72 ms** | 通知の状態読み（アプリ側） |
| 123,437 | **8.30 ms** | 通知の状態読み（椅子側） |

**アプリを速くしたら、DB が詰まりました。**負荷が 2 倍になったので、2 vCPU の DB では足りません。

## 2. DB も `c5.xlarge` に

```hcl
instance_types = ["c5.xlarge", "c5.xlarge", "c5.xlarge"]
```

```bash
terraform plan -no-color | grep -E '^  # |must be replaced|Plan:'
# aws_instance.app[2] will be updated in-place
# Plan: 0 to add, 1 to change, 0 to destroy
terraform apply -auto-approve
```

?> **app-3 の `isuride-python` を有効にしないでください。**DB 専用台です（[0170](../valid/0170-mysql-host.md)）。`systemctl restart` を打つと**無効化中のアプリが起動**してしまいます（実測で 1 回やりました）。確認は `systemctl is-active isuride-python` が `inactive` であることです。

### 実測

| 構成 | 1 本目 | 2 本目 | 3 本目 | いちばん悪い回 |
| --- | --- | --- | --- | --- |
| アプリ 4 vCPU × 2 / DB 2 vCPU | 27828 | 27171 | 28267 | 27171 |
| **アプリ 4 vCPU × 2 / DB 4 vCPU** | **35366** | **35608** | **38752** | **35366** |

**+30%**。digest も戻りました（`COMMIT` 0.26 ms、通知 0.32〜0.33 ms）。**全部 0.5 ms 以下**です。

| | スコア |
| --- | --- |
| AMI 直後 | 607 |
| 単一インスタンスの最高 | 7403 |
| 3 台分割 | 14694 |
| 3 台 + DB 専用 | 25349 |
| **3 台すべて `c5.xlarge`** | **38,752**（AMI の **約 64 倍**） |

## 3. `retry_after_ms` を短くする（不採用）

[0220](0220-more.md) の候補にあった「通知のポーリング間隔」を試しました。`retry_after_ms` は 30 ms です（[0190](../valid/0190-split-app.md) の通知応答）。

```python
# app/app_handlers.py / chair_handlers.py の 4 か所
retry_after_ms=15
```

| 構成 | 1 本目 | 2 本目 | `map[30]` |
| --- | --- | --- | --- |
| `retry_after_ms=30` | 35366 | 35608 | 10〜12 件 |
| **`retry_after_ms=15`** | 34674 | 36840 | **16〜28 件** |

**平均でわずかに下がり**（35,746 → 36,575 は誤差の範囲ですが、いちばん悪い回が 34,674 に落ちました）、**`nearby-chairs` の競合（`map[30]`）が増えました**。**戻しました。**

?> **マッチング間隔（0.05 秒）は測らずに「効果なし」と判断しました。**ライド 1 本の所要が 30〜60 秒なので、**0.05 秒の待ちは全体の 0.05%** です。ここを詰めても、**リクエストだけが増えます**。

## 4. アプリをもう 1 台（app-4）

```bash
# terraform.tfvars
app_instance_count = 4
instance_types = ["c5.xlarge", "c5.xlarge", "c5.xlarge", "c5.xlarge"]
```

```bash
terraform plan -no-color | grep -E '^  # |Plan:'
# aws_instance.app[3] will be created
# Plan: 1 to add, 0 to change, 0 to destroy
terraform apply -auto-approve
```

新しい台は **AMI のまま**なので、[0160](../valid/0160-split.md) と同じ手で `s1` を配ります（アプリのコード・索引・`env.sh` の DB 向き先・`bind = "0.0.0.0:8080"`）。そのうえで **nginx の上流に足します**。

```nginx
upstream isuride_app {
    server 127.0.0.1:8080;      # app-1
    server 10.0.1.132:8080;     # app-2
    server 10.0.1.174:8080;     # app-4（新規）
    keepalive 64;
}
```

?> **app-3 を上流から外してください。**DB 専用台なので、アプリは動いていません。残すと **4 回に 1 回は死んだ台に接続**して、リトライぶん遅くなります。

### 実測

?> **この構成は制約（3 台）違反のため、測ったあとで削除しました。**`app_instance_count = 3` に戻して `2 to destroy` を確認してから `apply` しています。

| 構成 | 1 本目 | 2 本目 | 3 本目 | いちばん悪い回 |
| --- | --- | --- | --- | --- |
| アプリ 2 台（c5.xlarge × 3） | 35366 | 35608 | 38752 | 35366 |
| **アプリ 3 台（app-4 追加）** | **54565** | **59810** | **53082** | **53082** |

**+47〜54%**。1 ベンチあたりのリクエストは **123,000 → 177,000**。振り分けは **182,112 / 176,744 / 171,468** とほぼ 3 等分です。digest は `COMMIT` 1.02 ms・通知 0.87〜0.90 ms で、**まだ DB に余裕があります**。

| | スコア |
| --- | --- |
| AMI 直後 | 607 |
| 3 台 + DB 専用 | 25349 |
| 3 台すべて c5.xlarge | 38,752 |
| **アプリ 3 台 + DB 専用（c5.xlarge × 4）** | **59,810**（AMI の **約 99 倍**） |

### 5-1. C ドライバ（試したが不採用）

`uv` があるので **Python 3.12 の venv は作れました**。`mysqlclient` は **`clang` を入れるとビルドできます**（無いと `error: No such file or directory: 'clang'` で**インストールごと失敗**します）。

?> **venv は最初から最終パスに作ってください。**`.venv312` に作って `.venv` に移動すると、`gunicorn` の **shebang が古いパス**を指したままになり、`No such file or directory` で起動しません（実際に 2 回踏みました）。`uv venv --python 3.12 .venv` と**最初から `.venv` に**作ります。

?> 台ごとに **`import app.main` と `gunicorn --version`** を通してから切り替えます。新しい AMI の台は `apt-get update` を先にしないと `clang` が入りません。

**結果**: 3 台とも起動・疎通できました（`gunicorn 23.0.0` / `import ok` / 401）が、**スコアが半減**しました。

| 構成 | 1 本目 | 2 本目 |
| --- | --- | --- |
| **pymysql（Python 3.13）** | **52047** | **53718** |
| mysqlclient（Python 3.12） | 20968 | 27514 |
| mysqlclient（`?charset=utf8mb4` を明示して再挑戦） | **12424** | **15349** |

**2 回試して 2 回とも戻しました。**`charset=utf8mb4` を明示しても **12,424 / 15,349** で、pymysql（41,913 / 40,062）の**3 分の 1**です。**この構成では mysqlclient が遅い**と結論づけます（原因は未特定。同期 SQLAlchemy との組み合わせを疑っています）。

### 5-2. 決済 → 書き込みの順序（3 台で採用）

[0220](0220-more.md) の 2-3 は **2 vCPU のとき不採用**にしました。3 台とも **c5.xlarge** になった状態で同じ改修を再測します。

| 構成 | 1 本目 | 2 本目 |
| --- | --- | --- |
| 決済がトランザクション内（基準） | 35487 | — |
| **決済 → 書き込み（再測）** | **41913** | **40062** |

**+13〜18%**。CPU に余裕ができて、**0.5 秒のロックを持たない**ことの効果が出ました。**3 台構成の最終形**として採用しています（`app_handlers.py` の `app_post_ride_evaluation`）。

?> 決済（約 0.5 秒）のあとに書き込みが失敗すると「**決済したのに完了していない**」が残ります。`Idempotency-Key`（[0100](../valid/0100-app-payment.md)）で同じ決済に戻れるので、評価の再試行で直ります。

### 5-3. 接続プールと C アクセラレータ（3 台）

| 手 | 実測 | 判断 |
| --- | --- | --- |
| `pool_size` 5 → 20 / `max_overflow` 5 → 10 | 36713 / 37850 | ✗ 戻した（**DB は接続待ちしていない**） |
| **`httptools` + `uvloop`**（uvicorn の C 製 HTTP パーサとイベントループ） | **45297** / **33998** / **45561** / **43120** | **採用**（中央値 39,100 → **44,200**） |

```bash
# アプリの台で（venv に入れる。pyproject.toml にも追記済み）
uv pip install --python ~/webapp/python/.venv/bin/python httptools uvloop
sudo systemctl restart isuride-python.service
```

?> **4 本のうち 1 本だけ 33,998 に落ちました。**この環境のばらつき（±20%）の範囲で、**残り 3 本は 43,000〜45,561** です。**中央値で +13%**、構造的にも C 実装のほうが有利なので採用しました。**1 本の外れ値で判断しない**（逆に、1 本の上振れでも判断しない）のがこの教材の原則です。

?> プールを増やして**効果が無かった**のは収穫でした。**同時実行の上限は DB 接続ではなく、アプリの CPU** です。

### 5-4. pydantic の再検証を外す（不採用）

FastAPI は `response_model` で**戻り値を再検証**します。ハンドラ側でモデルを作っているので、**2 回検証**していることになります。通知 2 本（全リクエストの約 65%）で、`response_model=None` + `model_dump_json(exclude_none=True)` を直接返す形に変えました。

| 構成 | 1 本目 | 2 本目 |
| --- | --- | --- |
| `response_model` あり（基準） | 45297 | 43120 |
| **`response_model=None` + 直接 JSON** | 41966 | 38328 |

**上がらなかったので戻しました。**pydantic の再検証は**同じモデルのインスタンス**に対しては十分速く、**差が出るほどではない**という実測です。

?> **`@router.get(` が 3 行のデコレータ**であることに注意してください。1 行目だけを置換すると `"/notification",` が宙に残り、**`IndentationError` でアプリが起動しません**（実際に踏みました）。**デコレータは `)` までまとめて置換**し、`ast.parse` と `curl` で確認します。

## 5. 次にやるなら

| 手 | 根拠 | 期待 |
| --- | --- | --- |
| **C ドライバ / async 化**（[0220](0220-more.md) ②） | アプリがまた上限（alp 0.084〜0.090 s、DB は 0.5 ms 未満） | 1 リクエストの CPU が 1〜3 割減 → **そのままスコア** |
| **決済 → 書き込みの順序**（[0220](0220-more.md) 2-3） | DB に余裕ができたので、2 秒のロックを持たない形が効く可能性 | 行ロックの削減 |
| アプリをもう 1 台 | `upstream` に足すだけ | ほぼ線形 |

## 6. 戻すには

```bash
# terraform.tfvars の instance_types を消す（app_instance_type = "c5.large" に戻る）
terraform apply -auto-approve
# ベンチ機の APP_IP はプライベート IP のままでよい（再起動しても変わらない）
```
