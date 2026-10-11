<a id="0070-infra-params-土台の-1-ノブずつカーネルソケットworkerfd"></a>

# 0070 土台の 1 ノブずつ（カーネル・ソケット・worker・FD）

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）は、手元の private-isu-terraform リポジトリで実行します。

- アプリの手前にある土台（OS や受け付けの設定）を、1 項目ずつ変えます。
- `oha` の p99（遅い順に並べて 99 番目の応答時間）と公式ベンチの点数で、確認します。

- `net.core.somaxconn`（接続の待ち行列の上限。溢れたときだけ効きます）
- `net.ipv4.ip_local_port_range`（外への接続に使う番号の一覧。使い捨て接続の上限で、`TIME-WAIT` と対で見ます）
- HTTP と unix socket の差分（同じ台の中なら unix が速いです。同じ台でしか使えません）
- gunicorn の worker プロセス数（少なすぎは詰まる、多すぎは CPU / メモリ / DB を圧迫します）
- ファイルディスクリプタ（同時に開けるファイルや接続の数。FD と略します）の上限数（足りないときだけ効きます。普段は動きません）

- ゴールは、変えた 1 項目の前後を言えることです。
- 見るのは、`oha` の p99 と公式ベンチの点数、溢れの兆候（`ListenOverflows` / `TIME-WAIT` / `established`）です。
- 「上げた」だけでは、終わりません。
- 動かない項目は、「動かない」と書いて戻します。それも結果です。

- [0060-static-cache.md](0060-static-cache.md) の続きです。
- Python（`isu-python.service`）で進めます。

- 順番: ルール固定（1.）→ somaxconn（2.）→ port 範囲（3.）→ unix 差分（4.）→ worker 数（5.）→ FD 上限（6.）→ ベンチ判定（7.）。
- 1 つずつ進めます。
- `fail`（エラーの件数）が 0 でない点数は、比べません。同時に 2 項目変えません。
- 出口は [0080-app-tuning.md](0080-app-tuning.md) です。

