# 手順書

環境を選んで、そのフォルダの **番号順** に読む。番号は 10 刻み（0010, 0020, 0030, …）なので間に差し込める。

| フォルダ | 内容 |
| --- | --- |
| [010_common/](010_common/README.md) | 言語切替・初動・計測・配布・参照。台数によらない |
| [040_practice/](040_practice/README.md) | 実 AWS の構築・分割と練習問題 |

## どれを開くか

- **AWS 1 台** — [040_practice/0010-env.md](040_practice/0010-env.md) で `webapp_instance_count = 1` → [010_common/0010-setup.md](010_common/0010-setup.md) → [040_practice/0030-measure.md](040_practice/0030-measure.md)
- **AWS 複数台** — 同じ [040_practice/0010-env.md](040_practice/0010-env.md) で `webapp_instance_count = 3` → [040_practice/0020-split.md](040_practice/0020-split.md)
- **練習問題** — 立てたあとに手を動かすドリル。[040_practice/](040_practice/README.md) に足していく
- **キャッシュ** — 1 台で計測を回せるようになったら [040_practice/0040-cache.md](040_practice/0040-cache.md)。[0030-measure.md](040_practice/0030-measure.md) の続きで memcached と nginx を 1 手ずつ
- **HTTPクライアント** — 同一ホストへのコネクションを使い回すなら [040_practice/0050-http-client.md](040_practice/0050-http-client.md)。タイムアウトと上限を 1 手ずつ
- **タイムアウト深掘り（Python）** — Go リスト 8/9 を Python で検証するなら [040_practice/0060-timeout.md](040_practice/0060-timeout.md)。[0050](040_practice/0050-http-client.md) の続き
- **静的・画像キャッシュ** — 画像や CSS/JS を速くして点数で判定するなら [040_practice/0070-static-cache.md](040_practice/0070-static-cache.md)。8-5 の条件付きリクエスト（`expires` + `304` + `ETag` 台間一致）
- **基盤パラメータ** — カーネル〜土台の1ノブで点数が動くか見るなら [040_practice/0080-infra-params.md](040_practice/0080-infra-params.md)。somaxconn / port範囲 / unix差分 / worker数 / FD上限
- **アプリ処理方式** — 処理方式の差分を点数で判定するなら [040_practice/0090-app-tuning.md](040_practice/0090-app-tuning.md)。画像ファイル化 / SQL / ADMIN PREPARE無効化

`terraform/` の `webapp_instance_count` 既定は 3。1 台にするときは手順側で `1` を書く。
