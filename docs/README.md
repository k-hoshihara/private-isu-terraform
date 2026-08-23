# 手順書

番号付きの一本道ではない。先にフレーバーを選び、台数を選ぶ。

| | 単一サーバ | 複数サーバ |
| --- | --- | --- |
| **軽量** | [lite/single](lite/single/README.md) | [lite/multi](lite/multi/README.md) |
| **重工** | [heavy/single](heavy/single/README.md) | [heavy/multi](heavy/multi/README.md) |
| **ローカル** | [local/single](local/single/README.md) | [local/multi](local/multi/README.md) |

言語切替・初動・計測・配布・プロンプトは [common/](common/README.md)。

## どれを開くか

- **軽量** — AWS で立てて、計測サイクルをすぐ回す。当日の 3 人タイムラインや終盤チェックは書かない。
- **重工** — 競技当日の手順をフルで練習する。既存の番号付き Markdown はここ。
- **ローカル** — AWS を使わず、AMI / EC2 相当を手元で再現する。公式の docker-compose（アプリ開発用）ではない。手順は未執筆。

Terraform の `webapp_instance_count` 既定は 3。1 台で立てるときは手順側で `1` を書く。
