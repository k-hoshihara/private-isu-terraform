# 0055 計測をやり直して、次の 1 手を決める

0110 で 3 台に分けて、スコアは 5,000〜6,000 点まで来ました。ここから先は、思いつきで直すと外します。0060 では slow log と `alp` で「重そうな処理」を見ましたが、**「何回呼ばれているか」「何行読んでいるか」**が見えていません。この章では MySQL の `performance_schema` を使って SQL の形ごとの回数・行数・時間を出し、**次の 1 手をデータで決めます**。

## 1. なぜやり直すのか

0060 の道具では、次が分かりません。

| 0060 で分かること | 0060 では分からないこと |
| --- | --- |
| 0.1 秒以上かかったクエリ（slow log） | **0.01 秒でも 2 万回呼ばれているクエリ** |
| API ごとの合計時間（`alp`） | その API の中で **どの SQL が何行読んでいるか** |
| Python の関数ごとの時間（`py-spy`） | その関数が **DB 待ちか、計算か** |

ISUCon は「1 回 10 秒のクエリ」より **「1 回 1 ミリ秒のクエリを 2 万回」**のほうが効くことがよくあります。とくに ISURIDE は**通知のポーリング**が大量に走るので、この見方が要ります。

## 2. 3 つの道具

| 見るもの | 道具 | 出す数字 |
| --- | --- | --- |
| API ごとの件数と時間 | `alp` | COUNT / AVG / P99 / SUM |
| **SQL ごとの回数・行数・時間** | `performance_schema` | COUNT_STAR / SUM_ROWS_EXAMINED / SUM_TIMER_WAIT |
| Python がどこで待っているか | `py-spy` | サンプルの内訳（DB 待ち / COMMIT 待ち / 計算） |

MySQL 8 は、実行した SQL を**形ごとにまとめて**集計しています。`pt-query-digest` を回さなくても、ここに答えがあります。

## 3. 窓を切って 1 本回します

累積値のままだと前のベンチと混ざるので、**1 本ぶんの窓**を作ります。アプリ用インスタンス（入口の `app-1`）で行います。

### 3-1. 計測の前に戻します

```bash
# app-1 で
sudo mv /var/log/nginx/access.ltsv /var/log/nginx/access.ltsv.previous
sudo kill -USR1 "$(cat /run/nginx.pid)"

# SQL の集計をリセットする（root で）
sudo mysql -e "TRUNCATE performance_schema.events_statements_summary_by_digest;"

# カウンタを控える
mysql -uisucon -pisucon -N -e "SHOW GLOBAL STATUS" > /tmp/status-before.tsv
date -Is
```

?> `performance_schema.events_statements_summary_by_digest` の `TRUNCATE` は `root` で行います。`isucon` ユーザーでは権限が足りません。

### 3-2. ベンチを 1 本回します

ベンチ用インスタンスで回します。`--payment-url` は **`BENCH_PRIV`** です（[0040-bench.md](0040-bench.md) の手順 3）。

```bash
# ベンチ用インスタンスで
source ~/bench.env
cd ~
./bench run --target https://isuride.xiv.isucon.net \
  --addr ${APP_IP}:443 --payment-url http://${BENCH_PRIV}:12345 -s -t 60
```

同時に、アプリ用インスタンスで `py-spy` を付けます。**worker を 1 つ選んで**取ります（理由は §7）。

```bash
# app-1 で（ベンチの開始に合わせて）
MASTER=$(ps -eo pid,ppid,args | awk '$2==1 && /gunicorn/ {print $1; exit}')
WORKER=$(ps --ppid "$MASTER" -o pid= | head -1)
sudo ~/.local/bin/py-spy record -p "$WORKER" -f raw -d 40 -r 100 -o /tmp/pyspy.txt
```

### 3-3. 集計します

