# 0100 全体最適チューニング

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）は、手元の private-isu-terraform リポジトリで実行します。

- DB を直しても残る重さは、アプリ側から取ります。
- py-spy（処理中のどこが重いかを記録するツール）で重い場所を見て、1 つずつ直します。
- [0080-app-tuning.md](0080-app-tuning.md) の次です。
- 出口は、[0110-split-1.md](0110-split-1.md)（3 台への分割）です。

- 順番: py-spy で取る（1.）→ 1 つ直してベンチ（2.）→ 出力例と照合（3.）→ 詰まったら切り分け（4.）。
- 1 つずつ進めます。
- `fail`（エラーの件数）が 0 でない点数は、比べません。

<a id="1-py-spy-でボトルネックを取る"></a>

## 1. py-spy でボトルネックを取る

- DB が mysqld で飽和している間は、alp / slp を先に見ます（[0020-measure.md](../010_common/0020-measure.md) の「サイクル」）。
- 飽和が抜けてから、アプリを見ます。

- 記録する台（アプリの台。1 台に全部入った構成では同じ台）に入れます。
- 無いときだけ入れます（Ubuntu 24.04 は `--break-system-packages` が要ります）。

```bash
# 1 台で打つ。python3-pip が無い台では先に apt で入れます
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pip
which py-spy >/dev/null || sudo python3 -m pip install --break-system-packages py-spy
py-spy --version
```

- ptrace（他のプロセスの中を見るための仕組み）が塞がれていると、取得できません。先に開けます。

```bash
cat /proc/sys/kernel/yama/ptrace_scope
```

- まず `sudo py-spy` で取得します。
- root の ptrace 権限で `ptrace_scope=1` のまま取れるなら、変更不要です。
- それでも取れず、制約を緩める必要があるときだけ、次の drop-in（設定の追加ファイル）を使います。
- `0` は、同じユーザー同士の ptrace 制限を緩めます。測定後は、元に戻します。

`backup/s1/etc/sysctl.d/99-isucon-ptrace.conf`:

```text
kernel.yama.ptrace_scope = 0
```

```bash
# 作業機
git pull --ff-only
mkdir -p backup/s1/etc/sysctl.d
# 上の内容を配置する
git add -A && git commit -m "tune: ptrace_scope を 0 にする" && git push
```

サーバーで反映します（同じ台で打つ）:

```bash
REPO=/home/isucon/private-isu-terraform
cd "$REPO" && git fetch origin
# これまで使っている feature ブランチのまま反映する
git pull --ff-only
sudo cp -a "$REPO/backup/s1/etc/sysctl.d/99-isucon-ptrace.conf" /etc/sysctl.d/
sudo sysctl -p /etc/sysctl.d/99-isucon-ptrace.conf
cat /proc/sys/kernel/yama/ptrace_scope  # 0 であること
```

- 負荷が乗っているあいだに、取得します。
- 別々の SSM セッションを用意します。片方で次をフォアグラウンド実行しているあいだに、もう片方で記録を始めます。
- プロファイラ（計測用の道具）を併用した点数は、単独ベンチの点数と分けて記録します。

```bash
# 1 台で打つ（ベンチ。py-spy 記録中の別セッションと同時に回します）
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
```

