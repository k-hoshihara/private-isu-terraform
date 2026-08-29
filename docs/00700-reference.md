# 参照

ログの切り方、台数、レギュレーション寄りの約束。手順の本体は [00200-setup.md](00200-setup.md) / [00300-measure.md](00300-measure.md) / [00400-ops.md](00400-ops.md)。  
後続のドリル（00500 / 00600）からもここにリンクする。

当日の値は [config.env.example](../config.env.example) を `config.env` に複製して埋める。

## ログを切る

### logrotate は使わない

ISUCON は logrotate に任せない。時間ベースなのでベンチの窓を外す。  
計測の直前に自分で切る。

切ることと、終盤にログを止めることは別。止める手順は [00400-ops.md](00400-ops.md#ログを止める終盤)。

### nginx

**A.** `truncate`（履歴は消える）

```bash
sudo truncate -s 0 /var/log/nginx/access.log
```

nginx は `O_APPEND` なので reload なしで空になる。直前のランのログは残らない。

**B.** `mv` + reopen（推奨。ランごとに残す）

```bash
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
# 同等: sudo kill -USR1 "$(cat /var/run/nginx.pid)"
```

`mv` は nginx が開いている inode を変えない。reopen を忘れると新しい `access.log` は空のまま（よくある）。  
1 本は数十 MB。alp に使うので、今切ったファイルは消さない。

### MySQL slow

nginx と同じ `truncate` は使わない。mysqld は `O_APPEND` ではなくオフセットを持つ。  
`truncate` するとその位置へ書くので、先頭が NUL のスパースファイルになる。`ls` は大きい、`du` は小さい、slp は壊れる。

一般的な例は `/var/log/mysql/slow.log`。このリポジトリの例（[config.env.example](../config.env.example) の `MYSQL_SLOW_LOG`）は `/var/log/mysql/mysql-slow.log`。当日のパスに直す。

```bash
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
sudo mysqladmin flush-logs
# または: mysql -e "FLUSH SLOW LOGS;"
```

`FLUSH SLOW LOGS` / `mysqladmin flush-logs` で設定パスのファイルを開き直す。`mv` だけだと古い inode に書き続ける。

### ベンチ直前（app / db）

ラッパーの `.sh` は置かない。下をコピーして打つ。app 台と db 台で中身が違う。  
`ROLE=both` なら両方。

app 台（nginx）:

```bash
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
sudo nginx -s reopen
df -h /
```

db 台（MySQL slow）:

```bash
TS=$(date +%Y%m%d%H%M%S)
sudo mv /var/log/mysql/mysql-slow.log /var/log/mysql/mysql-slow.log.$TS
sudo mysqladmin flush-logs
df -h /
```

`long_query_time=0` は全クエリを書く。数本で GB になる。切るたびに `df -h /` を見る。ディスクフルは毎年出る。

nginx だけなら A の `truncate` でも空にできる。slow には使わない。計測サイクル側は [00300-measure.md](00300-measure.md#ベンチ直前)。

### 複数台は SERVER* を回す

`config.env` の `SERVER*_IP` が空なら飛ばす。`s1` `s2` `s3` や `SERVER1/2/3` の直書きは本選 5 台で壊れる。  
6 台以上なら `SERVER6_` を足してループ上限を上げる。

作業機で一度定義して、上のブロックを `app_exec` / `db_exec` で包む。

```bash
. ./config.env

servers() {
  local i name ip role
  i=1
  while [ "$i" -le 9 ]; do
    eval "name=\${SERVER${i}_NAME-}"
    eval "ip=\${SERVER${i}_IP-}"
    eval "role=\${SERVER${i}_ROLE-}"
    [ -n "$ip" ] && printf '%s %s %s\n' "$name" "$ip" "$role"
    i=$((i + 1))
  done
}

remote() {
  local ip="$1"; shift
  if [ "$ip" = local ]; then
    bash -lc "$*"
  else
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "${SSH_USER}@${ip}" "$@"
  fi
}

all_exec() {
  local cmd="$1"
  while read -r name ip role; do
    echo "===== $name ($role) ====="
    remote "$ip" "$cmd"
  done <<EOF
$(servers)
EOF
}

app_exec() {
  local cmd="$1"
  while read -r name ip role; do
    case "$role" in app|both) ;; *) continue ;; esac
    echo "===== $name ($role) ====="
    remote "$ip" "$cmd"
  done <<EOF
$(servers)
EOF
}

db_exec() {
  local cmd="$1"
  while read -r name ip role; do
    case "$role" in db|both) ;; *) continue ;; esac
    echo "===== $name ($role) ====="
    remote "$ip" "$cmd"
  done <<EOF
$(servers)
EOF
}
```

```bash
TS=$(date +%Y%m%d%H%M%S)
app_exec "sudo mv ${NGINX_ACCESS_LOG} ${NGINX_ACCESS_LOG}.${TS} && sudo nginx -s reopen && df -h /"
db_exec "sudo mv ${MYSQL_SLOW_LOG} ${MYSQL_SLOW_LOG}.${TS} && sudo mysqladmin flush-logs && df -h /"
```

練習の 1 台（`SERVER1_IP=local` `ROLE=both`）なら `remote` は ssh しない。

### ディスク

切ったあと、古い回転ログを消して空きを作る。今のランのファイルは残す。  
目安は `DISK_WARN_PERCENT`（[config.env.example](../config.env.example)）。超えたら [00400-ops.md](00400-ops.md#ディスク)。

```bash
df -h /
sudo du -xh /var/log /home /tmp 2>/dev/null | sort -h | tail -n 20
sudo ls -lh /var/log/nginx/access.log* /var/log/mysql/mysql-slow.log* 2>/dev/null
```

### 終盤は止める

回転 ≠ 無効化。終盤は書くのを止める。ISUCON13 優勝はここでおおよそ 27 万 → 46 万。

```nginx
access_log off;
```

```sql
SET GLOBAL slow_query_log = 'OFF';
```

手順と戻しは [00400-ops.md](00400-ops.md#ログを止める終盤)。py-spy も止める。

## 台数

出典は過去大会の公開情報 / 優勝記。ここに無い年は書かない。

| 大会 | 競技者サーバ | 備考 |
| --- | --- | --- |
| ISUCON14（2024） | 3 × c5.large（2vCPU / 4GiB）、gp3 20GB | ベンチは ECS Fargate 8vCPU / 8GB |
| ISUCON13（2023） | 3 × c5.large、gp3 40GB | |
| ISUCON12 本選（2022） | 5 | 優勝チームは想定外。Ansible が 3 台向けだった |
| ISUCON12 予選（2022） | 3 × c5.large | |
| ISUCON9 予選（2019） | 3 × c5.large | |

3 × c5.large が型。RAM が小さいので DB / app を分ける。  
台数はレギュレーションに無いことが多い。当日マニュアルで初めて分かる。だから `config.env` の `SERVER*` は可変にする。

## レギュレーション

ISUCON14 / ISUCON2026 で形が同じ約束。

1. サーバの役割は変えてよい（DB 分離、app 分離）。
2. 運営の再起動順は保証されない。起動時に DB が落ちていても app が死なないこと。接続はリトライする。再起動試験は [00400-ops.md](00400-ops.md#再起動試験)。
3. ベンチ中に書いたデータは再起動後も読めること。ISUCON14 ではメモリ上のキャッシュが原因で上位（トップ 8）のチームが失格になった。メモリキャッシュや遅延書き込みで、この線を越えない。

## ISUCON2026

ソースメモ時点のスナップショット。席の空きは書かない。

| 項目 | 内容 |
| --- | --- |
| 日時 | 2026-10-31（土）10:00–18:00 |
| 形式 | ハイブリッド（オンライン + オフライン。さくらインターネット大阪本社ほか） |
| 運営 | LINE Yahoo からさくらインターネットへ。問題は第n西東京市 |
| 環境 | 自分の AWS アカウントで運営 AMI を起動（ISUCON14 と同じ。練習向き。課金は参加者） |
| 言語 | Go, Perl, PHP, Python, Ruby, Rust, Node.js |
| 申し込み | 3 波: 2026-07-28 / 08-01 / 08-05 |

## このリポジトリ

練習は自分の AWS で、指定 AMI を `ap-northeast-1` に立てる。インスタンスは `c7a.large`（T 系は使わない）。構築は [00100-env.md](00100-env.md)。

[config.env.example](../config.env.example) で当日変えるもの:

- `SERVER*_NAME` / `SERVER*_IP` / `SERVER*_ROLE`（空の IP は無視）
- `NGINX_ACCESS_LOG` / `MYSQL_SLOW_LOG`
- `PYSPY_DURATION` / `PYSPY_FORMAT`
- `DISK_WARN_PERCENT`
