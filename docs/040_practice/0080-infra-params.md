# 0080 土台の 1 ノブずつ（カーネル・ソケット・worker・FD）

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）はローカル環境の private-isu-terraform リポジトリにて実行します。

アプリの前にある土台を 1 項目ずつ変えて、`oha` の p99 と公式ベンチの点数で確認します。

- `net.core.somaxconn`（listen の待ち行列。溢れたときだけ効きます）
- `net.ipv4.ip_local_port_range`（使い捨て接続の上限。`TIME-WAIT` と対で見ます）
- HTTP と unix socket の差分（同一ホストなら unix が速いです。同一ホストでしか使えません）
- gunicorn の worker プロセス数（少なすぎは詰まる、多すぎは CPU / メモリ / DB を圧迫します）
- ファイルディスクリプタの上限数（足りないときだけ効きます。普段は動きません）

ゴールは「変えた 1 項目の前後で、`oha` の p99 と公式ベンチの点数、溢れ指標（`ListenOverflows` / `TIME-WAIT` / `established`）」を言えることです。「上げた」だけでは終わりません。動かない項目は「動かない」と書いて戻します。それも結果です。

[0070-static-cache.md](0070-static-cache.md) の続きです。Python（`isu-python.service`）で進めます。

順番: ルール固定（1.）→ somaxconn（2.）→ port 範囲（3.）→ unix 差分（4.）→ worker 数（5.）→ FD 上限（6.）→ ベンチ判定（7.）。1 手ずつ進めます。`fail` が 0 でない点数は比べません。同時に 2 項目変えません。出口は [0090-app-tuning.md](0090-app-tuning.md) です。

