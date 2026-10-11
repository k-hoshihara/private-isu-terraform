# 0020 TLS 証明書を差し替える

ISUCon 14 のベンチマーカーは HTTPS でアクセスし、TLS の証明書を検証します。AMI に入っている `*.xiv.isucon.net` の証明書は失効済みなので、ここを直さないとベンチが動きません。ここを直したら [0030-python.md](0030-python.md) へ進みます。

## 失効している理由

AMI 作成時に Let's Encrypt で発行された証明書で、有効期限が 2025 年 2 月 1 日でした。競技終了後も同じ証明書を AMI に入れているため、AMI を使い続けると必ず失効します。

```bash
sudo openssl x509 -in /etc/nginx/tls/_.xiv.isucon.net.crt -noout -subject -issuer -dates
```

```text
subject=CN=*.xiv.isucon.net
issuer=C=US, O=Let's Encrypt, CN=R10
notBefore=Nov  3 00:21:03 2024 GMT
notAfter=Feb  1 00:21:02 2025 GMT
```

`notAfter` が過去なら失効しています。ベンチは `bench/benchmarker/webapp/client.go` で Go の既定の Transport を clone するだけで、`InsecureSkipVerify` を有効にする手段は持っていません。したがって、証明書側を直します。

?> 本番のドメイン `isuride.xiv.isucon.net` は取得できないため、練習では自己署名の証明書を作ります。ベンチは検証を通る必要があるだけなので、自己署名で構いません。ブラウザは警告を出しますが「続行」で開けます。

## 1. 自己署名の証明書を作ります

`nginx` の vhost は `server_name xiv.isucon.net;` と `server_name *.xiv.isucon.net;` の 2 つでアプリに転送します。どちらのホスト名でも通るように SAN に 2 つ書きます。

```bash
sudo openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 3650 \
  -keyout /etc/nginx/tls/isuride.key \
  -out /etc/nginx/tls/isuride.crt \
  -subj "/CN=isuride.xiv.isucon.net" \
  -addext "subjectAltName=DNS:isuride.xiv.isucon.net,DNS:*.xiv.isucon.net"

sudo openssl x509 -in /etc/nginx/tls/isuride.crt -noout -dates -ext subjectAltName
```

成功の見え方: `notAfter` が 10 年後になっている行と、`X509v3 Subject Alternative Name` の `DNS:isuride.xiv.isucon.net, DNS:*.xiv.isucon.net` が出ます。

## 2. nginx の設定を差し替えます

`/etc/nginx/sites-enabled/isuride.conf` は `/etc/nginx/sites-available/isuride.conf` への symlink です。sites-available 側を直します。symlink を `cp` で上書きすると通常ファイルになってしまうので、`cp` ではなく `sed` でその場で書き換えます。

```bash
sudo cp -a /etc/nginx/sites-available/isuride.conf /etc/nginx/sites-available/isuride.conf.orig
sudo sed -i \
  -e 's|/etc/nginx/tls/_\.xiv\.isucon\.net\.crt|/etc/nginx/tls/isuride.crt|' \
  -e 's|/etc/nginx/tls/_\.xiv\.isucon\.net\.key|/etc/nginx/tls/isuride.key|' \
  /etc/nginx/sites-available/isuride.conf
grep -n 'ssl_certificate' /etc/nginx/sites-available/isuride.conf
sudo nginx -t
sudo systemctl reload nginx
```

`grep` の結果は 4 行です。1〜2 行目が bot 避け用の dummy、3〜4 行目が `isuride.crt` と `isuride.key` になっていることを確認します。

成功の見え方: `nginx -t` が `syntax is ok` / `test is successful` を出し、reload でエラーが出ません。

## 3. アプリサーバ自身に証明書を信頼させます

マッチング用の systemd サービス `isuride-matcher.service` は `curl -s https://isuride.xiv.isucon.net/api/internal/matching` を繰り返し叩きます。アプリサーバ自身の CA 信頼ストアに証明書を入れないと matcher が全部失敗します。

```bash
sudo cp /etc/nginx/tls/isuride.crt /usr/local/share/ca-certificates/isucon14.crt
sudo update-ca-certificates
```

