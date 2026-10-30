# 0140 MySQL の書き込み設定

[0120-app-cache.md](0120-app-cache.md) で `COMMIT` の**回数**を 23,786 → 6,996 回（−71%）に減らしました。それでも `COMMIT` は **1 回 43.76 ms**、合計 **306 秒**で SQL の 1 位のままです。この章は**1 回あたり**を削ります。

## 1. いまの値を見ます

```bash
mysql -uisucon -pisucon -e "
SELECT @@innodb_flush_log_at_trx_commit AS flush, @@innodb_flush_method AS method,
       @@@innodb_buffer_pool_size/1024/1024 AS bp_mb, @@innodb_log_file_size/1024/1024 AS log_mb,
       @@sync_binlog AS sync_binlog, @@log_bin AS log_bin;"
```

実測ではこうでした。

```text
flush=1  method=fsync  bp_mb=128  log_mb=48  sync_binlog=1  log_bin=1
```

**`flush=1` と `sync_binlog=1` は、どちらも「コミットのたびにディスクまで落とす」設定**です。InnoDB のログとバイナリログの**2 か所**で fsync しています。

?> ここで **ロック待ち**も見ておきます。`COMMIT` が遅い原因は fsync だけとは限りません。

```bash
mysql -uisucon -pisucon -N -e "SHOW GLOBAL STATUS LIKE 'Innodb_row_lock_%';"
```

実測: `Innodb_row_lock_time = 125,529 ms` / `Innodb_row_lock_waits = 2,421`（**1 回 51 ms の待ち**）。窓の `COMMIT` 合計 306 秒に対して **ロック待ちは 11 秒（3.5%）**です。**fsync が主犯**と分かります。**測ってから決める**の実例です。

## 2. fsync を 2 か所ともやめます

**どちらも動的に変えられます。**MySQL を再起動しません（再起動すると、アプリの接続プールに死んだ接続が残ります）。

```bash
sudo mysql -e "SET GLOBAL innodb_flush_log_at_trx_commit = 2;"
sudo mysql -e "SET GLOBAL sync_binlog = 0;"
mysql -uisucon -pisucon -e "SELECT @@innodb_flush_log_at_trx_commit, @@sync_binlog;"
```

再起動後も残るように、設定ファイルにも書きます。

```bash
sudo cp -a /etc/mysql/mysql.conf.d/mysqld.cnf /etc/mysql/mysql.conf.d/mysqld.cnf.pre0140
sudo python3 - <<'PY'
p = '/etc/mysql/mysql.conf.d/mysqld.cnf'
s = open(p).read()
add = ("\n# 0140: コミットごとの fsync をやめる（クラッシュ時に最大 1 秒ぶん失う可能性がある）\n"
       "innodb_flush_log_at_trx_commit = 2\n"
       "# 0140: コミットごとのバイナリログの fsync もやめる\n"
       "sync_binlog = 0\n")
open(p, 'w').write(s.replace('[mysqld]', '[mysqld]' + add, 1))
PY
grep -n -A2 'innodb_flush_log_at_trx_commit' /etc/mysql/mysql.conf.d/mysqld.cnf
```

成功の見え方: `[mysqld]` の下に両方が出ます。

?> **失う可能性があるもの**: クラッシュ時に**最大 1 秒ぶんのコミット**が消えます（プロセスが落ちただけなら消えません）。競技では許容しますが、**本番の DB では使いません**。

## 3. 実測（単一インスタンス、2026-10-18）

**まず `flush=2` だけを入れて測りました。**

| 指標 | 0120 の後 | `flush=2` の後 | 変化 |
| --- | --- | --- | --- |
| `Innodb_data_fsyncs`（1 窓） | 9,387 | **2,808** | **−70%** |
| `COMMIT` の 1 回 | 43.76 ms | **43.28 ms** | **変わらず** |

