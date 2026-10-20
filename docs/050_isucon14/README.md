# ISUCon 14（ISURIDE）の練習

ISUCon 14（2024年12月開催）の出題アプリ **ISURIDE** を、AWS 上で動かすためのチュートリアルです。private-isu とは別のアプリなので、このフォルダは独立しています。[../040_practice/](../040_practice) の private-isu 用の手順を、そのまま流用することはできません。

## 流れ

**単一インスタンスで「SQL → アプリ → インフラ」をやりきってから、3 台に分割します。**分割してから直すのでは、どの台の何が効いたか分からなくなるためです。分割したあとに、分割前提でもう一度やりきります。

| 段 | やること | 章 | 状態 |
| --- | --- | --- | --- |
| 0 | 単一インスタンスを作る（Terraform） | [0010](valid/0010-env.md) | 済 |
| 0 | バックアップを作る（`base` / `s1`） | [0015](valid/0015-backup.md) | 済 |
| 0 | TLS 証明書を差し替える | [0020](valid/0020-https.md) | 済 |
| 0 | Python 実装を起動する | [0030](valid/0030-python.md) | 済 |
| 0 | ベンチマーカーを回す（ベースライン） | [0040](valid/0040-bench.md) | 済 |
| 1 | 計測の型を作る（alp / slow log / py-spy） | [0050](valid/0050-measure.md) | 済 |
| 1 | 計測をやり直す（`performance_schema`・1 リクエストあたりの量） | [0055](valid/0055-measure-digest.md) | 済 |
| 2 | **SQL をやりきる**: 索引で SQL を短くする | [0060](valid/0060-sql-index.md) | 済 |
| 2 | SQL: 集計をやめて**列で持つ** | [0070](valid/0070-sql-columns.md) | 済 |
| 2 | SQL: N+1 を潰す | [0080](valid/0080-sql-n1.md) | 済 |
| 3 | **アプリをやりきる**: 通知（往復と COMMIT を減らす） | [0090](valid/0090-app-notification.md) | 済 |
| 3 | アプリ: 決済の冪等化（`Idempotency-Key`） | [0100](valid/0100-app-payment.md) | 済 |
| 3 | アプリ: マッチング（**いちばん効く**） | [0110](valid/0110-app-matching.md) | 済 |
| 3 | アプリ: **キャッシュ構造**（認証・椅子の状態） | [0120](valid/0120-app-cache.md) | 済 |
| 4-1 | **インフラ**（分割しても効くもの: gunicorn / nginx / ログ / FD） | [0130](valid/0130-infra.md) | 済 |
| 4-2 | **単一前提でもう一度**: MySQL の書き込み設定 | [0140](valid/0140-mysql-write.md) | 済 |
| 4-2 | ミドルウェアとライブラリ（接続プール・タイムアウト） | [0150](valid/0150-mw-lib.md) | 済 |
| 5 | **3 台に分割する** | [0160](valid/0160-split.md) | 済 |
| 6 | 分割前提のインフラ（**MySQL を専用台へ**） | [0170](valid/0170-mysql-host.md) | 済 |
| 7 | 分割前提の SQL（**接続数 + 残った走査**） | [0180](valid/0180-split-sql.md) | 済 |
| 8 | 分割前提のアプリ | [0190](valid/0190-split-app.md) | 済 |
| 9 | タイムアウト（ベンチ・セクション・DB 接続） | [0200](valid/0200-timeout.md) | 済 |
| — | 結果の読み方と後片付け | [0210](valid/0210-result.md) | 済 |
| — | 追加検証（0210 の残り） | [0220](invalid/0220-more.md) | 済 |
| — | **スコープ外**: スケールアップ（台・DB のスペック） | [0230](invalid/0230-scale-up.md) | 参考 |

?> **`▶ これから` の章は、単一インスタンスで実測しながら書いています。**前の版（3 台構成で測ったもの）は内容が古いので、いったん外してあります。1 手ずつ測り直して、その数字で書き直します。

## 進め方

- **1 手ずつ**入れます。1 手入れたらベンチを 1 本回し、`~/bench-notes/scores.txt` に 1 行足します
- **`pass=false` のスコアは比べません。**直し終わって `pass=true` に戻してから見ます
- **スコアは同じ構成でも大きくぶれます。**1 本で判断せず、同じ構成を複数本回して幅を見ます。判断の根拠には、`performance_schema` の回数・行数や `Innodb_data_fsyncs` のような**構造の数字**を使います
- **効かなかった手は戻します。**戻した記録も各章に残します

## このフォルダの前提

- 公式リポジトリ [isucon/isucon14](https://github.com/isucon/isucon14) が正本です。手順に書くパス・変数名・ポート番号はそこから確認しています
- 実装は Python（FastAPI + gunicorn）だけを使います。他の言語の実装は AMI に入っていますが、止めずに残します
- ベンチマーカー（`bench`）は別のインスタンスで走らせます。対象のサーバと同じ CPU を使わせないためです
- 環境は [0010-env.md](valid/0010-env.md) の Terraform で作ります。台数は `app_instance_count` で変えます
- ログの置き場は `~/bench-notes/scores.txt` です。[../040_practice/](../040_practice) と同じ場所に揃えています
- 残すと決めた変更は、リポジトリの `backup/isucon14/`（`base` / `s1` / `s2` / `s3`）に積みます。[0015-backup.md](valid/0015-backup.md) と [../040_practice/0110-split-1.md](../040_practice/0110-split-1.md) §3 が同じ流儀です

## スコアの式を先に覚える

ISUCon 14 のスコアは次の 3 項目の合計です（[manual.md の「スコアの計算」](https://github.com/isucon/isucon14/blob/main/docs/manual.md)）。

```text
スコア = 椅子がライドとマッチした位置から乗車位置までの移動距離の合計 * 0.1
       + 椅子の乗車位置から目的地までの移動距離の合計
       + ライド完了数 * 5
```

private-isu の「1 リクエストを速くする」問題とは考え方が違います。**できるだけ多くのライドを完了させ、どの椅子と組ませるかが問われます。** リクエストの処理時間よりも、マッチングの待ち時間と椅子の置き場所が点数を左右します。この比重を知らずに private-isu のドリルをそのまま当てはめると、点数は上がりません。

## 0210 のあとに採用した手

[valid/0215-applied.md](valid/0215-applied.md) — 決済 → 書き込みの順序、`httptools` + `uvloop`、worker 8、owner の N+1。

## 採用しなかった手・スコープ外（invalid/）

| ファイル | 中身 |
| --- | --- |
| [invalid/0220-more.md](invalid/0220-more.md) | DB 台にアプリを置くと負ける、決済を外に出すだけでは壊れる、worker 比較 |
| [invalid/0230-scale-up.md](invalid/0230-scale-up.md) | スコープ外のスケールアップ（削除済み）、C ドライバ、pydantic 再検証 |

?> **`invalid/` はチュートリアル本体ではありません。**測って**採用しなかった**手と**スコープ外**の記録です（採用した部分だけ `valid/` に戻してあります）。
