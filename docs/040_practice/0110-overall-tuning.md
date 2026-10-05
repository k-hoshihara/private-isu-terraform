# 0110 全体最適チューニング

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）はローカル環境の private-isu-terraform リポジトリにて実行します。

DB を直しても残る重さは、アプリ側から取ります。py-spy でホットスポットを見て 1 つずつ直します。
[0100-sql-tuning.md](0100-sql-tuning.md) の次。出口は終盤（[0030-ops.md](../010_common/0030-ops.md)）。

順番: py-spy で取る（1.）→ 1 つ直してベンチ（2.）→ 詰まったら切り分け（3.）。1 手ずつ。`fail` が 0 でない点数は比べない。

## 1. py-spy でボトルネックを取る

DB が mysqld で飽和している間は alp / slp を先に見ます（[0020-measure.md](../010_common/0020-measure.md) の「サイクル」）。飽和が抜けてからアプリを見ます。

記録する台（重いアプリの s2）に入れます。無いときだけ入れる（Ubuntu 24.04 は `--break-system-packages` が要る）:

```bash
# s2 で打つ。python3-pip が無い台では先に apt で入れます
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pip
which py-spy >/dev/null || sudo python3 -m pip install --break-system-packages py-spy
py-spy --version
```

ptrace が塞がれていると取得できません。先に開けます:

```bash
cat /proc/sys/kernel/yama/ptrace_scope
```

`1` や `2` の場合は `0` にします。

`backup/s2/etc/sysctl.d/99-isucon-ptrace.conf`:

```text
kernel.yama.ptrace_scope = 0
```

```bash
# 作業機
git pull --ff-only
mkdir -p backup/s2/etc/sysctl.d
# 上の内容を配置する
git add -A && git commit -m "tune: ptrace_scope を 0 にする" && git push
```

サーバーで反映します（s2 で打つ）:

```bash
REPO=/home/isucon/private-isu-terraform
cd "$REPO" && git fetch origin
git switch feature/backup 2>/dev/null || git switch -c feature/backup
git pull --ff-only
sudo cp -a "$REPO/backup/s2/etc/sysctl.d/99-isucon-ptrace.conf" /etc/sysctl.d/
sudo sysctl -p /etc/sysctl.d/99-isucon-ptrace.conf
cat /proc/sys/kernel/yama/ptrace_scope  # 0 であること
```

負荷が乗っているあいだに取得します。s1 でベンチを仕掛けてからすぐに s2 で実行します:

```bash
# s2 で打つ
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

`MainPID` は gunicorn のマスタです。`--subprocesses` を付けるとマスタと worker が別プロファイルに入ります。見るのは worker 側です（マスタは `wait_for_signals` で寝ているだけです）。集計はリーフフレーム（各スタックの末尾）で見ます。全体の出現率だと `run` が 100% になって何も分かりません。

可視化の手順:

1. JSON を作業機へ移します（SSH があるときは `scp`、SSM だけならファイルを持ち出します）
2. https://www.speedscope.app を開き、ドロップします
3. Left Heavy / Sandwich で見ます。SVG が欲しければ `--format svg` を使います

`--idle` を付けないと、DB 待ちやロック待ちは見えません。on-CPU と `--idle` の両方を確認します。

| 見え方 | 意味 | このアプリでやりがちな一手 |
| --- | --- | --- |
| on-CPU が `digest` / `subprocess` / `openssl` | パスワードハッシュをシェル呼び出しでやっている | 同じ処理をライブラリで計算します。タイムライン GET が主体のベンチでは上位に出ません。出ないときは「出ない」と書いて次へ進みます |
| `--idle` で `execute` / `make_posts` が厚い | N+1、余分なクエリ | コメントをまとめて取る。[0100-sql-tuning.md](0100-sql-tuning.md) のインデックスと併用する |
| `/image` が Sum 上位で `SELECT * FROM posts` | 一覧に不要な BLOB まで読んでいる | 画像用クエリの列指定から `imgdata` を外します。または画像をファイル化して配信します |

## 2. 1 つ直してベンチする

どれか **1 つ** だけ直して再ベンチします。点数と speedscope の同じ場所が痩せたかを見ます。`digest()` を直す例（`openssl` 呼び出し → `hashlib`）です。既存の passhash が読めることだけ確認します:

```bash
printf %s test | openssl dgst -sha512
python3 -c 'import hashlib; print(hashlib.sha512(b"test").hexdigest())'
# 同じ hex なら既存の passhash をそのまま読めます
```

`backup/s1/home/private_isu/webapp/python/app.py` と `backup/s2/home/private_isu/webapp/python/app.py` の両方（`digest()` は s1 と s2 の両方で使うため）:

```python
# 変更前: subprocess で openssl にシェルアウト
def digest(src: str):
    out = subprocess.check_output(
        f"printf %s {shlex.quote(src)} | openssl dgst -sha512 | sed 's/^.*= //'",
        shell=True,
        encoding="utf-8",
    )
    return out.strip()

