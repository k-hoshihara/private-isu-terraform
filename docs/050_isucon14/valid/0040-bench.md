# 0040 ベンチマーカーをビルドして 1 本回す

ベンチマーカー（`bench`）は Go で書かれています。ベンチ用インスタンスでビルドして 1 本回したら [0060-sql-index.md](0060-sql-index.md) へ進みます。

## 1. ベンチ用インスタンスにログインします

0010-env.md で立てた 2 台のうちの、ベンチ用（`c5.xlarge`）です。アプリ用とは別接続です。

```bash
ssh -i ~/.ssh/isucon14.pem ubuntu@${BENCH_IP}
sudo su - isucon
cat ~/env.sh
```

`env.sh` には DB の接続設定が入っています。ベンチはこのファイルを見ません。ベンチは DB を直接触りません。

ベンチは 2 つの IP を使います（対象サーバの IP と、自分自身の IP）。ベンチ機には変数が無いので、0010-env.md の手順 7 で作った `~/isucon14/env.sh` の値を書き込んでおきます。

```bash
cd ~
cat > bench.env <<EOF
export APP_IP='203.0.113.10'
export BENCH_IP='203.0.113.20'
export BENCH_PRIV='10.0.1.95'
EOF
source ~/bench.env
echo "APP_IP=$APP_IP BENCH_IP=$BENCH_IP BENCH_PRIV=$BENCH_PRIV"
```

成功の見え方: 3 つの値が空でずに表示されます。`203.0.113.0/24` は文書用の値なので、`~/isucon14/env.sh` の `APP_IP` / `BENCH_IP` / `BENCH_PRIV` に書き換えてください。以降のベンチはこのファイルを読み込んでから回します（セッションごとに `source ~/bench.env`）。

?> `BENCH_PRIV` はベンチ機の**プライベート IP**です。`--payment-url` にはこちらを渡します。理由は手順 3 に書きます。

## 2. ベンチ用インスタンスの決済モックを止めます

AMI には `isuride-payment_mock.service` が入っていて、12345 番でテスト用の決済サーバーを立てています。ベンチ自身も 12345 番で決済サーバー（`--payment-bind-port` の既定）を立てるため、ポートがぶつかります。ベンチ用インスタンスではアプリを使わないので、止められます。

```bash
sudo systemctl disable --now isuride-payment_mock.service
sudo ss -ltnp | grep 12345 || echo '12345 は空いています'
```

成功の見え方: `12345 は空いています` が出ます。

?> アプリ用インスタンスの `isuride-payment_mock.service` は止めません。0030 の `POST /api/initialize` はこのモックを決済先として指定しています。

## 3. 決済モックのポートを確認します

ベンチは負荷走行中、自分自身が決済サーバーになります。アプリ用インスタンスから、ベンチの 12345 番へ届く必要があります。

0010-env.md の Terraform は、2 台を同じセキュリティグループに入れ、SG の中の全プロトコル・全ポートを相互に許可しています。この時点で 12345 番は届いています。念のため、手元 PC から一覧を確認します（`~/isucon14/env.sh` を読込みます）。

```bash
source ~/isucon14/env.sh
aws ec2 describe-security-groups --group-ids "$SG_ID" \
  --query 'SecurityGroups[0].IpPermissions[].[IpProtocol,FromPort,ToPort]' --output table
```

成功の見え方: 次の 4 行が出ます。

- `-`（プロトコル `-1`、SG 自身からの全許可）
- `tcp` の `443`
- `tcp` の `22`
- `tcp` の `12345`

?> `12345` は `allowed_cidrs`（自分の IP）にだけ開いています。アプリ用インスタンスからベンチへは、SG 内の全許可（`-1`）で届きます。

ただし、**`--payment-url` にベンチのパブリック IP を書くと届きません**。SG の「SG 自身からの全許可」はプライベート IP の通信にだけ効き、ベンチのパブリック IP へ出て行って戻ってくる通信（ヘアピン）は許可されないためです。この状態でベンチを回すと、アプリから決済サーバーへの接続がタイムアウトし、`決済` 系のエラーで `pass=false` になります（実測で確認済み）。

