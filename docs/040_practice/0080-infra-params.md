# 0080 土台の1ノブずつ（カーネル・ソケット・worker・FD）

アプリの前にある土台を 1 ノブずつ振って、スコアが動くか数字で確認する。

- `net.core.somaxconn`（listen の待ち行列。溢れたときだけ効く）
- `net.ipv4.ip_local_port_range`（使い捨て接続の上限。`TIME-WAIT` と対で見る）
- HTTP と unix socket の差分（同一ホストなら unix が速い。同一ホストでしか使えない）
- gunicorn の worker プロセス数（少なすぎは詰まる、多すぎは CPU / メモリ / DB を圧迫）
- ファイルディスクリプタの上限数（足りないときだけ効く。普段は動かない）

ゴールは「変えた 1 ノブの前後で、`oha` の p99 と公式ベンチの点数、溢れ指標（`ListenOverflows` / `TIME-WAIT` / `established`）」を言えること。「上げた」だけでは終わらない。動かないノブは「動かない」と書いて戻す。それも結果。

[0070-static-cache.md](0070-static-cache.md) の続き。Python（`isu-python.service`）で進める。

順番: ルール固定（1.）→ somaxconn（2.）→ port 範囲（3.）→ unix 差分（4.）→ worker 数（5.）→ FD 上限（6.）→ ベンチ判定（7.）。1 手ずつ。`fail` が 0 でない点数は比べない。同時に 2 ノブ振らない。出口は [0090-app-tuning.md](0090-app-tuning.md)。