# 変更後: ライブラリで同じ値を計算する
def digest(src: str):
    return hashlib.sha512(src.encode("utf-8")).hexdigest()
```

`hashlib` の import も追加します:

```python
import datetime
import hashlib  # ← 追加
import os
```

`digest()` はログイン・登録経路（分割後は light の s1）で使うので、s1 と s2 の両方の `app.py` に同じ変更を入れ、両方の `isu-python` を restart します。実測では 58063 → 58506（`fail=0`）で、py-spy の通りこの混雑では小さい効果でした。小さいときは「小さい」と書きます。

```bash
# bench-prep（[0030-measure.md](0030-measure.md) の「bench-prep（s1: nginx 側）」「bench-prep（s3: slow 側）」と同じ mv + reopen / flush-logs。slow 側は s3）してから s1 で公式ベンチ 1 本
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=pyspy-tune" >> ~/bench-notes/scores.txt
```

残す変更は変えた台の `backup/s1`・`s2` に積みます（[0030-measure.md](0030-measure.md) の「残す変更は backup/s1・s2・s3 に積む」の流儀）。

終盤には py-spy の記録を残しません。

```bash
rm -f /tmp/pyspy*.json
```

### 観察すること

- on-CPU と `--idle` で絵が違います。片側だけでは原因を取り違えます
- 直した関数／クエリが Sandwich から落ち、スコアが動きます
- 直していないホットスポットが残っているなら、次の 1 手にします（また 1 つだけ）

## 3. トラブルシュート

### ディスクが埋まる（スローログ）

```bash
df -h /
sudo du -xh /var/log /home /tmp 2>/dev/null | sort -h | tail -n 20
sudo ls -lh /var/log/mysql/mysql-slow.log*
```

生きている `mysql-slow.log` は **truncate しません**（[0030-measure.md](0030-measure.md) の bench-prep と同じ `mv` + `flush-logs` で回します）。やることは次の順番です:

1. `long_query_time` を 1 以上に戻す、またはキャプチャを止める
2. 回転済みの `mysql-slow.log.$TS` / `access.log.$TS` を消して空きを返す
3. 終盤ならログ自体を止める → [0030-ops.md](../010_common/0030-ops.md) の「ログを止める」

### 観察すること

- `df` の Used がスローログと一致する。NUL で埋まると `ls -l` は大きいのに `slp` がすぐ失敗します

### py-spy が Permission denied / ptrace

`MainPID` が 0 なら unit が落ちています。`sudo` を付けない py-spy も拒否されます。`ptrace_scope` が 0 になってから取り直します。

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

- Ruby が enabled に戻っていたら [webapp-setup/python.md](../010_common/webapp-setup/python.md) を見て切り替え直します
- 依存パッケージの問題なら `sudo su - isucon` して `cd /home/isucon/private_isu/webapp/python && uv sync` から restart します
- nginx の `proxy_pass` とアプリの listen（8080）がずれていないか確認します

### mysqld が起きない

```bash
sudo journalctl -u mysql -n 80 --no-pager
sudo rm -f /etc/mysql/mysql.conf.d/99-isucon-*.cnf
sudo systemctl restart mysql
sudo mysql -N -e 'SELECT 1'
```

- drop-in を足した直後に死んだら、そのファイルを消して restart します
- `SELECT 1` が返れば復旧です。返らなければ error.log を見ます
