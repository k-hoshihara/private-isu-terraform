# 手順書

環境を選んで、そのフォルダの **番号順** に読む。番号は 10 刻み（0010, 0020, 0030, …）なので間に差し込める。

| フォルダ | 内容 |
| --- | --- |
| [010_common/](010_common/README.md) | 言語切替・初動・計測・配布・参照。台数によらない |
| [040_practice/](040_practice/README.md) | 実 AWS の構築・分割と練習問題（private-isu） |
| [050_isucon14/](050_isucon14/README.md) | ISUCon 14（ISURIDE）の環境構築と練習。private-isu とは別のアプリ |

## どれを開くか

- **AWS を 1 台で始める** — [040_practice/0010-env.md](040_practice/0010-env.md) で `webapp_instance_count = 1` → [010_common/0010-setup.md](010_common/0010-setup.md) → [040_practice/0015-sql-tuning.md](040_practice/0015-sql-tuning.md) から番号順に。0110 の分割までに `3` に増やします
- **分割して複数台で動かす** — 0100 までを 1 台で終えたら [040_practice/0110-split-1.md](040_practice/0110-split-1.md) / [0110-split-2.md](040_practice/0110-split-2.md) / [0110-split-3.md](040_practice/0110-split-3.md) / [0110-split-4.md](040_practice/0110-split-4.md) へ。役割を分けて点数を戻して超えます
- **練習問題** — 立てたあとに手を動かすドリル。[040_practice/](040_practice/README.md) に足していく
- **SQLチューニング** — 索引と N+1 を先に済ませるなら [040_practice/0015-sql-tuning.md](040_practice/0015-sql-tuning.md)
- **キャッシュ** — 1 台で計測を回せるようになったら [040_practice/0030-cache.md](040_practice/0030-cache.md)。[0020-measure.md](040_practice/0020-measure.md) の続きで memcached と nginx を 1 手ずつ
- **HTTPクライアント** — 同一ホストへのコネクションを使い回すなら [040_practice/0040-http-client.md](040_practice/0040-http-client.md)。タイムアウトと上限を 1 手ずつ
- **タイムアウト深掘り（Python）** — タイムアウトとプール上限を検証するなら [040_practice/0050-timeout.md](040_practice/0050-timeout.md)。[0040](040_practice/0040-http-client.md) の続き
- **静的・画像キャッシュ** — 画像や CSS/JS を速くして点数で判定するなら [040_practice/0060-static-cache.md](040_practice/0060-static-cache.md)。書籍「達人が教える Web パフォーマンスチューニング」8-5 の条件付きリクエスト（`expires` + `304` + `ETag` 台間一致）
- **基盤パラメータ** — カーネル〜土台の1ノブで点数が動くか見るなら [040_practice/0070-infra-params.md](040_practice/0070-infra-params.md)。somaxconn / port範囲 / unix差分 / worker数 / FD上限
- **アプリ処理方式** — 処理方式の差分を点数で判定するなら [040_practice/0080-app-tuning.md](040_practice/0080-app-tuning.md)。画像ファイル化 / FD確認 / ADMIN PREPARE無効化
- **全体最適チューニング** — py-spyで残りの重さを取るなら [040_practice/0100-overall-tuning.md](040_practice/0100-overall-tuning.md)。[0080](040_practice/0080-app-tuning.md) の次

`terraform/` の `webapp_instance_count` 既定は 3。1 台から始めるときは手順側で `1` を書き、0110 の分割までに `3` にします。

## ISUCon 14（ISURIDE）を練習するとき

private-isu ではなく ISUCon 14 の出題アプリ（ISURIDE）を練習する場合は [050_isucon14/](050_isucon14/README.md) を使います。構築は [050_isucon14/terraform/](050_isucon14/terraform) の Terraform で行います。公式 AMI `ami-0e334c50145a3ee41` は公開停止しているため、公開が続いている ISUCon 14 AMI に差し替えています。

| 番号 | 内容 |
| --- | --- |
| [0010](050_isucon14/valid/0010-env.md) | Terraform でアプリ用 1 台とベンチ用 1 台を立てる |
| [0015](050_isucon14/valid/0015-backup.md) | 変更を戻せるようにバックアップを取る |
| [0020](050_isucon14/valid/0020-https.md) | TLS 証明書を差し替える |
| [0030](050_isucon14/valid/0030-python.md) | Python 実装を起動する |
| [0040](050_isucon14/valid/0040-bench.md) | ベンチマーカーをビルドして 1 本回す |
| [0050](050_isucon14/valid/0050-measure.md) | alp・slow log・py-spy で計測の型を作る |
| [0055](050_isucon14/valid/0055-measure-digest.md) | `performance_schema` で計測をやり直す |
| [0060](050_isucon14/valid/0060-sql-index.md) | 索引で SQL を短くする |
| [0070](050_isucon14/valid/0070-sql-columns.md) | 集計をやめて列で持つ |
| [0080](050_isucon14/valid/0080-sql-n1.md) | N+1 を潰す |
| [0090](050_isucon14/valid/0090-app-notification.md) | 通知の往復と COMMIT を減らす |
| [0100](050_isucon14/valid/0100-app-payment.md) | 決済の冪等化 |
| [0110](050_isucon14/valid/0110-app-matching.md) | マッチング（いちばん効く） |
| [0120](050_isucon14/valid/0120-app-cache.md) | キャッシュと列で持つ |
| [0130](050_isucon14/valid/0130-infra.md) | インフラ（nginx / gunicorn / ログ） |
| [0140](050_isucon14/valid/0140-mysql-write.md) | MySQL の書き込み設定 |
| [0150](050_isucon14/valid/0150-mw-lib.md) | ミドルウェアとライブラリ（プール・タイムアウト） |
| [0160](050_isucon14/valid/0160-split.md) | 3 台に分割する |
| [0170](050_isucon14/valid/0170-mysql-host.md) | MySQL を専用台に置く |
| [0180](050_isucon14/valid/0180-split-sql.md) | 分割後の SQL と接続 |
| [0190](050_isucon14/valid/0190-split-app.md) | 分割前提のアプリ |
| [0200](050_isucon14/valid/0200-timeout.md) | タイムアウト |
| [0210](050_isucon14/valid/0210-result.md) | 結果と後片付け |
| [0220](050_isucon14/invalid/0220-more.md) | 追加検証 |

0070 以降（SQL の続き → アプリ → インフラ → 分割 → 分割後のチューニング）は、単一インスタンスで 1 手ずつ実測しながら書いています。
