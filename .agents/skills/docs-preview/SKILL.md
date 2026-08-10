---
name: docs-preview
description: >-
  今いるプロジェクト（cwd の git toplevel、git でなければ cwd）の Markdown を docsify でプレビューする。
  http://localhost:3030（占有時は空きポート）で配信し、ディスク上の .md がそのまま返るところまで確認する。
  「ドキュメントサーバ起動」「docsify 起動」「プレビュー確認」「markdown をブラウザで見る」
  と言われたら使う。 /docs-preview
---

# docs-preview — 今のプロジェクトの Markdown プレビュー

配信ルートは **今いるプロジェクト**。リポジトリ名やホーム配下のパスを skill に書かない。

## 手順

各ステップの結果を確認してから次へ進む。

### 1. 配信ルート

```sh
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
ROOT=$(cd "$ROOT" && pwd -P)
```

`$ROOT` 以下にプレビュー対象の `.md` があること。無ければ止めて報告する。

確認するパス（存在するファイルだけ。ユーザーが特定の `.md` を指定したらそれを必ず含める）:

- ルートの `README.md`（あれば）
- ルートの `_sidebar.md`（あれば）
- 指定された `.md`、無指定ならルートの `README.md`、それも無ければ `$ROOT` 直下の `.md` を 1 つ

`docsify: command not found` なら `npm i -g docsify-cli` を提案する（勝手にインストールしない）。

### 2. このルート向けの docsify を探す

他プロセスが 3030 を LISTEN していても、配信元がこの `$ROOT` でなければ「起動済み」とみなさない。

```sh
pgrep -af 'docsify serve'
```

各 PID について、コマンドラインか `/proc/<pid>/cwd` が `$ROOT` を指すものを探す。見つかったらその `-p` ポートを再利用し、起動はしない。

### 3. 無ければ起動

基準ポートは `3030`。埋まっていたら `3031`, `3032`, … と空くまで進める。

```sh
port=3030
while ss -tln | grep -q ":${port} "; do
  port=$((port + 1))
done
docsify serve "$ROOT" -p "$port"
```

`docsify serve` はフォアグラウンドで動き続けるので、**必ずバックグラウンド実行**する。使った `$port` を控える。

### 4. 疎通（今のプロジェクトのファイルと一致すること）

起動直後は少し待ってから当てる。`$rel` は `$ROOT` からの相対パス。

```sh
sleep 2
curl -s -o /dev/null -w '%{http_code}' "http://localhost:${port}/"
curl -s "http://localhost:${port}/" | head -20
curl -fsS "http://localhost:${port}/${rel}" | cmp -s - "$ROOT/${rel}"
```

成功条件:

- 手順 1 で選んだ各 `.md` が `200` で、本文がディスク上の同じファイルと一致する（`cmp` が成功する）
- `$ROOT/index.html` がある場合、`/` が `200` で `docsify` を参照する HTML である
- `index.html` が無い場合、`.md` の一致だけを成功とし、ブラウザでは docsify の画面にならない旨を報告する

`200` 以外・接続拒否・`cmp` 失敗なら、バックグラウンドの docsify 出力を見て原因を報告する。ポートが開いていても、返ってきた `.md` が `$ROOT` のファイルと違うなら再利用せず手順 3 で別ポートを起動する。

ブラウザで開くパス（docsify のハッシュルート、拡張子なし）:

- ホーム: `http://localhost:${port}/`
- ファイル: `http://localhost:${port}/#/${rel%.md}`

## 完了報告

- docsify が「このルート向けに既に起動していた」か「今回起動した」か
- 配信ルート（`$ROOT`）とポート
- 確認した `.md` の相対パスと、ディスクと一致したこと
- アクセス URL: `http://localhost:${port}/`

## トラブルシューティング

### docsify が `TypeError ... fileURLToPath ... Received undefined` でクラッシュする

docsify-cli が依存する **figlet 1.11.x の CJS ビルドが Node 24 で壊れている**（`node-figlet.cjs` 内の `fileURLToPath({}.url)` が undefined）。docsify-cli 内の figlet だけ下げる:

```sh
cd "$(npm root -g)/docsify-cli" && npm install figlet@1.8.2 --no-save --no-audit --no-fund
```

docsify-cli の再インストールや Node の入れ替えで再発する。その場合は再実行する。
