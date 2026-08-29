# 複数台に分割する

[00100-env.md](00100-env.md) で CloudShell / Terraform まで終わったあと。  
起動直後の AMI は各台がオールインワン（nginx + アプリ + MySQL）。この手順で役割を分け、余ったプロセスを止め、公式ベンチを nginx 役へ向ける。

方針は [00100-env.md](00100-env.md) と同じ。コマンドは Markdown のブロックのまま残す。ラッパーの `.sh` は置かない。

前提: Python 切替は済んでいること（[webapp-setup/python.md](webapp-setup/python.md) / [00200-setup.md](00200-setup.md)）。  
既定の練習は **3 台**。役割は入れ替えてよい。T 系は使わない（このリポジトリは `c7a.large`、`ap-northeast-1`）。

## 1. 台数と `config.env`

Terraform の既定は `webapp_instance_count = 3`。変えるときは `terraform.tfvars`。

```hcl
webapp_instance_count = 3   # 1 台に戻すなら 1。5 台なら 5
webapp_instance_type  = "c7a.large"
```

`apply` 済みなら `terraform apply` で台数が増減する。この手順書では apply しない。

出力の正本はリスト / マップ。1 台目のスカラー（`webapp_public_ip` など）は one-box 用の便宜。

```bash
cd ~/private-isu-terraform/terraform
terraform output webapp_public_ips
terraform output webapp_private_ips
terraform output webapp_instance_ids
terraform output ssm_login_commands
```

`config.env.example` は `SERVER1` … `SERVER5` まである。**空の `SERVER*_IP` は無視**。持っている台だけ埋める。練習の既定は 3 なので 4 と 5 は空のまま。

作業機（CloudShell またはジャンプホスト）で、IP をループで出す。`SERVER1/2/3` 決め打ちにしない。

```bash
cd ~/private-isu-terraform/terraform
n=1
paste \
  <(terraform output -json webapp_public_ips | jq -r '.[]') \
  <(terraform output -json webapp_private_ips | jq -r '.[]') \
  <(terraform output -json webapp_instance_ids | jq -r '.[]') |
while IFS=$'\t' read -r pub priv id; do
  echo "SERVER${n}_NAME=s${n}"
  echo "SERVER${n}_IP=${pub}"
  echo "# private=${priv}  id=${id}"
  n=$((n + 1))
done
```

`config.env` に貼る。SSH 用はパブリック IP。**nginx の upstream と `MYSQL_HOST` はプライベート IP**（同じ VPC）。

`ROLE` は `web` / `app` / `db` / `both`。複数なら `web,app`。既定の練習例:

```bash
# 3 台の例。2 台なら SERVER3 を空、5 台なら SERVER4/5 も埋める
SERVER1_NAME=s1
SERVER1_IP=           # パブリック
SERVER1_ROLE=web

SERVER2_NAME=s2
SERVER2_IP=
SERVER2_ROLE=app

SERVER3_NAME=s3
SERVER3_IP=
SERVER3_ROLE=db

APP_SERVICE=isu-python.service
APP_DIR=/home/isucon/private_isu/webapp
PYTHON_DIR=/home/isucon/private_isu/webapp/python
APP_PORT=8080
MYSQL_HOST=10.42.0.12   # s3 のプライベート IP。127.0.0.1 のままにしない
MYSQL_USER=isuconp
MYSQL_PASSWORD=isuconp
MYSQL_DATABASE=isuconp
NGINX_SITE_CONF=/etc/nginx/sites-enabled/isucon.conf
```

サイト設定の実体は AMI では `isucon.conf`。無ければ `ls /etc/nginx/sites-enabled`。

ログイン（1 台目の便宜と全台）:

```bash
cd ~/private-isu-terraform/terraform
# 1 台目
aws ssm start-session --target "$(terraform output -raw webapp_instance_id)"

# 全台のコマンド
terraform output ssm_login_commands
```

