# 0060 インデックスを足す

インデックスは、SQL の調整で最初に効く 1 手です。ISUCon 14 のテーブルには主キーと UNIQUE しか無く、`ride_statuses` と `rides` には制約がありません。`EXPLAIN` で全表スキャン（`type=ALL`）を見て、1 手ずつ足し、ベンチで確かめます。出口は [0050-measure.md](0050-measure.md) です。次は、重い処理を計測する道具をそろえます。

## 1. いまのインデックスを見ます

```bash
mysql -uisucon -pisucon isuride -e "
  SELECT table_name, index_name, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS cols
  FROM information_schema.statistics
  WHERE table_schema = 'isuride'
  GROUP BY table_name, index_name
  ORDER BY table_name, index_name;"
```

読み方は 2 つです。

- `users` / `owners` には `access_token` の UNIQUE がある → 認証 middleware の `WHERE access_token = ?` は索引が使えます
- `chairs` には `access_token` のインデックスが無い → 椅子認証は毎回全表スキャンです
- `ride_statuses` は主キーしか無く、`ride_id` のインデックスが無い → 通知とステータス取得が毎回全表スキャンです

## 2. EXPLAIN で今がどうなっているか見ます

```bash
mysql -uisucon -pisucon isuride -e "
  EXPLAIN SELECT status FROM ride_statuses
  WHERE ride_id = '01JEG4X2TZSE169T99XERS990M'
  ORDER BY created_at DESC LIMIT 1\G"
```

`type` が `ALL` なら全表スキャンです。`possible_keys` が `NULL` なら使えるインデックスが無いことを意味します。

?> 初期データでは行数が少ないため、`type=ALL` でも Actual 時間の差は見えません。`rows` の数を見て判断し、ベンチを 1 本回してデータが増えてから読み直します。

## 3. インデックス定義を書き出します

インデックスは `~/webapp/sql/1-schema.sql` ではなく、別のファイルにします。`1-schema.sql` は `init.sh` から毎回流し込まれ、`DROP TABLE IF EXISTS` を含むためです。定義ファイルを作って `init.sh` の最後から読み込む形にしておくと、`POST /api/initialize` のたびにインデックスが乗ります（ベンチは毎回この初期化を通ります）。

```bash
cd ~/webapp/sql
cp init.sh init.sh.orig
cat > 1-index.sql <<'EOF'
SET CHARACTER_SET_CLIENT = utf8mb4;
SET CHARACTER_SET_CONNECTION = utf8mb4;

USE isuride;

CREATE INDEX idx_ride_statuses_ride_id_created_at
  ON ride_statuses (ride_id, created_at);
EOF
cat 1-index.sql
```

## 4. init.sh から読み込みます

`init.sh` の最後（初期データの投入の後）に 1 行足します。`init.sh` は `set -eux` で、先頭で `ISUCON_DB_*` を定義しています。

```bash
cat >> init.sh <<'EOF'

# インデックス定義を取り込む
mysql -u"$ISUCON_DB_USER" \
		-p"$ISUCON_DB_PASSWORD" \
		--host "$ISUCON_DB_HOST" \
		--port "$ISUCON_DB_PORT" \
		"$ISUCON_DB_NAME" < 1-index.sql
EOF
bash -n init.sh && echo 'init.sh の構文はOK'
tail -n 8 init.sh
```

成功の見え方: `init.sh の構文はOK` が出ます。末尾に `mysql ... < 1-index.sql` が加わっています。

## 5. 初期化を通して反映します

`POST /api/initialize` を 1 回呼びます（このあとのベンチでも毎回通ります）。

```bash
curl -sS -X POST https://isuride.xiv.isucon.net/api/initialize \
  -H 'content-type: application/json' \
  -d '{"payment_server":"http://localhost:12345"}'
echo
mysql -uisucon -pisucon isuride -e "
  SELECT index_name, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS cols
  FROM information_schema.statistics
  WHERE table_schema='isuride' AND table_name='ride_statuses'
  GROUP BY index_name;"
```

成功の見え方: `{"language":"python"}` のあと、`idx_ride_statuses_ride_id_created_at | ride_id,created_at` が 1 行出ます。

## 6. EXPLAIN を作り直します

```bash
mysql -uisucon -pisucon isuride -e "
  EXPLAIN SELECT status FROM ride_statuses
  WHERE ride_id = '01JEG4X2TZSE169T99XERS990M'
  ORDER BY created_at DESC LIMIT 1\G"
```

成功の見え方: `key` が `idx_ride_statuses_ride_id_created_at` になり、`type` が `ref` になります。`rows` がテーブルの行数から `1` 相当に減っていることを確認します。

## 7. ベンチを 1 本回します

`~/bench-notes/scores.txt` に基づいて 1 手分だけ入れた判定をします。