```bash
# app-1 で
mysql -uisucon -pisucon -N -e "SHOW GLOBAL STATUS" > /tmp/status-after.tsv

# カウンタの差
awk -F'\t' 'NR==FNR{a[$1]=$2; next}
            {if ($1 in a) printf "%s\t%s\n", $1, $2-a[$1]}' \
  /tmp/status-before.tsv /tmp/status-after.tsv

# 窓のリクエスト数
wc -l < /var/log/nginx/access.ltsv

# API ごと（合計時間順 / 件数順の 2 通りで見る）
alp ltsv --file /var/log/nginx/access.ltsv --nosave-pos \
  -o count,method,uri,avg,p99,max,sum --percentiles 50,90,99 --sort sum -r | head -14
alp ltsv --file /var/log/nginx/access.ltsv --nosave-pos \
  -o count,uri,avg,sum --sort count -r | head -8

# SQL ごと（時間順 / 行数順の 2 通りで見る）
sudo mysql -e "
SELECT COUNT_STAR n, ROUND(SUM_TIMER_WAIT/1e12,2) total_s,
       ROUND(AVG_TIMER_WAIT/1e9,2) avg_ms, SUM_ROWS_EXAMINED rows_ex,
       LEFT(REPLACE(DIGEST_TEXT, '\n', ' '), 72) q
FROM performance_schema.events_statements_summary_by_digest
ORDER BY SUM_TIMER_WAIT DESC LIMIT 14;"
sudo mysql -e "
SELECT COUNT_STAR n, SUM_ROWS_EXAMINED rows_ex,
       ROUND(SUM_TIMER_WAIT/1e12,2) total_s,
       LEFT(REPLACE(DIGEST_TEXT, '\n', ' '), 72) q
FROM performance_schema.events_statements_summary_by_digest
ORDER BY SUM_ROWS_EXAMINED DESC LIMIT 10;"
```

?> `SUM_TIMER_WAIT` はピコ秒です。`/1e12` で秒、`/1e9` でミリ秒になります。

### 3-4. py-spy の生データを数えます

`-f raw` は「スタックを `;` で繋いだ行 + サンプル数」を出します。フレームごとに足し上げます。

```bash
awk '{n=$NF+0; sub(/ [0-9]+$/,"",$0); m=split($0,a,";");
      for(i=1;i<=m;i++) c[a[i]]+=n}
     END{for(f in c) printf "%d\t%s\n", c[f], f}' \
  /tmp/pyspy.txt | sort -rn | head -20
```

## 4. 実測（2026-10-17、`c5.large` × 3 台 + ベンチ `c5.xlarge`）

窓の中身です。

- 公式ベンチ: `pass=true スコア=5625 種別エラー数=map[25:17]`
- 3 つの不満率: マッチング待ち **72.6%**、椅子の到着 **54.8%**、椅子の実移動 **100.0%**
- 窓のリクエスト数: **26,056**

### 4-1. API ごと

合計時間の順です。

| COUNT | METHOD | URI | AVG(s) | P99(s) | MAX(s) | SUM(s) |
| --- | --- | --- | --- | --- | --- | --- |
| 13,892 | GET | `/api/chair/notification` | 0.089 | 0.243 | 0.456 | 1,235.7 |
| 7,094 | GET | `/api/app/notification` | 0.118 | 0.321 | 0.566 | 836.9 |
| 3,694 | POST | `/api/chair/coordinate` | 0.131 | 0.293 | 0.429 | 482.4 |
| 31 | GET | `/api/owner/chairs` | **8.689** | 10.001 | 10.001 | 269.3 |
| 133 | GET | `/api/app/nearby-chairs` | 1.606 | 3.498 | 3.538 | 213.6 |
| 370 | GET | `/api/internal/matching` | 0.156 | 6.409 | 9.176 | 57.6 |
| 105 | POST | `/api/app/rides` | 0.153 | 0.344 | 0.372 | 16.1 |
| 136 | GET | `/api/app/rides` | 0.056 | 0.244 | 0.300 | 7.6 |

**件数の 95% は上位 3 本**（`chair/notification`、`app/notification`、`chair/coordinate`）です。合計時間でも 82% を占めます。

### 4-2. SQL ごと（`performance_schema`）

時間の順です。

