# backup/isucon14

ISUCon 14 用のバックアップ置き場です。[../../docs/050_isucon14/0015-backup.md](../../docs/050_isucon14/0015-backup.md) の手順で使います。

| 置き場 | 中身 | 誰が作るか |
| --- | --- | --- |
| `base/` | 0010 の直後（AMI のまま）の状態。基準線。**書き換えない** | 最初に 1 回 |
| `s1/` | `app-1`（nginx + MySQL + アプリ）で残すと決めた変更 | 各章の最後に更新 |
| `s2/` | `app-2`（アプリ）の状態 | [0110-split.md](../../docs/050_isucon14/0110-split.md) で作る |
| `s3/` | `app-3`（アプリ）の状態 | 同上 |

- 入るのは「自分で書き換えるもの」だけです。アプリのソース（`webapp/python/app`、`webapp/sql`）、`env.sh`、nginx の設定、systemd の unit、MySQL の設定です。
- `/etc` を丸ごとは取りません。`shadow` やホスト鍵は入れません。
- `env.sh` に DB の認証情報が入るので、`backup/isucon14/` を public リポジトリへ push しないでください（教材の値 `isucon` / `isucon` 以外を書いた場合）。

## 使い方

```bash
# この台の状態を s1 に写す（この台で）
sudo /home/isucon/private-isu-terraform/backup/isucon14/snapshot.sh s1

# s1 の内容をこの台に戻す（この台で）
sudo /home/isucon/private-isu-terraform/backup/isucon14/restore.sh s1
```

`snapshot.sh` はリポジトリのルートを自分の位置から決めます。`backup/isucon14/` ごと別の場所へ動かした場合は使えません。

## いま何が入っているか

2026-10-17 の検証環境（`app-1` / `app-2` / `app-3`）から実体を写してあります。ファイルごとの出どころと md5 は [MANIFEST.md](MANIFEST.md) にあります。

- `base/` は AMI のままの状態を **組み立てた** ものです。各台に残っていた `*.orig` / `*.pre-split` を正本にしています（例: `home/isucon/env.sh` は `app-1` の `env.sh.orig`、`gunicorn.conf.py` は `app-2` の `gunicorn.conf.py.orig`）。
- `s1/` / `s2/` / `s3/` は各台のいまの状態そのままです。
- `base/` には `1-index.sql`、`isucon14-ltsv.conf`、`upstream.conf` がありません。AMI に無く、この教材で足したためです。
- `3-initial-data.sql.gz`（初期データ、821 KB）は 3 台で md5 まで同じです。4 つの置き場に同じものが入りますが、git は同一の中身を 1 つだけ持ちます。

`snapshot.sh` の対象パスに加えて、読むためのソース一式（`pyproject.toml` / `uv.lock` / `noxfile.py` / `.python-version` / `.gitignore` / `.pre-commit-config.yaml`）も置いています。`restore.sh` はこの追加分を触りません。検証中に作った使い捨ての控え（`*.pre0080` / `*.pre0090` / `*.broken_step4` / `*.pre_step4`）だけは外しています。