```bash
# ベンチ用インスタンスで
source ~/bench.env
cd ~
./bench run --target https://isuride.xiv.isucon.net \
  --addr ${APP_IP}:443 --payment-url http://${BENCH_PRIV}:12345 -s -t 60
```

成功の見え方: `msg=結果 pass=true` が出たら、スコアを `scores.txt` に 1 行足します。

### 実測サンプル

| 回 | score | tw | メモ |
| --- | --- | --- | --- |
| 0040 ベースライン（2026-10-17） | 661 | - | インデックスなし |
| 0050 `idx_ride_statuses_ride_id_created_at` 1 本 | 727 | - | +66 |

?> インデックスは `SELECT` を速くする一方で `INSERT` と `UPDATE` を遅くします。`ride_statuses` と `rides` はベンチ中に最も増えるテーブルなので、入れすぎると元より遅くなります。[0050-measure.md](0050-measure.md) の slow log で「挿入が遅くなった」と判断したら、インデックスを外して測り直します。

## 8. 2 手目、3 手目

1 手目が済んだら、同じ手順で次のインデックスを足します。**1 本ずつ**入れて、入れた 1 本ごとにベンチを 1 本回します。

```bash
cat >> ~/webapp/sql/1-index.sql <<'EOF'

CREATE INDEX idx_ride_statuses_ride_app_sent
  ON ride_statuses (ride_id, app_sent_at, created_at);
CREATE INDEX idx_ride_statuses_ride_chair_sent
  ON ride_statuses (ride_id, chair_sent_at, created_at);
CREATE INDEX idx_rides_user_created
  ON rides (user_id, created_at);
CREATE INDEX idx_rides_chair_updated
  ON rides (chair_id, updated_at);
CREATE INDEX idx_chair_locations_chair_created
  ON chair_locations (chair_id, created_at);
CREATE INDEX idx_coupons_used_by
  ON coupons (used_by);
CREATE INDEX idx_chairs_owner
  ON chairs (owner_id);
CREATE INDEX idx_chairs_is_active
  ON chairs (is_active);
EOF
```

- `idx_ride_statuses_ride_app_sent` は `GET /api/app/notification` の `WHERE ride_id = ? AND app_sent_at IS NULL ORDER BY created_at ASC` に対応します
- `idx_ride_statuses_ride_chair_sent` は `GET /api/chair/notification` の同じ形の検索に対応します
- `idx_rides_user_created` は `GET /api/app/rides` の `WHERE user_id = ? ORDER BY created_at DESC` に対応します
- `idx_rides_chair_updated` は `POST /api/chair/coordinate` と `GET /api/app/nearby-chairs` の `WHERE chair_id = ?` に対応します
- `idx_chairs_owner` は `GET /api/owner/chairs` と `GET /api/owner/sales` に対応します
- `idx_chairs_is_active` はマッチングの候補選択 `WHERE is_active = TRUE` に対応します
- `idx_coupons_used_by` は運賃計算の `WHERE used_by = ?` に対応します

椅子認証の `SELECT * FROM chairs WHERE access_token = ?` にはインデックスが無く、`chairs.access_token` は UNIQUE ではありません。手で足す場合は重複が無いことを確認してから `ALTER TABLE chairs ADD UNIQUE KEY (access_token);` を `1-index.sql` に追加します。

追加したら `POST /api/initialize` を 1 回呼び、`information_schema` でインデックスが消えていないことを確認してからベンチを回します。

?> 9 本すべてを入れて回した実測は次のとおりです。この時点がいちばん効きます。以降は Python 側の直しに入りますが、スコアの伸びはここまで大きくなりません。

| 回 | score | tw | メモ |
| --- | --- | --- | --- |
| 0040 ベースライン（2026-10-17） | 661 | - | インデックスなし |
| 0050 `idx_ride_statuses_ride_id_created_at` 1 本 | 727 | - | +66 |
| 0050 残り 8 本（計 9 本） | 1518 | - | +791 |

?> **単一インスタンスで測り直した値**: ベースライン（AMI・索引なし）が `742 / 607`、9 本入れたあとが `1758 / 804`（どちらも `pass=true`）。**同じ構成でも 2 倍近くぶれます。**数本回して、いちばん悪い回でも上がっているかを見ます。

## 9. インデックスを戻すには

```bash
cd ~/webapp/sql
mv init.sh.orig init.sh
rm -f 1-index.sql
curl -sS -X POST https://isuride.xiv.isucon.net/api/initialize \
  -H 'content-type: application/json' \
  -d '{"payment_server":"http://localhost:12345"}'
echo
```

## 出口

インデックスで足りないぶんは、**集計をやめて列で持つ**手（0070）と、**N+1 を潰す**手（0080）で取ります。

?> 0070 以降は、単一インスタンスで 1 手ずつ測り直しながら書いています。それまでは [0055-measure-digest.md](0055-measure-digest.md) の手順で、自分の環境の数字を取ってください。