`--payment-url` には必ず `BENCH_PRIV`（ベンチ機のプライベート IP）を使います。

## 4. ベンチをビルドします

ベンチ用インスタンスで、公式リポジトリを取ります。

```bash
sudo apt-get update -y && sudo apt-get install -y git
cd ~
git clone --depth 1 https://github.com/isucon/isucon14.git isucon14-src
cd isucon14-src/bench
ls
```

Go は AMI に入っています（`/home/isucon/local/golang/bin/go`、`~/.local.env` で PATH に入っています）。使うのはベンチマーカーのビルドだけです。アプリケーションは Python 実装のまま動かします（[0030-python.md](0030-python.md)）。

```bash
go version
/home/isucon/local/golang/bin/go build -o ~/bench .
ls -lh ~/bench
```

成功の見え方: `go version go1.23.2 linux/amd64` と、数十 MB の `~/bench` ができます。

?> 依存モジュールの取得で数分かかります。通信できない場合は `GOPROXY` の設定を確認します。

## 5. 短い確認を先に回します

`-t 0` を付けると、負荷走行をせず初期化と整合性チェックだけを行います。数分で終わります。

```bash
cd ~
./bench run \
  --target https://isuride.xiv.isucon.net \
  --addr ${APP_IP}:443 \
  --payment-url http://${BENCH_PRIV}:12345 \
  -s -t 0
```

成功の見え方:

```text
time=... level=INFO msg=prepareのみを実行します
time=... level=INFO msg=prepareが終了しました errors=[]
```

`errors=[]` が空なら初期化と整合性チェックは通っています。

### 引数の意味

| 引数 | 値 | 意味 |
| --- | --- | --- |
| `--target` | `https://isuride.xiv.isucon.net` | URL のホスト名。Host ヘッダと SNI になる。nginx はこの名前でアプリに転送する |
| `--addr` | `${APP_IP}:443` | 実際の接続先。ホスト名だけ別にする（`curl --resolve` と同じ） |
| `--payment-url` | `http://${BENCH_PRIV}:12345` | アプリに伝える決済サーバーの URL |
| `--payment-bind-port` | `12345`（既定） | ベンチが決済サーバーを待つポート |
| `-t` | `60` | 負荷走行の秒数。`0` なら prepare のみ |
| `-s` | （付ける） | 静的ファイルのチェックを飛ばす。この教材では常に付ける（理由は下） |

`--payment-url` の宛先は、**アプリが接続する先**です。ここではベンチ自身の 12345 番を指します。ベンチは `--payment-url` の値を `POST /api/initialize` の本文に入れてアプリに渡します。

### なぜ `-s` を付けるのか

付けないと prepare の静的ファイル検査で必ず落ちます（実測で確認済み）。

```text
msg=静的ファイルのチェックに失敗しました error="index.html の内容が一致しません"
```

AMI が配っている `index.html` の MD5 は `da70f1948cbf256d97049e5118a954d9` ですが、リポジトリのベンチが期待する値（`bench/benchrun/frontend_hashes.json`）は `c4fc4946c371c8577ce3162e457147fb` です。AMI のフロントエンドはリポジトリのどのタグとも一致しないため、リポジトリのベンチをビルドして回すと必ず食い違います。

競技本番ではフロントエンドも含めて提出物を揃えるので一致しますが、この教材は AMI のフロントエンドをそのまま使うため、`-s`（`--skip-static-sanity-check`）で検査だけを外します。

## 6. 1 本回します

初期化 → 整合性チェック → 負荷走行 60 秒 → 最終チェック、の順に進みます。全体が 3〜5 分かかります。

```bash
cd ~
./bench run \
  --target https://isuride.xiv.isucon.net \
  --addr ${APP_IP}:443 \
  --payment-url http://${BENCH_PRIV}:12345 \
  -s -t 60
```

成功の見え方（抜粋）:

```text
time=... level=INFO msg=負荷走行を開始します
time=... level=INFO msg=これまでに地域内の評判によって3人、既存ユーザーの招待経由で2人が新規登録しました
time=... level=INFO msg=これまでに低評価なライドによって0人が利用をやめました
time=... level=INFO msg="結果" pass=true スコア=661 種別エラー数=map[]
```