**fsync は 70% 減ったのに、`COMMIT` の 1 回は変わりませんでした。**残っているのは**バイナリログの fsync**（`sync_binlog=1` のほう）です。

**次に `sync_binlog=0` を足して測りました。**

| 指標 | `flush=2` の後 | `sync_binlog=0` の後 | 変化 |
| --- | --- | --- | --- |
| `COMMIT` の 1 回 | 43.28 ms | **31.11 ms** | **−28%** |
| `Innodb_row_lock_waits`（1 窓） | 2,421（累計） | **230** | — |
| `Innodb_row_lock_time`（1 窓） | 125,529 ms（累計） | **11,022 ms** | ロックは主因ではない |

?> **`flush=2` だけでは足りませんでした。**「fsync を止めたのに遅いまま」で終わらせず、**どこが fsync しているか**を数えたのがこの章の要点です。InnoDB のログとバイナリログは**別々に**記録されています。

### スコア（すべて `pass=true`）

| 構成 | 1 本目 | 2 本目 | 3 本目 | 4 本目 | いちばん悪い回 |
| --- | --- | --- | --- | --- | --- |
| 0120 の後 | 5571 | 5617 | 5674 | 5191 | 5191 |
| `flush=2` | **6140** | 5102 | — | — | 5102 |
| + `sync_binlog=0` | **6476** | **6296** | 6397 | 5802 | 5802 |
| 成績を同じ行から読む（下の §4） | 5107 | 6076 | — | — | 5107 |

**この手のあとの最高は 6476 です**（AMI は 607、0060 の後は 1591）。

## 4. ついで: 同じ行から読めるものは同じ行から読みます

0120 で `chairs` に成績の列を入れましたが、通知は**別のクエリ**で読みに行っていました。`SELECT * FROM chairs WHERE id = ?` の結果に**すでに入っている**ので、追加のクエリを消せます。

```python
# app/models.py: Chair に列を足す
class Chair(BaseModel):
    # 0120: chairs の列（集計せずに同じ行から読む）
    completed_count: int = 0
    evaluation_sum: int = 0
    ...
```

```python
# app/app_handlers.py の app_get_notification
            # 0120: 成績は同じ行に入っているので、追加のクエリを打たない
            _count = int(chair.completed_count)
            stats = AppGetNotificationResponseChairStats(
                total_rides_count=_count,
                total_evaluation_avg=(float(chair.evaluation_sum) / _count) if _count > 0 else 0.0,
            )
```

?> **認証キャッシュに載っている `Chair` を成績に使ってはいけません。**そちらは**キャッシュした時点の値**です。この通知は**自分で取り直した行**（`SELECT * FROM chairs WHERE id = ?`）を使うので、常に新しい値です。**同じクラスでも、どこから来たインスタンスかで鮮度が違う**ことに注意してください。

?> **負荷が上がると 1 回あたりの時間は上がります。**このインスタンスは **2 vCPU** で、アプリ・MySQL・nginx が同居しています。マッチングが速くなって**完了するライドが増えると、ベンチがそのぶん多くリクエストを出します**。窓の `Questions` が 129,444 → 261,358 と 2 倍になった回では、どのクエリの 1 回あたりも 1.5〜2 倍になっていました。**窓をまたいで比較するときは、`Questions` か nginx のリクエスト数で正規化**してください。

## 5. 戻すには

```bash
sudo mysql -e "SET GLOBAL innodb_flush_log_at_trx_commit = 1; SET GLOBAL sync_binlog = 1;"
sudo cp -a /etc/mysql/mysql.conf.d/mysqld.cnf.pre0140 /etc/mysql/mysql.conf.d/mysqld.cnf
```

## 出口

次は **0130（インフラ）**です。2 vCPU にアプリ・MySQL・nginx が同居しているので、**gunicorn の worker 数・nginx の上流 keepalive・アクセスログ**を見ます。その後 **0160（3 台に分割）**で、**MySQL に専用の CPU を渡します**。