- 点数は、`~/bench-notes/scores.txt` に 1 行書きます。変えた値も一緒に書きます。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  oha_p99=  note=infra-baseline" >> ~/bench-notes/scores.txt
```

| 回 | score | oha_p99 | 溢れ指標 | メモ |
| --- | --- | --- | --- | --- |
| ベースライン（0060） | 28660 | 静的 2.18ms | overflow=0 tw=5 | 0060 直後。土台無調整（worker 1） |
| §2 `somaxconn` 8192 | —（oha のみ） | 静的 1.23ms | overflow=0（前後不変） | 動かない→4096 に戻した |
| §3 port 範囲 | —（見るだけ） | — | tw=5〜571（上限 470rps に余裕） | 変更なし |
| §4 unix 差分 | —（計測のみ） | — | — | TCP 直結 12.6ms vs nginx 経由 0.9ms。切替なし |
| §5 worker 1→4 | 40682 | 動的 115.94ms（w1 時 162.49ms） | tw=243→4360、overflow=0 | +12022。残す |
| §6 FD 上限 | —（見るだけ） | — | fd 12/524288、枯渇ログなし | 変更なし |
| §7 最終 | 40682 | 動的 115.94ms / 静的 1.24ms | overflow=0 | worker 4 だけ残し |

## 1. ルールと測り方を固定する

- 土台の変更は、「変えた直後に壊れる」ことが多いです。
- 変える前に、戻し方を決めます。

- `sysctl`（OS の設定を変えるコマンド）は作業機で `backup/s1/etc/sysctl.d/99-isucon-*.conf` に書いて push → サーバーで pull → cp で反映 → `sudo sysctl -p` で適用します。`sysctl -w` だけは再起動で消えます。残したいものだけファイルに残します
- systemd の変更は本体（`/lib/systemd/system/isu-python.service`）を触らず drop-in（設定の追加ファイル。`/etc/systemd/system/isu-python.service.d/*.conf`）にします。`ExecStart=` の空行は「元の行を消す」ためです。忘れると新旧 2 行になって起動しません
- nginx の変更は site conf の対象箇所だけにします。変える前に `.orig`（変更前のコピー）を残します（[0110-split-1.md](0110-split-1.md) と同じです）
- 変えたら `curl` で疎通を確認してから `oha`、最後に公式ベンチを回します（`:8080` 直結も同じ台で打てます）

```bash
# 今の土台を控える（ベースラインの一部）
sysctl net.core.somaxconn net.ipv4.ip_local_port_range
nproc
free -h
ulimit -n
systemctl cat isu-python.service | grep -E 'ExecStart|LimitNOFILE'
systemctl show isu-python.service -p LimitNOFILE
```

- 疎通確認は、1 台でまとめてやります。
- nginx（`:80`）と gunicorn（`:8080`）の両方が、同じ台にあります。

```bash
# 1 台で打つ
sudo nginx -t
curl -fsS -o /dev/null -m 5 http://127.0.0.1/login
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/login
```

### 観察すること

- ベースラインの点数と `oha -n 1000 -c 20` の p99 を控えました
- 変えるファイル（sysctl drop-in / systemd drop-in / site conf のどれか）を言えます
- 戻しコマンド（ファイル削除 + `daemon-reload` + restart / reload）を先に言えます

<a id="2-netcoresomaxconn溢れたときだけ効く"></a>

## 2. `net.core.somaxconn`（溢れたときだけ効く）

- `somaxconn` は、接続の待ち行列（listen ソケット）の上限です。
- 溢れた分は、受け付けられず落とされます。
- 溢れていないのに上げても、点数は動きません。
- 「動かない」は、正常です。

```bash
sysctl net.core.somaxconn
ss -ltn | head -20
# 溢れの有無。Load が増えて ListenOverflows / ListenDrops が増える
nstat -az 2>/dev/null | grep -i -E 'ListenOverflows|ListenDrops'
```

- 負荷をかけてから、もう一度見ます（ベンチマーカーの前に `oha` で十分です）。

```bash
# nginx の待ち行列を確認する。重い動的経路を長時間回さない
oha -z 10s -c 50 --timeout 5s --no-tui http://127.0.0.1/css/style.css | tail -15
nstat -az 2>/dev/null | grep -i -E 'listen' | head
ss -ltn | grep -E ':80|:8080'
```

- 溢れ（`ListenOverflows` が増える、または `oha` が `connection refused` / reset を出す）があったときだけ、上げます。
- **§1 で控えた値より大きく**します。

`backup/s1/etc/sysctl.d/99-isucon-somaxconn.conf`:

```text
net.core.somaxconn = 8192
```

```bash
# 作業機
git pull --ff-only
mkdir -p backup/s1/etc/sysctl.d
# 上の内容を配置する
git add -A && git commit -m "tune: somaxconn を 8192 にする" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s1/etc/sysctl.d/99-isucon-somaxconn.conf /etc/sysctl.d/
sudo sysctl -p /etc/sysctl.d/99-isucon-somaxconn.conf
sysctl net.core.somaxconn
# アプリと nginx の listen を張り直してから測り直す
sudo systemctl restart isu-python.service
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
oha -z 10s -c 50 --timeout 5s --no-tui http://127.0.0.1/css/style.css | tail -15
```

- 溢れが無いのに大きくしても点数はほぼ動きません。そのときはファイルを消して戻します（下の「戻す」手順です）。「効かない設定」と書いて次へ進みます
- nginx 側の `listen 80 backlog=...` は溢れが無いなら付けません（ノイズになります）
- `net.core.netdev_max_backlog` はネットワークカード受信側の話なので触りません

- 戻します（作業機で消して、サーバーに反映します）:

```bash
# 作業機
git pull --ff-only
git rm backup/s1/etc/sysctl.d/99-isucon-somaxconn.conf
git add -A && git commit -m "tune: somaxconn を戻す" && git push
```

```bash
# サーバーで反映。値は §1 で控えたもの（Ubuntu 24.04 の既定は 4096）
git pull --ff-only
sudo rm -f /etc/sysctl.d/99-isucon-somaxconn.conf
sudo sysctl -w net.core.somaxconn=4096
sysctl net.core.somaxconn
```

### 観察すること

- 溢れの有無を `nstat` / `oha` の失敗率で言えます（想像で上げず、数字で判断します）
- 上げた前後で `oha` の p99 と失敗数が変わったか言えます。変わらなければ戻しました
- `backlog` を同時にいじっていません（`somaxconn` 単独の差です）

<a id="3-netipv4iplocalportrange見るだけ広げる"></a>

## 3. `net.ipv4.ip_local_port_range`（見るだけ→広げる）

- 外向き接続（使い捨ての HTTP クライアント、DB / memcached への新規接続）が使う、一時ポート（接続に一時的に割り当てる番号）の範囲です。
- `TIME-WAIT`（使い終わった接続が 60 秒残る状態）が溜まると、範囲が狭いほど枯渇します。
- [0040-http-client.md](0040-http-client.md) §2 の使い捨て再現と、対で見ます。

```bash
cat /proc/sys/net/ipv4/ip_local_port_range
sysctl net.ipv4.ip_local_port_range
ss -Htan state time-wait | wc -l
ss -s | head -10
```

- 枯渇の目安: 範囲の総数 ÷ 60 秒が、上限 rps です。
- 既定 `32768 60999`（約 28k）なら、約 470 rps です。
- 使い回していれば、詰まりません。
- [0040-http-client.md](0040-http-client.md) の使い回しが済んで `time-wait` が小さいなら、ここは「見るだけ」で次へ進みます。

広げるときだけ、次の 1 つをします。

`backup/s1/etc/sysctl.d/99-isucon-ports.conf`:

```text
net.ipv4.ip_local_port_range = 1024 65535
```

```bash
# 作業機
git pull --ff-only
mkdir -p backup/s1/etc/sysctl.d
# 上の内容を配置する
git add -A && git commit -m "tune: port 範囲を広げる" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s1/etc/sysctl.d/99-isucon-ports.conf /etc/sysctl.d/
sudo sysctl -p /etc/sysctl.d/99-isucon-ports.conf
cat /proc/sys/net/ipv4/ip_local_port_range
```

- `1024` 未満は well-known ポート（http の 80 番など、用途が決まっている番号）とぶつかるので範囲に含めません。範囲の下限はこの値にします
- `tcp_tw_reuse` / `tcp_tw_recycle` は触りません。範囲と使い回しの 2 つで足りると判断します
- 効果の確認は使い捨て再現でやります（[0040-http-client.md](0040-http-client.md) §2 の noreuse を回し、`time-wait` 増分と失敗率を見ます）。使い回し済みの本番経路では差が出なくて正常です

- 戻します（作業機で消して、サーバーに反映します）:

```bash
# 作業機
git pull --ff-only
git rm backup/s1/etc/sysctl.d/99-isucon-ports.conf
git add -A && git commit -m "tune: port 範囲を戻す" && git push
```

```bash
# サーバーで反映
git pull --ff-only
sudo rm -f /etc/sysctl.d/99-isucon-ports.conf
sudo sysctl -w net.ipv4.ip_local_port_range="32768 60999"
cat /proc/sys/net/ipv4/ip_local_port_range
```

- 既定値は、ディストリビューション（OS の配布形態）で違うことがあります。
- 変える前の値を §1 で控えていれば、そちらに戻します。

### 観察すること

- 範囲の総数と `time-wait` 数から、枯渇するかどうかを計算で言えます
- 使い回し済みなら差が出ないことを確認しました（出ないのが正解の回もあります）
- `tcp_*` を同時にいじっていません（範囲単独の差です）

<a id="4-http-と-unix-socket-の差分置き場所で決める"></a>

## 4. HTTP と unix socket の差分（置き場所で決める）

- 同じ台の中の nginx → アプリは、TCP（`127.0.0.1:8080`）でも unix socket（ネットワークを介さない内部の受け渡し口）でも届きます。
- unix は、同じ台の中では速いです。ただし別台へは使えません。
- [0110-split-2.md](0110-split-2.md) の分割では、s1 だけが unix、s2 は TCP になります。

- まずは、同じ台の経路の差を測ります。

```bash
# 1 台で打つ。アプリ直結（TCP）
curl -s -o /dev/null -w 'tcp-direct login time=%{time_total}\n' http://127.0.0.1:8080/login
# nginx 経由（upstream が TCP のままなら、間に nginx が 1 回入る）
curl -s -o /dev/null -w 'via-nginx login time=%{time_total}\n' http://127.0.0.1/login
```

- gunicorn を unix socket に切り替えるのは、1 つとして扱います。この章では切り替えません。
- 切替の型（drop-in の `--bind unix:...` + `--umask 007`、ソケットの権限、nginx の upstream を unix に向ける）は、[0110-split-2.md §6](0110-split-2.md#6-s1-の-gunicornunix-socket) に置きます。
- 複数台に分けたときに s1 に unix を使うのは、この差のうち「同じ台だから効く」分を取るためです。

- 軽いパスは 1ms 未満で差は誤差になります。unix が効くのは同じ台という配置の話で、点数に出ないことも正常です
- 別台へは unix が届かないので、台をまたぐ経路は TCP + keepalive（[0040-http-client.md](0040-http-client.md) の使い回し）で埋めます

### 観察すること

- 今の upstream が TCP か unix かを言えます（`sudo nginx -T` と `ss -lxp` で判定します）
- アプリ直結と nginx 経由の 2 つの実測値を言えます
- unix は同じ台限定と、切替が 1 つであることを言えます

<a id="5-gunicorn-の-worker-プロセス数少なすぎも多すぎも遅い"></a>

## 5. gunicorn の worker プロセス数（少なすぎも多すぎも遅い）

- worker は、同時に受け持つ処理の数そのものです。
- 少ないと待ち行列ができ、多いと CPU / メモリ / DB 接続を消費します。
- `2*CPU+1` は、出発点です。正解ではありません。ベンチで比べます。

- 今の値を見ます。

```bash
nproc
free -h
systemctl cat isu-python.service | grep ExecStart
ps -ef | grep -E 'gunicorn' | grep -v grep | head -20
ps -o rss,command -C gunicorn 2>/dev/null | head -20
# DB の接続数（同じ台。見るだけ）
sudo mysql -N -e "SHOW STATUS LIKE 'Threads_connected'; SHOW VARIABLES LIKE 'max_connections';"
```

- 変えるのは、1 つだけです（`-w` だけ。スレッドやワーカーの種類は同時に変えません）。
- `ExecStart=` の空行は、元の `-b 0.0.0.0:8080` を消すためのものです。忘れると、起動しません。

- 変更は、`backup/s1/etc/systemd/system/isu-python.service.d/workers.conf` に置きます。
- unix socket に切り替えている場合は、`--bind unix:... --umask 007` に合わせます（[0110-split-2.md §6](0110-split-2.md#6-s1-の-gunicornunix-socket)）。
- worker 増の効果を、unix 化と混同しません（1 つずつ）。

`backup/s1/etc/systemd/system/isu-python.service.d/workers.conf`:

```ini
[Service]
ExecStart=
ExecStart=/home/isucon/private_isu/webapp/python/.venv/bin/gunicorn app:app -b 0.0.0.0:8080 -w 4 --log-file - --access-logfile -
```

- `ExecStart` の本体（パス、`app:app`、`-b`、ログ指定）は、`systemctl cat` の現行に合わせます。
- 上のブロックは例示です。コピペ前に、自分の 1 行に直します。
- 値は nproc から決めます（小さい方から 2 → 4 → 8）。

```bash
# 作業機
git pull --ff-only
mkdir -p backup/s1/etc/systemd/system/isu-python.service.d
# 上の内容を配置する
git add -A && git commit -m "tune: worker を 4 にする" && git push
```

- まず反映します。
- リポジトリでは、前章で使っている feature ブランチを維持します。

```bash
cd /home/isucon/private-isu-terraform && git pull --ff-only
sudo cp -a backup/s1/etc/systemd/system/isu-python.service.d/workers.conf /etc/systemd/system/isu-python.service.d/
sudo systemctl daemon-reload
sudo systemctl restart isu-python.service
ps -ef | grep -E 'gunicorn' | grep -v grep | head -20
sudo ss -lxp | grep gunicorn.sock   # unix socket にした場合だけ見る
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/login
curl -fsS -o /dev/null -m 5 http://127.0.0.1/login
```


値の振り方:

- `2*nproc+1` は出発点の目安で、固定の上限ではありません。まず2 → 4など小さい値から測り、CPU・メモリ・DB接続数で上限を決めます
- 上げたら同じ時間・並列数・URLで `oha` を回し、§7の公式ベンチを実行します。p99が下がらなくなったら1つ戻します
- worker 毎の RSS（メモリ使用量）× N が実メモリを超えたらそこで止めます。`free -h` の available が枯れる前に戻します
- `Threads_connected` が跳ねて DB が `too many connections` を出したら worker の増やしすぎが原因です。`max_connections` を同時に上げません（まず worker を戻します）

- 戻します（作業機で消して、サーバーに反映します）:

```bash
# 作業機
git pull --ff-only
git rm backup/s1/etc/systemd/system/isu-python.service.d/workers.conf
git add -A && git commit -m "tune: worker を戻す" && git push
```

```bash
# サーバーで反映
git pull --ff-only
sudo rm -f /etc/systemd/system/isu-python.service.d/workers.conf
sudo systemctl daemon-reload
sudo systemctl restart isu-python.service
ps -ef | grep gunicorn | grep -v grep | head -5
```

- 戻すときも、同じです。
- 既存の drop-in（`unix.conf` など）は、残します。

### 観察すること

- worker 数と `oha` p99 / 点数の対応表を言えます（2 → 4 → 8 の 3 点があれば十分です）
- メモリと `Threads_connected` を見て上限を決めました（点数だけで決めません）
- py-spy（処理中のどこが重いかを記録するツール）を取るなら `MainPID` + `--subprocesses` です（マスタだけ取って worker を取りこぼしません）

<a id="6-ファイルディスクリプタの上限数足りないときだけ効く"></a>

## 6. ファイルディスクリプタの上限数（足りないときだけ効く）

- 足りているときに上げても、点数は動きません。
- 枯渇すると、`Too many open files` で 502 / 500 になります。
- 枯渇しているかの切り分けが、ゴールです。

```bash
ulimit -n
systemctl show isu-python.service -p LimitNOFILE
PID="$(systemctl show -p MainPID --value isu-python.service)"
grep -i 'open files' /proc/$PID/limits
sudo cat /proc/$PID/limits | head -20
# 開いている数の今（見るだけ）
sudo ls /proc/$PID/fd | wc -l
sudo ss -s | head -10
# nginx と mysqld も見るだけ
grep -rn 'worker_rlimit_nofile' /etc/nginx/nginx.conf /etc/nginx/conf.d/ 2>/dev/null | head
sudo mysql -N -e 'SHOW VARIABLES LIKE "open_files_limit";'   # MySQL の上限（同じ台。見るだけ）
```

- 枯渇を検知します（出ていなければ、「足りている」です）。

```bash
sudo journalctl -u isu-python.service --grep='Too many open files' --no-pager | head -5
sudo journalctl -u nginx.service --grep -i 'too many open' --no-pager | head -5
dmesg | grep -i -E 'too many|open files|VFS' | tail -5
```

- [0010-setup.md](../010_common/0010-setup.md) の「権限」節で、`isucon` の `nofile 65535` は済んでいます。
- unit 側の `LimitNOFILE` が小さい（例: 1024）ときだけ、上げます。
- drop-in を作ります。

`backup/s1/etc/systemd/system/isu-python.service.d/nofile.conf`:

```ini
[Service]
LimitNOFILE=65535
```

```bash
# 作業機
git pull --ff-only
mkdir -p backup/s1/etc/systemd/system/isu-python.service.d
# 上の内容を配置する
git add -A && git commit -m "tune: LimitNOFILE を上げる" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s1/etc/systemd/system/isu-python.service.d/nofile.conf /etc/systemd/system/isu-python.service.d/
sudo systemctl daemon-reload
sudo systemctl restart isu-python.service
systemctl show isu-python.service -p LimitNOFILE
grep -i 'open files' /proc/$(systemctl show -p MainPID --value isu-python.service)/limits
```

- 戻します（作業機で消して、サーバーに反映します）:

```bash
# 作業機
git pull --ff-only
git rm backup/s1/etc/systemd/system/isu-python.service.d/nofile.conf
git add -A && git commit -m "tune: LimitNOFILE を戻す" && git push
```

```bash
# サーバーで反映
git pull --ff-only
sudo rm -f /etc/systemd/system/isu-python.service.d/nofile.conf
sudo systemctl daemon-reload
sudo systemctl restart isu-python.service
```

- 上げても普段の点数は動きません。動かないのが正常です。枯渇していた回だけ `fail` / 502 が消えます
- `ulimit -n`（シェルの値）と `LimitNOFILE`（unit の値）は別物です。サービスに効くのは後者です。シェルで上げても unit は変わりません
- nginx の `worker_rlimit_nofile` と mysqld の `open_files_limit` は見るだけです。アプリの `fail` が FD 起因と決まってから触ります。同時に上げません
- [0080-app-tuning.md](0080-app-tuning.md) の FD 節はここの確認で済ませます。[0080-app-tuning.md](0080-app-tuning.md) では新規に上げません

### 観察すること

- `LimitNOFILE` の今とプロセス実効値（`/proc/<pid>/limits`）を両方言えます
- 枯渇ログの有無を言えます（無ければ「足りている」です）
- 上げた前後で点数が動かないことを確認しました（動かないのが正解の回もあります）

## 7. 公式ベンチで判定する

- §2〜§6 のどれか 1 つだけ残します。他は、戻した状態にします。
- その状態で、公式ベンチを 1 本回します。
- bench-prep は [0020-measure.md](0020-measure.md) と同じです（nginx のログは `mv` + `reopen`、slow log は `flush-logs`。同じ台です）。

- 先に slow log を回転してから、ベンチを実行します。

```bash
# ベンチ前（slow log の回転）
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysql -e 'FLUSH SLOW LOGS'
```

```bash
# 同じ台で打つ
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
fi
sudo nginx -s reopen
nstat -az 2>/dev/null | grep -i listen | tee /tmp/nstat-before.txt | head -5
TW_BEFORE=$(ss -Htan state time-wait | wc -l)
echo "tw_before=$TW_BEFORE"
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
TW_AFTER=$(ss -Htan state time-wait | wc -l)
nstat -az 2>/dev/null | grep -i listen | tee /tmp/nstat-after.txt | head -5
echo "tw_before=$TW_BEFORE tw_after=$TW_AFTER"
echo "$(date -Iseconds)  score=  pass=  fail=  oha_p99=  note=infra-<変えた項目>" >> ~/bench-notes/scores.txt
```

実測サンプル（gunicorn worker を 1 → 4 に変えた回）: `{"pass":true,"score":40682,"success":36068,"fail":0,"messages":[]}`（[0060-static-cache.md](0060-static-cache.md) の 28660 から +12022、`fail=0`）。

- 残す変更は、`backup/s1` に積みます（[0020-measure.md](0020-measure.md) の「残す変更は backup/s1 に積む」の流儀）。

- 判定は、点数＋`oha` p99＋溢れの兆候＋alp です。点だけを見ません。

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
diff /tmp/nstat-before.txt /tmp/nstat-after.txt || true
```

- 効かなければ、`scores.txt` に 1 行残して、§2〜§6 の戻し手順で戻します。

#### 実行例：

- 2026-10-13 の実測（`c7a.large` 1 台 / `ap-northeast-1` / 単一構成 / `nproc=2` / メモリ 3.7Gi / avail 2.7Gi）。
- 条件（日付、c7a.large 1 台、ap-northeast-1、nproc=2、メモリ 3.7Gi、単一構成）を明記します。
- gunicorn worker を **1 → 4**（`-w 4`、systemd drop-in）にしました。
- ベンチ: `{"pass":true,"score":40682,"success":36068,"fail":0,"messages":[]}`（[0060-static-cache.md](0060-static-cache.md) の 28660 から +12022、fail=0）。

```text
# 変更前
ExecStart=...gunicorn app:app -b 0.0.0.0:8080 --log-file - --access-logfile -
gunicorn プロセス数 = 2（master + worker 1）
RSS: master 28MB + worker 38MB

# 変更後
ExecStart=...gunicorn app:app -b 0.0.0.0:8080 -w 4 --log-file - --access-logfile -
gunicorn プロセス数 = 5（master + worker 4）
RSS: master 28MB + worker 38MB×4（合計約 180MB）、available 2.7Gi
Threads_connected = 1（アイドル時）/ max_connections = 151
```

`oha -n 200 -c 20 http://127.0.0.1/`（動的 `/`、同条件で前後比較）:

| | worker 1 | worker 4 |
| --- | --- | --- |
| p99 | 162.49ms | **115.94ms** |
| Requests/sec | 129.8 | **223.3** |
| Success | 100% | 100% |

- `oha -n 1000 -c 20`（静的 `/css/style.css`）は、p99 1.24ms で不変でした。
- nginx 直接配信のため、worker 数と無関係です。これが正しいです。

alp の変化（ベンチ 1 本分の access.log。`/image` と `/` の AVG が落ちました）:

| URI | 0060 ベンチ | worker 4 ベンチ |
| --- | --- | --- |
| `/` AVG | 0.048 秒 | **0.029 秒** |
| `/image/[0-9]+` AVG | 0.009 秒 | **0.003 秒** |
| `/posts/[0-9]+` AVG | 0.041 秒 | **0.011 秒** |

| 状態 | score | fail | 備考 |
| --- | --- | --- | --- |
| 0060 静的 expires 1h＋直接配信 | 28660 | 0 | |
| **0070 gunicorn worker 1→4** | **40682** | **0** | **+12022（最大の効果。2026-10-13、`c7a.large` 1 台 / 単一構成 / `nproc=2`）** |

- **worker 数が、この構成での最大のボトルネックでした。**
- gunicorn の既定は `-w 1` です。1 プロセスが、1 リクエストずつ処理します。
- 2 vCPU でも、DB の応答待ちが発生します。worker を増やすと、待ち時間を別のリクエストで埋められます。
- RSS 合計約 180MB に対して available 2.7Gi です。`max_connections = 151` に対して接続数は 1 桁です。どちらも余裕があります。
- `somaxconn = 8192` は、overflow=0 で点数に影響しませんでした。戻しました（正常）。
- 残す変更は、worker 4 だけです。

### 出力例

- 2026-10-13 の実測（`c7a.large` 1 台 / `ap-northeast-1` / 単一構成 / `nproc=2` / メモリ 3.7Gi / avail 2.7Gi）。
- 条件（日付、c7a.large 1 台、ap-northeast-1、nproc、メモリ、単一構成）を明記します。

- **効いたのは、worker 数だけ**でした。
- 他は、「見るだけ」か「動かない→戻す」で終わっています。
- 同時に 2 項目は、変えていません。

| ノブ | 変更 | score | fail | 判定 |
| --- | --- | --- | --- | --- |
| ベースライン（0060） | — | 28660 | 0 | |
| [§2](#2-netcoresomaxconn溢れたときだけ効く) `somaxconn` | 4096 → 8192 → 4096 に戻す | —（oha のみ） | — | **動かない**（overflow=0 のまま） |
| [§3](#3-netipv4iplocalportrange見るだけ広げる) `ip_local_port_range` | なし（32768-60999 のまま） | — | — | **見るだけ**（TIME-WAIT 5〜571、枯渇上限 470rps に余裕） |
| [§4](#4-http-と-unix-socket-の差分置き場所で決める) unix vs HTTP | なし（TCP のまま） | — | — | **計測のみ**（直結 12.6ms vs nginx 経由 0.9ms）。切替なし |
| **[§5](#5-gunicorn-の-worker-プロセス数少なすぎも多すぎも遅い) worker** | **1 → 4** | **40682** | **0** | **+12022（最大の効果）** |
| [§6](#6-ファイルディスクリプタの上限数足りないときだけ効く) FD 上限 | なし | — | — | **見るだけ**（12 / 524288、枯渇ログなし） |
| **§7 最終** | worker 4 だけ残し | **40682** | **0** | |

§1（ルール固定・ベースライン）:

```text
sysctl: somaxconn = 4096 / ip_local_port_range = 32768 60999
nproc = 2 / Mem 3.7Gi（available 2.7Gi）
shell ulimit -n = 1024 / unit LimitNOFILE = 524288
ExecStart = ...gunicorn app:app -b 0.0.0.0:8080 --log-file - --access-logfile -（-w 無し = worker 1）
疎通: :80/login ok / :8080/login ok
oha -n 1000 -c 20（静的）: 成功率 100% / p99 2.18ms / 42318 req/s
```

§2（`somaxconn`）:

```text
変更前 somaxconn = 4096   overflow = 0（TcpExtListenOverflows 0 / ListenDrops 0）
変更後 somaxconn = 8192   overflow = 0（不変）
ss -ltn の Recv-Q = 0（:80 も :8080 も待ち行列が積もっていない）
oha -n 1000 -c 20（静的）p99: 2.18ms → 1.23ms（静的は nginx 直接配信のため土台の影響外。差はノイズ）
oha -z 10s -c 50（静的）p99: 2.18ms（成功率 100%、568436 応答）
```

- overflow が 0 のままです。[冒頭](#0070-infra-params-土台の-1-ノブずつカーネルソケットworkerfd) の「溢れていないのに上げても点数は動きません。「動かない」は正常です」に該当します。
- ファイルを消して、`sysctl -w net.core.somaxconn=4096` に戻しました。

§3（`ip_local_port_range`）:

```text
変更前 = 32768 60999   総数 28231   枯渇上限 28231/60 = 470 rps
TIME-WAIT = 5（アイドル時）→ 571（oha 直後。一時的な上昇で枯渇せず）
```

- TIME-WAIT が小さく、範囲に対して 2% 程度です。範囲を広げても、差は出ません。
- [§3](#3-netipv4iplocalportrange見るだけ広げる) の「使い回していれば詰まりません。`time-wait` が小さいなら、ここは『見るだけ』で次へ進みます」に該当します。
- 変更なしです。

§4（unix socket vs HTTP）:

```text
TCP 直結 :8080/login       -> total=0.012595s（毎回新規 TCP＋アプリ処理）
nginx 経由 :80/login       -> total=0.000906s（upstream keepalive 32 の再利用で速い）
upstream app = server 127.0.0.1:8080（TCP のまま）
gunicorn listen = TCP :8080 のみ（unix socket なし）
```

- 差は、配置（同じ台）と keepalive 再利用の話です。
- unix 化は 1 つとして扱うため、この章では切り替えません（[§4](#4-http-と-unix-socket-の差分置き場所で決める) どおり）。
- 変更なしです。

§5（worker 数）:

```text
変更前: nproc=2  gunicorn 2 プロセス（master + worker 1）
        master RSS 28MB + worker 38MB    available 2.7Gi
変更後: -w 4  gunicorn 5 プロセス（master + worker 4）
        master RSS 28MB + worker 38MB×4（合計約 180MB）  available 2.7Gi
        Threads_connected = 1（アイドル時）  max_connections = 151
oha -n 200 -c 20（動的 /）: p99 162.49ms → 115.94ms / 129.8 → 223.3 req/s
oha -n 1000 -c 20（静的）: p99 1.24ms（不変。nginx 直接配信のため）
```

- **gunicorn の既定は `-w 1`** です。
- 2 vCPU でも、worker が 1 つだと、DB の応答待ちで並列化できません。
- `-w 4` にすると、応答待ちの時間を別の worker が埋めます。
- メモリ（180MB vs avail 2.7Gi）も DB 上限（1 桁 vs 151）も余裕があります。この値を残します。
- 8 worker は、未測定です。断定しません。

§6（FD 上限）:

```text
shell ulimit -n = 1024 / unit LimitNOFILE = 524288
プロセス実効値（/proc/<MainPID>/limits）: Max open files 1024（soft）/ 524288（hard）
開いている fd = 12
journalctl（isu-python / nginx）に 'Too many open files' なし
```

- 枯渇ログが無く、開いている数も、上限を大きく下回ります。
- drop-in を作りませんでした（§6 の「小さいときだけ上げます」の条件を満たさない）。

§7 の最終ベンチ: `{"pass":true,"score":40682,"success":36068,"fail":0,"messages":[]}`

alp の Sum 上位（worker 4 化後。0060 ベンチ比で `/` と `/image` と `/posts/<id>` の AVG が落ちました）:

| COUNT | URI | SUM | AVG | P99 |
| --- | --- | --- | --- | --- |
| 2325 | `/` | 68.322 | 0.029 | 0.060 |
| 17446 | `/image/[0-9]+` | 59.578 | 0.003 | 0.031 |
| 2287 | `/posts/[0-9]+` | 26.292 | 0.011 | 0.038 |

cache 分布（`/image` の proxy_cache 継続＋静的 304 継続）:

```text
18495 cache:-
 2774 cache:BYPASS
  609 cache:EXPIRED
12583 cache:HIT
 1480 cache:MISS
2312 GET /css/style.css   1 回 200 + 2311 回 304（以下 3 パスも同様）
```

- ListenOverflows 0 / ListenDrops 0。
- TIME-WAIT 243（ベンチ前）→ 4360（ベンチ後）。ベンチ由来の一時上昇です。枯渇上限 470rps に対して、範囲 28231 に余裕があります。

### この節でつまずいた点

1. **§2 の `oha -n 2000 -c 50 http://127.0.0.1/` が 1 時間を超えます**（:73）

   - この検証では、重い経路への負荷が終わらず、中止しました。
   - `-c 50` は並列実行です。平均応答時間 × 2000 では、所要時間を予測できません。
   - 今は §2 を、静的 URL への 10 秒負荷に直しています。リクエストごとに、5 秒のタイムアウトを付けます。
   - gunicorn 側の待ち行列を見るときは、同じ制限で s2 の動的経路を別に測ります。

2. **§5 の `workers.conf` は s2 専用ですが、分割構成では s1 にも必要です**（:246）

   - 手順書は、`backup/s2/.../workers.conf` に次を書いています。

   ```ini
   ExecStart=/home/isucon/private_isu/webapp/python/.venv/bin/gunicorn app:app -b 0.0.0.0:8080 -w 4 --log-file - --access-logfile -
   ```

   - 分割構成では、**s1（unix socket）も同じ変更が必要**です。
   - s1 は [0110-split-2.md](0110-split-2.md) §6 の `unix.conf` で `ExecStart` を上書きしています。
   - `workers.conf` も、unix bind を含む内容にしなければなりません。

   ```ini
   [Service]
   WorkingDirectory=/home/isucon/private_isu/webapp/python
   ExecStart=
   ExecStart=/home/isucon/private_isu/webapp/python/.venv/bin/gunicorn \
     --bind unix:/home/isucon/tmp/gunicorn.sock \
     --umask 007 \
     -w 4 \
     app:app
   ```

   - drop-in の読み込み順（`restart.conf` → `unix.conf` → `workers.conf`）で、後勝ちになります。
   - **s1 側を忘れると、worker だけ 1 のまま**になります。

3. **§1 の `:8080` 直結が s1 で失敗します**（:49-50）

   ```bash
   curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/  # :8080 がある台で打つ（s1 は unix socket なので :80 のみ）
   ```

   - コメントは、「s1 は unix socket なので :80 のみ」と正しく注記しています。
   - ただし、コマンド自体はコピペで失敗します。
   - §1 の 3 行目に `:8080` が混ざっています。**s1 で実行する場合は行ごと消す**か、台を分けて実行してください。

### 観察すること

- 残した 1 項目の値と理由（p99 / overflow / tw のどれが動いたか）を言えます
- alp の Sum が落ちたか、落ちないなら土台は詰まっていません（アプリ / DB の仕事に戻ります）
- `fail` が 0 です。`fail` が出たら worker の上げすぎか unix 化の権限を疑います

## 8. トラブルシュート

### `sysctl -p` が失敗する

- drop-in の書式を確認します（`key = value` の `=` 忘れ、引用符の閉じ忘れなど）。`cat` で 1 行確認してから `-p` します
- ファイル名が `*.conf` か確認します（`/etc/sysctl.d/` は `.conf` だけ読みます）
- `sysctl -w` で直値は入るか切り分けます。入るならファイルの問題、入らないならキー名の問題です

### systemd drop-in で起動しない

- `ExecStart=` の空行忘れが一番多いです（新旧 2 行でエラー）。`systemctl cat isu-python.service` で 2 本出ていないか見ます
- `daemon-reload` を忘れています。`restart` だけでは drop-in が載りません
- `ExecStart` のパス・モジュールが現行と違います。コピペ前に `systemctl cat` の 1 行に直します

### unix 化で 502

- `ls -l /home/isucon/tmp/gunicorn.sock` が無い → gunicorn が落ちています。`journalctl -u isu-python.service -n 40` を見ます
- sock はあるが 502 → ソケットの所有者・グループ・モードと、親ディレクトリの通過権限を見ます。この構成のソケットは `isucon:isucon`、モード 770 です。`www-data` を `isucon` グループに追加し、そのあと nginx を restart します
- `proxy_pass` の向き違い（TCP のまま / upstream 名の typo）です。`grep -rn proxy_pass /etc/nginx/sites-enabled/` を見ます

### worker を増やしたら遅くなる / 落ちる

- 正常です（増やして悪化を確かめる試験なので、遅くなるのが正解のこともあります）。それとも OOM（メモリ不足で強制終了）か切り分けます。`free -h` と `dmesg | tail`（oom-killer）を見ます
- DB の `too many connections` なら worker を戻します。`max_connections` を同時に上げません
- CPU を mysqld が使い切っているなら worker の仕事ではありません。alp / slp に戻ります

### FD を上げても変わらない

- 正常です。枯渇ログが無ければ足りていたということです。ファイルを残さず戻します
- `ulimit -n` を上げても unit は変わりません。効かせるのは `LimitNOFILE` + `daemon-reload` + restart です