SSH を使うなら [00200-setup.md](00200-setup.md) の SSH 節。`enable_ssh = true` が必要。台間 rsync も同じ。

## 2. 各台で何が動いているか

AMI 直後は **全台** で nginx・MySQL・Ruby（`isu-ruby`）が enabled。Python 切替後は `isu-python` も。

各台で:

```bash
hostname
ip -4 -br addr
systemctl list-unit-files --type=service --no-legend | grep -Ei 'isu|python|ruby|nginx|mysql|memcached'
printf '%-24s %-10s %-10s\n' UNIT ACTIVE ENABLED
for u in nginx.service mysql.service mysqld.service isu-python.service isu-ruby.service isu-go.service memcached.service; do
  systemctl list-unit-files --type=service --no-legend | awk '{print $1}' | grep -qx "$u" || continue
  printf '%-24s %-10s %-10s\n' "$u" "$(systemctl is-active "$u")" "$(systemctl is-enabled "$u")"
done
sudo ss -lntup | grep -E ':80|:8080|:3306|:11211'
ps -ef | grep -Ei 'gunicorn|unicorn|mysqld|nginx' | grep -v grep
```

Flask + gunicorn は `8080`、unit は `isu-python.service`。nginx が前段。ストックのアプリは Ruby。

## 3. 役割の例（既定の練習）

入れ替えてよい。名前の `s1` が必ず nginx ではない。

| 役割 | 動かす | 止める |
| --- | --- | --- |
| s1 `web` | nginx + `public/`（静的）。必要ならアプリも | MySQL。アプリを置かないなら gunicorn |
| s2 `app` | `isu-python`（gunicorn :8080）。Flask-Session 用に memcached | nginx、MySQL、Ruby |
| s3 `db` | MySQL | nginx、gunicorn、Ruby |

s1 にアプリを残すなら `SERVER1_ROLE=web,app`。upstream は s2 だけでも、s1 の 8080 でもよい。

### 2 台 / 5 台

- **2 台**: s1 = nginx + gunicorn + 静的、s2 = MySQL。`SERVER3_IP` は空。
- **5 台**（ISUCON12 本選は 5）: `webapp_instance_count = 5`。例は nginx 1 + app 3 + MySQL 1。`SERVER4` / `SERVER5` を埋め、nginx `upstream` に app のプライベート IP を並べる。静的専用を足してもよい。

## 4. 余ったプロセスを止める

`disable --now` にする。`stop` だけだと再起動で戻る（[python.md](webapp-setup/python.md)）。

**全台**（Ruby ストック）:

```bash
sudo systemctl disable --now isu-ruby.service
sudo systemctl disable --now isu-go.service 2>/dev/null || true
sudo systemctl disable --now isu-node.service 2>/dev/null || true
```

**s1（web）** — アプリを置かない場合:

```bash
sudo systemctl enable --now nginx.service
sudo systemctl disable --now isu-python.service
sudo systemctl disable --now mysql.service
sudo systemctl disable --now memcached.service 2>/dev/null || true
```

**s2（app）**:

```bash
sudo systemctl enable --now isu-python.service
sudo systemctl enable --now memcached.service
sudo systemctl disable --now nginx.service
sudo systemctl disable --now mysql.service
```

**s3（db）**:

```bash
sudo systemctl enable --now mysql.service
sudo systemctl disable --now nginx.service
sudo systemctl disable --now isu-python.service
sudo systemctl disable --now memcached.service 2>/dev/null || true
```

確認:

```bash
systemctl is-enabled nginx isu-python mysql isu-ruby 2>/dev/null
systemctl is-active nginx isu-python mysql isu-ruby 2>/dev/null
sudo ss -lntup | grep -E ':80|:8080|:3306'
```

web 台で 3306 / 8080 が残っていたら、unit 名を `systemctl status` で特定して `disable --now`。

## 5. nginx / アプリ / 静的ファイル / MySQL

台間の通信はプライベート IP。セキュリティグループは同一 SG 内で 80 / 8080 / 3306 / 11211 を許可済み。