?> この章の変更を残すなら、作業機で `backup/isucon14/s1` に積みます（[0015-backup.md](0015-backup.md) の手順 4）。

## 単一インスタンスで足した 2 本

### `chairs.access_token`（採用）

椅子の認証（`chair_auth_middleware`）は、**椅子の API のたびに** `SELECT * FROM chairs WHERE access_token = ?` を投げます。索引が無いと**テーブル全体**を読んでいました。

```sql
-- 1-index.sql の末尾
CREATE UNIQUE INDEX idx_chairs_access_token
  ON chairs (access_token);
```

入れる前に重複を確かめます（`access_token` は `secure_random_str(32)` なので重複しません）。

```bash
mysql -uisucon -pisucon isuride -e "
  SELECT access_token, COUNT(*) c FROM chairs GROUP BY access_token HAVING c > 1;"
```

**実測（単一インスタンス、`pass=true` のみ）**

| 構成 | 1 本目 | 2 本目 | 3 本目 | いちばん悪い回 |
| --- | --- | --- | --- | --- |
| 9 本（`access_token` なし） | 1758 | 804 | — | **804** |
| 10 本（`access_token` あり） | 1901 | 1697 | 1591 | **1591** |

**いちばん悪い回が 804 → 1591** に上がりました。`performance_schema` でも、このクエリは **13,630 回で読んだ行が 13,631 行**（1 回 1 行）になりました。

### `idx_ride_statuses_ride_id_created_at` をカバリングに（**戻した**）

`get_latest_ride_status`（`SELECT status FROM ride_statuses WHERE ride_id = ? ORDER BY created_at DESC LIMIT 1`）は、索引で行を見つけてから本体を読みに行きます。`status` まで索引に入れれば本体を読まずに済みます。

```sql
-- (ride_id, created_at) を (ride_id, created_at, status) にする
```

**1 リクエストあたりは速くなります**（3 台構成で測ったときは 9.46 ms → 8.28 ms）。**しかしスコアは下がりました。**

| 構成 | 1 本目 | 2 本目 | 3 本目 | いちばん悪い回 |
| --- | --- | --- | --- | --- |
| 10 本（2 列のまま） | 1901 | 1697 | 1591 | **1591** |
| 11 本（カバリング） | 1363 | 1619 | 1225 | **1225** |

索引が 3 列に広がるぶん、**他のクエリが選ぶ索引も変わります**。1 リクエストが速くなっても、全体のスコアは保証されません。**戻しました。**

?> ここが索引のむずかしいところです。**「1 本足せば必ず良くなる」わけではありません。**1 本足すごとに、必ずベンチを回して**いちばん悪い回**で比べます。

## この時点の SQL の内訳（単一インスタンス）

`performance_schema` の時間順です（`pass=true スコア=1115` の窓）。

| 回数 | 合計 | 1 回 | 読んだ行 | SQL |
| --- | --- | --- | --- | --- |
| 39 | **296.0 s** | **7,590 ms** | 487 | `SELECT id, owner_id, name, ... FROM chairs LEFT JOIN (... LAG ...)` |
| 35,353 | 188.7 s | 5.3 ms | 0 | `COMMIT` |
| 12,698 | 93.0 s | 7.3 ms | 12,712 | `SELECT status FROM ride_statuses WHERE ride_id = ? ... LIMIT 1` |
| 13,576 | 47.6 s | 3.5 ms | 8,827 | `SELECT * FROM rides WHERE chair_id = ? ORDER BY updated_at DESC` |
| 5,721 | 40.4 s | 7.1 ms | 1,642 | `SELECT * FROM ride_statuses ... chair_sent_at IS NULL ...` |
| 3,499 | 30.9 s | 8.8 ms | 8,490 | `SELECT * FROM ride_statuses WHERE ride_id = ? ORDER BY created_at` |
| 5,722 | 27.3 s | 4.8 ms | 5,722 | `SELECT * FROM users WHERE id = ? FOR SHARE` |
| 13,630 | 26.4 s | 1.9 ms | **13,631** | `SELECT * FROM chairs WHERE access_token = ?` |
| 3,723 | 24.6 s | 6.6 ms | 1,165 | `SELECT * FROM ride_statuses ... app_sent_at IS NULL ...` |
| 3,794 | 20.2 s | 5.3 ms | 3,198 | `SELECT * FROM coupons WHERE used_by = ?` |

1 位は **`owner/chairs` の窓関数（1 回 7 秒）**です。ここは索引では取れません（`chair_locations` 全体を毎回たどっているためです）。**集計をやめて列で持つ**手（0070）で取ります。

?> 上位の多くは「**1 回は軽いが回数が多い**」クエリです（`get_latest_ride_status` は 7 ミリ秒、`access_token` は 1 行だけ）。これは索引では取れません。**往復の回数を減らす**＝アプリ側の手です（0090 以降）。
