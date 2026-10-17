# 0130 レスポンス生成を詰める

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）は、手元の private-isu-terraform リポジトリで実行します。

- [0120-db-load.md](0120-db-load.md) で DB の仕事量を減らすと、**アプリが応答を組み立てる時間**と、**リクエストごとにかかる固定の手間**が上位に残ります。
- この章は、py-spy（処理中のどこが重いかを記録するツール）でその内訳を取ります。
- ログイン状態の保持（セッション）・接続の確認・ログ・テンプレート（表示のひな形）を、1 つずつ詰めます。

- この章の大事な観察は、「**py-spy で見えた割合ほど点数は動かないことが多い**」です。
- 待ち時間が、別の制約に隠れるためです。
- 重かった場所が消えたことと点数を、セットで記録します。
- 効果が小さければ、「小さかった」と書きます。
- `fail`（エラーの件数）が 0 でない点数は、比べません。

- 順番: 内訳を取る（1.）→ セッションを cookie に（2.）→ 接続確認の `ping` を間引く（3.）→ アクセスログを止める（4.）→ 1 投稿ブロックを Python で組み立てる（5.）→ 公式ベンチで判定する（6.）。
- ベンチは [0110-split-4.md](0110-split-4.md) と同じです（s1 で bench-prep → `localhost` へ）。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  note=resp-baseline" >> ~/bench-notes/scores.txt
```

<a id="1-内訳を取る"></a>

## 1. 内訳を取る（py-spy）

- 計測は、[0100-overall-tuning.md §1](0100-overall-tuning.md#1-py-spy-でボトルネックを取る) の手順と同じです。
- 複数台に分けたあとは、アプリが 2 台に分かれています。
- **ベンチ中に s1 と s2 の両方**で、記録します。
- 計測ツールを併用した点数は、単独ベンチと分けて記録します。

```bash
# s1 と s2 で打つ
UNIT=isu-python.service
PID="$(systemctl show -p MainPID --value "$UNIT")"
test "$PID" -gt 0 || exit 1
sudo py-spy record --pid "$PID" --subprocesses \
  --format raw --duration 30 --rate 100 -o /tmp/pyspy-0130.txt
```

- 集計は、[0100 §1](0100-overall-tuning.md#1-py-spy-でボトルネックを取る) の awk を使います。
- 見る場所は、リーフフレーム（各スタックの末尾。いま計算している関数）と、`render` / `session` / `ping` / `memcache` などの名前が付く場所です。

実測サンプル（2026-10-13、`c7a.large` 3 台 / `ap-northeast-1` / `nproc=2` / メモリ 3.7Gi、計測ツール併用ベンチ）: `score=159656`（`fail=0`。単独ベンチは 173356 なので、併用の点数は比べません）。s1 の worker 計 3159 サンプルの内訳:

| フレーム | サンプル | 読み |
| --- | --- | --- |
| `db (app.py:63)` | 370 | **接続確認の `ping` が毎回の通信** |
| `query (MySQLdb/connections.py:286)` | 353 | DB への問い合わせ本体 |
| `_recv (pymemcache/client/base.py:1750)` | 330 | **セッションの memcached 往復** |
| `close_graceful` / `write` / `flush`（gunicorn・logging） | 204 / 89 / 155 | **アクセスログの書き込み** |
| `render_template` / `_render` / `render`（jinja2） | 289 / 280 / 266 | **テンプレート描画** |
| `flask_session save_session` / `open_session` | 262 / 102 | セッションの保存・読取 |

### 観察すること

- 「消せるもの」と「DB の仕事」を分けて言えます（`query` は [0120-db-load.md](0120-db-load.md) の仕事、他はこの章の仕事です）
- 1 つ選んでから次に進みます（全部まとめて入れません）

<a id="2-セッションを-cookie-に"></a>

## 2. セッションを署名付き cookie に

- 素体（配布された元の状態）は、[0030-cache.md](0030-cache.md) で見た Flask-Session（memcached に保存する方式）です。
- 保存・読取のたびに、s2 の memcached へ往復します。
- **Flask 既定の署名付き cookie**（データに見破られない目印を付けてブラウザに持たせる方式）に寄せると、その往復が消えます。

```python
# 変更前
app.config["SESSION_TYPE"] = "memcached"
app.config["SESSION_MEMCACHED"] = memcache()
Session(app)

# 変更後（from flask_session import Session も外す）
app.config["SECRET_KEY"] = "isuconp-tutorial-secret"
```

- s1 と s2 の両方に反映して、restart → ベンチです。

```bash
# s1 で打つ
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=resp-cookie-session" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）: `score=169876` → 再計測 `score=171913`（ともに `fail=0`）。**点数はノイズ内でした**（cookie の目印を作る手間で相殺されます）。ただし py-spy の `_recv (pymemcache)` は **330 → 0** に消え、memcached への往復が無くなったことは確認できています。効果が小さくても「往復を消せた」記録は残ります。

?> cookie セッションは、1 個の cookie に 4KB 程度しか入れられません。`SECRET_KEY` を変えると、全員のログイン状態が無効になります。投稿データを cookie に入れない構成（素体のまま）が、前提です。