| COUNT | 合計(s) | 平均(ms) | 読んだ行 | SQL |
| --- | --- | --- | --- | --- |
| 31 | 316.6 | **10,214** | 477 | `SELECT id, owner_id, name, ... FROM chairs LEFT JOIN (… LAG …)` |
| 17,798 | 214.0 | 12.0 | **9,353,240** | `SELECT * FROM chairs WHERE access_token = ?` |
| 19,955 | 180.2 | 9.0 | 20,070 | `SELECT status FROM ride_statuses WHERE ride_id = ? ORDER BY created_at DESC LIMIT 1` |
| 51,746 | 168.7 | 3.3 | 0 | `COMMIT` |
| 9,993 | 105.4 | 10.6 | 16,251 | `SELECT * FROM ride_statuses WHERE ride_id = ? AND chair_sent_at IS NULL …` |
| 133 | 104.4 | 784.8 | 614,647 | `SELECT chairs.id, … chair_locations …`（`nearby-chairs`） |
| 133 | 101.5 | 763.1 | 49,744 | `SELECT DISTINCT rides.chair_id FROM rides INNER JOIN (… GROUP BY ride_id …)` |
| 10,343 | 83.2 | 8.1 | 50,822 | `SELECT * FROM ride_statuses WHERE ride_id = ? ORDER BY created_at` |
| 7,093 | 76.7 | 10.8 | 8,218 | `SELECT * FROM ride_statuses WHERE ride_id = ? AND app_sent_at IS NULL …` |
| 105 | 48.5 | 462.0 | 486,424 | `SELECT chairs.id, chairs.owner_id, … FROM chairs …`（`match_one` の椅子選び） |
| 3,694 | 30.3 | 8.2 | 0 | `INSERT INTO chair_locations …` |
| 17,584 | 29.7 | 1.7 | 13,660 | `SELECT * FROM rides WHERE chair_id = ? ORDER BY updated_at DESC LIMIT ?` |

行数の順では、`chairs WHERE access_token = ?` が **935 万行**で 1 位です。`chairs` は 531 行なので、**1 回の呼び出しで 526 行 = テーブル全体を読んでいます**。

```text
mysql> EXPLAIN SELECT * FROM chairs WHERE access_token = 'x'\G
   type: ALL
   rows: 531
  Extra: Using where
```

`chairs.access_token` に索引が無いためです。[0060-sql-index.md](0060-sql-index.md) で足した 9 本に入っていませんでした。

?> 索引を足す前に、重複が無いことを確かめます。`UNIQUE` を張りたいためです。

```bash
mysql -uisucon -pisucon isuride -e "
  SELECT access_token, COUNT(*) c FROM chairs GROUP BY access_token HAVING c > 1;"
```

実測では 531 行 / 531 種類で、重複は 0 でした。

### 4-3. Python のどこで待っているか（`py-spy`）

2,046 サンプルの内訳です。

| サンプル | 場所 | 意味 |
| --- | --- | --- |
| 1,445（**71%**） | `_read_packet (pymysql/connections.py:744)` | MySQL の応答待ち |
| 474（**23%**） | `__exit__ (contextlib.py:148)` | `with engine.begin()` の抜け = **COMMIT 待ち** |
| 残り（6%） | アプリの Python の計算 | ここはほとんど無い |

**アプリのコードは速い**ということです。Python を書き換えても、この 2 つは減りません。

### 4-4. 1 リクエストあたり

窓のリクエスト数（26,056）で割った値です。

| 指標 | 窓の合計 | 1 リクエストあたり |
| --- | --- | --- |
| `Questions` | 245,850 | **9.4 本** |
| `Com_select` | 129,655 | 5.0 本 |
| `Com_commit` | 51,747 | 1.99 回 |
| `Com_rollback` | 51,749 | 1.99 回 |
| `Innodb_rows_read` | 11,997,371 | **460 行** |
| `Innodb_data_fsyncs` | 11,889 | 0.46 回 |

`Innodb_data_fsyncs` は 60 秒で 11,889 回、**1 秒あたり 198 回**の書き込み待ちです。

