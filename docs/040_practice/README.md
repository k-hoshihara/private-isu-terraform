# 練習問題

AWS 上に立てて、その上で手を動かします。計測の見方は [010_common/0020-measure.md](../010_common/0020-measure.md)。番号順に並べます。今後のドリルもここに追加します。

| 番号 | ファイル | 内容 |
| --- | --- | --- |
| 0010 | [0010-env.md](0010-env.md) | 練習用 EC2 を立てる。1 台は `webapp_instance_count = 1`、複数台は `3` |
| 0020 | [0020-split-1.md](0020-split-1.md)・[0020-split-2.md](0020-split-2.md)・[0020-split-3.md](0020-split-3.md) | 役割を分ける（s1 nginx+unix / s2 HTTP / s3 MySQL）。前半§1–§7・中盤§8・後半§9–§12 |
| 0030 | [0030-measure.md](0030-measure.md) | 計測ツールの導入と計測サイクルを回す |
| 0040 | [0040-cache.md](0040-cache.md) | 0030 の続き。memcached / nginx を 1 手ずつ |
| 0050 | [0050-http-client.md](0050-http-client.md) | 同一ホストへのコネクションを使い回す。タイムアウトと上限を 1 手ずつ |
| 0060 | [0060-timeout.md](0060-timeout.md) | 0050 の続き。タイムアウトとプール上限の深掘り |
| 0070 | [0070-static-cache.md](0070-static-cache.md) | 8-5 の条件付きリクエスト。画像と静的を `expires` + `304` で高速化。判定は点数 |
| 0080 | [0080-infra-params.md](0080-infra-params.md) | 土台を 1 ノブずつ。somaxconn / port 範囲 / unix 差分 / worker 数 / FD 上限 |
| 0090 | [0090-app-tuning.md](0090-app-tuning.md) | アプリ処理方式の差分。画像ファイル化 / SQL / ADMIN PREPARE 無効化 |
| 0100 | [0100-sql-tuning.md](0100-sql-tuning.md) | SQL チューニング。対象 1 本 → EXPLAIN → インデックス 1 本 → 設定ノブ |
| 0110 | [0110-overall-tuning.md](0110-overall-tuning.md) | 全体最適チューニング。py-spy と切り分け |

番号は 10 刻み。入口は [../README.md](../README.md)。