変数は自分の値に直す。

```bash
APP_PRIV=10.42.0.11    # s2
DB_PRIV=10.42.0.12     # s3
SITE=/etc/nginx/sites-enabled/isucon.conf
PUBLIC=/home/isucon/private_isu/webapp/public
```

### MySQL（s3）

Ubuntu の mysqld は `bind-address = 127.0.0.1` のことが多い。他台から届かない。

```bash
sudo ss -lntp | grep 3306
sudo test -f /etc/mysql/mysql.conf.d/mysqld.cnf.orig || \
  sudo cp -a /etc/mysql/mysql.conf.d/mysqld.cnf /etc/mysql/mysql.conf.d/mysqld.cnf.orig
sudo tee /etc/mysql/mysql.conf.d/99-bind-all.cnf >/dev/null <<'EOF'
[mysqld]
bind-address = 0.0.0.0
mysqlx-bind-address = 0.0.0.0
EOF
sudo systemctl restart mysql
sudo ss -lntp | grep 3306   # 0.0.0.0:3306 であること
```

初期ユーザは `isuconp` / `isuconp`。`localhost` だけだとアプリ台から入れない。

```bash
sudo mysql <<'SQL'
CREATE USER IF NOT EXISTS 'isuconp'@'10.42.%' IDENTIFIED BY 'isuconp';
GRANT ALL PRIVILEGES ON isuconp.* TO 'isuconp'@'10.42.%';
FLUSH PRIVILEGES;
SELECT user, host FROM mysql.user WHERE user = 'isuconp';
SQL
```

VPC CIDR が `10.42.0.0/16` 以外なら `10.42.%` を合わせる。

### アプリ（s2）— `ISUCONP_DB_HOST`

Python は `ISUCONP_DB_HOST`（未設定なら `localhost`）。`/home/isucon/env.sh` を直す。unit の `EnvironmentFile=`。

```bash
sudo test -f /home/isucon/env.sh.orig || sudo cp -a /home/isucon/env.sh /home/isucon/env.sh.orig
# DB_PRIV は上で入れた値
sudo sed -i '/^ISUCONP_DB_HOST=/d' /home/isucon/env.sh
echo "ISUCONP_DB_HOST=${DB_PRIV}" | sudo tee -a /home/isucon/env.sh
grep ISUCONP_ /home/isucon/env.sh
```

gunicorn のストックは `-b 0.0.0.0:8080`。`127.0.0.1` になっていたら nginx 台から届かない。`systemctl cat isu-python.service`。

```bash
sudo systemctl restart isu-python.service
curl -fsS -o /dev/null -m 5 "http://127.0.0.1:8080/" || \
  sudo journalctl -u isu-python.service -n 80 --no-pager
```

### nginx（s1）— upstream

ストックは `proxy_pass http://localhost:8080;`。アプリ台のプライベート IP に変える。

```bash
sudo test -f "${SITE}.orig" || sudo cp -a "$SITE" "${SITE}.orig"
sudo sed -i "s#proxy_pass http://localhost:8080;#proxy_pass http://${APP_PRIV}:8080;#" "$SITE"
grep proxy_pass "$SITE"
sudo nginx -t && sudo systemctl reload nginx
```

複数 app なら `upstream` を足す（5 台のとき）。

```nginx
upstream app {
  server 10.42.0.11:8080;
  server 10.42.0.13:8080;
}
# location / の proxy_pass http://app;
```

`proxy_pass` を IP 直書きから `http://app;` に変える。

### 静的ファイルと画像

`webapp/public/` に css / js / `img/`（UI）/ favicon。Flask は `static_folder=../public`。ストックの nginx は `root` がそのディレクトリだが、`location /` が全部 `proxy_pass` するので **静的もアプリ経由**。

投稿画像 `/image/<id>.<ext>` のストックは **MySQL `posts.imgdata`**。ディスク上のファイルではない。nginx で `/css` `/js` `/img` `/image` を返すときは、ファイルを nginx 台へ置き、www-data が読めるようにする。

