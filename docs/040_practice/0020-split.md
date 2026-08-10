# 0020 複数台に分割する

[0010-env.md](0010-env.md) で `webapp_instance_count = 3` として立てたあと。  
起動直後の AMI は各台がオールインワン（nginx + アプリ + MySQL）。この手順で役割を分け、余ったプロセスを止め、公式ベンチを nginx 役へ向ける。

方針は [0010-env.md](0010-env.md) と同じ。コマンドは Markdown のブロックのまま残す。ラッパーの `.sh` は置かない。

前提: 分割の前に **全台で Python に切り替える**（[3.](#3-python-に切り替える全台)）。手順は [webapp-setup/python.md](../010_common/webapp-setup/python.md) / [0010-setup.md](../010_common/0010-setup.md) と同じ。  
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
cd /tmp/private-isu-terraform/terraform
terraform output webapp_public_ips
terraform output webapp_private_ips
terraform output webapp_instance_ids
terraform output ssm_login_commands
```

`config.env.example` は `SERVER1` … `SERVER5` まである。**空の `SERVER*_IP` は無視**。持っている台だけ埋める。練習の既定は 3 なので 4 と 5 は空のまま。

作業機（CloudShell またはジャンプホスト）で、IP をループで出す。`SERVER1/2/3` 決め打ちにしない。

```bash
cd /tmp/private-isu-terraform/terraform
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

`ROLE` は `web` / `app` / `db` / `both`。複数なら `web,app`。既定の練習は s1 が nginx + 軽いアプリ（unix socket）、s2 が重いアプリ（HTTP）、s3 が DB。

```bash
# 3 台の例。2 台なら SERVER3 を空、5 台なら SERVER4/5 も埋める
SERVER1_NAME=s1
SERVER1_IP=           # パブリック
SERVER1_ROLE=web,app

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
MEMCACHED_HOST=10.42.0.11   # s2 のプライベート IP。127.0.0.1 のままにしない
NGINX_SITE_CONF=/etc/nginx/sites-enabled/isucon.conf
```

サイト設定の実体は AMI では `isucon.conf`。無ければ `ls /etc/nginx/sites-enabled`。

ログイン（1 台目の便宜と全台）:

```bash
cd /tmp/private-isu-terraform/terraform
# 1 台目
aws ssm start-session --target "$(terraform output -raw webapp_instance_id)"

# 全台のコマンド
terraform output ssm_login_commands
```

SSH を使うなら [0010-setup.md](../010_common/0010-setup.md) の SSH 節。`enable_ssh = true` が必要。台間 rsync も同じ。

## 2. 各台で何が動いているか

AMI 直後は **全台** で nginx・MySQL・Ruby（`isu-ruby`）が enabled。Python 切替後は `isu-python` も。

各台で下記を実施し、情報を収集

```bash
# IPアドレス表示
ip -4 -br addr

# 各種サービスの表示
systemctl list-unit-files --type=service --no-legend | grep -Ei 'isu|python|ruby|nginx|mysql|memcached'


printf '%-24s %-10s %-10s\n' UNIT ACTIVE ENABLED
for u in nginx.service mysql.service mysqld.service isu-python.service isu-ruby.service isu-go.service memcached.service; do
  systemctl list-unit-files --type=service --no-legend | awk '{print $1}' | grep -qx "$u" || continue
  printf '%-24s %-10s %-10s\n' "$u" "$(systemctl is-active "$u")" "$(systemctl is-enabled "$u")"
done

# 各種プロセスの詳細表示
sudo ss -lntup | grep -E ':80|:8080|:3306|:11211'


ps -ef | grep -Ei 'gunicorn|unicorn|mysqld|nginx' | grep -v grep
```

Flask + gunicorn は `8080`、unit は `isu-python.service`。nginx が前段。ストックのアプリは Ruby。

## 3. Python に切り替える（全台）

AMI 直後は Ruby。分割の前に **3 台とも** Python にする。unit 名は回ごとに違うので、先に一覧を見る。

各台で:

```bash
systemctl list-unit-files --type=service --no-legend | grep -Ei 'isu'
systemctl cat isu-python.service
```

```bash
sudo systemctl disable --now isu-ruby.service
sudo systemctl disable --now isu-go.service 2>/dev/null || true
sudo systemctl disable --now isu-node.service 2>/dev/null || true
sudo systemctl enable --now isu-python.service

cd /home/isucon/private_isu/webapp/python
command -v uv >/dev/null && sudo -u isucon uv sync

curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/ || \
  sudo journalctl -u isu-python.service -n 80 --no-pager
```

`disable` しないと再起動で Ruby が戻る。詳細は [python.md](../010_common/webapp-setup/python.md)。

## 4. 役割の例（既定の練習）

入れ替えてよい。名前の `s1` が必ず nginx ではない。

既定は **s1 が入口**、**s2 が重いアプリ**、**s3 が DB**。


| 役割           | 動かす                                                                 | 止める                                         |
| ------------ | ------------------------------------------------------------------- | ------------------------------------------- |
| s1 `web,app` | nginx。`public/` の静的。gunicorn は **unix socket**（軽い処理）。memcached は s2 を使う | MySQL。ローカル memcached。HTTP の 8080 は開けない（unix だけ） |
| s2 `app`     | gunicorn **:8080**（重い処理）。共有 memcached（Flask-Session）                 | nginx、MySQL、Ruby                            |
| s3 `db`      | MySQL                                                               | nginx、gunicorn、Ruby、memcached               |


s1 の nginx が振り分ける。

- `/css` `/js` `/img` `/favicon.ico` → ディスク（s1）
- 軽いアプリ（ログインなど）→ s1 の unix socket
- 残り（`/`、`/posts`、`/image`、投稿）→ s2 の `http://APP_PRIV:8080`

何を軽いにするかは alp の Sum を見て入れ替える。初期値は下の nginx 例。

ストックの Python は Flask-Session（`SESSION_TYPE=memcached`）と gunicorn の複数ワーカー。セッションはプロセスメモリに置かない。ログイン（s1 の unix）のあと `/` は s2 へ行くので、s1 と s2 は **同じ memcached（s2）** を見る。変数は `ISUCONP_MEMCACHED_ADDRESS`（未設定なら `127.0.0.1:11211`）。s1 のアプリが `127.0.0.1` 固定なら、s2 の `host:11211` に直す。

### 2 台 / 5 台

- **2 台**: s1 = nginx + unix socket + 静的、s2 = MySQL（アプリも同居するなら s2 に gunicorn :8080）。memcached はアプリが居る台に 1 本。`SERVER3_IP` は空。
- **5 台**（ISUCON12 本選は 5）: `webapp_instance_count = 5`。例は nginx 1 + 重い app 3 + MySQL 1。`SERVER4` / `SERVER5` を埋め、nginx の重い `upstream` に app のプライベート IP を並べる。memcached は重い app の 1 台に 1 本。残りの app も同じ `ISUCONP_MEMCACHED_ADDRESS` を見る。

## 5. 分割前のベースライン

まだ全台オールインワン。プロセスは止めない。Python 切替（[3.](#3-python-に切り替える全台)）だけ済んだ状態で公式ベンチ 1 本。点数を手元に書く。あとの [11.](#11-ヘルスチェックと公式ベンチ)（分割後）と比べる。

この時点の memcached は各台の `127.0.0.1` で足りる。共有にするのは分割後。

公式ベンチの `-t` は **nginx がある台**。分割前は各台オールインワンなので、入口にする s1 の localhost でよい。アプリ台や DB 台へ向けない。

s1 で:

```bash
sudo su - isucon
/home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
```

ログを切ってから回すなら [0020-measure.md](../010_common/0020-measure.md#ベンチ直前)。nginx access は `mv` + `nginx -s reopen`（または `truncate`）。MySQL slow は `truncate` しない（先頭が NUL のスパースファイルになる）。

## 6. 分割前のバックアップ

これ以降で `/etc`・`env.sh`・gunicorn の bind を変える。**変える前に取る**。ローカルの orig と、GitHub の private リポジトリの 2 系統。

- **Linux の設定** — 各台で `~/kit-backup/` に `/etc/nginx` `/etc/mysql` `/etc/memcached.conf` systemd unit / drop-in、`env.sh` をコピーする。壊したらこのディレクトリから戻す。
- **アプリのファイル** — `webapp/`（`.venv` は除く）を GitHub の **private** リポジトリへ push する。設定のコピーも同じリポジトリの `configs/<台名>/` に載せる。インスタンスを捨てても GitHub から戻せる。

public リポジトリにしない。`env.sh` と投稿データが入りうる。AMI のストックは残るが、自分の差分は残らない。

### 各台で `~/kit-backup`（Linux 設定）

s1 / s2 / s3 の **全部**。`isucon` で打つ。

```bash
TS=$(date +%Y%m%d%H%M%S)
BK=/home/isucon/kit-backup/$TS
mkdir -p "$BK/etc" "$BK/home"

sudo test -d /etc/nginx && sudo cp -a /etc/nginx "$BK/etc/nginx"
sudo test -d /etc/mysql && sudo cp -a /etc/mysql "$BK/etc/mysql"
sudo test -f /etc/memcached.conf && sudo cp -a /etc/memcached.conf "$BK/etc/memcached.conf"

# unit の実体は /lib か /etc。cat でパスを確認してからコピー
systemctl cat isu-python.service | head
sudo cp -a /etc/systemd/system/isu-python.service "$BK/etc/" 2>/dev/null || true
sudo cp -a /lib/systemd/system/isu-python.service "$BK/etc/" 2>/dev/null || true
sudo test -d /etc/systemd/system/isu-python.service.d && \
  sudo cp -a /etc/systemd/system/isu-python.service.d "$BK/etc/isu-python.service.d"
sudo test -d /etc/systemd/system/memcached.service.d && \
  sudo cp -a /etc/systemd/system/memcached.service.d "$BK/etc/memcached.service.d"

sudo test -f /home/isucon/env.sh && sudo cp -a /home/isucon/env.sh "$BK/home/env.sh"

sudo chown -R isucon:isucon /home/isucon/kit-backup
find "$BK" -maxdepth 2 -ls
```

あとの各手順でも、触るファイルの `.orig` をその場で取る。`~/kit-backup` は残す。消さない。

戻す例（nginx を壊したとき、s1）:

```bash
# $TS は上で作ったディレクトリ名
sudo cp -a /home/isucon/kit-backup/$TS/etc/nginx /etc/nginx
sudo nginx -t && sudo systemctl reload nginx
```

### GitHub へ（アプリ + 設定）

作業機で **空の private リポジトリ** を 1 つ作る。例: `private-isu-app`。clone URL を控える。

```bash
# GitHub UI で empty の private リポジトリを作る。public にしない。README は付けない
GIT_URL=https://github.com/<user>/private-isu-app.git
```

認証は PAT（classic、scope `repo`）か SSH 鍵。インスタンスの `.git/config` にトークンを書かない。

```bash
# 作業機または各台。打ち終わったら unset
export GH_TOKEN=ghp_...          # GitHub → Settings → Developer settings → Tokens
export GIT_URL=https://github.com/<user>/private-isu-app.git
```

AMI の `webapp/` は upstream の git が付いていることがある。**origin を公式へ向けたまま push しない**。退避用は別ディレクトリ。

**s1**（アプリの正本。この時点は全台同じソース）:

```bash
STASH=/home/isucon/app-stash
rm -rf "$STASH"
mkdir -p "$STASH/webapp" "$STASH/configs/s1"
rsync -a --exclude '.venv/' --exclude '__pycache__/' --exclude '.git/' \
  /home/isucon/private_isu/webapp/ "$STASH/webapp/"
rsync -a /home/isucon/kit-backup/ "$STASH/configs/s1/"

cd "$STASH"
git init
git config user.email "isucon@localhost"
git config user.name "isucon"
git add -A
git status
git commit -m "baseline after python switch, before split"
git branch -M main
git remote add origin "$GIT_URL"
git -c "http.extraHeader=Authorization: Bearer ${GH_TOKEN}" push -u origin main
```

**s2 / s3**（設定だけ足す。同じリポジトリ）:

```bash
STASH=/home/isucon/app-stash
NAME=s2   # s3 なら s3
rm -rf "$STASH"
git -c "http.extraHeader=Authorization: Bearer ${GH_TOKEN}" clone "$GIT_URL" "$STASH"

mkdir -p "$STASH/configs/$NAME"
rsync -a /home/isucon/kit-backup/ "$STASH/configs/$NAME/"
cd "$STASH"
git config user.email "isucon@localhost"
git config user.name "isucon"
git add "configs/$NAME"
git commit -m "configs $NAME"
git -c "http.extraHeader=Authorization: Bearer ${GH_TOKEN}" pull --rebase origin main
git -c "http.extraHeader=Authorization: Bearer ${GH_TOKEN}" push origin main
```

`clone` の URL にトークンを埋め込んだら、終わったら `git remote set-url origin "$GIT_URL"` で消す。

分割の途中でも、触ったファイルを `app-stash` に同期して commit / push してよい。

アプリを GitHub から戻す（s1 の例）:

```bash
cd /home/isucon
git clone "$GIT_URL" app-stash-restore
rsync -a --exclude '.git/' app-stash-restore/webapp/ /home/isucon/private_isu/webapp/
# python/.venv は rsync していないので、必要なら uv sync
cd /home/isucon/private_isu/webapp/python
command -v uv >/dev/null && sudo -u isucon uv sync
sudo systemctl restart isu-python.service
```

## 7. 余ったプロセスを止める

`disable --now` にする。`stop` だけだと再起動で戻る（[python.md](../010_common/webapp-setup/python.md)）。

各台で、止める前に **いま有効な unit を出す**。無い名前を `disable` しない。

gunicorn はワーカーが別プロセス。セッションやキャッシュをプロセスメモリに置くとワーカー間で見えない。**アプリ層は memcached を使う**。実体は s2 に 1 本。s1 の軽い gunicorn も s2 を見る。s1 で memcached を `disable` するのは「ローカルの二本目を立てない」ためで、アプリが memcached を使わないことではない。

```bash
systemctl list-unit-files --type=service --no-legend | grep -Ei 'isu|python|ruby|go|nginx|mysql|memcached'
printf '%-24s %-10s %-10s\n' UNIT ACTIVE ENABLED
for u in nginx.service mysql.service mysqld.service isu-python.service isu-ruby.service isu-go.service isu-node.service memcached.service; do
  systemctl list-unit-files --type=service --no-legend | awk '{print $1}' | grep -qx "$u" || continue
  printf '%-24s %-10s %-10s\n' "$u" "$(systemctl is-active "$u")" "$(systemctl is-enabled "$u")"
done
```

**全台**（Ruby ストック。3. で済んでいれば skipped）:

```bash
sudo systemctl disable --now isu-ruby.service
sudo systemctl disable --now isu-go.service 2>/dev/null || true
sudo systemctl disable --now isu-node.service 2>/dev/null || true
```

**s1（web + 軽いアプリ）** — ローカル memcached は止める（二本目を立てない）。gunicorn は次節で s2 の 11211 を見る。

```bash
sudo systemctl enable --now nginx.service
sudo systemctl enable --now isu-python.service
sudo systemctl disable --now mysql.service
sudo systemctl disable --now memcached.service 2>/dev/null || true
```

**s2（重いアプリ + 共有 memcached）**:

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

確認（役割どおりか）:

```bash
systemctl is-enabled nginx isu-python mysql isu-ruby memcached 2>/dev/null
systemctl is-active nginx isu-python mysql isu-ruby memcached 2>/dev/null
sudo ss -lntup | grep -E ':80|:8080|:3306|:11211'
```

- s1: 80 は listen。8080 は **無い**（unix socket にするのは次節）。11211 は無い（s2 を使う）
- s2: 8080 と 11211。80 / 3306 は無い
- s3: 3306。80 / 8080 / 11211 は無い

残っていたら、上の一覧で unit 名を特定して `disable --now`。

## 8. nginx / アプリ / 静的ファイル / MySQL

台間の通信はプライベート IP。セキュリティグループは同一 SG 内で 80 / 8080 / 3306 / 11211 を許可済み。

この節で変えるもの。**1 台ずつ入って、見出しの「どこで」に書いてあるコマンドだけ打つ。**

| 順 | どこで | 何を変える | 何が変わる |
| --- | --- | --- | --- |
| 1 | 作業機 | なし。IP を控える | 下の変数 |
| 2 | s3 | `/etc/mysql/mysql.conf.d/99-bind-all.cnf` を足す。ユーザ GRANT | MySQL が他台から見える |
| 3 | s1 と s2 | `/home/isucon/env.sh` に DB と memcached のアドレスを足す | アプリが localhost を見なくなる |
| 4 | s2 | `/etc/memcached.conf` の `-l`（足りなければ systemd drop-in） | s1 から 11211 が届く |
| 5 | s2 | `isu-python` を restart（ストックが `0.0.0.0:8080` なら unit は触らない） | env が載る。s1 から 8080 |
| 6 | s1 | systemd drop-in `unix.conf` を足して `daemon-reload` + restart | gunicorn が unix socket |
| 7 | s1 | `/etc/nginx/sites-enabled/isucon.conf` を書き換えて reload | 静的 / 軽い unix / 重い HTTP |

本体の unit ファイル（`/lib/systemd/system/isu-python.service` など）は編集しない。上書きは drop-in。MySQL の `mysqld.cnf` も本体は触らず drop-in。

### 1. 作業機 — プライベート IP を変数にする

```bash
cd /tmp/private-isu-terraform/terraform
terraform output webapp_private_ips
```

出てきた順が s1, s2, s3。**各台に入ったあと**、そのシェルで自分の値を入れる。下は例（`10.42.0.144` が s2、`10.42.0.177` が s3）。

```bash
APP_PRIV=10.42.0.144    # s2 のプライベート IP（重いアプリ、HTTP）
MC_PRIV=10.42.0.144     # 共有 memcached。既定は s2 なので APP_PRIV と同じ
DB_PRIV=10.42.0.177     # s3 のプライベート IP
WEB_PRIV=10.42.0.10     # s1 のプライベート IP（静的の rsync 用。後で使う）
SOCK=/home/isucon/tmp/gunicorn.sock
SITE=/etc/nginx/sites-enabled/isucon.conf
```

`SITE` が無ければ `ls /etc/nginx/sites-enabled`。AMI は `isucon.conf`。

### 2. MySQL（s3 だけ）

**目的:** ストックは `bind-address = 127.0.0.1`。s1/s2 から届かない。`0.0.0.0` にし、`isuconp@10.42.%` を作る。

**変えるファイル:** `/etc/mysql/mysql.conf.d/99-bind-all.cnf`（無ければ作る）。`mysqld.cnf` 本体は触らない。

s3 に入って:

```bash
# 今 127.0.0.1:3306 なら他台から届いていない
sudo ss -lntp | grep 3306

sudo test -f /etc/mysql/mysql.conf.d/mysqld.cnf.orig || \
  sudo cp -a /etc/mysql/mysql.conf.d/mysqld.cnf /etc/mysql/mysql.conf.d/mysqld.cnf.orig
sudo tee /etc/mysql/mysql.conf.d/99-bind-all.cnf >/dev/null <<'EOF'
[mysqld]
bind-address = 0.0.0.0
mysqlx-bind-address = 0.0.0.0
EOF
sudo systemctl restart mysql
sudo ss -lntp | grep 3306   # 0.0.0.0:3306 または *:3306 であること
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

VPC CIDR が `10.42.0.0/16` 以外なら `10.42.%` を合わせる。`SELECT` で `isuconp` / `10.42.%` が見えること。

### 3. アプリの env.sh（s1 と s2。同じ内容）

**目的:** ストックの `env.sh` には `ISUCONP_DB_USER` などだけで、ホストが無い。Python は未設定なら DB=`localhost`、memcached=`127.0.0.1:11211`。分割後は **DB=s3、memcached=s2**。

**変えるファイル:** `/home/isucon/env.sh`（unit の `EnvironmentFile=`）。`app.py` は、ストックどおり `os.environ.get` なら触らない。

s1 で 1 回、s2 で 1 回。変数 `DB_PRIV` / `MC_PRIV` は上で入れた値。

```bash
cat /home/isucon/env.sh
sudo test -f /home/isucon/env.sh.orig || sudo cp -a /home/isucon/env.sh /home/isucon/env.sh.orig
sudo sed -i '/^ISUCONP_DB_HOST=/d; /^ISUCONP_MEMCACHED_ADDRESS=/d' /home/isucon/env.sh
echo "ISUCONP_DB_HOST=${DB_PRIV}" | sudo tee -a /home/isucon/env.sh
echo "ISUCONP_MEMCACHED_ADDRESS=${MC_PRIV}:11211" | sudo tee -a /home/isucon/env.sh
grep ISUCONP_ /home/isucon/env.sh
```

`ISUCONP_DB_HOST` が s3、`ISUCONP_MEMCACHED_ADDRESS` が `s2のIP:11211` であること。

この時点では gunicorn はまだ古い環境のまま。**restart は下の 5. と 6. でやる**（`EnvironmentFile` は起動時に読む）。

`app.py` が `127.0.0.1:11211` 固定なら、`os.environ.get("ISUCONP_MEMCACHED_ADDRESS", "127.0.0.1:11211")` に戻すか、s2 のアドレスを直書きする。直したらそれも restart 後に載る。

### 4. memcached（s2 だけ）

**目的:** ストックは `-l 127.0.0.1`。s1 の gunicorn から届かない。`-l 0.0.0.0` にする。

**変えるファイル:** まず `/etc/memcached.conf`。`systemctl cat memcached.service` の `ExecStart` に `-l 127.0.0.1` がある回は conf だけでは足りないので drop-in。

s2 で:

```bash
systemctl cat memcached.service
sudo ss -lntp | grep 11211
sudo test -f /etc/memcached.conf.orig || sudo cp -a /etc/memcached.conf /etc/memcached.conf.orig
sudo sed -i 's/^-l .*/-l 0.0.0.0/' /etc/memcached.conf
grep '^-l' /etc/memcached.conf
sudo systemctl restart memcached.service
sudo ss -lntp | grep 11211   # 0.0.0.0:11211 であること
echo stats | nc -w 1 127.0.0.1 11211 | head
```

`ss` がまだ `127.0.0.1:11211` なら、unit 側の `-l` が勝っている。drop-in で上書きする。`ExecStart=` 空行は「元を消す」ため。パスと `-u` と `-m` は `systemctl cat` に合わせる。

```bash
systemctl cat memcached.service
sudo mkdir -p /etc/systemd/system/memcached.service.d
sudo tee /etc/systemd/system/memcached.service.d/listen.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/memcached -m 64 -p 11211 -u memcache -l 0.0.0.0
EOF
sudo systemctl daemon-reload
sudo systemctl restart memcached.service
sudo ss -lntp | grep 11211
```

### 5. s2 の gunicorn（HTTP :8080）

**目的:** env.sh を載せる。listen はストックが `-b 0.0.0.0:8080` なので、そのままで s1 から届く。ファイルは触らない。

s2 で:

```bash
systemctl cat isu-python.service
# ExecStart に -b 0.0.0.0:8080 があること。127.0.0.1 なら下の drop-in
sudo systemctl restart isu-python.service
curl -fsS -o /dev/null -m 5 "http://127.0.0.1:8080/" || \
  sudo journalctl -u isu-python.service -n 80 --no-pager
sudo ss -lntp | grep 8080   # 0.0.0.0:8080 であること
```

`ExecStart` が `127.0.0.1:8080` のときだけ:

```bash
sudo mkdir -p /etc/systemd/system/isu-python.service.d
sudo tee /etc/systemd/system/isu-python.service.d/bind.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/home/isucon/private_isu/webapp/python/.venv/bin/gunicorn app:app -b 0.0.0.0:8080 --log-file - --access-logfile -
EOF
sudo systemctl daemon-reload
sudo systemctl restart isu-python.service
sudo ss -lntp | grep 8080
```

### 6. s1 の gunicorn（unix socket）

**目的:** s1 では TCP 8080 を開けない。nginx が同じホストの unix socket に軽い処理を流す。

**変えるもの:**

1. ソケット用ディレクトリ `/home/isucon/tmp`（無ければ作る。nginx=`www-data` が触れる権限）
2. systemd drop-in `/etc/systemd/system/isu-python.service.d/unix.conf`（`ExecStart` を unix bind に上書き）
3. `daemon-reload`（drop-in を読ませる）
4. `restart isu-python`（新しい gunicorn が sock を作る。env.sh もここで載る）

本体の `/lib/systemd/system/isu-python.service` は編集しない。

s1 で、この順:

```bash
# 1. 現行。ストックは -b 0.0.0.0:8080
systemctl cat isu-python.service

# 2. ソケットの置き場。所有者 isucon、グループ www-data、ディレクトリは 775
mkdir -p /home/isucon/tmp
sudo chown isucon:www-data /home/isucon/tmp
sudo chmod 775 /home/isucon/tmp
```

`systemctl cat` の `ExecStart` が `.venv/bin/gunicorn` と `app:app` であることを確認する。違うときは次の drop-in のパスとモジュールだけ直す。

```bash
# 3. drop-in。ExecStart= の空行は元の -b 0.0.0.0:8080 を消す
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

# 4. drop-in を読ませる
sudo systemctl daemon-reload

# 5. 再起動。sock が作られ、8080 は閉じる
sudo systemctl restart isu-python.service

# 6. 確認
systemctl cat isu-python.service    # unix.conf が載っていること
ls -l /home/isucon/tmp/gunicorn.sock
sudo ss -lntp | grep 8080 || echo '8080 closed (expected)'
sudo -u www-data curl --unix-socket /home/isucon/tmp/gunicorn.sock \
  -fsS -o /dev/null -m 5 http://localhost/login || \
  sudo journalctl -u isu-python.service -n 80 --no-pager
```

ソケットは `isucon:www-data`、モード `770` が目安（`--umask 007`）。nginx が 502 なら:

```bash
sudo usermod -aG isucon www-data
sudo systemctl reload nginx
```

### 7. nginx（s1）— 静的 / 軽い unix / 重い HTTP

**目的:** ストックは全部 `proxy_pass http://localhost:8080;`。s1 の 8080 はもう無い。location で分ける。

**変えるファイル:** `$SITE`（既定 `/etc/nginx/sites-enabled/isucon.conf`）。ストックは短いので、**ファイル全体を置き換えてよい**。

s1 で:

```bash
ls -l /etc/nginx/sites-enabled
sudo cat "$SITE"
sudo test -f "${SITE}.orig" || sudo cp -a "$SITE" "${SITE}.orig"
```

`server 10.42.0.144:8080` を自分の `APP_PRIV` に直してから置く。

```bash
sudo tee "$SITE" >/dev/null <<EOF
server {
  listen 80;
  client_max_body_size 10m;
  root /home/isucon/private_isu/webapp/public/;

  upstream light {
    server unix:/home/isucon/tmp/gunicorn.sock;
  }

  upstream heavy {
    server ${APP_PRIV}:8080;
  }

  location /css/ { }
  location /js/ { }
  location /img/ { }
  location = /favicon.ico { }

  location = /login {
    proxy_set_header Host \$host;
    proxy_pass http://light;
  }
  location = /logout {
    proxy_set_header Host \$host;
    proxy_pass http://light;
  }
  location = /register {
    proxy_set_header Host \$host;
    proxy_pass http://light;
  }

  location / {
    proxy_set_header Host \$host;
    proxy_pass http://heavy;
  }
}
EOF
sudo nginx -t && sudo systemctl reload nginx
```

`tee` の heredoc で `$host` を nginx 変数のまま出すために `\$host` にしてある。手で書くなら `$host` でよい。

複数の重い app なら `upstream heavy` に `server` を並べる。`proxy_pass http://heavy;` のまま。

確認（s1）:

```bash
curl -fsS -o /dev/null -m 5 http://127.0.0.1/login
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/css/style.css || true
```

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

`/image` をファイルにしたら、上の `location /` より前に足す。未出力なら付けない（重いアプリへ proxy）。

```nginx
# location /image/ {
#   try_files $uri @app;
# }
# location @app {
#   proxy_set_header Host $host;
#   proxy_pass http://heavy;
# }
```

権限（nginx は `www-data`。`/home/isucon` が 750 だと 403）:

```bash
sudo chmod o+x /home/isucon /home/isucon/private_isu /home/isucon/private_isu/webapp
sudo chmod -R a+rX /home/isucon/private_isu/webapp/public
# 画像ディレクトリを足したら同様。所有者は isucon のままでよい
```

`/image` をファイルにするなら、アプリが書き込むディレクトリを nginx 台へ rsync する（または共有しない構成なら nginx 台だけで完結させる）。**tmpfs や `/dev/shm` に置かない**（次節）。

## 9. DB は起動順を仮定しない

ISUCON は再起動順を保証しない。アプリが先に上がってもよい。

- `After=mysql.service` は **同じホストの mysqld** 向け。リモート DB では付けない。
- systemd は `Restart=always`。接続失敗で落ちても DB 待ちで起き直す。
- アプリは接続を使い回さず、切れたら張り直す。

s1 と s2（アプリが動く台）:

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

## 10. ベンチ中の書き込みは再起動後も残す

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

## 11. ヘルスチェックと公式ベンチ

役割ごとに。失敗したら journalctl / nginx error / MySQL ログ。

```bash
# s3
mysql -uisuconp -pisuconp -e 'SELECT 1'
sudo ss -lntp | grep 3306

# s2（重いアプリ台から DB・memcached・自分）
mysql -uisuconp -pisuconp -h "$DB_PRIV" isuconp -e 'SELECT 1'
echo stats | nc -w 1 127.0.0.1 11211 | head
curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/
systemctl is-active isu-python.service memcached.service

# s1（入口。unix の軽い処理と、s2 への重い転送。memcached も s2）
sudo -u www-data curl --unix-socket /home/isucon/tmp/gunicorn.sock \
  -fsS -o /dev/null -m 5 http://localhost/login
curl -fsS -o /dev/null -m 5 "http://${APP_PRIV}:8080/"
echo stats | nc -w 1 "${MC_PRIV}" 11211 | head
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/login
curl -fsS -o /dev/null -m 5 http://127.0.0.1/css/style.css || true
# ブラウザは s1 のパブリック IP
```

公式ベンチの `-t` は **nginx 役**。分割後にアプリ台や DB 台の localhost へ向けない。

AMI にはベンチマーカーが入っている。CPU を食い始めたら [0010-env.md](0010-env.md) のとおりベンチ専用インスタンスを分ける。

```bash
# nginx 役、またはベンチ専用機。NGINX_URL は s1 の URL（同じホストなら http://localhost）
NGINX_URL=http://10.42.0.10
sudo su - isucon
/home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t "$NGINX_URL"
```

### bench-prep

コピーして値を直す。ログを空にしてから公式ベンチ。計測の中身は [0020-measure.md](../010_common/0020-measure.md)。

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
    if role_has "$role" web; then
      ssh -i "$SSH_KEY" "${SSH_USER}@${ip}" \
        'sudo -u www-data curl --unix-socket /home/isucon/tmp/gunicorn.sock -fsS -o /dev/null -m 5 http://localhost/login'
    else
      ssh -i "$SSH_KEY" "${SSH_USER}@${ip}" 'curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/'
    fi
  fi
  if role_has "$role" db || role_has "$role" both; then
    ssh -i "$SSH_KEY" "${SSH_USER}@${ip}" "mysql -u${MYSQL_USER} -p${MYSQL_PASSWORD} -e 'SELECT 1'"
  fi
  if role_has "$role" app && ! role_has "$role" web; then
    ssh -i "$SSH_KEY" "${SSH_USER}@${ip}" 'echo stats | nc -w 1 127.0.0.1 11211 | head'
  fi
done

# 公式ベンチは nginx 役の 1 台目へ。URL はパブリックでもプライベートでも可
WEB_IP="$(servers | awk '$3 ~ /(^|,)(web|both)(,|$)/ { print $2; exit }')"
echo "bench target: http://${WEB_IP}"
ssh -i "$SSH_KEY" "${SSH_USER}@${WEB_IP}" \
  "/home/isucon/private_isu/benchmarker/bin/benchmarker -u /home/isucon/private_isu/benchmarker/userdata -t http://localhost"
```

SSM のみなら、s1 に入って `curl` と `benchmarker -t http://localhost` を手で打つ。

## 12. リモートコマンドをループする

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
app_exec 'systemctl is-active isu-python.service'
db_exec 'mysql -uisuconp -pisuconp -e "SELECT 1"'
web_exec 'curl -fsS -o /dev/null -m 5 http://127.0.0.1/'
```

SSM で全台に同じシェルを投げる（CloudShell、`terraform/`）:

```bash
IDS=$(terraform output -json webapp_instance_ids | jq -r '.[]')
aws ssm send-command \
  --instance-ids $IDS \
  --document-name AWS-RunShellScript \
  --parameters commands='["hostname; systemctl is-active nginx mysql isu-python isu-ruby memcached 2>/dev/null; ss -lnt | grep -E \":80|:8080|:3306|:11211\" || true"]' \
  --region ap-northeast-1
```



TODO: サーバ側での作業が完了した後、アプリコード(.gitプロジェクト)と /etc の中の設定ファイルを.gitプロジェクトに移してgitレポジトリに挙げる操作を追加したい

TODO: gitリポジトリに上がっているものを特定のアプリディレクトリに配置する作業と、/etcの中の設定ファイルを置き換える手順を追記したい



## 備考

### 502 / nginx は生きているがアプリに届かない

```bash
grep -E 'proxy_pass|upstream|unix:' /etc/nginx/sites-enabled/*
ls -l /home/isucon/tmp/gunicorn.sock
sudo -u www-data curl -v --unix-socket /home/isucon/tmp/gunicorn.sock http://localhost/login
curl -v "http://${APP_PRIV}:8080/"
sudo journalctl -u isu-python.service -n 80 --no-pager
```

SG、gunicorn の bind（s1 は unix、s2 は 0.0.0.0:8080）、ソケット権限、`ISUCONP_DB_HOST`、`ISUCONP_MEMCACHED_ADDRESS`、mysqld の `bind-address`、memcached の `-l`。

### ログインできない / セッションが飛ぶ

s1 でログインしたあと `/` が s2。別の memcached を見ているとセッションが無い。`ISUCONP_MEMCACHED_ADDRESS` が s2 の `host:11211` か。s2 の `-l` が `127.0.0.1` のままなら s1 から届かない。s1 で `echo stats | nc -w 1 "${MC_PRIV}" 11211`。

### MySQL にアプリ台から入れない

`isuconp`@`localhost` だけになっていないか。`10.42.%` の GRANT。`ss` が `127.0.0.1:3306` のままなら bind-address。

### 再起動したら Ruby や mysqld が戻る

その台で `systemctl is-enabled`。`disable --now` 漏れ。

### `webapp_url` が古い / 1 台目しか止まらない

パブリック IP は停止→起動で変わる。`terraform apply -refresh-only`。停止は全台:

```bash
aws ec2 stop-instances --instance-ids $(terraform output -json webapp_instance_ids | jq -r '.[]')
```

