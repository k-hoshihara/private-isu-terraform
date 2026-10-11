# 0020 計測サイクルを回す

[0015-sql-tuning.md](0015-sql-tuning.md) の続きです。

- **1 台に nginx / アプリ / memcached / MySQL が揃った状態**で、測って直す一連の手順（計測サイクル）を手で回します。
- 手順の型は「ベンチ前の準備（bench-prep）→ 公式ベンチ → `scores.txt` に記録 → `alp` / `slp` で読む」です。ここで固めます。
- 計測ツールの導入とログの LTSV 化は、[0015-sql-tuning.md §1](0015-sql-tuning.md#1-計測ツールを入れる) で終えています。

- 順番: ツールの確認（1.）→ 計測サイクルを回す（2.）。
- 出口は [0030-cache.md](0030-cache.md)。

?> 点数は、構成・データ・ベンチ条件で桁が変わります。この章以降の「出力例」は実測記録です。章をまたいだ絶対比較はしません。比べるのは、同じ条件で撮った点数どうしです。`fail`（エラーの件数）が 0 のものだけを比べます。

## 1. 計測ツールの確認

- 計測ツールは、[0015-sql-tuning.md §1](0015-sql-tuning.md#1-計測ツールを入れる) で入れてあります。
- 1 台に全部入っているので、同じ台で有無を確認します。

```bash
which alp slp oha mysql mysqladmin pt-query-digest
alp --version 2>/dev/null
slp --help >/dev/null && echo 'slp ok'
```

- 無いものがあれば、[0015-sql-tuning.md §1](0015-sql-tuning.md#1-計測ツールを入れる) の手順で足します。
- apt で `percona-toolkit` / `netcat-openbsd`、バイナリで `alp` / `slp` / `oha` を入れます。

### 観察すること

- `which alp slp oha mysql pt-query-digest` が全部返ること
- `mysql` でアプリと同じ接続先に入れること
- `sudo mysql -e 'SELECT ...'` で今の slow 設定値（遅い問い合わせの記録設定）が見えること

## 2. 計測サイクルを回す

- ログを回してから、公式ベンチを 1 本回します。
- nginx のログも MySQL の slow log（遅い問い合わせの記録）も、同じ台にあります。
- 点数は、`~/bench-notes/scores.txt` に書きます。
- 内容を空にする操作は、ログの**回転（`mv` + `reopen`）**です。ログ停止ではありません。

?> `aws ssm send-command` は root で実行されます。リポジトリは isucon の持ち物です。git を触るときは、`sudo -u isucon -H` を付けるか、SSM セッションで isucon になってから打ちます。

### bench-prep（ログを回す）

- nginx のログは、`mv` + `nginx -s reopen` で回します。
- mysqld の slow log に、`truncate -s 0` は使いません。
- `truncate` は、途中が **NUL（空白の意味不明な文字）で埋まります**。ファイルだけ巨大になり、`slp` / `pt-query-digest` が壊れます。

```bash
# 1 台で打つ
TS=$(date +%Y%m%d%H%M%S)

if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
  sudo nginx -s reopen
fi

if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysqladmin flush-logs 2>/dev/null || sudo mysql -e 'FLUSH SLOW LOGS'

ls -l /var/log/nginx/access.log
sudo ls -l /var/log/mysql/mysql-slow.log
df -h /
sudo du -xh /var/log /home /tmp 2>/dev/null | sort -h | tail -n 10
```

- slow log の永続化（drop-in＝設定の追加ファイル）は、[0010-setup.md](../010_common/0010-setup.md) の「ログ（nginx LTSV / MySQL slow）」節を見ます。

### スロークエリを ON にする

- `long_query_time`（この秒数を超えた問い合わせだけを記録する、という基準）を、最初から 0 にはしません。
- ベンチ 1 本で数百 MB〜GB になり、ディスクが埋まるためです。
- まず 1 から始めます。

```bash
# 1 台で打つ
sudo mkdir -p /var/log/mysql
sudo touch /var/log/mysql/mysql-slow.log
sudo chown mysql:mysql /var/log/mysql/mysql-slow.log
sudo mysql -e "SET GLOBAL slow_query_log_file = '/var/log/mysql/mysql-slow.log'"
sudo mysql -e "SET GLOBAL slow_query_log = 1"
# まず遅いものだけ。ディスクを守る
sudo mysql -e "SET GLOBAL long_query_time = 1"
sudo mysql -N -e 'SELECT @@slow_query_log, @@slow_query_log_file, @@long_query_time'
```

- `long_query_time` の `SET GLOBAL` は、既につながっている接続の値を変えません。
- `slow_query_log` の ON/OFF は、サーバー全体に効きます。
- `long_query_time` は、新しい接続から効きます。
- `sudo systemctl restart isu-python.service` でアプリを再起動してから、測ります。
- 永続化は [0010-setup.md](../010_common/0010-setup.md) の drop-in を使います。
- ただし、全件キャプチャ用の 0 は残しません。

短い全件キャプチャ（速い問い合わせも全部記録する取り方。ベンチ 1 本だけ）:

```bash
# 上の bench-prep のあと。1 台で打つ
sudo mysql -e "SET GLOBAL long_query_time = 0"
# sudo systemctl restart isu-python.service（新規接続に 0 を効かせる）
# 公式ベンチ 1 本（次の節）を実行する
# 収集後は解析前に戻し、アプリも restart する
sudo mysql -e "SET GLOBAL long_query_time = 1"
sudo slp my --file /var/log/mysql/mysql-slow.log \
  --sort sum-query-time --reverse --output standard
df -h /
```

- 取り終わったら、`long_query_time` を 1 に戻します。
- `sudo systemctl restart isu-python.service` で、アプリを再起動します。
- `df -h /` と、回転済みの `.log.$TS` のサイズを確認します。
- ログはベンチ前に回転します。ベンチ後は、その 1 回分を解析します。

### 公式ベンチ

- ベンチ先は、同じ台の `localhost` です（nginx が listen している 80 番）。

```bash
# 1 台で打つ
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
```

実測サンプル（2026-10-13、`c7a.large` 1 台 / `ap-northeast-1` / 単一構成 / `nproc=2` / メモリ 3.7Gi）: `score=15125`（`fail=0`。[0015-sql-tuning.md](0015-sql-tuning.md) の 2 つを残した状態の基準値。[0040-http-client.md](0040-http-client.md) の推移表に記録）。

- JSON の `score` / `pass` / `fail` を残します。
- `fail` が 0 でない点数は、比べません。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  note=baseline" >> ~/bench-notes/scores.txt
```

| 回 | 時刻 | score | pass | fail | メモ |
| --- | --- | --- | --- | --- | --- |
| ベースライン | 2026-10-13T09:31:17+09:00 | 15125 | true | 0 | ログ ON、回転済み |

### 出力例

ツールの確認結果（1 台分）:

```text
alp                /usr/local/bin/alp     1.0.21
slp                /usr/local/bin/slp     ok
oha                /usr/local/bin/oha     1.12.1
mysql              /usr/bin/mysql         Ver 8.0.45-0ubuntu0.24.04.1
pt-query-digest    /usr/bin/pt-query-digest
nc                 /usr/bin/nc
py-spy             /usr/local/bin/py-spy  0.4.2
```

`alp ltsv --sort sum` の上位（`/image` が筆頭候補になる。2026-10-13、`c7a.large` 1 台 / `ap-northeast-1` / 単一構成 / `nproc=2` / メモリ 3.7Gi）:

| COUNT | URI | SUM | AVG | P99 | SUM(BODY) |
| --- | --- | --- | --- | --- | --- |
| 6453 | `/image/[0-9]+` | 328.122 | 0.051 | 0.188 | 2.08 GB |
| 1134 | `/` | 64.413 | 0.057 | 0.193 | 3.37 MB |
| 849 | `POST /login` | 46.057 | 0.054 | 0.199 | 161 KB |
| 670 | `/posts/[0-9]+` | 30.092 | 0.045 | 0.158 | 624 KB |
| 521 | `/css/style.css` | 26.411 | 0.051 | 0.194 | 807 KB |

?> この時点（0015 完了直後）は、静的ファイル（`/js` `/css`）もアプリ経由です。SUM が 0 になりません。静的を nginx で直接返すのは、[0060-static-cache.md](0060-static-cache.md) の手順です。

この時点までの点数（[0015-sql-tuning.md §8](0015-sql-tuning.md#8-公式ベンチで判定する) の推移の続き）:

| 回 | score | pass | fail | 備考 |
| --- | --- | --- | --- | --- |
| 0015 §8 判定 | 14817 | true | 0 | 索引 + N+1 一括化を残した状態 |
| 0020 bench-prep 後 | 15125 | true | 0 | ログ ON、回転済み。ここをこの章の原点にする |

### この節でつまずいた点

1. **`etc/nginx/ltsv.conf` が AMI 側に無い**（[0010-setup.md](../010_common/0010-setup.md) の「ログ」節）

   ```bash
   cp: cannot stat '/home/isucon/private-isu-terraform/etc/nginx/ltsv.conf': No such file or directory
   nginx: [emerg] open() "/etc/nginx/ltsv-format.conf" failed (2: No such file or directory)
   ```

   - 前提は、作業機の `etc/nginx/ltsv.conf` を AMI に `cp` することです。
   - ただし、AMI の clone したリポジトリには `etc/` がありません。
   - そのまま進めると、**nginx が `[emerg]` で起動不能**になります。
   - まず `include` を外して復旧します。
   - `backup/etc/nginx/ltsv-format.conf` として push してから、入れ直します。

2. **`include` の追加と `access_log` の書き換えの順序**

   ```bash
   sudo sed -i '\#include /etc/nginx/mime.types;#a include /etc/nginx/ltsv-format.conf;' /etc/nginx/nginx.conf
   sudo sed -i 's#access_log /var/log/nginx/access.log;#...ltsv;#' /etc/nginx/nginx.conf
   ```

   - エラーになるのは、`log_format ltsv` を読む前に `access_log ... ltsv` を指定した場合です。
   - 直す順序は、次のとおりです。
   - 定義ファイルを配置 → include を追加 → `nginx -t` → `access_log` を書き換え → 再度 `nginx -t` → reload
   - include 追加後のテストを挟むこと自体は、問題ありません。

3. **`conflicting server name ""` の警告**

   ```text
   nginx: [warn] conflicting server name "" on 0.0.0.0:80, ignored
   ```

   - AMI の `/etc/nginx/sites-available/isucon-php.conf` が、同じ `listen 80` で競合します。
   - `nginx -t` は通ります。放置しても大丈夫です。
   - ただし、`nginx -T` の出力を読むときに紛らわしくなります。

### alp / slp で読む

- マッチャ（URI のまとまり方の定義）は、`etc/alp/matching_groups.json` です（作り方は [prompts/alp-matchers.md](../010_common/prompts/alp-matchers.md)）。

```bash
# 1 台で打つ。LTSV 化（[0010-setup.md](../010_common/0010-setup.md) の「ログ」節）済みが前提
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -12
```

```bash
# 同じ台で打つ
sudo slp my --file /var/log/mysql/mysql-slow.log
# slp が無いとき
sudo pt-query-digest /var/log/mysql/mysql-slow.log
```

- `alp ltsv` が空表だけ返すときは、LTSV 化前のログが残っています。
- LTSV 化してから、ログを回し直します。

読み方:

- alp の Sum 上位（合計時間）と、slp の examined（読んだ行数）/ sent（返した行数）の比を見る
- Count も Sum も大きい → 呼ばれすぎ。[0030-cache.md](0030-cache.md)（キャッシュ）へ
- examined が sent の桁違い → 余計な行を読んでいる。[0015-sql-tuning.md](0015-sql-tuning.md)（インデックス）へ

### 観察すること

- `access.log` / `mysql-slow.log` が回転済みで、サイズが小さい（または 0 から増え始めた）
- `df -h /` に余裕がある
- ベンチが `pass: true` になること。これがこのあとの比較原点
- ログを回しただけで点数はほぼ変わらない（止めたわけではない）

### 残す変更は backup/s1 に積む

- 試して残すと決めた変更は、作業している台の分を `backup/s1` に入れます。
- それを、feature ブランチに積みます。
- 流儀は「作業機で作る → push → 台で pull → `cp` で移す」です。
- 複数台に分けたあとの配布も、同じ流れです（[0110-split-2.md](0110-split-2.md)）。

```bash
# 作業機（手元の private-isu-terraform）で打つ
git fetch origin
git switch feature/backup 2>/dev/null || git switch -c feature/backup
git pull --ff-only 2>/dev/null || true
# 例: 残す my.cnf の drop-in を置く
mkdir -p backup/s1/etc/mysql/mysql.conf.d
# 台で pull したあと sudo cp -a で /etc へ移す
git add -A && git commit -m "tune: 残す設定" && git push -u origin feature/backup
```

- `sudo cp -a` は、所有者を `root` にしたまま置きます。
- 置き先ごとの扱いは、次のとおりです。

- `/etc` 配下（nginx 設定・systemd drop-in・my.cnf）は、`root:root` のままで構いません。`nginx` / `mysqld` は root として読みます
- アプリのソース（`/home/isucon/private_isu/webapp/python/` 配下）は、`sudo chown isucon:isucon` で `isucon` に戻します。置いた直後に同じファイルを編集する作業があるためです
- `sites-enabled/` は symlink です。`readlink -f` で実体を選び、実体を置きます（[0030-cache.md](0030-cache.md) の出力例を参照）

- 戻すときは、逆に `cp` で戻します。
- 以後のチュートリアル（0030〜0100）でも、残す変更はここに積みます。
- [0110-split-2.md](0110-split-2.md) の分割では、この `backup/s1` が s1・s2 への配布元になります。
