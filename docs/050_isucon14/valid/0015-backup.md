# 0015 変更をバックアップする

0010 で環境ができたら、0020 へ進む前に「戻れる場所」を用意します。以降の章は、作業機（`app-1`）の `/etc` と `~/webapp` を直接書き換えます。変更がその台のディスクにしかないと、0110 で 3 台に分けるときに `app-2` / `app-3` へ配れません。出口は [0020-https.md](0020-https.md) です。

?> 040 と同じ流儀です。リポジトリの中に台ごとの置き場を作り、変更を積んでいきます（[../040_practice/0110-split-1.md](../../040_practice/0110-split-1.md) §3）。

## なぜ要るか

- **0110 の分割で必要になります。** `app-2` / `app-3` は AMI の直後の状態なので、0050 のインデックスも 0070〜0100 の Python の直しも入っていません。揃えないと 3 台に分けた意味が出ません
- **壊したときに戻せます。** 0060 の計測中に設定を触って `pass=false` になったとき、基準線があると切り分けが 1 手で済みます
- **どの変更が効いたかを残せます。** 1 手 = 1 コミットにしておくと、`git diff` で「何を変えたら何点になったか」を後から読めます

## 1. 置き場

リポジトリの `backup/isucon14/` に置きます。

| 置き場 | 中身 | 誰が作るか |
| --- | --- | --- |
| `backup/isucon14/base/` | 0010 の直後（AMI のまま）の状態。基準線。**書き換えない** | この章で 1 回 |
| `backup/isucon14/s1/` | `app-1` で残すと決めた変更 | 各章の最後に更新 |
| `backup/isucon14/s2/` | `app-2`（アプリ）の状態 | **0160-split.md**（この章はこれから） で作る |
| `backup/isucon14/s3/` | `app-3`（アプリ）の状態 | 同上 |

入るのは「自分で書き換えるもの」だけです。アプリのソース（`webapp/python/app`、`webapp/sql`）、`env.sh`、nginx の設定、systemd の unit、MySQL の設定です。`/etc` を丸ごとは取りません（`shadow` やホスト鍵を抱えないためです）。

?> `env.sh` に DB の認証情報が入ります。教材の値（`isucon` / `isucon`）以外を書いたなら、そのリポジトリを public にしないでください。

## 2. 作業機にリポジトリを置きます

作業機（`app-1`）で行います。

```bash
sudo apt-get update -y && sudo apt-get install -y git
REPO=/home/isucon/private-isu-terraform
[ -d "$REPO/.git" ] || git clone https://github.com/k-hoshihara/private-isu-terraform.git "$REPO"
cd "$REPO"
git checkout -b feature/backup
git config user.name  "<GitHub ユーザー名>"
git config user.email "<GitHub のメール>"
git status --short
```

成功の見え方: `On branch feature/backup` になります。`git remote -v` が `k-hoshihara/private-isu-terraform` を指します。

?> `push` には認証が要ります。[../040_practice/0110-split-1.md](../../040_practice/0110-split-1.md) §3 と同じく `gh auth login` を使うか、`git remote set-url origin https://<ユーザー名>:<トークン>@github.com/k-hoshihara/private-isu-terraform.git` にします。`gh config` が `root` 所有になっていると `permission denied` になります。`sudo chown -R isucon:isucon /home/isucon/.config/gh` で直します。

## 3. 基準線（base）を取ります

`backup/isucon14/snapshot.sh` が、いまの台の「残すファイル」を `backup/isucon14/<名前>/` に写します。

```bash
cd /home/isucon/private-isu-terraform
sudo ./backup/isucon14/snapshot.sh base
git add backup/isucon14/base
git commit -m 'backup/isucon14/base: 0010 の直後の状態'
git push -u origin feature/backup
```

成功の見え方: `wrote .../backup/isucon14/base` のあとに、写したファイルの一覧が出ます。`env.sh`、`webapp/python/app/*.py`、`webapp/sql/*`、nginx と systemd の設定が含まれます。