public を nginx 台へ（アプリ台から。SSH が通っているとき）:

```bash
# s2 で。s1 のプライベート IP
WEB_PRIV=10.42.0.10
rsync -a --delete \
  /home/isucon/private_isu/webapp/public/ \
  "isucon@${WEB_PRIV}:/home/isucon/private_isu/webapp/public/"
```

SSM のみなら、作業機経由で tar を往復させる。

```bash
# 作業機。INSTANCE は terraform output の ID
SRC=i-app
DST=i-web
aws ssm start-session --target "$SRC"   # 中で tar czf /tmp/public.tgz -C /home/isucon/private_isu/webapp public
# 作業機へ取り出して DST へ置く、は当日の経路に合わせる
```

nginx がファイルを返す例（s1 の `$SITE`）。先に `.orig` があること。

```nginx
root /home/isucon/private_isu/webapp/public/;

location /css/ { }
location /js/ { }
location /img/ { }
location = /favicon.ico { }

# 画像をファイルに出したあと。未出力ならこの location は付けない（アプリへ proxy）
# location /image/ {
#   try_files $uri @app;
# }

location / {
  proxy_set_header Host $host;
  proxy_pass http://10.42.0.11:8080;
}

# location @app {
#   proxy_set_header Host $host;
#   proxy_pass http://10.42.0.11:8080;
# }
```

権限（nginx は `www-data`。`/home/isucon` が 750 だと 403）:

```bash
sudo chmod o+x /home/isucon /home/isucon/private_isu /home/isucon/private_isu/webapp
sudo chmod -R a+rX /home/isucon/private_isu/webapp/public
# 画像ディレクトリを足したら同様。所有者は isucon のままでよい
```

`/image` をファイルにするなら、アプリが書き込むディレクトリを nginx 台へ rsync する（または共有しない構成なら nginx 台だけで完結させる）。**tmpfs や `/dev/shm` に置かない**（次節）。

## 6. DB は起動順を仮定しない

ISUCON は再起動順を保証しない。アプリが先に上がってもよい。

- `After=mysql.service` は **同じホストの mysqld** 向け。リモート DB では付けない。
- systemd は `Restart=always`。接続失敗で落ちても DB 待ちで起き直す。
- アプリは接続を使い回さず、切れたら張り直す。

s2:

```bash
sudo mkdir -p /etc/systemd/system/isu-python.service.d
sudo tee /etc/systemd/system/isu-python.service.d/restart.conf >/dev/null <<'EOF'
[Unit]
After=network-online.target
Wants=network-online.target

[Service]
Restart=always
RestartSec=1
EOF
sudo systemctl daemon-reload
sudo systemctl restart isu-python.service
systemctl show isu-python.service -p Restart -p RestartUSec
```

`app.py` の `db()` は成功した接続をグローバルに保持する。起動時に MySQL が居なくても gunicorn は起きることがあるが、初回リクエストで落ちる。再接続を足す（サーバ上のファイル。このリポジトリには置かない）。

```python
# db() を置き換える例。OperationalError で _db = None して ping/connect をやり直す
import time
from MySQLdb import OperationalError

def db():
    global _db
    last = None
    for _ in range(30):
        try:
            if _db is None:
                conf = config()["db"].copy()
                conf["charset"] = "utf8mb4"
                conf["cursorclass"] = MySQLdb.cursors.DictCursor
                conf["autocommit"] = True
                conf["connect_timeout"] = 5
                _db = MySQLdb.connect(**conf)
            _db.ping(True)
            return _db
        except OperationalError as e:
            last = e
            _db = None
            time.sleep(1)
    raise last
```

確認: s3 を先に `sudo reboot` し、s2 の unit が `activating` → `active` になること。`After=mysql.service` が残っていないこと（`systemctl cat isu-python.service`）。

## 7. ベンチ中の書き込みは再起動後も残す

