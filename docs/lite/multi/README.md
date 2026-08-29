# 軽量・複数

3 台を立てて役割を分け、nginx 役へベンチを向ける。

飛ばす: 当日 3 人タイムライン、終盤チェック。5 台の話は後回し。

## 読む順

1. 構築: [heavy/multi/00100-env.md](../../heavy/multi/00100-env.md)（既定 3 台）
2. 各台で Python: [webapp-setup/python.md](../../common/webapp-setup/python.md)
3. 役割分割: [00500-multi-server.md](../../heavy/multi/00500-multi-server.md) の 1〜5 と 8（ヘルスと公式ベンチ）
4. 計測: [00300-measure.md](../../common/00300-measure.md)（ログは nginx 役と DB 役）

起動順・再起動試験・永続化の確認は [重工・複数](../../heavy/multi/README.md)。
