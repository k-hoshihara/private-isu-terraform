# 計測

alp → slp。DB が抜けてから py-spy。配布は [00400-ops.md](00400-ops.md)。  
1 台で順に手を動かすドリルは [00600-practice.md](../heavy/single/00600-practice.md)。

## 1 サイクル

1. ログを空にする  
2. 公式ベンチ  
3. alp → スロークエリ  
4. 残すなら `mkdir ~/bench-notes` して点数を書く  

DB がまだ重い（スローが多い、CPU が mysqld）うちは alp と slp だけ見る。

| 順 | 見る指標 | 次の一手 |
| --- | --- | --- |
| alp | **Sum** | 遅いパスの SQL / クエリ回数 |
| slp | **Rows examined / Rows sent 比** | 複合インデックス、N+1 |
| py-spy | on-CPU と `--idle` | アプリ側の無駄・待ち |

- Count も Sum も大きい → 呼ばれすぎ（N+1、キャッシュ）
- Avg / p99 が大きい → 1 回が重い
- examined >> sent → インデックス不足
- `--idle` なしだけ見ると、DB 待ちが消えて「CPU は暇」に見える

## ベンチ直前

```bash
sudo truncate -s 0 /var/log/nginx/access.log
sudo truncate -s 0 /var/log/mysql/mysql-slow.log
```

## alp

マッチャは `etc/alp/matching_groups.json` の `groups`。生成は [prompts/alp-matchers.md](prompts/alp-matchers.md)。

```bash
jq -r '.groups | join(",")' etc/alp/matching_groups.json
MATCH='/posts/[0-9]+,/image/[0-9]+'

sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH"
# LTSV 前の combined / json なら
# alp regexp --sort sum --reverse
# alp json --sort sum --reverse
# --sort avg|count|p95|p99
```

残すときだけ:

```bash
mkdir -p ~/bench-notes
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | tee ~/bench-notes/alp.txt
```

## スロークエリ

```bash
sudo slp my --file /var/log/mysql/mysql-slow.log
sudo pt-query-digest /var/log/mysql/mysql-slow.log   # slp が無いとき
```

LLM への提案依頼は [prompts/index-from-slp.md](prompts/index-from-slp.md)。

## インデックス

一括適用はしない。slp を見て 1 本ずつ `EXPLAIN` → `CREATE INDEX` → 再計測。

| 比 | 意味 | やること |
| --- | --- | --- |
| 数倍以内 | 必要な行だけ読んでいる | インデックスより呼び出し回数 |
| 桁が違う | 余計な行を読んでいる | `WHERE` / `ORDER BY` / `JOIN` の左から複合 |
| sent が 1 で examined が全行 | フルスキャン | 等価条件の先頭列 |

```sql
SELECT table_name, index_name, seq_in_index, column_name
FROM information_schema.statistics
WHERE table_schema = DATABASE()
ORDER BY table_name, index_name, seq_in_index;

SHOW INDEX FROM posts;
EXPLAIN SELECT * FROM posts WHERE user_id = 1 ORDER BY created_at DESC LIMIT 30;
```

`type` は `ref` / `range` が欲しい。`ALL` はフルスキャン。`key` が `NULL` なら使っていない。

MySQL は `CREATE INDEX IF NOT EXISTS` が使えない。

```sql
SELECT COUNT(*) FROM information_schema.statistics
WHERE table_schema = DATABASE()
  AND table_name = 'posts'
  AND index_name = 'idx_posts_user_created';

-- 0 なら。等価 → 範囲 → ORDER BY
CREATE INDEX idx_posts_user_created ON posts (user_id, created_at);
```

作り終わったら同じ `EXPLAIN` で `key` が変わったことを見てからベンチする。効かなければ `DROP INDEX`。  
再起動で初期化 SQL が DB を作り直す回は、終盤にもう一度 `SHOW INDEX`。

やってはいけないこと: slp を見ない「よくあるインデックス集」、フラグ列だけ、同じ先頭列を何本も。

## py-spy

負荷が乗っているあいだに取る。

```bash
UNIT=isu-python.service
PID="$(systemctl show -p MainPID --value "$UNIT")"
sudo py-spy record --pid "$PID" --subprocesses \
  --format speedscope --duration 30 --rate 100 -o /tmp/pyspy.json
sudo py-spy record --pid "$PID" --subprocesses --idle \
  --format speedscope --duration 30 --rate 100 -o /tmp/pyspy-idle.json
```

https://www.speedscope.app/ にドロップ。

## スコアのメモ

```bash
mkdir -p ~/bench-notes
echo "score=12345  $(date -Iseconds)" >> ~/bench-notes/scores.txt
```

## oha / リソース

```bash
oha -n 1000 -c 20 --no-tui http://127.0.0.1/posts/1
uptime; free -h; df -h /; iostat -xz 1 3
```

## 備考

### alp が空 / パスが割れすぎ

ログを空にした直後や、フォーマットが LTSV でない。`head` で 1 行見て `alp ltsv` / `json` / `regexp` を合わせる。ID 付き URL は `-m` でまとめる。

### slp がファイル無し

slow が有効か、パスが合っているか。

```bash
mysql -N -e 'SELECT @@slow_query_log, @@slow_query_log_file, @@long_query_time'
ls -l /var/log/mysql/mysql-slow.log
```

### ベンチが通らない

ブラウザでトップ、`APP_PORT` と nginx `proxy_pass`、alp の 5xx。フロントが壊れていないかも見る。

### py-spy が Permission denied

`/proc/sys/kernel/yama/ptrace_scope` が 0 か。[00200-setup.md](00200-setup.md#権限py-spy--ulimit) を入れたあと、取り直す。
