# 0140 残りを削る（日時・圧縮・静的・ベンチマーカー）

?> リポジトリを触る操作（`git pull` / 編集 / `git push`）は、手元の private-isu-terraform リポジトリで実行します。

- [0130-response-build.md](0130-response-build.md) で主要な部分を詰めたあとの、**残った固定の手間と転送**を 1 つずつ削ります。
- 効果が小さいものも多く、「小さかった」という結果をどう扱うかが、主題です。
- 出口は [0150-scale.md](0150-scale.md)（スペックと構成を変える）です。

- この章に出てくる手は、**ベンチマーカー（負荷をかけて点数を出すプログラム）をどの台で回すかで効果が変わります**。
- 今のベンチは、s1 の `localhost` 向きです。転送のコストが 0 です。
- 圧縮を入れても、CPU だけを使います。
- 効果が出なさそうな手は、[0150-scale.md](0150-scale.md) でベンチマーカーを別機に分けたあとに測り直します。
- `fail`（エラーの件数）が 0 でない点数は、比べません。

- 順番: 残りから選ぶ（1.）→ 日時変換を避ける（2.）→ gzip の対象を広げる（3.）→ 静的の `expires` を残りパスへ（4.）→ ベンチマーカーの FD を見る（5.）→ 公式ベンチで判定する（6.）。
- ベンチは [0110-split-4.md](0110-split-4.md) と同じです（s1 で bench-prep → `localhost` へ）。

```bash
mkdir -p ~/bench-notes
echo "$(date -Iseconds)  score=  pass=  fail=  note=cut-baseline" >> ~/bench-notes/scores.txt
```

<a id="1-残りから選ぶ"></a>

## 1. 残りから選ぶ

