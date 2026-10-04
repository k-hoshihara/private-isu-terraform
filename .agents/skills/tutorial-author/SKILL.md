---
name: tutorial-author
description: >-
  このリポジトリのチュートリアル（docs/040_practice/*.md）を作る・直すときに使う。
  backup/base を正本として手順の正しさを検証し、Python 専用・backup/s*＋featureブランチ方式・
  0010→0090 の地続き・ベンチ必須の流儀で書く。
  「チュートリアル作成」「0030以降を直して」「手順書レベルで書き直して」と言われたら使う。
---

# tutorial-author — チュートリアル作成

## 適用範囲

- 対象は `docs/040_practice/` のチュートリアル（0010-env / 0020-split-1〜3 / 0030〜0090）。
- Python 専用。Go 言語の要素は入れない（他言語 unit の停止・発見のための `disable` / 一覧表示は除く。ポート競合を防ぐために必要）。
- 日本語で書く。補足は docsify のハイライト記法 `?>` で書く。

## 0. 正本の確認（書く前に必ずやる）

手順に書くパス・値・既定は推測しない。`backup/base/`（素体のスナップショット。不変の基準線）を正本として確認する。

| 確認すること | 正本での値 |
| --- | --- |
| アプリ配置 | `/home/isucon/private_isu/webapp/python`（`app.py`、`pyproject.toml`、`templates/`）。`golang` / `ruby` / `node` / `php` は使わない |
| `env.sh` | `ISUCONP_DB_USER/PASSWORD/NAME` のみ。`DB_HOST` / `MEMCACHED_ADDRESS` は無い（未設定時の既定はコード側: DB=`localhost:3306`、memcached=`127.0.0.1:11211`） |
| gunicorn unit | `.venv/bin/gunicorn app:app -b 0.0.0.0:8080`、`User=isucon`、`WorkingDirectory=.../python`、`EnvironmentFile=/home/isucon/env.sh` |
| DB ドライバ | `MySQLdb`（mysqlclient）。クライアント側エスケープでサーバ `PREPARE` を出さない |
| 外への HTTP 呼び出し | 素体に無い（`requests` / `urllib` / `http.client` の使用なし）。HTTP 系ドリルは合成再現 + nginx upstream でやる |
| `/image/<id>.<ext>` | DB の `posts.imgdata` から返す。ファイルではない |
| ルート | `/login`・`/register`・`/logout` の GET あり。`static_folder=../public` |
| N+1 の形 | `make_posts` が post 毎に COUNT + comments(LIMIT 3) + コメント毎 user + post 毎 user を読む |
| パスワードハッシュ | `digest()` が `openssl dgst -sha512` にシェルアウトする |
| nginx 素体 | `sites-enabled/isucon.conf` は `sites-available/isucon.conf` への symlink。中身は `root public/` + 全部 `proxy_pass http://localhost:8080` |
| nginx 全体 | `worker_processes auto`、`worker_connections 768`、`access_log` は combined（LTSV 化は 0010-setup でやる） |
| memcached 素体 | `-u memcache`、`-m 64`、`-p 11211`、`-l 127.0.0.1`（+ `::1`） |
| mysqld 素体 | `bind-address = 127.0.0.1`。slow log はコメントアウト（有効化は 0010-setup）。`max_connections` は既定（見るだけ） |
| `public/` | `css` / `js` / `img` / `favicon.ico`（`image/` は無い） |
| DDL | リポジトリ内に `.sql` は無い。スキーマ確認は実 DB の `SHOW INDEX` / `EXPLAIN` でやる |

`backup/base/` に無いもの（slow log の有無、`nc` の有無など）は 0010-setup の手順が面倒を見る。前提に 0010-setup 済みを書く。

## 1. 方向性（全チュートリアル共通）

- `backup/base/` は不変の基準線。feature ブランチで触らない。
- `backup/s1`・`s2`・`s3` は台ごとの生きたコピー。変更はここに積む。
- やり取りは feature ブランチ（`feature/backup` / `feature/split` / `feature/tuning` など目的別）。main に直接 push しない。
- 設定ファイルはサーバで直接編集しない。作業機で作る → push → 各台で pull → `cp` で移す → 反映（reload / restart）→ 確認。
- 0010 → 0020 → 0030 → … → 0090 は地続き。各ファイルに出口を 1 行書く。前の続きであることは冒頭の 1 行で示す。「前提:」の箇条書きは書かない（手順に不要な情報は削る）。
- ベンチは各チュートリアル最低 1 回。分割実装（0020-split-2）は反映ごとに回す。ベンチ時刻は毎回フルコマンドを書く（「冒頭の型を見よ」だけにしない）。
- 判定は `~/bench-notes/scores.txt` に 1 行。`fail` が 0 でない点数は比べない。1 手ずつ（同時に入れて比べない）。

## 2. サーバを壊さない規則（手順化の必須条件）

- `upstream` は http コンテキストにだけ書く（`server` ブロックの中は不可。`nginx -t` が落ちる）。`sites-enabled/` の site conf とは別ファイル（例: `/etc/nginx/conf.d/upstream.conf`）に分けるか、http 直下に置く。
- `nginx -t` が通ってから reload / restart する。変える前に `.orig` を残す。
- unit 本体は触らず drop-in で上書きする。`ExecStart=` の空行を忘れない。変えたら `daemon-reload` する。
- `sites-enabled/isucon.conf` は symlink。`tee` / `cp` で置き換えると通常ファイルになる。以後は `sites-enabled` の実体が正になることを書く。
- Ubuntu 24.04 の `pip install` には `--break-system-packages` を付ける（`py-spy`、`requests` など）。
- `nc`（netcat-openbsd）・`jq`・percona-toolkit（`pt-query-digest`）は 0010-setup で入れる。前提に書く。
- 生成スクリプトは土台ファイルの存在を先に確認する。無いまま進むと空ファイルで上書きする（例: `env.sh` 生成前に `[ -s ... ] || exit 1`）。
- MySQL slow log は `truncate` しない（`mv` + `flush-logs`）。`long_query_time=0` は短いキャプチャだけにして必ず戻す。
- `access.log` の回転は存在確認してから `mv` する。
- ベンチ中の再起動試験は正本確認のためだけにやる。普段の restart と混ぜない。

## 3. 文章の水準

- タイトルは内容を表す名前にする（「1台ドリル」のような意味不明な名前は付けない）。
- 用語は初出で定義し、意味不明な語は使わない（`正本` → `投稿の本体データ`）。
- 情報のない節を置かない。初出のツールは install 手順を網羅する。
- 内容はその番号・段階にふさわしくする。早すぎる内容は後の番号に移す。
- 手順書レベルにする（コマンド全文・期待出力・成功の見え方。「〜を見よ」だけにしない）。
- 述語を欠落させない（「〜のどちらか言える」→「どちらかを言える」、「ベンチ 1 本」→「ベンチを 1 本回す」）。
- スラング・造語を使わない（殴る、当たり、即死、両建て、素叩き、2 数、こっち、ディストリ、軽重、同時入れ、本番適用）。
- 番号参照は文書名を明示する（「0030 の戻し方」→「0030-ops.md の戻し方」）。
- 手順の箇条書きは「やること + 確認すること」の形にする。確認なしの変更手順を置かない。
- コマンドの前後に「何のためか 1 行」と「成功の見え方 1 行」を書く。

## 4. 作業手順

1. 対象ファイルを通読し、「0. 正本」と突き合わせて事実誤認を洗う。
2. 「2. サーバを壊さない規則」に照らして手順欠陥（順序・権限・前提不足）を洗う。
3. Go 要素があれば Python に書き換える（停止・発見のための行は残す）。
4. 「1. 方向性」に沿って構成を直す（前提・出口、backup パス、feature ブランチ、ベンチ）。
5. 「3. 文章の水準」で直す。
6. 仕上げに `tutorial-review` SKILL でレビューし、指摘が無くなるまで修正を回す。
