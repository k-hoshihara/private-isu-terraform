# 0215 0210 のあとに採用した手

[0210](0210-result.md) のあとで測り、**採用した**手だけをまとめます。**捨てた手**と**スコープ外の検証**は [`../invalid/`](../invalid/) に記録しています（採用しなかった理由つき）。

| 手 | 実測 | 記録 |
| --- | --- | --- |
| **決済 → 書き込みの順序**（0.5 秒のロックを外す） | 35,487 → **41,913 / 40,062** | [invalid/0230](../invalid/0230-scale-up.md) §5-2 |
| **`httptools` + `uvloop`**（uvicorn の C 実装） | 中央値 39,100 → **44,200**（最高 45,561） | [invalid/0230](../invalid/0230-scale-up.md) §5-3 |
| **gunicorn worker 8**（4 vCPU に合わせて 2 倍） | 4 が最良 → 8（コア数 × 2） | [invalid/0220](../invalid/0220-more.md) 2-5 |
| **`owner_get_sales` の N+1 を 1 クエリに** | digest から 1 行読みが消えた | [invalid/0220](../invalid/0220-more.md) 2-4 |

## 1. 決済 → 書き込みの順序

評価は決済（約 0.5 秒）を **DB のトランザクションの外**に出し、そのあとで `evaluation`・`COMPLETED`・椅子の成績を**短いトランザクション**で書きます。

- 「完了しているなら必ず決済済み」は、**決済が終わってから書く**ことで保たれます
- 決済後の書き込みが失敗しても、`Idempotency-Key`（[0100](0100-app-payment.md)）で同じ決済に戻れるので再試行で直ります

## 2. httptools + uvloop

```bash
# アプリの台で
uv pip install --python ~/webapp/python/.venv/bin/python httptools uvloop
sudo systemctl restart isuride-python.service
```

`pyproject.toml` にも追記済みです。**入れるだけで uvicorn が自動で使います**（設定不要）。

## 3. worker はコア数に合わせる

2 vCPU で 4、4 vCPU で 8。**多いほど良くはありません**（6 や 8 を 2 vCPU に載せると CPU の取り合いでスコアが振れます）。

## 4. 現在の構成（制約: 3 台 + ベンチ 1 台）

| 台 | 役割 |
| --- | --- |
| app-1 | nginx + アプリ（8 worker） |
| app-2 | アプリ（8 worker） |
| app-3 | MySQL 専用 |

**最高 45,561**（AMI 直後は 607）。