- `msg=結果` の行が判定です。`pass=true` でスコアが記録されます
- `pass=false` なら FAIL です。スコアを見ません。ログの `クリティカルエラーが発生しました` を探します
- 実行中は 3 秒ごとに結果をレポート用の記述子へ送りますが、`ISUXBENCH_REPORT_FD` を設定していないときは何も表示されません。標準出力に出るのは最後の `msg=結果` の 1 行だけです

### 実測サンプル

| 回 | score | tw | メモ |
| --- | --- | --- | --- |
| 0040 ベースライン（2026-10-17） | 661 | - | Python 実装・インデックスなし。`種別エラー数=map[]` |

`tw` は `ss -tan | grep -c TIME-WAIT`（アプリ機、ベンチ直後）です。この教材では計測していないので `-` にしています。

?> 上の `661` は、この教材の環境で実測した値です。アプリ 1 台、Python 実装、インデックスなし、の状態で `-t 60` を回した結果です。マシンの性能とタイミングで大きくぶれます（同じ設定でも数百点動きます）。数を比べるときは「何回か回してよい方」を見てください。

記録します。`~/bench-notes/scores.txt` はベンチ機ではなく手元 PC に置きます。`score` には最終行の `スコア=` の値を、`pass` には `pass=true` / `pass=false` を入れて 1 行ずつ足します。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=661  pass=true  note=ベースライン(0040)" >> ~/bench-notes/scores.txt
cat ~/bench-notes/scores.txt
```

以降の変更は 0050 以降のファイルごとに行います。1 手入れて 1 本回して、1 行足します。

## 7. ログの読み方

途中の `INFO` 行が、失点理由の手がかりです。

| 出方 | 意味 |
| --- | --- |
| `地域内の評判によって…新規登録` | 評価の高い environs に新しい利用者が増える |
| `低評価なライドによって…利用をやめました` | 評価の低いライドがあった場合 |
| `…%のライドは椅子がマッチされるまでの時間…に不満` | マッチングが遅い |
| `…%のライドはマッチされた椅子が乗車地点までに掛かる時間…に不満` | 乗車位置に近い椅子が選ばれていない |
| `…%のライドは椅子の実移動時間に不満` | 椅子の速度や経路の選択 |

この 3 行は「どの段階が遅いのか」を教えてくれます。どこを直すかは **0110-app-matching.md**（この章はこれから） で判断します。

## 8. 失敗したときの切り分け

| 出方 | 対処 |
| --- | --- |
| `x509: certificate has expired` | [0020-https.md](0020-https.md) の CA 登録をやり直します |
| `initializeに失敗しました` | アプリ側で `POST /api/initialize` が 500 を返しています。[0030-python.md](0030-python.md) の `journalctl` を見ます |
| `静的ファイルのチェックに失敗しました` | `-s`（`--skip-static-sanity-check`）を付けて回します。この教材では常に付けます（手順 5 を参照） |
| `POST /api/owner/owners のリクエストが失敗しました` | SG の 443 を確認します。ベンチからアプリへ届いているかの確認です |
| 決済まわりのエラー（`決済` を含む `error`）が大量に出る | `--payment-url` がベンチの**プライベート IP**を指しているか確認します（手順 3） |
| `クリティカルエラーが発生しました` | 直前に足した変更を戻して切り分けます。ログの `error` を読みます |

## 9. 決済先を戻します

ベンチは `--payment-url` の値を `POST /api/initialize` に書いてアプリへ渡します。回ったあとも DB にはベンチの決済サーバー URL が残っています。手元から `curl` を叩くときは、アプリ用インスタンスのモックに戻します。

アプリ用インスタンスで:

```bash
curl -sS -X POST https://isuride.xiv.isucon.net/api/initialize \
  -H 'content-type: application/json' \
  -d '{"payment_server":"http://localhost:12345"}'
echo
```

## 出口

`msg=結果 pass=true` が出たら [0050-measure.md](0050-measure.md) へ進みます。次は、計測の型を作ります。
