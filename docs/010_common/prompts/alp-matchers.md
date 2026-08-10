# 当日プロンプト: alp の URL 正規化

本番中、Claude Code / 作業用 LLM に次を投げる。

---

以下は今回の問題のアプリケーションコードです。  
alp の URL 正規化用マッチャ設定（`etc/alp/matching_groups.json`）を生成してください。

制約:

- 既存ファイルの JSON 形を保つ（`groups` は文字列配列）
- パスパラメータ（数値 ID、UUID、ユーザー名）を正規表現でまとめる
- 具体的なパスほど配列の前に置く
- 静的ファイル（`/static` `/assets` `/image` など）もまとめる
- サンプルの private-isu 用パターンを残さない。この問題のルートだけにする
- 根拠になったルート定義のファイルと行をコメント（`_comment` 配列）に書く

<ルーティング定義を貼る>

（Flask / FastAPI なら `@app.route` / `@app.get`、テンプレートの `url_for`、nginx の location もあれば併せて貼る）

---
