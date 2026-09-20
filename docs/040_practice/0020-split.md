# 0020 複数台に分割する

[0010-env.md](0010-env.md) で `webapp_instance_count = 3` として立てたあと。  
起動直後の AMI は各台がオールインワン（nginx + アプリ + MySQL）。この手順で役割を分け、余ったプロセスを止め、公式ベンチを nginx 役へ向ける。

方針は [0010-env.md](0010-env.md) と同じ。コマンドは Markdown のブロックのまま残す。ラッパーの `.sh` は置かない。

前提: 分割の前に **全台で Python に切り替える**（[4.](#4-python-に切り替える全台)）。手順は [webapp-setup/python.md](../010_common/webapp-setup/python.md) / [0010-setup.md](../010_common/0010-setup.md) と同じ。  
既定の練習は **3 台**。役割は入れ替えてよい。T 系は使わない（このリポジトリは `c7a.large`、`ap-northeast-1`）。

## 1. 台数と `config.env`

Terraform の既定は `webapp_instance_count = 3`。変えるときは `terraform.tfvars`。

```hcl
webapp_instance_count = 3   # 1 台に戻すなら 1。5 台なら 5
webapp_instance_type  = "c7a.large"
```

`apply` 済みなら `terraform apply` で台数が増減する。この手順書では apply しない。

出力の正本はリスト / マップ。1 台目のスカラー（`webapp_public_ip` など）は one-box 用の便宜。

IP はマネジメントコンソール（GUI）で確認する。手順：

1. 画面右上でリージョンが `ap-northeast-1` であることを確認する。
2. EC2 → Instances を開く。
3. 検索ボックスに `<name_prefix>-webapp-` と入れ、対象台に絞る（既定は `private-isu-webapp-1/2/3`。`terraform.tfvars` で `name_prefix` を変えていたら読み替える）。
4. State が `Running` であることを確認する。停止中はパブリック IP が空になる（起動で変わる）。
5. Public IPv4 address / Private IPv4 addresses 列を読む。列が出ていないときは右上の歯車（Preferences）で表示する。
6. 読み取った値をローカルのメモに写す。

読み取った値の用途：

- s1 のパブリック IP は必須（ブラウザで `webapp_url` を開く確認に使う）
- s2 / s3 のパブリック IP は SSH で入る場合に利用するので一応ローカルに控えておく
- プライベート IP は nginx の upstream・`-`・`MEMCACHED_HOST` に使う（同じ VPC）



サイト設定の実体は AMI では `isucon.conf`。無ければ `ls /etc/nginx/sites-enabled`。

基本的にEC2へのアクセスはEC2 &gt; 接続からアクセスできるSession Manager（GUI）を使う。



s1 に入って素振りのベンチを 1 本回す（動作確認用）:

```bash
# s1 の Session Manager シェルで実行
sudo su - isucon

/home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
```

通れば OK。失敗したら先に進まず [備考](#備考) を見る。

実行したら、スコアを手元にメモに控えておく

## 2. 各台で何が動いているかを確認する

AMI 直後は **全台** で nginx・MySQL・Ruby（`isu-ruby`）が enabled。Python 切替後は `isu-python` も。

各台で下記を実施し、情報を収集

```bash
# IPアドレス表示
ip -4 -br addr

# 各種サービスの表示
systemctl list-unit-files --type=service
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

Flask + gunicorn は `8080`、unit は `isu-python.service`。nginx が前段。初期に起動しているアプリはRuby.

## 3. 分割前のバックアップ

サーバ操作（次の Python 切替（[4.](#4-python-に切り替える全台)）を含む）を始める前に取る。これ以降で `/etc`・`env.sh`・gunicorn の bind を変える。**変える前に取る**。ローカルの orig と、GitHub の private リポジトリの 2 系統。

- **Linux の設定** — 各台で `~/kit-backup/` に `/etc/nginx` `/etc/mysql` `/etc/memcached.conf` systemd unit / drop-in、`env.sh` をコピーする。壊したらこのディレクトリから戻す。
- **アプリのファイル** — `webapp/`（`.venv` は除く）を GitHub の **private** リポジトリへ push する。設定のコピーも同じリポジトリの `<台名>/` に載せる。インスタンスを捨てても GitHub から戻せる。

public リポジトリにしない。`env.sh` と投稿データが入りうる。AMI のストックは残るが、自分の差分は残らない。

### 各台で `~/kit-backup`（Linux 設定）

s1 / s2 / s3 の **全部**。`isucon` で打つ。

```bash
TS=$(date +%Y%m%d%H%M%S)
BK=/home/isucon/kit-backup/$TS
mkdir -p "$BK"

# home directoryの内容をバックアップコピー。$BK の下に置く
cp -r $HOME /tmp/home_bk
mv /tmp/home_bk "$BK/"

# 設定ファイルを全量避難させる
sudo cp -r /etc "$BK/"

# unit の実体がどこにあるかを確認。 /etcにあるが、そのパスになっていることを cat で確認
systemctl cat isu-python.service

find "$BK" -maxdepth 2 -ls
```

### GitHub へ（アプリ + 設定）

`$BK` を丸ごと push しない。`sudo cp -r /etc` には `/etc/shadow`、`/etc/ssh/ssh_host_*_key`、`/etc/sudoers.d` が入る。private リポジトリでも鍵とパスワードハッシュは置かない。**必要な設定だけを allowlist で拾う**。

例では `k-hoshihara/isucon2026` を使う。自分のリポジトリに読み替える。**private であることを先に確認する**（`env.sh` に DB 認証情報が入る）。

```bash
gh repo view k-hoshihara/isucon2026 --json visibility
```

#### 認証（各台で 1 回）

```bash
command -v gh
```

`gh` があるとき。手元のブラウザにコードを入れるだけで通る。

```bash
gh auth login        # GitHub.com → HTTPS → device code
gh auth setup-git
```

無いとき。fine-grained PAT（Contents: Read and write）を作り、credential helper に覚えさせる。

```bash
git config --global credential.helper store
git clone https://github.com/k-hoshihara/isucon2026.git /home/isucon/isucon2026
# Username: <GitHub ユーザー名> / Password: <PAT>
```

PAT は `~/.git-credentials` に平文で残る。捨てるインスタンスなので競技中は許容する。終わったら revoke する。

#### リポジトリに取り込む（各台）

`HOST` を台ごとに変える。`TS` は `~/kit-backup` の下のディレクトリ名（`ls /home/isucon/kit-backup` で見る）。

```bash
TS=<kit-backup の下のディレクトリ名>
BK=/home/isucon/kit-backup/$TS
REPO=/home/isucon/isucon2026
HOST=s1   # 台ごとに s1 / s2 / s3

[ -d "$REPO/.git" ] || git clone https://github.com/k-hoshihara/isucon2026.git "$REPO"

# sudo cp で作ったので root 所有。isucon から読めるようにする
sudo chown -R isucon:isucon "$BK"

D="$REPO/$HOST"
mkdir -p "$D/etc" "$D/home"

# /etc は必要なものだけ拾う。shadow / ssh host key / sudoers は入れない
for p in nginx mysql memcached.conf sysctl.d systemd/system security/limits.conf; do
  src="$BK/etc/$p"
  [ -e "$src" ] || continue
  mkdir -p "$D/etc/$(dirname "$p")"
  rm -rf "$D/etc/$p"
  cp -a "$src" "$D/etc/$p"
done

# アプリと env.sh。.venv と .git は除く
rsync -a --delete --exclude '.venv/' --exclude '.git/' --exclude 'node_modules/' \
  "$BK/home_bk/private_isu/" "$D/home/private_isu/"
cp -a "$BK/home_bk/env.sh" "$D/home/env.sh"

du -sh "$D"
find "$D" -type f -size +50M   # 出たら push 前に外す。GitHub は 100MB 超を拒否する
```

`go/`・`backup/`・`kit-backup/` は入れない。ビルド成果物とバックアップの入れ子で膨らむだけ。要るものが他にあれば個別に足す。

#### push

```bash
cd "$REPO"
git config user.name  "<GitHub ユーザー名>"
git config user.email "<GitHub に登録したメールアドレス>"

git add -A
git commit -m "$HOST: 分割前ベースライン ($TS)"
git pull --rebase origin main   # 他の台が先に push していることがある
git push -u origin main
```

3 台が同じリポジトリに push する。順番にやるか、毎回 `git pull --rebase` を挟む。`$HOST` でディレクトリが分かれるので中身は衝突しない。

空のリポジトリを作った直後で `clone` できないときだけ、`$REPO` で `git init -b main` と `git remote add origin <URL>` を先に打つ。

#### 戻す

```bash
sudo cp -a /home/isucon/isucon2026/s1/etc/nginx /etc/
sudo nginx -t && sudo systemctl reload nginx
```

`~/kit-backup/` が生きていればそちらが速い。GitHub 側はインスタンスを捨てた後の保険。

## 4. Python に切り替える（全台）

AMI 直後は Ruby。分割の前に **3 台とも** Python にする。unit 名は回ごとに違うので、先に一覧を見る。

各台で:

```bash
# serviceの全量を確認
systemctl list-unit-files --type=service

# isu- とついているserviceを確認
systemctl list-unit-files --type=service | grep -Ei 'isu'
systemctl cat isu-python.service
```

```bash
# 他のserviceを停止させ、gunicornのServiceのみ起動させる
sudo systemctl disable --now isu-ruby.service
sudo systemctl disable --now isu-go.service 2>/dev/null || true
sudo systemctl disable --now isu-node.service 2>/dev/null || true
sudo systemctl enable --now isu-python.service

# 起動確認
systemctl list-unit-files --type=service | grep -Ei 'isu'

cd /home/isucon/private_isu/webapp/python

# 依存を .venv に同期する（pyproject.toml / uv.lock → .venv）。
# sudo -u isucon なのは作業ファイルの所有者が isucon のため。サービスは .venv/bin/gunicorn を直接起動するので同期しないと反映されない
command -v uv >/dev/null && sudo -u isucon uv sync

# gunicorn が 8080 で応答するか確認する（-f は HTTP エラーで失敗、-sS は進捗を消してエラーを出す、-o /dev/null は本文を捨てる、-m 5 は 5 秒でタイムアウト）。失敗したら unit ログの直近 80 行を見て原因を特定する
curl http://127.0.0.1:8080/

curl -fsS -o /dev/null -m 5 http://127.0.0.1:8080/ || \
  sudo journalctl -u isu-python.service -n 80 --no-pager
```



## 5. 役割を考える

基本的にはs1 が入口、s2 が重いアプリ、s3 が DB。


| 役割           | 起動状態にする                                                                 | 停止状態にする                                         |
| ------------ | ----------------------------------------------------------------------- | ----------------------------------------------- |
| s1 `web,app` | nginx。`public/` の静的。gunicorn は **unix socket**（軽い処理）。memcached は s2 を使う | MySQL。ローカル memcached。HTTP の 8080 は開けない（unix だけ） |
| s2 `app`     | gunicorn **:8080**（重い処理）。共有 memcached（Flask-Session）                    | nginx、MySQL、Ruby                                |
| s3 `db`      | MySQL                                                                   | nginx、gunicorn、Ruby、memcached                   |


- `/css` `/js` `/img` `/favicon.ico` → ディスク（s1）
- 軽いアプリ（ログインなど）→ s1 の unix socket
- 残り（`/`、`/posts`、`/image`、投稿）→ s2 の `http://APP_PRIV:8080`

何を軽い処理にするかはパフォーマンスチューニングのログを見て入れ替える。

セッション情報の取り扱いをするため、基本的には共有memcachedを起動させる。

また、memcachedへのアクセス頻度が最も多い処理を回す計算機に共有memcached serviceを起動させる。（おそらく重い処理を回すプロセスがmemcachedアクセス回数が多い？と想定されるのでチュートリアルではs2に入れる。 ただし実測をしてみるとs1に入れる方が良い可能性もあるためログを見て判断する）



## 6. 分割前のベースライン

Python 切替だけ済んだ状態で公式ベンチ 1 本実施し、あとの（分割後）と比べる。

s1 で下記を実行

```bash
sudo su - isucon
/home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
```

点数は手元のローカルメモに残す

## 7. 余ったプロセスを止める

`disable --now` にする。`stop` だけだと再起動で戻る（[python.md](../010_common/webapp-setup/python.md)）。

gunicorn はワーカーが別プロセス。セッションやキャッシュをプロセスメモリに置くとワーカー間で見えない。**アプリ層は memcached を使う**。実体は s2 に 1 本。s1 の軽い gunicorn も s2 を見る。s1 で memcached を `disable` するのは「ローカルの二本目を立てない」ためで、アプリが memcached を使わないことではない。

```bash
systemctl list-unit-files --type=service --no-legend | grep -Ei 'isu|python|ruby|go|nginx|mysql|memcached'

# 見やすく表示
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

# 確認（役割どおりか）
systemctl is-enabled nginx isu-python mysql isu-ruby memcached 2>/dev/null
systemctl is-active nginx isu-python mysql isu-ruby memcached 2>/dev/null
sudo ss -lntup | grep -E ':80|:8080|:3306|:11211'
```



**s2（重いアプリ + 共有 memcached）**:

```bash
sudo systemctl enable --now isu-python.service
sudo systemctl enable --now memcached.service
sudo systemctl disable --now nginx.service
sudo systemctl disable --now mysql.service

# 確認（役割どおりか）
systemctl is-enabled nginx isu-python mysql isu-ruby memcached 2>/dev/null
systemctl is-active nginx isu-python mysql isu-ruby memcached 2>/dev/null
sudo ss -lntup | grep -E ':80|:8080|:3306|:11211'
```



**s3（db）**:

```bash
sudo systemctl enable --now mysql.service
sudo systemctl disable --now nginx.service
sudo systemctl disable --now isu-python.service
sudo systemctl disable --now memcached.service 2>/dev/null || true

# 確認（役割どおりか）
systemctl is-enabled nginx isu-python mysql isu-ruby memcached 2>/dev/null
systemctl is-active nginx isu-python mysql isu-ruby memcached 2>/dev/null
sudo ss -lntup | grep -E ':80|:8080|:3306|:11211'
```



## 8. nginx / アプリ / 静的ファイル / MySQL

台間の通信はプライベート IP。セキュリティグループは同一 SG 内で 80 / 8080 / 3306 / 11211 を許可済み。

この節で変えるもの。**1 台ずつ入って、見出しの「どこで」に書いてあるコマンドだけ打つ。**


| 順   | どこで     | 何を変える                                                      | 何が変わる                  |
| --- | ------- | ---------------------------------------------------------- | ---------------------- |
| 1   | 作業機     | なし。IP を控える                                                 | 下の変数                   |
| 2   | s3      | `/etc/mysql/mysql.conf.d/99-bind-all.cnf` を足す。ユーザ GRANT    | MySQL が他台から見える         |
| 3   | s1 と s2 | `/home/isucon/env.sh` に DB と memcached のアドレスを足す            | アプリが localhost を見なくなる  |
| 4   | s2      | `/etc/memcached.conf` の `-l`（足りなければ systemd drop-in）       | s1 から 11211 が届く        |
| 5   | s2      | `isu-python` を restart（ストックが `0.0.0.0:8080` なら unit は触らない） | env が載る。s1 から 8080     |
| 6   | s1      | systemd drop-in `unix.conf` を足して `daemon-reload` + restart | gunicorn が unix socket |
| 7   | s1      | `/etc/nginx/sites-enabled/isucon.conf` を書き換えて reload       | 静的 / 軽い unix / 重い HTTP |


本体の unit ファイル（`/lib/systemd/system/isu-python.service` など）は編集しない。上書きは drop-in。MySQL の `mysqld.cnf` も本体は触らず drop-in。

### 1. 作業機 — プライベート IP を変数にする

[1.](#1-台数と-configenv) で控えたプライベート IP を変数に入れる。**各台に入ったあと**、そのシェルで自分の値を入れる。下は例（`10.42.0.144` が s2、`10.42.0.177` が s3）。

```bash
APP_PRIV=10.42.0.112    # s2 のプライベート IP（重いアプリ、HTTP）
MC_PRIV=10.42.0.112     # 共有 memcached。既定は s2 なので APP_PRIV と同じ
DB_PRIV=10.42.0.47     # s3 のプライベート IP
WEB_PRIV=10.42.0.197     # s1 のプライベート IP（静的の rsync 用。後で使う）
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

# 元ファイルを .orig として退避
sudo test -f /etc/mysql/mysql.conf.d/mysqld.cnf.orig || \
  sudo cp -a /etc/mysql/mysql.conf.d/mysqld.cnf /etc/mysql/mysql.conf.d/mysqld.cnf.orig

# IPアドレスのbind設定を変更
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

VPC CIDR が `10.42.0.0/16` 以外なら `10.42.%` を合わせる。

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



TODO: ここまでは目を通しているが、以降はあまり魂を込められていない。 適宜修正を実施する想定



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

分割前の記録（[6.](#6-分割前のベースライン)）と同じ条件で回し、点数を比べる。条件を揃える：同じ benchmarker と userdata、`-t` は nginx 役（既定は s1 の `http://localhost`）、下の bench-prep でログを切ってから。

```text
# 分割後（11.）
日時:
-t:
score:
pass/fail・エラー:
```

分割が効いていればスコアは変わる。**変わらないときは分割が効いていない**（upstream が localhost のまま、余ったプロセスが生きている、DB・memcached を見に行っていない等）。[備考](#備考) の切り分けに戻る。

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

SSM で全台に同じシェルを投げる（AWS CLI のある環境、`terraform/`）:

```bash
IDS=$(terraform output -json webapp_instance_ids | jq -r '.[]')
aws ssm send-command \
  --instance-ids $IDS \
  --document-name AWS-RunShellScript \
  --parameters commands='["hostname; systemctl is-active nginx mysql isu-python isu-ruby memcached 2>/dev/null; ss -lnt | grep -E \":80|:8080|:3306|:11211\" || true"]' \
  --region ap-northeast-1
```



## 13. 作業結果を git リポジトリに上げる・戻す

サーバ側の作業が一区切りついたら、アプリと触った `/etc` を GitHub の private リポジトリ（[3.](#3-分割前のバックアップ) の `private-isu-app.git`。以下 `GIT_URL`）に上げる。`/etc` は `configs/<sN>/etc/` に集める。unit を変えたら戻す側で `daemon-reload` を忘れない。

上げる（各台。`NAME` は s1 / s2 / s3）:

```bash
STASH=/home/isucon/app-stash
NAME=s1   # s2 / s3 なら変える
rm -rf "$STASH"
git -c "http.extraHeader=Authorization: Bearer ${GH_TOKEN}" clone "$GIT_URL" "$STASH"

rsync -a --exclude '.venv/' --exclude '__pycache__/' --exclude '.git/' \
  /home/isucon/private_isu/webapp/ "$STASH/webapp/"

ETC="$STASH/configs/$NAME/etc"
sudo mkdir -p "$ETC/nginx/sites-enabled" "$ETC/mysql/mysql.conf.d" "$ETC/systemd/system"
sudo cp -a /etc/nginx/sites-enabled/isucon.conf "$ETC/nginx/sites-enabled/" 2>/dev/null || true
sudo cp -a /etc/mysql/mysql.conf.d/99-bind-all.cnf "$ETC/mysql/mysql.conf.d/" 2>/dev/null || true
sudo cp -a /etc/memcached.conf "$ETC/" 2>/dev/null || true
sudo cp -a /etc/systemd/system/isu-python.service.d "$ETC/systemd/system/" 2>/dev/null || true
sudo cp -a /etc/systemd/system/memcached.service.d "$ETC/systemd/system/" 2>/dev/null || true
sudo chown -R "$(id -un):$(id -gn)" "$STASH"

cd "$STASH"
git config user.email "isucon@localhost"
git config user.name "isucon"
git add -A
git status
git commit -m "$NAME working tree: app + etc"
git -c "http.extraHeader=Authorization: Bearer ${GH_TOKEN}" pull --rebase origin main
git -c "http.extraHeader=Authorization: Bearer ${GH_TOKEN}" push origin main
```

`GH_TOKEN` / `GIT_URL` は [3.](#3-分割前のバックアップ) と同じ。打ち終わったら `unset GH_TOKEN`。URL にトークンを埋め込んだら `git remote set-url origin "$GIT_URL"` で消す。

戻す（配置先の台。`NAME` はその台）:

```bash
STASH=/home/isucon/app-stash
NAME=s1   # その台に変える
cd "$STASH"
git -c "http.extraHeader=Authorization: Bearer ${GH_TOKEN}" pull --rebase origin main

rsync -a --exclude '.git/' "$STASH/webapp/" /home/isucon/private_isu/webapp/
# python/.venv は上げていないので、必要なら uv sync
cd /home/isucon/private_isu/webapp/python
command -v uv >/dev/null && sudo -u isucon uv sync

ETC="$STASH/configs/$NAME/etc"
test -f "$ETC/nginx/sites-enabled/isucon.conf" && \
  sudo cp -a "$ETC/nginx/sites-enabled/isucon.conf" /etc/nginx/sites-enabled/isucon.conf
test -f "$ETC/mysql/mysql.conf.d/99-bind-all.cnf" && \
  sudo cp -a "$ETC/mysql/mysql.conf.d/99-bind-all.cnf" /etc/mysql/mysql.conf.d/99-bind-all.cnf
test -f "$ETC/memcached.conf" && \
  sudo cp -a "$ETC/memcached.conf" /etc/memcached.conf
test -d "$ETC/systemd/system/isu-python.service.d" && \
  sudo cp -a "$ETC/systemd/system/isu-python.service.d" /etc/systemd/system/
test -d "$ETC/systemd/system/memcached.service.d" && \
  sudo cp -a "$ETC/systemd/system/memcached.service.d" /etc/systemd/system/

sudo systemctl daemon-reload
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl restart isu-python.service
systemctl is-active nginx isu-python.service
```

unit（drop-in 含む）を置き換えたら必ず `daemon-reload`。`nginx -t` が通ってから reload / restart。DB 台（s3）は `mysql` の再起動が必要なときだけ `sudo systemctl restart mysql`（アプリが繋いでいる時間帯は避ける）。



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