点数は `~/bench-notes/scores.txt` に 1 行。変えた値を一緒に書く:

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  oha_p99=  note=infra-baseline" >> ~/bench-notes/scores.txt
```

| 回 | score | oha_p99 | 溢れ指標 | メモ |
| --- | --- | --- | --- | --- |
| ベースライン |  |  | 例: overflow=0 tw=40 | [0030-measure.md](0030-measure.md) までの状態。土台無調整 |
| 1 手目 |  | 例: 0.09s | 例: overflow=0 tw=38 | 例: somaxconn 1024 |

## 1. ルールと測り方を固定する

土台の変更は「変えた直後に壊れる」ことが多いです。変える前に戻し方を決めます。

- `sysctl` は作業機で `backup/s1/etc/sysctl.d/99-isucon-*.conf` に書いて push → サーバーで pull → cp で反映 → `sudo sysctl -p` で適用します。`sysctl -w` だけは再起動で消えます。永続化したいものだけファイルに残します
- systemd の変更は本体（`/lib/systemd/system/isu-python.service`）を触らず drop-in（`/etc/systemd/system/isu-python.service.d/*.conf`）にします。`ExecStart=` の空行は「元を消す」ためです。忘れると新旧 2 行になって起動しません
- nginx の変更は site conf の対象箇所だけにします。変える前に `.orig` を残します（[0020-split-1.md](0020-split-1.md) と同じです）
- 変えたら `curl` で疎通を確認してから `oha`、最後に公式ベンチを回します（`:8080` 直結は s2 で打ちます）

```bash
# 今の土台を控える（ベースラインの一部）
sysctl net.core.somaxconn net.ipv4.ip_local_port_range
nproc
free -h
ulimit -n
systemctl cat isu-python.service | grep -E 'ExecStart|LimitNOFILE'
systemctl show isu-python.service -p LimitNOFILE
sudo nginx -t
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/  # :8080 がある台で打つ（s1 は unix socket なので :80 のみ）
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- ベースラインの点数と `oha -n 1000 -c 20` の p99 を控えました
- 変えるファイル（sysctl drop-in / systemd drop-in / site conf のどれか）を言えます
- 戻しコマンド（ファイル削除 + `daemon-reload` + restart / reload）を先に言えます

## 2. `net.core.somaxconn`（溢れたときだけ効く）

`somaxconn` は listen ソケットの待ち行列の上限です。溢れた分は受け付けられず落とされます。溢れていないのに上げても点数は動きません。「動かない」は正常です。

```bash
sysctl net.core.somaxconn
ss -ltn | head -20
# 溢れの有無。Load が増えて ListenOverflows / ListenDrops が増える
nstat -az 2>/dev/null | grep -i -E 'ListenOverflows|ListenDrops'
```

負荷をかけてからもう一度見ます（ベンチマーカーの前に `oha` で十分です）:

```bash
oha -n 2000 -c 50 --no-tui http://127.0.0.1/ | tail -15
nstat -az 2>/dev/null | grep -i -E 'listen' | head
ss -ltn | grep -E ':80|:8080'
```

溢れ（`ListenOverflows` が増える、または `oha` が `connection refused` / reset を出す）があったときだけ上げます。**§1 で控えた値より大きく**します。

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
sudo systemctl reload nginx 2>/dev/null || sudo systemctl restart nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
oha -n 2000 -c 50 --no-tui http://127.0.0.1/ | tail -15
```

- 溢れが無いのに大きくしても点数はほぼ動きません。そのときはファイルを消して戻します（下の「戻す」手順です）。「効かないノブ」と書いて次へ進みます
- nginx 側の `listen 80 backlog=...` は溢れが無いなら付けません（ノイズになります）
- `net.core.netdev_max_backlog` は NIC 受信側の話なので触りません

戻す（作業機で消してサーバーに反映します）:

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

## 3. `net.ipv4.ip_local_port_range`（見るだけ→広げる）

外向き接続（使い捨ての HTTP クライアント、DB / memcached への新規接続）が使う一時ポートの範囲です。`TIME-WAIT`（60 秒）が溜まると、範囲が狭いほど枯渇します。[0050-http-client.md](0050-http-client.md) §2 の使い捨て再現と対で見ます。

```bash
cat /proc/sys/net/ipv4/ip_local_port_range
sysctl net.ipv4.ip_local_port_range
ss -tan 'state time-wait' | wc -l
ss -s | head -10
```

枯渇の目安: 範囲の総数 ÷ 60 秒が上限 rps です。既定 `32768 60999`（約 28k）なら約 470 rps です。使い回していれば詰みません。[0050-http-client.md](0050-http-client.md) の使い回しが済んで `time-wait` が小さいなら、ここは「見るだけ」で次へ進みます:

広げるときだけ（1 手）します。

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

- `1024` 未満は well-known ポートとぶつかるので範囲に含めません。範囲の下限はこの値にします
- `tcp_tw_reuse` / `tcp_tw_recycle` は触りません。範囲と使い回しの 2 つで足りると判断します
- 効果の確認は使い捨て再現でやります（[0050-http-client.md](0050-http-client.md) §2 の noreuse を回し、`time-wait` 増分と失敗率を見ます）。使い回し済みの本番経路では差が出なくて正常です

戻す（作業機で消してサーバーに反映します）:

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

既定値はディストリビューションで違うことがあります。変える前の値を §1 で控えていればそちらに戻します。

### 観察すること

- 範囲の総数と `time-wait` 数から、枯渇するかどうかを計算で言えます
- 使い回し済みなら差が出ないことを確認しました（出ないのが正解の回もあります）
- `tcp_*` を同時にいじっていません（範囲単独の差です）

## 4. HTTP と unix socket の差分（置き場所で決める）

同一ホスト内の nginx → アプリは TCP（`127.0.0.1:8080`）でも unix socket でも届きます。unix は同一ホストでは速いですが、別ホストへは使えません。

分割後は置き場所が決まっています。全体を unix にする切替はやりません（s2 が別ホストなので unix が届きません）。やることは配置の確認と軽いパスの実測だけです:

```bash
# s1: 軽い処理は unix socket（0020-split-2 の light 構成のまま）
ls -l /home/isucon/tmp/gunicorn.sock
sudo -u www-data curl --unix-socket /home/isucon/tmp/gunicorn.sock \
  -s -o /dev/null -w 'unix-sock login time=%{time_total}\n' http://localhost/login
# s1: 同じ軽い処理を nginx 経由で
curl -s -o /dev/null -w 'via-nginx login time=%{time_total}\n' http://127.0.0.1/login
# s2: 重い処理は TCP（別ホストに置くため unix は使えない）
curl -s -o /dev/null -w 'tcp-direct login time=%{time_total}\n' http://127.0.0.1:8080/login
```

- 軽いパスは 1ms 未満で差は誤差です。unix が効くのは同一ホストという配置の話で、分割後は s1 の軽い処理がその恩恵を受けています
- 1 台ドリルで全体を unix にする切替は [0020-split-2.md](0020-split-2.md) の s1 unix と同じ型です。分割構成では s2（別ホスト）へ unix は使えないので、TCP + keepalive（[0050-http-client.md](0050-http-client.md) の使い回し）に戻します

### 観察すること

- s1 の軽い処理が unix socket で動いています（sock の存在）
- s2 の重い処理は TCP です（別ホストなので unix は使えません）
- 軽いパスの 3 つの実測値（unix 直結 / nginx 経由 / s2 直結）を言えます

## 5. gunicorn の worker プロセス数（少なすぎも多すぎも遅い）

worker は並列度そのものです。少ないと待ち行列ができ、多いと CPU / メモリ / DB 接続を消費します。`2*CPU+1` は出発点であって正解ではありません。ベンチで比べます。

今の値を見ます:

```bash
nproc
free -h
systemctl cat isu-python.service | grep ExecStart
ps -ef | grep -E 'gunicorn' | grep -v grep | head -20
ps -o rss,command -C gunicorn 2>/dev/null | head -20
# DB 側の接続数（s3 で打つ。見るだけ）
sudo mysql -N -e "SHOW STATUS LIKE 'Threads_connected'; SHOW VARIABLES LIKE 'max_connections';"
```

変えるのは 1 手だけです（`-w` だけ。スレッドやワーカー種別は同時に変えません）。`ExecStart=` の空行は元の `-b 0.0.0.0:8080` を消すためのもので、忘れると起動しません。

`backup/s2/etc/systemd/system/isu-python.service.d/workers.conf`:

```ini
[Service]
ExecStart=
ExecStart=/home/isucon/private_isu/webapp/python/.venv/bin/gunicorn app:app -b 0.0.0.0:8080 -w 4 --log-file - --access-logfile -
```

`ExecStart` の本体（パス、`app:app`、`-b`、ログ指定）は `systemctl cat` の現行に合わせます。上のブロックは例示なので、コピペ前に自分の 1 行に直します。値は nproc から決めます（小さい方から 2 → 4 → 8）。

```bash
# 作業機
git pull --ff-only
mkdir -p backup/s2/etc/systemd/system/isu-python.service.d
# 上の内容を配置する
git add -A && git commit -m "tune: worker を 4 にする" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s2/etc/systemd/system/isu-python.service.d/workers.conf /etc/systemd/system/isu-python.service.d/
sudo systemctl daemon-reload
sudo systemctl restart isu-python.service
ps -ef | grep -E 'gunicorn' | grep -v grep | head -20
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/  # :8080 がある台で打つ（s1 は unix socket なので :80 のみ）
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

値の振り方:

- `2*nproc+1` を上限の目安にします。小さい方から上げます（例: 2 → 4 → 8）。上限は超えません
- 上げたら `oha -n 1000 -c 20` と公式ベンチを回します。p99 が下がらなくなったら 1 つ戻します
- worker 毎の RSS × N が実メモリを超えたらそこで止めます。`free -h` の available が枯れる前に戻します
- `Threads_connected` が跳ねて DB が `too many connections` を出したら worker の増やしすぎが原因です。`max_connections` を同時に上げません（まず worker を戻します）

戻す（作業機で消してサーバーに反映します）:

```bash
# 作業機
git pull --ff-only
git rm backup/s2/etc/systemd/system/isu-python.service.d/workers.conf
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

### 観察すること

- worker 数と `oha` p99 / 点数の対応表を言えます（2 → 4 → 8 の 3 点があれば十分です）
- メモリと `Threads_connected` を見て上限を決めました（点数だけで決めません）
- py-spy を取るなら `MainPID` + `--subprocesses` です（マスタだけ取って worker を取りこぼしません）

## 6. ファイルディスクリプタの上限数（足りないときだけ効く）

足りているときに上げても点数は動きません。枯渇すると `Too many open files` で 502 / 500 になります。枯渇しているかの切り分けがゴールです。

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
# s3 で打つ
sudo mysql -N -e 'SHOW VARIABLES LIKE "open_files_limit";'
```

枯渇の検知（出ていなければ「足りている」です）:

```bash
sudo journalctl -u isu-python.service --grep='Too many open files' --no-pager | head -5
sudo journalctl -u nginx.service --grep -i 'too many open' --no-pager | head -5
dmesg | grep -i -E 'too many|open files|VFS' | tail -5
```

[0010-setup.md](../010_common/0010-setup.md) の「権限」節で `isucon` の `nofile 65535` は済んでいます。unit 側の `LimitNOFILE` が小さい（例: 1024）ときだけ上げます。drop-in を作ります。

`backup/s2/etc/systemd/system/isu-python.service.d/nofile.conf`:

```ini
[Service]
LimitNOFILE=65535
```

```bash
# 作業機
git pull --ff-only
mkdir -p backup/s2/etc/systemd/system/isu-python.service.d
# 上の内容を配置する
git add -A && git commit -m "tune: LimitNOFILE を上げる" && git push
```

サーバーで反映します:

```bash
git pull --ff-only
sudo cp -a backup/s2/etc/systemd/system/isu-python.service.d/nofile.conf /etc/systemd/system/isu-python.service.d/
sudo systemctl daemon-reload
sudo systemctl restart isu-python.service
systemctl show isu-python.service -p LimitNOFILE
grep -i 'open files' /proc/$(systemctl show -p MainPID --value isu-python.service)/limits
```

戻す（作業機で消してサーバーに反映します）:

```bash
# 作業機
git pull --ff-only
git rm backup/s2/etc/systemd/system/isu-python.service.d/nofile.conf
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
- [0090-app-tuning.md](0090-app-tuning.md) の FD 節はここの確認で済ませます。[0090-app-tuning.md](0090-app-tuning.md) では新規に上げません

### 観察すること

- `LimitNOFILE` の今とプロセス実効値（`/proc/<pid>/limits`）を両方言えます
- 枯渇ログの有無を言えます（無ければ「足りている」です）
- 上げた前後で点数が動かないことを確認しました（動かないのが正解の回もあります）

## 7. 公式ベンチで判定する

§2〜§6 のどれか 1 手だけ残し、他は戻した状態にして公式ベンチを 1 本回します。bench-prep は [0030-measure.md](0030-measure.md) と同じです（s1 で `mv` + `reopen`、s3 で `flush-logs`）。

```bash
# s1 で打つ
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
nstat -az 2>/dev/null | grep -i listen | tee /tmp/nstat-before.txt | head -5
TW_BEFORE=$(ss -tan 'state time-wait' | wc -l)
echo "tw_before=$TW_BEFORE"
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
TW_AFTER=$(ss -tan 'state time-wait' | wc -l)
nstat -az 2>/dev/null | grep -i listen | tee /tmp/nstat-after.txt | head -5
echo "tw_before=$TW_BEFORE tw_after=$TW_AFTER"
echo "$(date -Iseconds)  score=  pass=  fail=  oha_p99=  note=infra-<変えた項目>" >> ~/bench-notes/scores.txt
```

```bash
# s3 で打つ
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysqladmin flush-logs 2>/dev/null || sudo mysql -e 'FLUSH SLOW LOGS'
```

残す変更は台ごとの `backup/s1`・`s2`・`s3` に積みます（[0030-measure.md](0030-measure.md) の「残す変更は backup/s1・s2・s3 に積む」の流儀）。

判定は点数＋`oha` p99＋溢れ指標＋alp です。点だけ見ません:

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
diff /tmp/nstat-before.txt /tmp/nstat-after.txt || true
```

効かなければ `scores.txt` に 1 行残して、§2〜§6 の戻し手順で戻します。

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
- sock はあるが 502 → 権限です。`isucon:www-data`、`--umask 007`、`sudo usermod -aG isucon www-data` の順に見ます
- `proxy_pass` の向き違い（TCP のまま / upstream 名の typo）です。`grep -rn proxy_pass /etc/nginx/sites-enabled/` を見ます

### worker を増やしたら遅くなる / 落ちる

- 正常です（悪化を確認する逆向きの試験です）。それとも OOM か切り分けます。`free -h` と `dmesg | tail`（oom-killer）を見ます
- DB の `too many connections` なら worker を戻します。`max_connections` を同時に上げません
- CPU を mysqld が使い切っているなら worker の仕事ではありません。alp / slp に戻ります

### FD を上げても変わらない

- 正常です。枯渇ログが無ければ足りていたということです。ファイルを残さず戻します
- `ulimit -n` を上げても unit は変わりません。効かせるのは `LimitNOFILE` + `daemon-reload` + restart です