`COMMIT` と `ROLLBACK` が**同じ回数**なのがポイントです。`engine.begin()` は 1 回で `COMMIT` を 1 つ打ち、接続をプールに返すときに SQLAlchemy が後始末の `ROLLBACK` を打ちます。つまり**読み取りだけの API でも 2 往復を無駄に払っています**。

認証（`middlewares.py`）とハンドラが**別々に** `engine.begin()` を開くので、1 リクエストあたり 2 回 = `COMMIT` + `ROLLBACK` が 4 回です。

## 5. 読み方

1. **`alp` は件数と合計時間の両方**で見ます。合計時間だけだと「件数が多い 1 本」を見落とします
2. **`performance_schema` は行数順で見ます。**`chairs WHERE access_token` は 12 ミリ秒なので slow log には出ませんが、935 万行を読んでいます
3. **`py-spy` が `_read_packet` だらけなら、アプリのコードを直しても効きません。**DB の往復を減らすか、行数を減らします
4. **`COMMIT` / `ROLLBACK` の回数は「中身のない往復」**です。これを減らすのは、クエリを書き換えるのと同じ効果があります

?> 上の 4 つは [../040_practice/0120-db-load.md](../../040_practice/0120-db-load.md) の「仕事量を数える」と同じ考え方です。private-isu では `pt-query-digest`、ISURIDE では `performance_schema` を使います。

## 6. 作戦（次の 1 手の候補）

実測から出てくる候補です。**1 手ずつ**試すので、この章では決めるところまでにします。

| 候補 | 根拠（実測） | 変える場所 | 大きさ |
| --- | --- | --- | --- |
| **`chairs.access_token` に索引** | 17,798 回・**935 万行**・214 秒。1 回でテーブル全走査 | `sql/1-index.sql` に 1 行 | 小 |
| `owner/chairs` の窓関数をやめる | 31 回・**317 秒**・1 回 10.2 秒。`CODE=25` の出どころ | `owner_handlers.py` + `chair_handlers.py` + スキーマ | 中 |
| 読み取りで `engine.begin()` を使わない | `COMMIT`+`ROLLBACK` が **103,496 回**、`COMMIT` だけで 169 秒。`py-spy` の 23% | `middlewares.py` と各 GET | 小 |
| 通知まわりの `ride_statuses` の読みを減らす | 4 種類で **47,384 回・445 秒**（呼び手は通知 3 本が主） | `app_handlers.py` / `chair_handlers.py` | 中 |
| `chair_post_coordinate` の読み直しを消す | 3,694 回・30 秒 + 1 往復 | `chair_handlers.py` | 小 |

?> 一番大きいのは `owner/chairs` の 317 秒ですが、**31 回しか呼ばれません**。`chairs.access_token` は 17,798 回呼ばれるので、他の API の待ち時間にも効きます。この章では行数の 1 位から始めます。

## 7. この章でつまずいた点

1. **`--payment-url` にパブリック IP を渡すと落ちます。**`BENCH_IP` を使ったら `pass=false スコア=619 種別エラー数=map[6:7]` になりました。`map[6]` は「ユーザーの評価がタイムアウトした」で、ベンチのログには `context deadline exceeded`（評価の API が返ってこない）が出ます。`BENCH_PRIV` に直したら通りました（[0040-bench.md](0040-bench.md) の手順 3 の再確認です）。
2. **`py-spy record --subprocesses` はサンプルが薄くなります。**master に付けて `--subprocesses` を付けたら 45 秒で **43 サンプル**しか取れませんでした。worker を 1 つ選んで付けると 2,000 サンプル取れます。
3. **`alp --limit` を小さくするとエラーになります。**`--limit 8` で `Too many URI's (8 or less)` が出ました。`--limit` は「表示件数」ではなく「読み込む URI の上限」のようです。`head` で切ります。
4. **SSM で流すシェルは `sh`（dash）です。**`source` も配列（`EX=(...)`）も使えません。`. ファイル` と `for f in ...; do` で書きます。

## 出口

作戦が決まったら [0060-sql-index.md](0060-sql-index.md) へ進みます。**SQL チューニングをやりきってから**アプリに入ります。
