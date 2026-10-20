# 0180 分割後の SQL と接続

[0170](0170-mysql-host.md) で **DB を専用台**にしました。この章は、**そこで残った SQL のムダ**と、**分割して初めて出る接続数の問題**を潰します。

## 1. 分割後に出たエラーの正体は「接続数」でした

3 台に分けた直後から、ベンチの `種別エラー数` に **`map[1:...]`** が現れました（最大 103 件）。`pass=true` は維持していましたが、**分割前には出ていなかった**種類です。

アプリのログを見ます。

```bash
sudo journalctl -u isuride-python --since '-30min' --no-pager | grep -i 'too many'
# (pymysql.err.OperationalError) (1040, 'Too many connections')
```

MySQL 側でも裏が取れます。

```sql
SHOW VARIABLES LIKE 'max_connections';                    -- 151
SHOW GLOBAL STATUS LIKE 'Max_used_connections';           -- 152
SHOW GLOBAL STATUS LIKE 'Connection_errors_max_connections'; -- 1576
```

**数が合います。**`[0150](0150-mw-lib.md)` のプール設定は `pool_size=5` + `max_overflow=5` でした。

```text
6 worker × (5 + 5) = 60 接続 / アプリ 1 台
60 × 3 台 = 180 接続  >  max_connections = 151
```

**1 台構成では 60 で足りていた**のに、**3 台に増やした瞬間に上限を超えました**。分割の副作用です。

## 2. `max_connections` を上げます

```bash
sudo mysql -e "SET GLOBAL max_connections = 1000;"
mysql -uisucon -pisucon -e "SHOW VARIABLES LIKE 'max_connections';"
```

設定ファイルにも書きます。

```bash
sudo python3 - <<'PY'
p = '/etc/mysql/mysql.conf.d/mysqld.cnf'
s = open(p).read()
if '\nmax_connections' in s:
    print('already')
else:
    s = s.replace('[mysqld]', '[mysqld]\n# 0180: 3 台 x 6 worker x プール 10 = 最大 180 接続。余裕を持って 1000\nmax_connections = 1000', 1)
    open(p, 'w').write(s)
PY
grep -n '^max_connections' /etc/mysql/mysql.conf.d/mysqld.cnf
```

?> **`mysqld.cnf` には `# max_connections = 151` が最初からコメントで書かれています。**「`max_connections` という文字があるか」で判定すると**コメントに引っかかって設定を足せません**（実測で 1 回やらかしました）。**行頭が `max_connections` か**で見ます。

### 実測

| 指標 | 修正前 | 修正後 |
| --- | --- | --- |
| `Connection_errors_max_connections` | 1,576 | **0** |
| `map[1:...]`（500 のエラー） | 最大 103 件 | **消えた** |
| スコア（DB は app-1 のまま） | 11015 / 14604 | **14886 / 11348** |

?> **`map[1:...]` は「接続が取れずに 500 を返した」でした。**エラーの種類が減ったことは、**スコアより先に見るべき指標**です。

## 3. 残った走査を索引で潰します

DB 専用にしたあとの digest で、**1 つだけ行数を読んでいるクエリ**が残っていました。

| 回数 | 1 回 | 読んだ行 | SQL |
| --- | --- | --- | --- |
| 1,868 | 3.69 ms | **872 行**（合計 1,628,425 行） | `SELECT DISTINCT rides.chair_id FROM rides WHERE chair_id IS NOT NULL AND evaluation IS NULL` |

`nearby-chairs` が「今ライド中の椅子」を集めるクエリです（[0080](0080-sql-n1.md)）。**`evaluation` に索引が無い**ので、毎回ほぼ全件を走査していました。

```sql
-- webapp/sql/1-index.sql の末尾
-- 0180: nearby-chairs の「今ライド中の椅子」を索引で引く
ALTER TABLE rides ADD INDEX idx_rides_evaluation_chair (evaluation, chair_id);
```

```bash
mysql -uisucon -pisucon isuride -e "ALTER TABLE rides ADD INDEX idx_rides_evaluation_chair (evaluation, chair_id);"
```

?> **`1-index.sql` は 3 台すべてに置きます。**`initialize` は **app-1 / app-2 のどちらに来ても `init.sh` を実行する**ので、**置き場所が 1 台でも欠けると、その台が初期化したときに索引が消えます**。

### 実測

| 構成 | 1 本目 | 2 本目 |
| --- | --- | --- |
| 索引なし（DB 専用） | 13926 | **21819** |
| **+ `idx_rides_evaluation_chair`** | **21025** | **18961** |

スコアは帯の中ですが、**1 回あたり 872 行の走査が索引レンジに変わります**（バッファプールを汚さなくなります）。

## 4. 分割後の digest（DB 専用・索引後）

| 回数 | 合計 | 1 回 | SQL |
| --- | --- | --- | --- |
| 44,114 | 22.3 s | **0.51 ms** | `COMMIT` |
| 38,920 | 15.1 s | **0.39 ms** | 通知の状態読み（アプリ側） |
| 36,140 | 14.0 s | **0.39 ms** | 通知の状態読み（椅子側） |
| 70,510 | 13.1 s | **0.19 ms** | `SELECT * FROM rides WHERE chair_id = ? ...` |
| 33,290 | 7.0 s | **0.21 ms** | `SELECT status FROM ride_statuses ... LIMIT 1` |

**全部 1 ms 以下です。**`slp` は 0 件のまま。**SQL の章はここで終わり**です。

?> **「全部 1 ms 以下なのに、なぜ 130 ms かかるのか」**——alp の `chair/notification` は AVG 0.130 s でした。**DB は 1 ms で返しているのに、アプリが 130 ms かけています**。残りは**アプリ側**（往復の回数・Python の処理・worker の順番待ち）です。次は **0190（分割前提のアプリ）**でそこを削ります。

## 5. 戻すには

```bash
sudo mysql -e "SET GLOBAL max_connections = 151;"
# 設定ファイルの max_connections 行を消す
mysql -uisucon -pisucon isuride -e "ALTER TABLE rides DROP INDEX idx_rides_evaluation_chair;"
# 3 台の 1-index.sql から -- 0180 のブロックを消す
```

## 出口

次は **0190（分割前提のアプリ）**です。**DB が 1 ms で返しているのにアプリが 130 ms かけている**ので、そこを詰めます。