成功の見え方: `update-ca-certificates` の最後で `isucon14.crt` を含む更新数が 1 増えます。

## 4. 証明書が検証されるか確認します

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://isuride.xiv.isucon.net/
curl -sS -o /dev/null -w '%{http_code}\n' https://isuride.xiv.isucon.net/api/internal/matching
```

成功の見え方:

- 1 行目が `200`。`/` は `try_files $uri /index.html` でフロントエンドの `index.html` が出ます
- 2 行目が `204`。`/api/internal/` は `allow 127.0.0.1; deny all;` なので、loopback からの `GET /api/internal/matching` だけが通ります。アプリに届いている証拠です
- どちらかが `000` や `error` なら `sudo update-ca-certificates` が効いていません。3 をやり直します

## 5. ベンチ用インスタンスにも配ります

ベンチは別のインスタンスから `https://isuride.xiv.isucon.net` に接続します。接続のたびに証明書が検証されるので、ベンチ機側でも CA に登録します。手元 PC を経由して渡します。

アプリ用サーバで、isucon が読めるように証明書を取り出します。

```bash
sudo cat /etc/nginx/tls/isuride.crt > ~/isuride.crt
ls -l ~/isuride.crt
```

手元 PC に戻って送ります（`APP_IP` / `BENCH_IP` は 0010 で書いた `~/isucon14/env.sh` に入っています）。

```bash
scp -i ~/.ssh/isucon14.pem ubuntu@${APP_IP}:~/isuride.crt ./isuride.crt
scp -i ~/.ssh/isucon14.pem ./isuride.crt ubuntu@${BENCH_IP}:/tmp/isuride.crt
```

ベンチ用インスタンスにログインして登録します。

```bash
ssh -i ~/.ssh/isucon14.pem ubuntu@${BENCH_IP}
sudo cp /tmp/isuride.crt /usr/local/share/ca-certificates/isucon14.crt
sudo update-ca-certificates
rm /tmp/isuride.crt
```

ベンチ機からアプリサーバへつなげられるか確認します。ベンチは `--addr` で接続先を差し替えるので、ベンチ機の `/etc/hosts` は書き換えません。`curl` の `--resolve` と同じ考え方です。

```bash
APP_IP='203.0.113.10'   # 0010 の手順 9 で控えたアプリ用サーバのグローバル IP
curl -sS -o /dev/null -w '%{http_code}\n' \
  --resolve isuride.xiv.isucon.net:443:${APP_IP} \
  https://isuride.xiv.isucon.net/
```

成功の見え方: `200` が返ります。`x509` や `certificate` のエラーが出たら、CA 登録をやり直します。

?> `--resolve` を使わずに検証したい場合は、ベンチ機の `/etc/hosts` に `${APP_IP} isuride.xiv.isucon.net` を足して構いません。どちらでも構いません。

## 6. ブラウザでも開いておきます

手元 PC の `hosts` ファイルに 1 行足します。ブラウザは自己署名で警告を出します。

```text
203.0.113.10 isuride.xiv.isucon.net
```

- Mac / Linux: `/etc/hosts`
- Windows: `C:\Windows\System32\drivers\etc\hosts`

変更を反映させるにはブラウザを再起動します。`https://isuride.xiv.isucon.net` を開き、警告で「詳細」→「進む」を選びます。ログイン画面が出るはずです。

?> 内部 IP を直打ちするのではなく、`isuride.xiv.isucon.net` という名前で到達することが必要です。IP を直打ちすると nginx の default_server に繋がり、`/api/` がアプリに転送されません。

## 元に戻すには

失効した証明書に戻すには `.orig` を戻します。ベンチが動かなくなったときの保険です。

```bash
sudo cp -a /etc/nginx/sites-available/isuride.conf.orig /etc/nginx/sites-available/isuride.conf
sudo nginx -t && sudo systemctl reload nginx
sudo rm /usr/local/share/ca-certificates/isucon14.crt
sudo update-ca-certificates --fresh
```

## 出口

`curl` で `200` と `204` が返ったら [0030-python.md](0030-python.md) へ進みます。次は Go 実装を止めて Python 実装を起動します。