### 観察すること

- 重かった場所が消えたことと、点数の両方を記録します（片方だけでは終わりません）
- ログイン・登録・ログアウトが `fail=0` であること

<a id="3-接続確認の-ping-を間引く"></a>

## 3. 接続確認の `ping` を間引く

- [0110-split-3.md §9](0110-split-3.md#9-db-は起動順を仮定しない) で入れた `db()` の再接続は、毎回 `_db.ping(True)` で DB との接続が生きているかを確認します。
- これが、**リクエスト 1 つにつき 1 往復**になっています。
- 確認を 1 秒に 1 回までに間引けば、再接続の保険は残せます。

```python
_db_ping_at = 0.0

def db():
    global _db, _db_ping_at
    ...
            # ping は毎回の往復になるので 1 秒に 1 回まで。DB 起動待ちの再接続は保つ
            now = time.monotonic()
            if now - _db_ping_at > 1.0:
                _db.ping(True)
                _db_ping_at = now
            return _db
```

- s1 と s2 に反映して、restart → ベンチです。

```bash
# s1 で打つ
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=resp-db-ping-throttle" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）: `{"pass":true,"score":176249,"success":160131,"fail":0,"messages":[]}`（171913 から **+4336**）。py-spy の `db (app.py)` の計算は **370 → 0**（上位から消える）になりました。

### 観察すること

- 再接続の要件（起動順を仮定しない）を壊していないこと。間引くと 1 秒に 1 回までは古い接続を掴む可能性がありますが、`OperationalError`（接続エラー）のやり直しで拾います
- s3 を `reboot` しても s1・s2 が起き直すことを確認できれば保険は生きています（[0110-split-3.md §10](0110-split-3.md#10-ベンチ中の書き込みは再起動後も残す) の再起動試験と同じ流儀）

<a id="4-アクセスログを止める"></a>

## 4. アクセスログを止める

- gunicorn のアクセスログは、**応答を返したあとに**毎リクエスト書かれます（`write` / `flush` / `close_graceful` が上位に乗っていました）。
- nginx 側のアクセスログは、alp（解析ツール）や計測に必要です。残します。
- **アプリ側のアクセスログだけ**止めます。

```ini
# s1: /etc/systemd/system/isu-python.service.d/unix.conf
ExecStart=/home/isucon/private_isu/webapp/python/.venv/bin/gunicorn \
  --bind unix:/home/isucon/tmp/gunicorn.sock \
  --umask 007 \
  -w 4 \
  --log-file - \
  app:app
```

```ini
# s2: /etc/systemd/system/isu-python.service.d/workers.conf
ExecStart=/home/isucon/private_isu/webapp/python/.venv/bin/gunicorn app:app -b 0.0.0.0:8080 -w 4 --log-file -
```

- `--access-logfile` を外したのが、差分です。
- エラーログ（`--log-file -`）は、残します。
- 反映は、drop-in（設定の追加ファイル）→ `daemon-reload` → restart の順です（s1 は socket の残骸を消してから start。[0110-split-2.md §6](0110-split-2.md#6-s1-の-gunicornunix-socket) と同じ）。

```bash
# s1 で打つ
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=resp-access-log-off" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）: `{"pass":true,"score":177683,"success":161316,"fail":0,"messages":[]}`（176249 から +1434）。

### 観察すること

- 計測の記録が nginx のアクセスログに一本化されます。alp は nginx のログを見るのでそのまま使えます
- エラーログは残ります（`journalctl -u isu-python.service` で見られます）

<a id="5-1-投稿ブロックを-python-で組み立てる"></a>

## 5. 1 投稿ブロックを Python で組み立てる（テンプレート脱出の 1 手）

- テンプレートの描画の手間は、1 投稿ごとの `{% include 'post.html' %}` に集中しています。
- **その 1 投稿ブロックだけ**を、Python の関数で組み立てて `Markup`（そのまま出力してよい文字列）で埋めます（[§1](#1-内訳を取る) で `render` 系が 9% 残っていたため）。

1. `templates/post.html` の出力をそのまま返す `_post_html(post, csrf_token)` をアプリに書きます（`escape` / `nl2br` / `image_url` は素体のものを使い回します）
2. `templates/posts.html` を `{{ posts_html | safe }}` に置き換えます
3. `get_index()` / `get_posts()` / `get_user_list()` は `posts_html=render_posts_html(posts, csrf)` を渡します（詳細ページの `post.html` はそのまま残します）

- 出力を合わせるコツは、2 つです。

- 記号の扱い（エスケープ）は Jinja の自動エスケープと同じく `markupsafe.escape` を使う（自前の `replace` では足りません）
- `created_at` は `{{ post['created_at'] }}` と同じく `str()` した値になります。表示形式が変わると `fail` になります

- s1 と s2 に反映して、restart します。
- 一覧が 20 件出ることを目で見てから、ベンチします。

```bash
# s1 で打つ
curl -fsS -m 5 http://127.0.0.1/ | grep -c 'isu-post" id="pid_'   # 20 が出れば OK
curl -fsS -o /dev/null -m 5 http://127.0.0.1/posts/1
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=resp-post-renderer" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）: `{"pass":true,"score":178812,"success":162305,"fail":0,"messages":[]}`（177683 から +1129）。py-spy の `render_template` は **289 → 89** に減りましたが、点数への効果は小さめでした（描画時間が待ちに隠れていたため）。

### 観察すること

- 一覧の件数・削除済みユーザーの非表示・詳細ページの全コメント・ページ送りが `fail=0` で通ること（出力の見た目ではなく、ベンチの検証で判定します）
- 重かった場所が減ったことと点数をセットで記録します

<a id="6-公式ベンチで判定する"></a>

## 6. 公式ベンチで判定する

- 残す変更を、全部載せた状態にします。
- 公式ベンチを 1 本回します（bench-prep は s1 で `mv` + `reopen`、s3 で slow log の回転）。

```bash
# s1 で打つ
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=resp-final" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）: `{"pass":true,"score":181103,"success":164229,"fail":0,"messages":[]}`（0120 最終の 173356 から +5456、1.03 倍）。

- 点数の推移は、次のとおりです（この章）。

| 回 | score | fail | 備考 |
| --- | --- | --- | --- |
| 0120 §7 最終（基準） | 173356 | 0 | |
| §2 セッションを cookie に | 169876 / 171913 | 0 | ノイズ内（`_recv` 330 → 0 は確認済み） |
| §3 `ping` を 1 秒に 1 回 | 176249 | 0 | +4336 |
| §4 アクセスログ停止 | 177683 | 0 | +1434 |
| §5 1 投稿ブロックを Python で | 178812 | 0 | +1129 |
| **§6 最終** | **181103** | **0** | |

### 残った 1 手（次にやるなら）

- **日時の変換**（`DateTime_or_None`）: `created_at` を SQL 側で文字列として受け取るか、キャッシュに載せます。まだ上位に残っています → [0140-cut-remaining.md §2](0140-cut-remaining.md#2-日時変換を避ける) でやります
- **投稿・ユーザーのメモリキャッシュ**（読み取りの結果をプロセスに保持）: [0120-db-load.md §6](0120-db-load.md#6-残った上位から-1-つ) で試した「軽い問い合わせのキャッシュ」は効かなかったので、重い結果（一覧の組み立て結果など）を載せる形になります。無効化（書き換えたら忘れる処理）の設計が要ります
- **ベンチマーカーの分離**（[0010-env.md](0010-env.md) の `enable_benchmarker_instance`）: 今の点数には s1 で負荷を生成している分が乗っています

### 観察すること

- 最終点数が基準を超えています。超えた理由を「消えた計算」の名前で言えます
- `fail` が 0 です。`fail` が出たら §5 の出力差分（記号の扱い・日時・件数）を疑います

### この節でつまずいた点

?> **この表の問題は、すべて本文へ反映済みです。**「直し方」列のとおりに直してあります。同じ症状が出たら、本文の手順どおりに直して再計測します。

| # | 箇所 | 問題 | 修正内容 |
| --- | --- | --- | --- |
| 1 | §2・§5 | py-spy の割合（11%・9%）ほど点数が動かなかった | 待ちが別の制約に隠れることを明記し、重かった場所と点数の両方を記録する形にしました |
| 2 | §5 | 自前のエスケープでは出力が Jinja と合わない | `markupsafe.escape` と素体の `nl2br` を使い回します |
| 3 | §3 | `ping` を消すと再接続の保険が無くなる | 1 秒に 1 回の間引きにして、起動順の要件を残しました |

## 7. トラブルシュート

### ログインが 500 になる（セッション）

- `SECRET_KEY` が両台で一致しているか、restart 済みかを見ます（[0110-split-2.md §3](0110-split-2.md#3-アプリの-envshs1-と-s2同じ内容) の env と同じで、`cp` だけでは効きません）
- cookie に 4KB を超える量を入れると切り捨てられます。セッションに入れるのは ID と CSRF トークン（送信元確認の印）だけにします

### 一覧の見た目が崩れる / fail が出る（§5）

- `_post_html()` の出力と `templates/post.html` を比べます。`class` 名・`id` 形式・日時の形が合っているか
- `comment_count` の表示がズレていたら [0120 §5](0120-db-load.md#5-コメント数を列に持つ) の初期化での再計算を見ます

### 点数が動かない

- 効果が小さいことは結果です。「小さかった」と書いて、残すか戻すかを選びます
- 次に見る場所は py-spy の上位です（[§1](#1-内訳を取る) に戻ります）。手を増やす前に測り直します

- 0130 まで終わったら、[0140-cut-remaining.md](0140-cut-remaining.md)（残りの削減）→ [0150-scale.md](0150-scale.md)（スペックと構成）に進みます。
- 全部終わったら、終盤作業（[0030-ops.md](../010_common/0030-ops.md) の「ログを止める（終盤）」「終盤チェック（17:00 以降）」）に進みます。