スコアの根拠をメモリだけにしない。再起動で投稿や画像が消える構成は失格に近い。

- `posts` / `users` / `comments` の正本は MySQL（InnoDB）。memcached はストックではセッション。投稿の唯一のコピーを memcached にしない。
- `/image` をファイルにしたら **EBS 上**（`/home/isucon/...`）。`tmpfs`、`/dev/shm`、`/tmp` にしない。
- nginx の `proxy_cache` だけを正本にしない。

確認:

```bash
# s3
mysql -uisuconp -pisuconp isuconp -e "SELECT COUNT(*) FROM posts;"
# マーカーを 1 行入れてから全台 reboot、もう一度 COUNT とマーカー
```

## 8. ヘルスチェックと公式ベンチ

役割ごとに。失敗したら journalctl / nginx error / MySQL ログ。

```bash
# s3
mysql -uisuconp -pisuconp -e 'SELECT 1'
sudo ss -lntp | grep 3306

# s2（アプリ台から DB と自分）
mysql -uisuconp -pisuconp -h "$DB_PRIV" isuconp -e 'SELECT 1'
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/
systemctl is-active isu-python.service

# s1
curl -fsS -o /dev/null -m 5 "http://${APP_PRIV}:8080/"
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/css/style.css || true
# ブラウザは s1 のパブリック IP
```

公式ベンチの `-t` は **nginx 役**。分割後にアプリ台や DB 台の localhost へ向けない。

AMI にはベンチマーカーが入っている。CPU を食い始めたら [00100-env.md](00100-env.md) のとおりベンチ専用インスタンスを分ける。

```bash
# nginx 役、またはベンチ専用機。NGINX_URL は s1 の URL（同じホストなら http://localhost）
NGINX_URL=http://10.42.0.10
sudo su - isucon
/home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t "$NGINX_URL"
```

### bench-prep

コピーして値を直す。ログを空にしてから公式ベンチ。計測の中身は [00300-measure.md](00300-measure.md)。

```bash
# bench-prep
set -euo pipefail
. ./config.env
SSH_USER="${SSH_USER:-isucon}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"

servers() {
  local i name ip role
  for i in $(seq 1 9); do
    eval "name=\${SERVER${i}_NAME-}"
    eval "ip=\${SERVER${i}_IP-}"
    eval "role=\${SERVER${i}_ROLE-}"
    [ -n "${ip}" ] || continue
    printf '%s %s %s\n' "${name:-s$i}" "${ip}" "${role:-}"
  done
}

role_has() {
  echo ",$1," | grep -q ",$2,"
}

# ログを空に（nginx 役・DB 役）
servers | while read -r name ip role; do
  if role_has "$role" web || role_has "$role" both; then
    ssh -i "$SSH_KEY" "${SSH_USER}@${ip}" 'sudo truncate -s 0 /var/log/nginx/access.log || true'
  fi
  if role_has "$role" db || role_has "$role" both; then
    ssh -i "$SSH_KEY" "${SSH_USER}@${ip}" 'sudo truncate -s 0 /var/log/mysql/mysql-slow.log || true'
  fi
done

# ヘルス
servers | while read -r name ip role; do
  echo "===== health $name $role ====="
  if role_has "$role" web || role_has "$role" both; then
    ssh -i "$SSH_KEY" "${SSH_USER}@${ip}" 'curl -fsS -o /dev/null -m 5 http://127.0.0.1/'
  fi
  if role_has "$role" app || role_has "$role" both; then
    ssh -i "$SSH_KEY" "${SSH_USER}@${ip}" 'curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/'
  fi
  if role_has "$role" db || role_has "$role" both; then
    ssh -i "$SSH_KEY" "${SSH_USER}@${ip}" "mysql -u${MYSQL_USER} -p${MYSQL_PASSWORD} -e 'SELECT 1'"
  fi
done

# 公式ベンチは nginx 役の 1 台目へ。URL はパブリックでもプライベートでも可
WEB_IP="$(servers | awk '$3 ~ /(^|,)(web|both)(,|$)/ { print $2; exit }')"
echo "bench target: http://${WEB_IP}"
ssh -i "$SSH_KEY" "${SSH_USER}@${WEB_IP}" \
  "/home/isucon/private_isu/benchmarker/bin/benchmarker -u /home/isucon/private_isu/benchmarker/userdata -t http://localhost"
```

