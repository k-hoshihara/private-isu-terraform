# 0110 全体最適チューニング

DB を直しても残る重さを、アプリ側から取る。py-spy でホットスポットを見て 1 つずつ直す。
[0100-sql-tuning.md](0100-sql-tuning.md) の次。出口は終盤（[0030-ops.md](../010_common/0030-ops.md)）。

順番: py-spy で取る（1.）→ 1 つ直してベンチ（2.）→ 詰まったら切り分け（3.）。1 手ずつ。`fail` が 0 でない点数は比べない。

## 1. py-spy でボトルネックを取る

DB がまだ mysqld で飽和しているうちは alp / slp を先に見る（[0020-measure.md](../010_common/0020-measure.md#1-サイクル)）。飽和が抜けてからアプリを見る。

無いときだけ入れる（Ubuntu 24.04 は `--break-system-packages` が要る）:

```bash
command -v py-spy >/dev/null || sudo python3 -m pip install --break-system-packages py-spy
py-spy --version
```

ptrace が塞がれていると取れない。先に開ける:

```bash
cat /proc/sys/kernel/yama/ptrace_scope
# 1 や 2 だと attach できない。0 にする（中身は etc/sysctl/99-isucon-ptrace.conf）
echo 'kernel.yama.ptrace_scope = 0' | sudo tee /etc/sysctl.d/99-isucon-ptrace.conf
sudo sysctl -p /etc/sysctl.d/99-isucon-ptrace.conf
cat /proc/sys/kernel/yama/ptrace_scope
```

負荷が乗っているあいだに取る。ベンチを仕掛けてからすぐ:

```bash
UNIT=isu-python.service
PID="$(systemctl show -p MainPID --value "$UNIT")"
echo "MainPID=$PID"
pgrep -af gunicorn

# on-CPU だけ
sudo py-spy record --pid "$PID" --subprocesses \
  --format speedscope --duration 30 --rate 100 -o /tmp/pyspy.json

# wall clock（待ちを含む）
sudo py-spy record --pid "$PID" --subprocesses --idle \
  --format speedscope --duration 30 --rate 100 -o /tmp/pyspy-idle.json

ls -lh /tmp/pyspy.json /tmp/pyspy-idle.json
```

`MainPID` は gunicorn のマスタ。`--subprocesses` 無しだと worker を取りこぼす。

可視化の手順:

1. JSON を作業機へ移す（SSH があるときは `scp`、SSM だけならファイルを持ち出す）
2. https://www.speedscope.app を開き、ドロップ
3. Left Heavy / Sandwich で見る。SVG が欲しければ `--format svg`

`--idle` なしだけ見ると、DB 待ちやロック待ちはサンプルに出ない。「アプリの CPU は暇」に見える。on-CPU のホット関数と、idle 側で伸びている待ちを両方確認する。

| 見え方 | 意味 | このアプリでやりがちな一手 |
| --- | --- | --- |
| on-CPU が `digest` / `subprocess` / `openssl` | パスワードハッシュをシェル呼び出しでやっている | 同じ処理をライブラリでやる（リクエスト経路の無駄） |
| `--idle` で `execute` / `make_posts` が厚い | N+1、余分なクエリ | コメントをまとめて取る。[0100-sql-tuning.md](0100-sql-tuning.md) のインデックスと併用する |
| `/image` が Sum 上位で `SELECT * FROM posts` | 一覧に不要な BLOB まで読んでいる | 画像用クエリから `imgdata` 以外を外す、またはファイルへ |

## 2. 1 つ直してベンチする

どれか **1 つ** だけ直して再ベンチし、点数と speedscope の同じ場所が痩せたかを見る。

```bash
# bench-prep（[0030 §2](0030-measure.md#2-計測サイクルを回す) と同じ mv + reopen / flush-logs）してから公式ベンチ 1 本
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=pyspy-tune" >> ~/bench-notes/scores.txt
```

残す変更は `backup/s1` に積む（[0030 §2](0030-measure.md#残す変更は-backups1-に積む) の流儀）。

終盤には py-spy の記録を残さない（`/tmp/pyspy*.json` は消してよい）。

### 観察すること

- on-CPU と `--idle` で絵が違う。片側だけだと原因を取り違える
- 直した関数／クエリが Sandwich から落ち、スコアが動く
- 直していないホットスポットが残っているなら、次の 1 手（また 1 つだけ）

## 3. トラブルシュート

### ディスクが埋まる（スローログ）

```bash
df -h /
sudo du -xh /var/log /home /tmp 2>/dev/null | sort -h | tail -n 20
sudo ls -lh /var/log/mysql/mysql-slow.log*
```

生きている `mysql-slow.log` は **truncate しない**（0030 §2 の bench-prep と同じ `mv` + `flush-logs` で回す）。やることは次の順番:

1. `long_query_time` を 1 以上に戻す、またはキャプチャを止める
2. 回転済みの `mysql-slow.log.$TS` / `access.log.$TS` を消して空きを返す
3. 終盤ならログ自体を止める → [0030-ops.md](../010_common/0030-ops.md#ログを止める終盤)

### 観察すること

- `df` の Used がスローログと一致する。NUL 埋めだと `ls -l` は大きいのに `slp` がすぐ失敗する

### py-spy が Permission denied / ptrace

`MainPID` が 0 なら unit が落ちている。`sudo` を付けない py-spy も拒否される。`ptrace_scope` が 0 になってから取り直す。

### 再起動後 502

```bash
UNIT=isu-python.service
systemctl is-active nginx.service mysql.service "$UNIT"
systemctl is-enabled isu-ruby.service "$UNIT"
sudo journalctl -u "$UNIT" -n 80 --no-pager
ss -lntp | grep 8080
curl -v http://127.0.0.1:8080/
curl -v -o /dev/null http://127.0.0.1/
```

- Ruby が enabled に戻っている → [webapp-setup/python.md](../010_common/webapp-setup/python.md) を見て切り替え直す
- 依存パッケージの問題なら `sudo su - isucon` して `cd /home/isucon/private_isu/webapp/python && uv sync` から restart する
- nginx の `proxy_pass` とアプリの listen（8080）がずれていないか確認する

### mysqld が起きない

```bash
sudo journalctl -u mysql -n 80 --no-pager
sudo rm -f /etc/mysql/mysql.conf.d/99-isucon-*.cnf
sudo systemctl restart mysql
sudo mysql -N -e 'SELECT 1'
```

- drop-in を足した直後に死んだら、そのファイルを消して restart する
- `SELECT 1` が返れば復旧。返らなければ error.log を見る