点数は `~/bench-notes/scores.txt` に 1 行。変えた値を一緒に書く:

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  oha_p99=  note=infra-baseline" >> ~/bench-notes/scores.txt
```

| 回 | score | oha_p99 | 溢れ指標 | メモ |
| --- | --- | --- | --- | --- |
| ベースライン |  |  | 例: overflow=0 tw=40 | 0030 までの状態。土台無調整 |
| 1 手目 |  | 例: 0.09s | 例: overflow=0 tw=38 | 例: somaxconn 1024 |

## 1. ルールと測り方を固定する

土台の変更は「変えた直後に壊れる」ことが多い。変える前に戻し方を決める。

- `sysctl` は `/etc/sysctl.d/99-isucon-*.conf` に書いて `sudo sysctl -p`。`sysctl -w` だけは再起動で消える。永続化したいものだけファイルに残す
- systemd の変更は本体（`/lib/systemd/system/isu-python.service`）を触らず drop-in（`/etc/systemd/system/isu-python.service.d/*.conf`）。`ExecStart=` の空行は「元を消す」ため。忘れると新旧 2 行になって起動しない
- nginx の変更は site conf の対象箇所だけ。変える前に `.orig` を残す（[0020-split-1.md](0020-split-1.md) と同じ）
- 変えたら必ず `curl` 2 本（`:8080` 直結と `:80` 経由）で確認してから `oha`、最後に公式ベンチを回す

```bash
# 今の土台を控える（ベースラインの一部）
sysctl net.core.somaxconn net.ipv4.ip_local_port_range
nproc; free -h
ulimit -n
systemctl cat isu-python.service | grep -E 'ExecStart|LimitNOFILE'
systemctl show isu-python.service -p LimitNOFILE
sudo nginx -t
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- ベースラインの点数と `oha -n 1000 -c 20` の p99 を控えた
- 変えるファイル（sysctl drop-in / systemd drop-in / site conf のどれか）を言える
- 戻しコマンド（ファイル削除 + `daemon-reload` + restart / reload）を先に言える

## 2. `net.core.somaxconn`（溢れたときだけ効く）

`somaxconn` は listen ソケットの待ち行列の上限。接続が殺到して捌き切れないとき、溢れた分は受け付けられず落とされる。溢れていないのに上げても点数は動かない。「動かない」は正常。

```bash
sysctl net.core.somaxconn
ss -ltn | head -20
# 溢れの有無（数字が増え続けるか）
nstat -az 2>/dev/null | grep -i -E 'listen|drop' | head
cat /proc/net/netstat | tr ' ' '\n' | grep -i -E 'ListenOverflows|ListenDrops' | head
```

負荷をかけてからもう一度見る（ベンチマーカーの前に `oha` で十分）:

```bash
oha -n 2000 -c 50 --no-tui http://127.0.0.1/ | tail -15
nstat -az 2>/dev/null | grep -i -E 'listen' | head
ss -ltn | grep -E ':80|:8080'
```

溢れ（`ListenOverflows` が増える、または `oha` が `connection refused` / reset を出す）があったときだけ上げる:

```bash
echo 'net.core.somaxconn = 1024' | sudo tee /etc/sysctl.d/99-isucon-somaxconn.conf
sudo sysctl -p /etc/sysctl.d/99-isucon-somaxconn.conf
sysctl net.core.somaxconn
# アプリと nginx の listen を張り直してから測り直す
sudo systemctl restart isu-python.service
sudo systemctl reload nginx 2>/dev/null || sudo systemctl restart nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
oha -n 2000 -c 50 --no-tui http://127.0.0.1/ | tail -15
```

- 溢れが無いのに 4096 まで上げても点数はほぼ動かない。そのときはファイルを消して戻す（下の「戻す」手順）。「効かないノブ」と書いて次へ
- nginx 側の `listen 80 backlog=...` は溢れが取れてから対で見る。先に触らない。溢れが無い回の `backlog` 追加はノイズになる
- `net.core.netdev_max_backlog` は NIC 受信側の話。ここでは触らない。対象は listen 待ちだけ

戻す:

```bash
sudo rm -f /etc/sysctl.d/99-isucon-somaxconn.conf
sudo sysctl -w net.core.somaxconn=128
sysctl net.core.somaxconn
```

`128` は例の値。変える前の値を 1. で控えていればそちらに戻す。

### 観察すること

- 溢れの有無を `nstat` / `oha` の失敗率で言える（想像で上げず、数字で判断する）
- 上げた前後で `oha` の p99 と失敗数が変わったか言える。変わらなければ戻した
- `backlog` を同時にいじっていない（`somaxconn` 単独の差）

## 3. `net.ipv4.ip_local_port_range`（見るだけ→広げる）

外向き接続（使い捨ての HTTP クライアント、DB / memcached への新規接続）が使う一時ポートの範囲を決める設定。`TIME-WAIT`（60 秒）が溜まると、範囲が狭いほど枯渇する。[0050 §2](0050-http-client.md#2-合成再現で差を見る使い回す-vs-使い捨て) の使い捨て再現と対で見る。

```bash
cat /proc/sys/net/ipv4/ip_local_port_range
sysctl net.ipv4.ip_local_port_range
ss -tan 'state time-wait' | wc -l
ss -s | head -10
```

枯渇の目安: 範囲の総数 ÷ 60 秒が、使い捨て接続の上限 rps。既定 `32768 60999`（約 28k）なら約 470 rps を超えると使い捨てでは詰む。使い回していれば関係ない。そのため先に [0050](0050-http-client.md) の使い回しを済ませる。使い回し済みで `time-wait` が小さいなら、ここは「見るだけ」で次へ進む。

広げるときだけ（1 手）:

```bash
sudo test -f /etc/sysctl.d/99-isucon-ports.conf.orig || \
  sudo cp -a /etc/sysctl.d/99-isucon-ports.conf /etc/sysctl.d/99-isucon-ports.conf.orig 2>/dev/null || true
echo 'net.ipv4.ip_local_port_range = 1024 65535' | sudo tee /etc/sysctl.d/99-isucon-ports.conf
sudo sysctl -p /etc/sysctl.d/99-isucon-ports.conf
cat /proc/sys/net/ipv4/ip_local_port_range
```

- `1024` 未満は well-known ポートとぶつかるので範囲に含めない。範囲の下限はこの値にする
- `tcp_tw_reuse` / `tcp_tw_recycle` は触らない。カーネル任せ。範囲と使い回しで足りる
- 効果の確認は使い捨て再現でやる（[0050 §2b/2c](0050-http-client.md#2-合成再現で差を見る使い回す-vs-使い捨て) の noreuse を回し、`time-wait` 増分と失敗率を見る）。使い回し済みの本番経路では差が出なくて正常

戻す:

```bash
sudo rm -f /etc/sysctl.d/99-isucon-ports.conf
sudo sysctl -w net.ipv4.ip_local_port_range="32768 60999"
cat /proc/sys/net/ipv4/ip_local_port_range
```

既定値はディストリビューションで違うことがある。変える前の値を 1. で控えていればそちらに戻す。

### 観察すること

- 範囲の総数と `time-wait` 数から、枯渇するかどうかを計算で言える
- 使い回し済みなら差が出ないことを確認した（出ないのが正解の回もある）
- `tcp_*` を同時にいじっていない（範囲単独の差）

## 4. HTTP と unix socket の差分（1 台で比べる）

同一ホスト内の nginx → アプリは TCP（`127.0.0.1:8080`）でも unix socket でも届く。unix は TCP のハンドシェイク・ポート消費が無く、同一ホストでは速い。代わりに別ホストへは使えない。分割後（[0020-split.md](0020-split-1.md)）は s1 の軽い処理が unix、s2 への重い転送が TCP という使い分けになる。ここでは 1 台で全体を unix にして差を測る。

今の形（TCP か unix か）を見る:

```bash
systemctl cat isu-python.service | grep -E 'ExecStart|WorkingDirectory'
grep -rn 'proxy_pass\|unix:' /etc/nginx/sites-enabled/ | head -20
sudo ss -lntp | grep -E ':8080|gunicorn.sock' | head
```

切り替え（[0020 §8.6](0020-split-2.md#6-s1-の-gunicornunix-socket) と同じ型。1 台なので振り分けせず全体を unix にする）:

```bash
SITE=/etc/nginx/sites-enabled/isucon
sudo test -f "${SITE}.orig" || sudo cp -a "$SITE" "${SITE}.orig"

mkdir -p /home/isucon/tmp
sudo chown isucon:www-data /home/isucon/tmp
sudo chmod 775 /home/isucon/tmp

sudo mkdir -p /etc/systemd/system/isu-python.service.d
sudo tee /etc/systemd/system/isu-python.service.d/unix.conf >/dev/null <<'EOF'
[Service]
WorkingDirectory=/home/isucon/private_isu/webapp/python
ExecStart=
ExecStart=/home/isucon/private_isu/webapp/python/.venv/bin/gunicorn \
  --bind unix:/home/isucon/tmp/gunicorn.sock \
  --umask 007 \
  app:app
EOF
```

`systemctl cat` の `ExecStart` が `.venv/bin/gunicorn` と `app:app` でなければ、パスとモジュールだけ読み替える。`ExecStart=` の空行は元の `-b 0.0.0.0:8080` を消すため。忘れると起動しない。

```bash
sudo systemctl daemon-reload
sudo systemctl restart isu-python.service
ls -l /home/isucon/tmp/gunicorn.sock
sudo ss -lntp | grep 8080 || echo '8080 closed (expected)'
sudo -u www-data curl --unix-socket /home/isucon/tmp/gunicorn.sock \
  -fsS -o /dev/null -m 5 http://localhost/login || \
  sudo journalctl -u isu-python.service -n 40 --no-pager
```

nginx 側は `proxy_pass` を unix に向ける（対象の `location /`。静的 `location` は触らない）:

```nginx
upstream app_unix {
  server unix:/home/isucon/tmp/gunicorn.sock;
}

# location / の中だけ
proxy_pass http://app_unix;
```

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/login
```

差を見る（bench-prep でログを回してから先に `oha`、あとでベンチ）:

```bash
oha -n 1000 -c 20 --no-tui http://127.0.0.1/ | tail -15
ss -tan 'state time-wait' | wc -l
# :8080 宛の time-wait が出なくなるのが unix 化の証拠
```

- 速くなることを期待する。変わらないならこの 1 台では TCP が詰まっていなかったということ。戻して次へ
- 502 ならソケット権限（`isucon:www-data`、`--umask 007`）、`www-data` の `isucon` グループ（`sudo usermod -aG isucon www-data`）、`proxy_pass` の向きの順に見る（[0020 備考](0020-split-3.md#備考) と同じ）
- 1 台ドリルの unix 化は練習用。分割構成では s2（別ホスト）へ unix は使えない。TCP + keepalive（[0050 §4](0050-http-client.md#4-nginx-の-upstream-を使い回す1-台でも効く)）に戻す

戻す:

```bash
sudo rm -f /etc/systemd/system/isu-python.service.d/unix.conf
sudo systemctl daemon-reload
sudo systemctl restart isu-python.service
sudo ss -lntp | grep 8080
sudo cp -a "${SITE}.orig" "$SITE"
sudo nginx -t && sudo systemctl reload nginx
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

### 観察すること

- `8080 closed` と sock の存在を両方確認した（片方だけでは終わらない）
- TCP 時と unix 時の `oha` p99 と `time-wait` を 2 つの数字で言える
- 502 のとき権限・グループ・`proxy_pass` のどれだったか言える

## 5. gunicorn の worker プロセス数（少なすぎも多すぎも遅い）

worker は並列度そのもの。少ないと待ち行列ができ、多いと CPU / メモリ / DB 接続を消費する。`2*CPU+1` は出発点であって正解ではない。ベンチで振る。

今の値を見る:

```bash
nproc; free -h
systemctl cat isu-python.service | grep ExecStart
ps -ef | grep -E 'gunicorn' | grep -v grep | head -20
ps -o rss,command -C gunicorn 2>/dev/null | head -20
# DB 側の接続数（worker を増やす前に控える。見るだけ）
sudo mysql -N -e "SHOW STATUS LIKE 'Threads_connected'; SHOW VARIABLES LIKE 'max_connections';" 2>/dev/null || \
  mysql -uisuconp -pisuconp -N -e "SHOW STATUS LIKE 'Threads_connected'; SHOW VARIABLES LIKE 'max_connections';"
```

変えるのは 1 手だけ（`-w` だけ。スレッドやワーカー種別は同時に変えない）:

```bash
# 今の ExecStart を読んで -w N だけ足す。例: -w 4（値は nproc から決める）
sudo tee /etc/systemd/system/isu-python.service.d/workers.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/home/isucon/private_isu/webapp/python/.venv/bin/gunicorn app:app -b 0.0.0.0:8080 -w 4 --log-file - --access-logfile -
EOF
sudo systemctl daemon-reload
sudo systemctl restart isu-python.service
ps -ef | grep -E 'gunicorn' | grep -v grep | head -20
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
```

`ExecStart` の本体（パス、`app:app`、`-b`、ログ指定）は `systemctl cat` の現行に合わせる。上のブロックは例示なので、コピペ前に自分の 1 行に直す。

値の振り方:

- 出発点は `2*nproc+1` を上限の目安にし、小さい方から上げる（例: 2 → 4 → 8）。いきなり 16 にしない
- 上げたら `oha -n 1000 -c 20` と公式ベンチを回す。p99 が下がらなくなったら 1 つ戻す（逆振り）
- worker 毎の RSS × N が実メモリを超えたらそこで止める。`free -h` の available が枯れる前に戻す
- `Threads_connected` が跳ねて DB が `too many connections` を出したら worker の増やしすぎが原因。`max_connections` を同時に上げない（まず worker を戻す）

戻す:

```bash
sudo rm -f /etc/systemd/system/isu-python.service.d/workers.conf
sudo systemctl daemon-reload
sudo systemctl restart isu-python.service
ps -ef | grep gunicorn | grep -v grep | head -5
```

### 観察すること

- worker 数と `oha` p99 / 点数の対応表を言える（2 → 4 → 8 の 3 点があれば十分）
- メモリと `Threads_connected` を見て上限を決めた（点数だけで決めない）
- py-spy を取るなら `MainPID` + `--subprocesses`（マスタだけ取って worker を取りこぼさない）

## 6. ファイルディスクリプタの上限数（足りないときだけ効く）

ファイルディスクリプタの上限数のこと。足りているときに上げても点数は動かない。枯渇すると `Too many open files` で 502 / 500 になる。その切り分けができることがゴール。

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
sudo mysql -N -e 'SHOW VARIABLES LIKE "open_files_limit";' 2>/dev/null || \
  mysql -uisuconp -pisuconp -N -e 'SHOW VARIABLES LIKE "open_files_limit";'
```

枯渇の検知（出ていなければ「足りている」）:

```bash
sudo journalctl -u isu-python.service --grep='Too many open files' --no-pager | head -5
sudo journalctl -u nginx.service --grep -i 'too many open' --no-pager | head -5
dmesg | grep -i -E 'too many|open files|VFS' | tail -5
```

[0010-setup.md](../010_common/0010-setup.md#権限py-spy--ulimit) で `isucon` の `nofile 65535` は済んでいる。unit 側の `LimitNOFILE` が小さい（例: 1024）ときだけ上げる:

```bash
sudo tee /etc/systemd/system/isu-python.service.d/nofile.conf >/dev/null <<'EOF'
[Service]
LimitNOFILE=65535
EOF
sudo systemctl daemon-reload
sudo systemctl restart isu-python.service
systemctl show isu-python.service -p LimitNOFILE
grep -i 'open files' /proc/$(systemctl show -p MainPID --value isu-python.service)/limits
```

- 上げても普段の点数は動かない。動かないのが正常。枯渇していた回だけ `fail` / 502 が消える
- `ulimit -n`（シェルの値）と `LimitNOFILE`（unit の値）は別物。サービスに効くのは後者。シェルで上げても unit は変わらない
- nginx の `worker_rlimit_nofile` と mysqld の `open_files_limit` は見るだけ。アプリの `fail` が FD 起因と決まってから触る。同時に上げない
- [0090](0090-app-tuning.md) の FD 節はここの確認で済ませる。0090 では新規に上げない

### 観察すること

- `LimitNOFILE` の今とプロセス実効値（`/proc/<pid>/limits`）を両方言える
- 枯渇ログの有無を言える（無ければ「足りている」）
- 上げた前後で点数が動かないことを確認した（動かないのが正解の回もある）

## 7. 公式ベンチで判定する

2.〜6. のどれか 1 手だけ残し、他は戻した状態にして公式ベンチを 1 本回す。bench-prep は [0030-measure.md](0030-measure.md#2-計測サイクルを回す) と同じ `mv` + `reopen` / `flush-logs`。

```bash
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
if sudo test -f /var/log/mysql/mysql-slow.log; then
  sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
fi
sudo mysqladmin flush-logs 2>/dev/null || sudo mysql -e 'FLUSH SLOW LOGS'
nstat -az 2>/dev/null | grep -i listen | tee /tmp/nstat-before.txt | head -5
TW_BEFORE=$(ss -tan 'state time-wait' | wc -l)
echo "tw_before=$TW_BEFORE"
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
TW_AFTER=$(ss -tan 'state time-wait' | wc -l)
nstat -az 2>/dev/null | grep -i listen | tee /tmp/nstat-after.txt | head -5
echo "tw_before=$TW_BEFORE tw_after=$TW_AFTER"
echo "$(date -Iseconds)  score=  pass=  fail=  oha_p99=  note=infra-unix" >> ~/bench-notes/scores.txt
```

残す変更は `backup/s1` に積む（[0030 §2](0030-measure.md#残す変更は-backups1-に積む) の流儀）。

判定は点数＋`oha` p99＋溢れ指標＋alp。点だけ見ない:

```bash
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -20
diff /tmp/nstat-before.txt /tmp/nstat-after.txt || true
```

効かなければ残さない。`scores.txt` に 1 行残して戻す（2.〜6. の戻し手順）。

### 観察すること

- 残した 1 ノブの値と理由（p99 / overflow / tw のどれが動いたか）を言える
- alp の Sum が落ちたか、落ちないなら土台は詰まっていない（アプリ / DB の仕事に戻る）
- `fail` が 0。`fail` が出たら worker の上げすぎか unix 化の権限を疑う

## 8. トラブルシュート

### `sysctl -p` が失敗する

- drop-in の書式を確認する（`key = value` の `=` 忘れ、引用符の閉じ忘れなど）。`cat` で 1 行確認してから `-p` する
- ファイル名が `*.conf` か（`/etc/sysctl.d/` は `.conf` だけ読む）
- `sysctl -w` で直値は入るか切り分ける。入るならファイルの問題、入らないならキー名の問題

### systemd drop-in で起動しない

- `ExecStart=` の空行忘れが一番多い（新旧 2 行でエラー）。`systemctl cat isu-python.service` で 2 本出ていないか見る
- `daemon-reload` を忘れている。`restart` だけでは drop-in が載らない
- `ExecStart` のパス・モジュールが現行と違う。コピペ前に `systemctl cat` の 1 行に直す

### unix 化で 502

- `ls -l /home/isucon/tmp/gunicorn.sock` が無い → gunicorn が落ちている。`journalctl -u isu-python.service -n 40`
- sock はあるが 502 → 権限。`isucon:www-data`、`--umask 007`、`sudo usermod -aG isucon www-data` の順
- `proxy_pass` の向き違い（TCP のまま / upstream 名の typo）。`grep -rn proxy_pass /etc/nginx/sites-enabled/`

### worker を増やしたら遅くなる / 落ちる

- 正常な逆振りか OOM か切り分ける。`free -h` と `dmesg | tail`（oom-killer）を見る
- DB の `too many connections` なら worker を戻す。`max_connections` を同時に上げない
- CPU を mysqld が使い切っているなら worker の仕事ではない。alp / slp に戻る

### FD を上げても変わらない

- 正常。枯渇ログが無ければ足りていたということ。ファイルを残さず戻す
- `ulimit -n` を上げても unit は変わらない。効かせるのは `LimitNOFILE` + `daemon-reload` + restart