?> `snapshot.sh` はリポジトリのルートを自分の位置から決めます。`backup/isucon14/` ごと動かすと動きません。

## 4. 変更を s1 に積みます

0020 以降の章で「これは残す」と決めたら、その章の最後に 1 回だけ実行します。

```bash
cd /home/isucon/private-isu-terraform
sudo ./backup/isucon14/snapshot.sh s1
git add backup/isucon14/s1
git commit -m 'backup/isucon14/s1: <何を変えたか>'
git push
```

- **1 手 = 1 コミット**にします。まとめて積むと、どの変更が効いたか読めなくなります
- 試して戻した変更は積みません。`s1` は「いま残っている状態」の写しです
- どの章で何を積むかの対応は **0210-result.md**（この章はこれから） の表にまとめます

## 5. 戻すには

`s1` の内容を今の台に戻します。

```bash
cd /home/isucon/private-isu-terraform
git pull --ff-only
sudo ./backup/isucon14/restore.sh s1
sudo systemctl daemon-reload
sudo systemctl restart isuride-python isuride-matcher nginx mysql
```

基準線まで戻したいときは `base` を指定します。

```bash
sudo ./backup/isucon14/restore.sh base
```

成功の見え方: `restored s1 from /home/isucon/private-isu-terraform/backup/isucon14/s1` と、このあと叩く `daemon-reload` / 再起動のコマンドが表示されます。実測では、この手順で `app-2` / `app-3` のアプリ 3 ファイルが `app-1` と md5 まで一致しました（`0160-split.md` の手順 2-2）。

?> `restore.sh` は `/etc` と `~/webapp` を上書きします。いま編集中の内容は消えます。先に `snapshot.sh s1` で写しておけば、`git diff` で差分を見られます。

?> **`base` は公式リポジトリと突き合わせて確かめます。**`base` を「台の現物」から取ると、**その台にあとから入れた変更が混ざります**（実測で、`base` の `chair_handlers.py` / `middlewares.py` / `owner_handlers.py` が調整後のコードになっていました。`base` を取ったあとに 3 台へ配ったためです）。公式リポジトリ（AMI の中身の正本）と md5 を比べると分かります。

```bash
# ベンチ用インスタンスに clone した公式リポジトリと比べる
ssh -i ~/.ssh/isucon14.pem ubuntu@${BENCH_IP} \
  'md5sum /home/isucon/isucon14-src/webapp/python/app/*.py'
md5sum ~/private-isu-terraform/backup/isucon14/base/home/isucon/webapp/python/app/*.py
```

?> **復元したあとは、ミドルウェアを再起動します。**設定ファイルを戻しても、**動いているプロセスは古い設定のまま**です（実測で、`isuride.conf` を戻したのに nginx が古い `upstream`（app-2 / app-3）へ転送し続けて、ベンチの `initialize` が 500 になりました）。`sudo systemctl reload nginx` と、`mysqld.cnf` を戻したときの `sudo systemctl restart mysql` を忘れないでください。MySQL を再起動したら、アプリのプールに死んだ接続が残るので、3 台で `sudo systemctl restart isuride-python.service` も行います（**0210-result.md**（この章はこれから） §4）。

## 6. 3 台に分けたあとは

**0160-split.md**（この章はこれから） で `app-2` / `app-3` を足したら、同じスクリプトで配ります。

- `app-2` / `app-3` にもリポジトリを clone します
- `app-2` / `app-3` で `restore.sh s1` を叩くと、調整済みのアプリと SQL が入ります。配るパスは 3 台で同じなので、`s1` の中身をそのまま使えます
- 台ごとに違うもの（`gunicorn.conf.py` の `bind`、`env.sh` の `ISUCON_DB_HOST`）を直したあと、`snapshot.sh s2` / `snapshot.sh s3` で台ごとの状態を取ります

## 出口

基準線と `s1` がリポジトリに入ったら [0020-https.md](0020-https.md) へ進みます。次は TLS 証明書を差し替えて HTTPS を通します。