- 計測は、[0130 §1](0130-response-build.md#1-内訳を取る) と同じ py-spy（処理中のどこが重いかを記録するツール）です。
- 前回の内訳で残っていたのは、次の 3 つでした。

| 残り | 前回の観測 | 直し方 |
| --- | --- | --- |
| 日時の変換 | `DateTime_or_None` がリーフ上位 | SQL 側で文字列にして受け取る（[§2](#2-日時変換を避ける)） |
| 転送 | gzip は `text/html` だけ | 対象を広げる（[§3](#3-gzip-の対象を広げる)） |
| 静的 | `expires` は `css` だけ | 残りのパスへ（[§4](#4-静的の-expires-を残りパスへ)） |

### 観察すること

- 1 つずつです。効果が小さければ「小さかった」と書いて、残すか戻すかを決めます
- **転送に関わる手は、ベンチマーカーの置き場で効果が変わります**（[§5](#5-ベンチマーカーの-fd-を見る) と [0150-scale.md](0150-scale.md)）

<a id="2-日時変換を避ける"></a>

## 2. 日時変換を避ける

- MySQLdb（DB に接続する部品）は、`DATETIME`（日時の列）を Python の `datetime` に変換して返します。
- 表示は `str()` と同じ形です。
- **SQL 側で文字列として受け取れば**、この変換が消えます。

```python
# get_index() / get_posts() の SELECT の一部（1 手目）
"CAST(p.`created_at` AS CHAR) AS `created_at` "
```

- s1 と s2 に反映して、restart します。
- 一覧の `data-created-at` が `YYYY-MM-DD HH:MM:SS` のままであることを確認してから、ベンチします。

```bash
# s1 で打つ
curl -fsS -m 5 http://127.0.0.1/ | grep -m1 -o 'data-created-at="[^"]*"'
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=cut-datetime" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13、`c7a.large` 3 台 / `ap-northeast-1` / `nproc=2` / メモリ 3.7Gi）: `{"pass":true,"score":182100,"success":165428,"fail":0,"messages":[]}`（0130 最終の 181103 から +997。**効果は小さめ**でした）。

?> **`DATE_FORMAT(created_at, '%Y-%m-%d ...')` は、MySQLdb で壊れます。** `%` を値の埋め込み場所（プレースホルダ）として解釈し、SQL エラーになります。`GET /posts` が 500 になります（`fail=302` が出ました）。`CAST(... AS CHAR)` なら、書式が同じで `%` を含みません。

### 観察すること

- 表示の日時の形が変わっていないこと（`data-created-at` / `datetime` 属性）。変わると `fail` になります
- 効果が小さくても、消えた計算を記録します

<a id="3-gzip-の対象を広げる"></a>

## 3. gzip の対象を広げる

- AMI 既定の nginx は、`gzip on`（転送する量を減らす圧縮）です。ただし `gzip_types`（圧縮するファイルの種類）が無く、**`text/html` だけ**が圧縮されています。
- css / js / json を足します。

```nginx
# /etc/nginx/conf.d/gzip-types.conf
gzip_vary on;
gzip_proxied any;
gzip_comp_level 5;
gzip_min_length 256;
gzip_types text/plain text/css application/json application/javascript text/xml application/xml text/javascript;
```

- 反映は、`nginx -t` → reload です。
- **HEAD では `Content-Encoding`（圧縮の目印）が出ないことがあります。GET で確認します。**

```bash
# s1 で打つ
curl -s -H 'Accept-Encoding: gzip' -D - -o /dev/null http://127.0.0.1/css/style.css \
  | grep -i 'content-encoding\|vary'
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=cut-gzip-types" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）: `score=176796` → 再計測 `score=174935`（ともに `fail=0`）。0130 最終の 181103 から **-5000 前後**でした。

- **点数が下がった理由**は、読み取れます。
- ベンチマーカーは、s1 の `localhost` を叩いています。**転送が同じ台の中（0 コスト）**です。
- 圧縮は転送量を節約できず、CPU だけを使います。
- 効果を正しく測るには、ベンチマーカーを別機にします。[0150-scale.md §2](0150-scale.md#2-gzip-を測り直す) で測り直します。

### 観察すること

- `Content-Encoding: gzip` と `Vary: Accept-Encoding` が出ること（GET で）
- **転送が無料の環境では、圧縮はマイナスになり得る**ことを言えます

<a id="4-静的の-expires-を残りパスへ"></a>

## 4. 静的の `expires` を残りパスへ

- [0110-split-4.md §3-c](0110-split-4.md#3-転送とホップを減らす) で `css` に付けた `expires`（「どれだけの時間保存してよい」という指定）を、残りの静的ファイルにも広げます。
- 1 箇所ずつではなくまとめて 1 つとして扱う場合は、前後のベンチで判定します。

```nginx
  location /js/ { expires 1d; }
  location /img/ { expires 1d; }
  location = /favicon.ico { expires 1d; }
```

```bash
# s1 で打つ（反映 → ベンチ）
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=cut-expires-rest" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）: `{"pass":true,"score":177270,"success":161093,"fail":0,"messages":[]}`（174935 から +2335）。**ベンチマーカーはブラウザの記憶（キャッシュ）を再現しないため、点数は動きません**（[0030-cache.md](0030-cache.md) と同じ）。実際のブラウザのための手として残します。

### 観察すること

- 点数が動かない手もあること。動かない理由を言えます（ベンチマーカーが覚えない）

<a id="5-ベンチマーカーの-fd-を見る"></a>

## 5. ベンチマーカーの FD を見る（新規に上げない）

- `too many open files` が出ている環境では、ベンチマーカー側の FD（同時に開けるファイルや接続の数）の上限が、点数の頭打ちになります（[0070-infra-params.md §6](0070-infra-params.md#6-ファイルディスクリプタの上限数足りないときだけ効く) の考え方と同じです）。
- 見るだけです。

```bash
# s1 で打つ
sudo -iu isucon bash -c 'ulimit -n; ulimit -Hn'
grep -iE 'nofile' /etc/security/limits.d/*.conf /etc/security/limits.conf 2>/dev/null
sudo journalctl --since '-30min' | grep -ci 'too many open files'
```

実測サンプル（2026-10-13）: `soft/hard = 65535`（`/etc/security/limits.d/99-isucon.conf` で設定済み）、`too many open files` は **0 件**。上げる余地が無いため変更しません（[0080 §3](0080-app-tuning.md#3-ファイルディスクリプタの上限は確認だけ新規に上げない) と同じ「見るだけ」の節です）。

### 観察すること

- 上げるのは、足りなくなっていることが分かってからです。前持って上げません

<a id="6-公式ベンチで判定する"></a>

## 6. 公式ベンチで判定する

- 残す変更を、全部載せた状態にします。公式ベンチを 1 本回します。

```bash
# s1 で打つ
sudo -iu isucon /home/isucon/private_isu/benchmarker/bin/benchmarker \
  -u /home/isucon/private_isu/benchmarker/userdata \
  -t http://localhost
echo "$(date -Iseconds)  score=  pass=  fail=  note=cut-final" >> ~/bench-notes/scores.txt
```

実測サンプル（2026-10-13）: `{"pass":true,"score":178760,"success":162553,"fail":0,"messages":[]}`。

| 回 | score | fail | 備考 |
| --- | --- | --- | --- |
| 0130 §6 最終（基準） | 181103 | 0 | |
| §2 日時変換を SQL 側へ | 182100 | 0 | +997（小さめ） |
| §3 gzip の対象拡張 | 176796 / 174935 | 0 | **-5000 前後**（localhost ベンチでは圧縮が逆効果） |
| §4 `expires` を残りパスへ | 177270 | 0 | +2335（覚えないベンチでは動きにくい） |
| §5 ベンチマーカー FD | — | — | 見るだけ（既に 65535） |
| **§6 最終** | **178760** | **0** | gzip を残した状態 |

- gzip を外すと、319353（[0150-scale.md §2](0150-scale.md#2-gzip-を測り直す) で別機に分けたあとの測定）まで伸びる余地があります。
- **この章の締めは、「localhost ベンチでの最終」**とします。構成を変えた測り直しは、[0150-scale.md](0150-scale.md) に回します。

### 観察すること

- ここ 3 章の点数の動きが「効く手」と「効かない手」に分かれていること。効かない手にも理由があります
- `fail` が 0 です

### この節でつまずいた点

?> **この表の問題は、すべて本文へ反映済みです。**「直し方」列のとおりに直してあります。同じ症状が出たら、本文の手順どおりに直して再計測します。

| # | 箇所 | 問題 | 修正内容 |
| --- | --- | --- | --- |
| 1 | §2 | `DATE_FORMAT` の `%` が MySQLdb のプレースホルダと衝突し、`GET /posts` が 500（`fail=302`） | `CAST(... AS CHAR)` に変えました（書式は同じ） |
| 2 | §3 | gzip を入れて点数が下がった（-5000） | localhost ベンチでは転送が 0 コストと判明。0150 の分離後に測り直す形にしました |
| 3 | §3 | HEAD で `Content-Encoding` を確認しようとして「出ていない」と誤認 | GET で確認します（HEAD では出ないことがあります） |

## 7. トラブルシュート

### `GET /posts` が 500 になる（日時を SQL 側で取ったあと）

- SQL 文字列に `%` が残っていませんか（`DATE_FORMAT` など）。MySQLdb は `%s` を値の埋め込み場所として解釈します
- 表示の形が変わっていたら `CAST(... AS CHAR)` の書式を見ます（`YYYY-MM-DD HH:MM:SS` が素体と同じ形です）

### 圧縮を入れて遅くなった

- ベンチマーカーが同じ台の `localhost` 向きなら転送は無料です。圧縮は CPU だけを使います
- 効果を測るときは、ベンチマーカーを別機にします（[0150-scale.md](0150-scale.md)）

### 点数が動かない

- 動かないことも結果です。理由を書いて、残すか戻すかを選びます
- 次に見る場所は py-spy の上位か、構成（どの台が余っているか）です。[0150-scale.md](0150-scale.md) に進みます