SSM のみなら、s1 に入って `curl` と `benchmarker -t http://localhost` を手で打つ。

## 9. リモートコマンドをループする

`config.env` を読んだスニペット。リポジトリにはスクリプトを置かない。空 IP は `servers` が飛ばす。

```bash
. ./config.env
SSH_USER="${SSH_USER:-isucon}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"

servers() {
  local i name ip role
  for i in $(seq 1 9); do
    eval "name=\${SERVER${i}_NAME-}"
    eval "ip=\${SERVER${i}_IP-}"
    eval "role=\${SERVER${i}_ROLE-}"
    [ -n "${ip}" ] || continue
    printf '%s %s %s\n' "${name:-s$i}" "${ip}" "${role:-}"
  done
}

role_has() { echo ",$1," | grep -q ",$2,"; }

all_exec() {
  servers | while read -r name ip role; do
    echo "===== $name $ip ($role) ====="
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "${SSH_USER}@${ip}" "$@"
  done
}

app_exec() {
  servers | while read -r name ip role; do
    role_has "$role" app || role_has "$role" both || continue
    echo "===== $name $ip ====="
    ssh -i "$SSH_KEY" "${SSH_USER}@${ip}" "$@"
  done
}

web_exec() {
  servers | while read -r name ip role; do
    role_has "$role" web || role_has "$role" both || continue
    echo "===== $name $ip ====="
    ssh -i "$SSH_KEY" "${SSH_USER}@${ip}" "$@"
  done
}

db_exec() {
  servers | while read -r name ip role; do
    role_has "$role" db || role_has "$role" both || continue
    echo "===== $name $ip ====="
    ssh -i "$SSH_KEY" "${SSH_USER}@${ip}" "$@"
  done
}

# 例
all_exec 'hostname; systemctl is-active nginx isu-python mysql isu-ruby 2>/dev/null'
app_exec 'systemctl is-active isu-python.service; curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/'
db_exec 'mysql -uisuconp -pisuconp -e "SELECT 1"'
web_exec 'curl -fsS -o /dev/null -m 5 http://127.0.0.1/'
```

SSM で全台に同じシェルを投げる（CloudShell、`terraform/`）:

```bash
IDS=$(terraform output -json webapp_instance_ids | jq -r '.[]')
aws ssm send-command \
  --instance-ids $IDS \
  --document-name AWS-RunShellScript \
  --parameters commands='["hostname; systemctl is-active nginx mysql isu-python isu-ruby 2>/dev/null; ss -lnt | grep -E \":80|:8080|:3306\" || true"]' \
  --region ap-northeast-1
```

## 備考

### 502 / nginx は生きているがアプリに届かない

```bash
grep proxy_pass /etc/nginx/sites-enabled/*
curl -v "http://${APP_PRIV}:8080/"
sudo journalctl -u isu-python.service -n 80 --no-pager
```

SG、gunicorn の bind、`ISUCONP_DB_HOST`、mysqld の `bind-address`。

### MySQL にアプリ台から入れない

`isuconp`@`localhost` だけになっていないか。`10.42.%` の GRANT。`ss` が `127.0.0.1:3306` のままなら bind-address。

### 再起動したら Ruby や mysqld が戻る

その台で `systemctl is-enabled`。`disable --now` 漏れ。

### `webapp_url` が古い / 1 台目しか止まらない

パブリック IP は停止→起動で変わる。`terraform apply -refresh-only`。停止は全台:

```bash
aws ec2 stop-instances --instance-ids $(terraform output -json webapp_instance_ids | jq -r '.[]')
```