- 実測（2026-10-13）: `score=43178`（on-CPU 記録中）/ `score=41307`（`--idle` 記録中）。ともに `pass=true` / `fail=0`。
- プロファイラ併用の点数です。単独ベンチの点数と比べません（[§3](#3-出力例) の表）。

- SSM だけで集計するときは、`--format raw` を使います。
- 1 行に同じ流れ（スタック）のサンプル数がまとまるので、サーバ上で集計できます。
- 可視化が必要なら、`speedscope`（記録を見える形にするツール）を選びます。

```bash
# 1 台で打つ
UNIT=isu-python.service
PID="$(systemctl show -p MainPID --value "$UNIT")"
test "$PID" -gt 0 || exit 1
echo "MainPID=$PID"
pgrep -af gunicorn

# 既定のactiveサンプル（以下では「on-CPU」と略記）
sudo py-spy record --pid "$PID" --subprocesses \
  --format raw --duration 30 --rate 100 -o /tmp/pyspy-cpu.txt

# wall clock（待ちを含む）
sudo py-spy record --pid "$PID" --subprocesses --idle \
  --format raw --duration 30 --rate 100 -o /tmp/pyspy-idle.txt

ls -lh /tmp/pyspy-cpu.txt /tmp/pyspy-idle.txt
```

- `MainPID` は、gunicorn のマスタです。`--subprocesses` で worker（処理を受け持つ子プロセス）も記録します。
- 集計では、`gunicorn/workers/` を含むスタックだけを選びます。マスタの `wait_for_signals` は除外します。
- 見る場所は、リーフフレーム（各スタックの末尾。いま計算している関数）です。
- `run` など共通の呼び出し元を数えても、原因は分かりません。

?> 既定の記録は、OS の CPU 使用時間そのものではありません。`--idle` を付けると、待機中の処理も含められます。どちらも、サンプル（一定間隔の観測）による観測です。掲載の比率を「CPU の何% が消えた」とは、読み替えません。ログイン・登録経路の `digest()` を調べる場合は、その経路を回しながら記録します。

- `raw` の 1 行は、「`;` 区切りのスタック + 行末のサンプル数」です。
- 行数ではありません。末尾の数を、重みとして足します。
- 次の集計を `/tmp/pyspy-idle.txt` にも実行し、待ちの違いを見ます。

```bash
# リーフフレーム（各スタックの末尾 1 フレーム）の集計
sudo awk '$0 ~ /gunicorn\/workers\// { n=$NF; s=$0; sub(/ [^ ]*$/,"",s); sub(/.*;/,"",s); if (n+0>0) c[s]+=n }
         END { for (k in c) printf "%8d  %s\n", c[k], k }' /tmp/pyspy-cpu.txt | sort -rn | head -20

# アプリの呼び出し元フレームも、末尾のサンプル数で重み付けする
sudo awk '$0 ~ /gunicorn\/workers\// {
  n=$NF; s=$0; sub(/ [^ ]*$/,"",s); nf=split(s,frames,";"); delete seen;
  for (i=1; i<=nf; i++) if (frames[i] ~ /\(app\.py:[0-9]+\)/) seen[frames[i]]=1;
  for (k in seen) c[k]+=n
} END { for (k in c) printf "%8d  %s\n", c[k], k }' /tmp/pyspy-cpu.txt | sort -rn | head -15

# どのルートが重いか（ディスパッチ先の集計）
sudo awk '$0 ~ /gunicorn\/workers\// { n=$NF; s=$0; sub(/ [^ ]*$/,"",s);
  while (match(s, /dispatch_request \(flask\/app.py:[0-9]+\);[a-z_]+ \(app\.py:[0-9]+\)/)) {
    t=substr(s,RSTART,RLENGTH); sub(/.*;/,"",t); c[t]+=n; s=substr(s,RSTART+RLENGTH) } }
  END { for (k in c) printf "%8d  %s\n", c[k], k }' /tmp/pyspy-cpu.txt | sort -rn | head -12
```

- 可視化する場合は、新しいベンチの負荷中に `speedscope` を記録します。
- ベンチが終わってから取得すると、待機時だけの記録になります。

```bash
sudo py-spy record --pid "$PID" --subprocesses \
  --format speedscope --duration 30 --rate 100 -o /tmp/pyspy.json
```

可視化の手順:

1. JSON を作業機へ移します（SSH があるときは `scp`、SSM だけならファイルを持ち出します）
2. https://www.speedscope.app を開き、ドロップします
3. Left Heavy / Sandwich で見ます。SVG が欲しければ `--format svg` を使います

- 既定の記録と `--idle` の両方を確認します。
- サンプルは、別々の 30 秒です。
- 絶対数だけでなく、worker の総サンプル数に対する比率と、同じ呼び出し元の変化を比べます。

| 見え方 | 意味 | このアプリでやりがちな一手 |
| --- | --- | --- |
| on-CPU が `digest` / `subprocess` / `openssl` | パスワードの照合を、外部コマンドの呼び出しで計算している | 同じ計算をライブラリで行います。タイムライン GET が主体のベンチでは上位に出ません。**出ないときは §2 の `digest()` 差し替えはしません**。「出ない」と書いて次の 1 つ（`--idle` 側）へ進みます |
| `--idle` で `execute` / `make_posts` が厚い | 同じ問い合わせの繰り返し（N+1）、余分な問い合わせ | コメントをまとめて取る。[0015-sql-tuning.md](0015-sql-tuning.md) のインデックスと併用する |
| 一覧経路で `SELECT * FROM posts` が重い | 一覧に不要な画像データ（BLOB）まで読んでいる | 一覧の問い合わせから `imgdata` を外します。画像経路の `imgdata` は必要なので消しません |

<a id="2-1-つ直してベンチする"></a>

## 2. 1 つ直してベンチする

- どれか **1 つ** だけ直して、再ベンチします。
- 点数と py-spy（speedscope）の同じ場所が痩せたかを見ます。

?> **`digest()` の差し替えは、条件付きです。**ログイン・登録の負荷を観測してから選びます。今回の記録（[§3](#3-出力例)）では目立ちました。ただし経路ごとに重さは違うので、別の記録で出ないときは選びません。重い場所（ホットスポット）を直す場合は、以下の投稿一覧・N+1 の例へ進みます。

- ハッシュ計算が候補になった場合の例は、`openssl` 呼び出し → `hashlib` です。
- 既存のパスワードの照合結果（passhash）が読めることを確認します。

```bash
printf %s test | openssl dgst -sha512
python3 -c 'import hashlib; print(hashlib.sha512(b"test").hexdigest())'
# 同じ hex なら既存の passhash をそのまま読めます
```

- `backup/s1/home/private_isu/webapp/python/app.py` と `backup/s2/home/private_isu/webapp/python/app.py` の両方を直します（`digest()` は s1 と s2 の両方で使うため）。

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

- `hashlib` の import も、追加します。

```python
import datetime
import hashlib  # ← 追加
import os
```

- `digest()` は、ログイン・登録経路で使います。`app.py` に変更を入れ、`isu-python` を restart します。
- py-spy で、対象の計算が減ったことを確認します。それから、ベンチを 1 本回します。
- 効果が小さいときは、「小さい」と書きます。

```bash
# bench-prep（[0020-measure.md](0020-measure.md) と同じ mv + reopen / flush-logs）してから公式ベンチ 1 本
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
fi
sudo nginx -s reopen
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=pyspy-tune" >> ~/bench-notes/scores.txt
```

実測（2026-10-13）: `score=49716`（`pass=true` / `fail=0`、`digest()` → `hashlib` の 1 つ。同じアプリの再計測が `score=51143`）。

実測（2026-10-13、py-spy 取り直しの併用ベンチ）: `score=44107`（on-CPU 記録中）/ `score=42309`（`--idle` 記録中）、ともに `pass=true` / `fail=0`。対象の計算が消えた確認に使った点数で、プロファイラ併用のため単独ベンチとは比べません。

- 残す変更は、`backup/s1` に積みます（[0020-measure.md](0020-measure.md) の「残す変更は backup/s1 に積む」の流儀）。

### 投稿一覧とN+1を直す場合

- これまでの観測では、一覧の全件取得と `make_posts()` の繰り返し問い合わせが候補でした。
- ハッシュ計算の変更とは、同時に行いません。
- `backup/s1/home/private_isu/webapp/python/app.py` と s2 側の同じファイルを編集します。
- `backup/base` は、変更しません。

- **1 つ目は、`get_index()` だけ**を直します。
- 削除済みユーザーを SQL で除外してから、20 件に制限します。
- 単に `LIMIT 20` を足すだけでは足りません。削除済みの投稿を Python 側で除いたあと、20 件未満になることがあります。

```python
@app.route("/")
def get_index():
    me = get_session_user()
    cursor = db().cursor()
    cursor.execute(
        "SELECT p.`id`, p.`user_id`, p.`body`, p.`created_at`, p.`mime` "
        "FROM `posts` p JOIN `users` u ON u.`id` = p.`user_id` "
        "WHERE u.`del_flg` = 0 ORDER BY p.`created_at` DESC LIMIT 20"
    )
    posts = make_posts(cursor.fetchall())
    return flask.render_template("index.html", posts=posts, me=me)
```

- 配布・再起動・ベンチで、1 つ目を判定します。
- それから、**2 つ目として `make_posts()` だけ**を置き換えます。
- MySQL 8 の窓関数（行ごとに順位を付ける書き方）で、投稿ごとの新しい 3 コメントを取得します。件数・ユーザーもまとめます。
- 詳細ページの `all_comments=True` は、全コメントを返すままです。

```python
def make_posts(results, all_comments=False):
    posts = []
    if not results:
        return posts
    cursor = db().cursor()
    users = {}
    chunk_size = 200

    for start in range(0, len(results), chunk_size):
        chunk_posts = results[start : start + chunk_size]
        post_ids = [post["id"] for post in chunk_posts]
        marks = ",".join(["%s"] * len(post_ids))
        if all_comments:
            query = (
                "SELECT `id`, `post_id`, `user_id`, `comment`, `created_at` "
                f"FROM `comments` WHERE `post_id` IN ({marks}) "
                "ORDER BY `created_at` DESC"
            )
        else:
            query = (
                "SELECT `id`, `post_id`, `user_id`, `comment`, `created_at` FROM ("
                "SELECT `id`, `post_id`, `user_id`, `comment`, `created_at`, "
                "ROW_NUMBER() OVER (PARTITION BY `post_id` ORDER BY `created_at` DESC) AS rn "
                f"FROM `comments` WHERE `post_id` IN ({marks})"
                ") AS ranked WHERE rn <= 3 ORDER BY `created_at` DESC"
            )
        cursor.execute(query, post_ids)
        comments_by_post = {}
        user_ids = set()
        for comment in cursor:
            comments_by_post.setdefault(comment["post_id"], []).append(comment)
            user_ids.add(comment["user_id"])

        cursor.execute(
            f"SELECT `post_id`, COUNT(*) AS `count` FROM `comments` "
            f"WHERE `post_id` IN ({marks}) GROUP BY `post_id`",
            post_ids,
        )
        counts = {row["post_id"]: row["count"] for row in cursor}
        user_ids.update(post["user_id"] for post in chunk_posts)
        user_ids.difference_update(users)
        if user_ids:
            user_marks = ",".join(["%s"] * len(user_ids))
            cursor.execute(
                "SELECT `id`, `account_name`, `authority`, `del_flg`, `created_at` "
                f"FROM `users` WHERE `id` IN ({user_marks})",
                list(user_ids),
            )
            users.update((user["id"], user) for user in cursor)

        for post in chunk_posts:
            post["user"] = users[post["user_id"]]
            if post["user"]["del_flg"]:
                continue
            post["comment_count"] = counts.get(post["id"], 0)
            comments = comments_by_post.get(post["id"], [])
            for comment in comments:
                comment["user"] = users[comment["user_id"]]
            post["comments"] = list(reversed(comments))
            posts.append(post)
            if len(posts) >= POSTS_PER_PAGE:
                return posts
    return posts
```

?> 1 チャンク（まとまり）で最大 3 クエリです。「入力全件をまとめてから、最後に 20 件を切る」形には戻さないでください。`get_posts()` の `fetchall()` 自体は、この変更では残ります。同じ `created_at` のコメントの順序は、素体と同様に未指定です。同時刻の行がある場合も、公式ベンチで表示を検証します。

- 各手の変更後は、作業機で構文と対象差分を確認します。
- 利用者のレビュー後に、git で配布します。

```bash
# 作業機
python3 - <<'PY'
import ast
from pathlib import Path
path = Path("backup/s1/home/private_isu/webapp/python/app.py")
ast.parse(path.read_text())
print("app.py: syntax OK")
PY
git diff -- backup/s1/home/private_isu/webapp/python/app.py
# レビュー後に利用者が対象ファイルだけをcommit / pushする
```

サーバーで反映します。

```bash
REPO=/home/isucon/private-isu-terraform
cd "$REPO" && git pull --ff-only
test -s "$REPO/backup/s1/home/private_isu/webapp/python/app.py" || exit 1
sudo cp -a "$REPO/backup/s1/home/private_isu/webapp/python/app.py" /home/isucon/private_isu/webapp/python/app.py
sudo chown isucon:isucon /home/isucon/private_isu/webapp/python/app.py
sudo systemctl restart isu-python.service
systemctl is-active isu-python.service
```

- `sudo cp -a` は、所有者を `root` にしたまま配布します。
- アプリがこのファイルを書き換える経路がある場合も動くよう、`isucon:isucon` へ戻してから restart します。

- nginx 経由で、疎通を確認します。1 つずつ、同じベンチを回します。

```bash
# 1 台で打つ
curl -fsS -o /dev/null -m 5 http://127.0.0.1/
curl -fsS -o /dev/null -m 5 http://127.0.0.1/posts/1
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f /var/log/nginx/access.log; then
  sudo mv /var/log/nginx/access.log /var/log/nginx/access.log.$TS
fi
sudo nginx -s reopen
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
MATCH='/posts/[0-9]+,/image/[0-9]+,/@[a-zA-Z0-9_]+'
sudo cat /var/log/nginx/access.log | alp ltsv --sort sum --reverse -m "$MATCH" | head -12
mkdir -p ~/bench-notes
echo "$(date -Iseconds) score= pass= fail= note=pyspy-one-change" >> ~/bench-notes/scores.txt
```

- 実測（2026-10-13）: 投稿一覧・N+1 の例は、[0015-sql-tuning.md](0015-sql-tuning.md) で適用済みです。この章では、未実行です。
- 1 つだけの判定に使った単独ベンチは、`score=49716` と `score=51143`（ともに `pass=true` / `fail=0`、[§3](#3-出力例)）。

- `fail=0` を確認します。あわせて、一覧の件数・削除済みユーザーの非表示・詳細ページの全コメント・ページ送りも確認します。
- 空の `score=` などには、実際の結果を記入します。
- py-spy を取り直し、対象の計算が減ったかも確認します。

### 点数が落ちたときの切り分け（元に戻して測り直す）

- **1 回点数が落ちても、変更が原因とは限りません。**
- 変更前のファイルを確実に指定して戻します。同じ条件で、再測定します。
- `HEAD~1` は直前のコミットです。変更前とは、限りません。
- 未コミットの別作業も上書きしないよう、対象ファイルを確認します。

```bash
# 作業機。変更前のコミットIDを自分の履歴から確認する
git log --oneline -- backup/s1/home/private_isu/webapp/python/app.py
git diff -- backup/s1/home/private_isu/webapp/python/app.py
BEFORE='変更前と確認したコミットIDに置き換える'
git show "$BEFORE:backup/s1/home/private_isu/webapp/python/app.py" > /tmp/app-before.py
test -s /tmp/app-before.py || exit 1
# 上書きしてよい対象だと確認した後だけ配置する
cp /tmp/app-before.py backup/s1/home/private_isu/webapp/python/app.py
git diff -- backup/s1/home/private_isu/webapp/python/app.py
# 利用者が差分をレビューして commit / push してからサーバーへ配布する
```

- スナップショットを pull し、配置・再起動します。

```bash
REPO=/home/isucon/private_isu-terraform
cd "$REPO" && git pull --ff-only
sudo cp -a "$REPO/backup/s1/home/private_isu/webapp/python/app.py" /home/isucon/private_isu/webapp/python/app.py
sudo chown isucon:isucon /home/isucon/private_isu/webapp/python/app.py
sudo systemctl restart isu-python.service
systemctl is-active isu-python.service
```

| 状態 | ベンチで記録すること | 判定 |
| --- | --- | --- |
| 変更前 | 同じ設定で複数回の score・fail・alp | 基準のばらつき |
| 変更後 | 同じ項目を再測定 | 点数と対象経路が改善したか |
| 変更前へ戻す | さらに再測定 | 差が変更に追従するか |

- 比較条件を揃えます。
- 対象経路の遅延と点数の変化が、繰り返し再現するか確認します。
- 繰り返しの確認が足りないなら、効果は確定しません。実行した事実だけを残します。

### 観察すること

- on-CPU と `--idle` で絵が違います。片側だけでは原因を取り違えます
- 直した関数／クエリが Sandwich から落ち、スコアが動きます
- 直していない重い場所（ホットスポット）が残っているなら、次の 1 つにします（また 1 つだけ）

<a id="3-出力例"></a>

## 3. 出力例

- 2026-10-13 の実測（`c7a.large` 1 台 / `ap-northeast-1` / 1 台に全部入った構成（nginx + Python + MySQL が同じ台）/ `nproc=2` / メモリ 3.7Gi / gunicorn `-w 4` / 0015〜0080 適用済み: 索引＋N+1 一括化・画像ファイル化・expires + proxy_cache・worker 4＝**DB 設定ノブ調整後の状態** / py-spy 0.4.2）。
- `fail` が 0 の記録だけを比べています。プロファイラ併用の点数は、単独ベンチの点数と分けて記録します。

- **今回点数を動かしたのは、§2 の `digest()` → `hashlib` の 1 つだけでした**（単独ベンチ 45814 → 49716、再計測 51143。どちらも `fail=0`）。
- ログイン・登録経路を回す今回の記録では、`digest` / `subprocess` が観測に出ました。
- そのため、手順書どおりハッシュ計算をライブラリに寄せました。
- 投稿一覧と N+1 は、[0015-sql-tuning.md](0015-sql-tuning.md) で済ませてあります。この章の §2 では、残っていた `digest()` を選びます。

| 節 | 内容 | score | pass | fail | 判定 |
| --- | --- | --- | --- | --- | --- |
| 0080 最終 | DB 設定ノブ調整後の単独ベンチ（比較の原点） | 45814 | true | 0 | |
| §1 | py-spy 記録中のベンチ（on-CPU） | 43178 | true | 0 | プロファイラ併用。単独の点数と比べない |
| §1 | py-spy 記録中のベンチ（`--idle`） | 41307 | true | 0 | 同上 |
| **§2** | **`digest()` → `hashlib`（1 つだけ）** | **49716** | **true** | **0** | **原点比 +3902** |
| §2 確認 | 変更後の py-spy（on-CPU） | 44107 | true | 0 | 対象の計算が消えた |
| §2 確認 | 変更後の py-spy（`--idle`） | 42309 | true | 0 | 同上 |
| §2 再計測 | 同じアプリの単独ベンチ | 51143 | true | 0 | `success=47786`。49716 と同水準 |

### §1: py-spy でボトルネックを取る

- `ptrace_scope` は、AMI 既定の `1` でした。
- 取得は `0` にした状態で行い、**終了時に `1` へ戻しました**。
- 戻した後で `sudo py-spy record`（5 秒）が `ptrace_scope=1` のまま実行できることを、確認済みです。
- 今回の取得に、drop-in は必須ではありませんでした。

```text
py-spy 0.4.2（0020 で導入済み）
cat /proc/sys/kernel/yama/ptrace_scope   →  1   （取得時のみ 0。終了時に 1 へ復帰・drop-in は削除）
MainPID=105391（gunicorn master）
105391 master / 105397〜105400 worker（-w 4）
nproc = 2 / メモリ 3.7Gi
```

- `--format raw` で取り、サーバーで集計しました（[§1](#1-py-spy-でボトルネックを取る) の awk のとおり）。
- on-CPU と `--idle` は、**別々の 30 秒**です。
- 変更前の記録どうし、変更後の記録どうしで比べます。
- `--idle` の 2 回（変更前・変更後）は、worker の総サンプル数が 11996 で一致しました。

リーフフレーム（worker 総サンプル: on-CPU 5210 / `--idle` 11996）:

| リーフフレーム | on-CPU | `--idle` |
| --- | --- | --- |
| `query (MySQLdb/connections.py:286)` | 886 | 5201 |
| `_fetch_row (MySQLdb/cursors.py:339)` | 470 | 462 |
| `communicate (subprocess.py:1208)` | 34 | **1122** |
| `_get_result (MySQLdb/cursors.py:363)` | 128 | 352 |
| `_posix_spawn (subprocess.py:1819)` | 63 | 94 |
| `DateTime_or_None (MySQLdb/times.py:89)` | 172 | 171 |

`digest` 経路（パスワード照合の外部コマンド呼び出し。`--idle` では 2 番手）:

| フレーム | on-CPU | `--idle` |
| --- | --- | --- |
| `digest (app.py:107)` | 116 | **1237** |
| `calculate_passhash (app.py:120)` | 117 | 1238 |
| `check_output (subprocess.py:473)` | 116 | 1236 |
| `communicate (subprocess.py:1208)` | 34 | 1124 |
| `calculate_salt (app.py:116)` | 62 | 657 |

ディスパッチ先（どのルートが重いか）:

```text
on-CPU（変更前）                 --idle（変更前）
get_posts (app.py:390) 1224      get_posts (app.py:390) 1987
get_image (app.py:465)  375      calculate_passhash (app.py:120) 1238
get_posts (app.py:393)  275      digest (app.py:107) 1237
make_posts (app.py:158) 179      post_login (app.py:259) 1082
post_login (app.py:259) 144      get_index (app.py:315) 1068
```

`DateTime_or_None` は MySQL の日時値を Python の `datetime` に変換する処理です。

### §1 の判断: 手順書の 3 行との突き合わせ

| [§1](#1-py-spy-でボトルネックを取る) の表 | 実測 | 判定 |
| --- | --- | --- |
| on-CPU が `digest` / `subprocess` / `openssl` | **出た**（on-CPU `digest` 116 / `check_output` 116 / `_posix_spawn` 63、`--idle` では `digest` 1237 が 2 番手） | **[§2](#2-1-つ直してベンチする) の `digest()` → `hashlib` をやります**（ログイン・登録経路を回しながら記録したため。タイムライン主体の記録では出ないことがあります） |
| `--idle` で `execute` / `make_posts` が厚い | **`query()` が 5201 サンプル**で最大。`make_posts` 系も残る | N+1 一括化は 0015 で済み。残る候補は `get_posts` の全件 `fetchall()`（この章は 1 つだけなので次の手に回す） |
| `/image` が Sum 上位で `SELECT * FROM posts` | `/image` は [0080-app-tuning.md §2](0080-app-tuning.md#2-アップロード画像を静的ファイルにする投稿の本体データは-db-に残す) でファイル配信。`SELECT *` は DB フォールバックのみ | **変更しません** |

- `make_posts` は、投稿ごとに件数・コメント本文・投稿者を取得します。表示コメントごとに、ユーザーも取得します。
- コメントが 3 件ある投稿なら 6 クエリです。20 投稿すべてがその状態なら、120 クエリです。
- コメントが少ない投稿では、本数も少なくなります。

### §2: `digest()` を `hashlib` に差し替える（1 つだけ）

- 既存の passhash が、そのまま読めることを先に確認しました（`openssl dgst -sha512` と `hashlib.sha512` の hex が一致）。

```text
$ printf %s test | openssl dgst -sha512
SHA2-512(stdin)= ee26b0dd4af7e749aa1a8ee3c10ae9923f618980772e473f8819a5d4940e0db27ac185f8a0e1d5f84f88bc887fd67b143732c304cc5fa9ad8e6f57f50028a8ff
$ python3 -c '...'   # hashlib.sha512(b"test").hexdigest()
ee26b0dd4af7e749aa1a8ee3c10ae9923f618980772e473f8819a5d4940e0db27ac185f8a0e1d5f84f88bc887fd67b143732c304cc5fa9ad8e6f57f50028a8ff
```

- 変更したのは、`digest()` の本体と `import hashlib` の 2 箇所だけです（[§2](#2-1-つ直してベンチする) のコードのとおり）。
- `syntax OK`（`ast.parse`）を確認して、`isu-python` を restart しました。
- `GET /`・`GET /posts/1` が 200 であることも確認しました。

- ベンチは、bench-prep 済みの単独ベンチです（score / pass / fail は `~/bench-notes/scores.txt` に 1 行ずつ記録）。

```text
2026-10-13T11:35:16+09:00  score=49716 pass=True fail=0 note=pyspy-digest
2026-10-13T12:06:50+09:00  score=51143 pass=True fail=0 note=pyspy-digest-repeat
{"pass":true,"score":51143,"success":47786,"fail":0,"messages":[]}   ← 再計測の JSON
```

- alp は `POST /login` と `POST /register` です。
- 変更前はプロファイラ併用 2 ベンチの混合ログです。COUNT は 2 本分なので、AVG のみを比べます。

| | 変更前（併用 2 本の混合ログ） | 変更後（単独ベンチ 49716） |
| --- | --- | --- |
| `POST /login` AVG | 0.050 秒（COUNT 2893） | **0.022 秒**（COUNT 1236） |
| `POST /register` AVG | 0.060 秒（COUNT 693） | **0.035 秒**（COUNT 317） |
| `GET /posts`（ページ送り）AVG | 0.146 秒 | 0.127 秒 |
| `/` AVG | 0.053 秒 | 0.044 秒 |

- py-spy を取り直し、対象の計算が減ったことを確認しました。
- `digest()` が 5 行減ったぶん、後ろの関数の行番号がずれます。
- `get_posts (app.py:390)` → `get_posts (app.py:385)` は、同じ関数です。

| フレーム | on-CPU 変更前 → 変更後 | `--idle` 変更前 → 変更後 |
| --- | --- | --- |
| `digest (app.py)` | 116 → **1** | 1237 → **1** |
| `calculate_passhash (app.py)` | 117 → **1** | 1238 → **2** |
| `check_output (subprocess.py:473)` | 116 → **0** | 1236 → **0** |
| `communicate (subprocess.py:1208)` | 34 → **0** | 1124 → **0** |
| `_posix_spawn (subprocess.py:1819)` | 63 → **0** | 94 → **0** |
| `query (MySQLdb/connections.py:286)` | 886 → 924 | 5201 → 5633 |

- 変更後の上位は、`query` / `_fetch_row` など MySQL の往復と行変換のままです。
- アプリ側 1 番は、`get_posts` が残ります（`--idle` 2207 サンプル。`created_at <= ?` を全件 `fetchall()` するページ送り）。`GET /posts` の AVG 0.127 秒と、対応します。
- **今回は、直していません**（[§2](#2-1-つ直してベンチする) は 1 つだけ。次の 1 つの候補です）。

?> py-spy は、`behind in sampling` と出すことがありました（最大 16 秒遅れ）。サンプルの絶対数は、その都度の観測です。CPU 使用率には換算しません。比較は、同じ手順（`--format raw --duration 30 --rate 100`）の記録どうしで行います。

### この節でつまずいた点

?> **この表の問題はすべて本文へ反映済みです。**「直し方」列のとおりに直してあります。同じ症状が出たら本文の手順どおりに直して再計測します。

| # | 箇所 | 問題 | 修正内容 |
| --- | --- | --- | --- |
| 1 | §1 | SSM だけでの集計手順が無かった | `raw` でサーバー上で集計する例を追加しました |
| 2 | §1 | 行数を数えていたため、サンプル数の重みとマスタの除外が欠けていた | worker だけを選び、行末のサンプル数を足します |
| 3 | §1・§2 | 1 経路（タイムライン）の観測だけでハッシュ処理まで軽いと判断していた | ログイン・登録経路を回しながら記録する条件を明記しました |
| 4 | §2 | `HEAD~1` を変更前と決め打ちしていた | 変更前の内容を退避ファイルで確認してから戻し、比較は同条件で反復します |
| 5 | §4 | `FLUSH` だけでログを消せると誤解していた | `mv` → `FLUSH SLOW LOGS` と回転済みファイルの削除を分けました |
| 6 | §4 | sudo 前の glob 展開で、読めないディレクトリを「ファイル無し」と誤認した | glob を使わない `sudo ls -lh /var/log/mysql/` にしました |

### ディスクとスローログ（今回の状態）

- ベンチごとに、slow log（遅い問い合わせの記録）は `mv` + `FLUSH SLOW LOGS` で回転しました。
- `long_query_time` は、`1` のまま扱いました（全件キャプチャの `0` は、この章では使いません）。
- 終了時点の状態は、次のとおりです。

```text
sudo mysql -N -e 'SELECT @@global.long_query_time, @@global.slow_query_log'
1.000000	1

df -h /（終了時点）
/dev/root        38G   15G   23G  40% /

sudo ls -lh /var/log/mysql/（今回分の退避ログは 178B〜422B。slow は 1 秒超のみ）
```

?> `sudo ls /var/log/mysql/mysql-slow.log*` の `*` は sudo より前に呼び出し側のシェルが展開します。読めないディレクトリでは「ファイル無し」に見えることがあります（[§4](#ディスクが埋まるスローログ) のとおり `sudo ls -lh /var/log/mysql/` で見ます）。`FLUSH SLOW LOGS` は同じファイルを開き直すだけで、改名・消去はしません。

### 観察したこと（実測）

- 今回のベンチはログイン・登録経路を回すため、`digest` / `subprocess` が観測に出ました（`--idle` で `digest` 1237 が 2 番手）。`hashlib` への差し替えを 1 つだけ直し、45814 → 49716 → 51143 でした（どちらも `fail=0`）
- 変更後は `digest` 経路の計算がほぼ 0（`check_output` / `communicate` / `_posix_spawn` は 0）。上位は `query` / `_fetch_row` など MySQL の往復と行変換のままです
- 残った重い場所（ホットスポット）は `get_posts` の全件 `fetchall()`（`--idle` 2207 サンプル、`GET /posts` AVG 0.127 秒）。この章は 1 つだけなので次の手に回しました
- slow log の逼迫はありませんでした（`df -h /` は 40%）。回転は `mv` + `FLUSH SLOW LOGS` だけで運用し、truncate はしていません

## 4. トラブルシュート

<a id="ディスクが埋まるスローログ"></a>

### ディスクが埋まる（スローログ）

```bash
df -h /
sudo du -xh /var/log /home /tmp 2>/dev/null | sort -h | tail -n 20
# globを使わず、ディレクトリ自体をroot権限で読む
sudo ls -lh /var/log/mysql/
```

?> `sudo ls /var/log/mysql/mysql-slow.log*` の `*` は、sudo より前に呼び出し側のシェルが展開します。呼び出し側がディレクトリを読めないと、bash では一致しないパターンがそのまま渡されます。zsh では `no matches found` になることがあります。`sudo ls -lh /var/log/mysql/` なら、この glob 展開を避けられます。

- 生きている `mysql-slow.log` は、**truncate しません**。
- やることは、次の順番です。

1. MySQL で `long_query_time` を1へ戻し、アプリを再起動して既存接続にも反映する
2. 実際の `slow_query_log_file` を確認し、現在のログを `mv` で日付付きの名前へ退避する
3. `FLUSH SLOW LOGS` で設定されたパスを開き直す
4. 退避したログを解析して結果を記録した後、そのファイルだけを削除する

```bash
# 1 台で打つ
sudo mysql -e 'SET GLOBAL long_query_time = 1'
sudo mysql -N -e 'SELECT @@global.slow_query_log, @@global.slow_query_log_file, @@global.long_query_time'
# 表示されたパスが以下と一致することを確認する
LOG=/var/log/mysql/mysql-slow.log
TS=$(date +%Y%m%d%H%M%S)
if sudo test -f "$LOG"; then
  sudo mv "$LOG" "$LOG.$TS"
fi
# 親ディレクトリにmysqldの書き込み権限が無くても再開できるよう、新規ファイルを用意する
sudo test -f "$LOG" || sudo install -o mysql -g mysql -m 0640 /dev/null "$LOG"
sudo mysql -e 'FLUSH SLOW LOGS'
sudo ls -lh "$LOG"
df -h / | tail -1
```

- 新しいログが元のパスにあり、退避したログが日付付きの名前で残っていることを確認します。
- アプリも再起動してから、計測へ戻ります。

```bash
# 1 台で打つ
sudo systemctl restart isu-python.service
systemctl is-active isu-python.service
```

- 容量を回復するには、**結果を記録した退避済みファイルだけ**消します。

```bash
# 1 台。先ほどのLOG・TSを保持している同じセッションで実行する
sudo ls -lh "$LOG.$TS"
# 解析・記録が済み、不要と確認した場合だけ
sudo rm -- "$LOG.$TS"
df -h /
```

?> `FLUSH SLOW LOGS` は、明示したパスでも有効です。ただし単独では、改名・消去しません。回転が失敗したら、`sudo journalctl -u mysql -n 80 --no-pager` で権限・ファイル作成のエラーを確認します。詳しくは [MySQL 8.0のログ保守](https://dev.mysql.com/doc/refman/8.0/en/log-file-maintenance.html) を参照してください。

### 観察すること

- `df` でファイルシステム全体の使用量を、`du` でログの占有量を確認します。`ls -l` の論理サイズだけで判断しません
- ディレクトリに読み取り権限が無いことと、ファイルが存在しないことを区別します

### 計測用のファイルとptrace設定を戻す

- 記録は、比較結果を残してから消します。
- `sudo py-spy` が作ったファイルは root 所有です。削除にも sudo を使います。

```bash
# 1 台。今回作った記録だけを指定する
sudo rm -f /tmp/pyspy-cpu.txt /tmp/pyspy-idle.txt /tmp/pyspy.json /tmp/pyspy-idle.json
```

- ptrace の drop-in を今回追加した場合は、作業機の `backup/s1/etc/sysctl.d/99-isucon-ptrace.conf` も元に戻します。
- 利用者が差分をレビューして、git 経由で配布します。
- s2 では、そのファイルだけ外し、記録した変更前の値へ戻します（この回は 1）。
- 元から必要な設定だった場合は、削除しません。

```bash
# 1 台。この章のためだけに追加した場合
sudo rm -f /etc/sysctl.d/99-isucon-ptrace.conf
sudo sysctl -w kernel.yama.ptrace_scope=1
cat /proc/sys/kernel/yama/ptrace_scope
```

### py-spy が Permission denied / ptrace

- `MainPID` が 0 なら、unit が落ちています。まず sudo で実行しているかを確認します。
- root でも拒否される場合は、ptrace の制約・実行環境の権限を調べます。
- `ptrace_scope=0` へ変更するだけで、必ず取得できるとは限りません。

### 再起動後 502

```bash
UNIT=isu-python.service
systemctl is-active nginx.service mysql.service "$UNIT"
systemctl is-enabled isu-ruby.service "$UNIT"
sudo journalctl -u "$UNIT" -n 80 --no-pager
# nginx 経由と HTTP 直結を確認する
# 1 台で打つ
curl -v -o /dev/null http://127.0.0.1/login
ss -lntp | grep 8080
curl -v -o /dev/null http://127.0.0.1:8080/login
# unix socket にした場合だけ
sudo ss -lxp | grep gunicorn.sock
```

- Ruby が enabled に戻っていたら [webapp-setup/python.md](../010_common/webapp-setup/python.md) を見て切り替え直します
- 依存パッケージの問題なら、AMI 同梱の `.venv` に `gunicorn` / `flask` / `MySQLdb` が入っているかを `sudo -u isucon -H .venv/bin/python3 -c 'import flask, MySQLdb'` で確認します。`uv` はこの AMI にありません（[0110-split-1.md](0110-split-1.md) §4 を参照）
- nginx の `proxy_pass` とアプリの listen（8080）がずれていないか確認します

### mysqld が起きない

```bash
sudo journalctl -u mysql -n 80 --no-pager
sudo rm -f /etc/mysql/mysql.conf.d/99-isucon-flush.cnf
sudo systemctl restart mysql
sudo mysql -N -e 'SELECT 1'
```

- drop-in を足した直後に死んだら、そのファイルを消して restart します
- `SELECT 1` が返れば復旧です。返らなければ error.log を見ます

- 0100 まで終わったら、[0110-split-1.md](0110-split-1.md) の分割に進みます。
- この章で残した変更は、`backup/s1` に積みます（流儀は [0020-measure.md](0020-measure.md) の「残す変更は backup/s1 に積む」と同じ）。
- 終盤に、py-spy の記録は残しません（[§2](#2-1-つ直してベンチする) の「計測用のファイルとptrace設定を戻す」で消します）。
