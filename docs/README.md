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

`terraform/` の `webapp_instance_count` 既定は 3。1 台にするときは手順側で `1` を書く。
