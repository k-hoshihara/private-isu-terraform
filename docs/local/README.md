# ローカル版

AWS の EC2 / 公開 AMI 相当を、手元で再現する。課金せずに手順を回すため。

**使わないもの:** [catatsuy/private-isu](https://github.com/catatsuy/private-isu) の docker-compose。あれはアプリ開発用で、競技 AMI（systemd、nginx、mysqld、公式ベンチマーカー同梱）ではない。

| 台数 | 入口 |
| --- | --- |
| 1 台 | [single](single/README.md) |
| 複数台 | [multi](multi/README.md) |

具体手順は未執筆。候補だけ先に置く。

- [matsuu/cloud-init-isucon](https://github.com/matsuu/cloud-init-isucon/tree/main/private-isu) — cloud-init で AMI に近い構成
- 公開 AMI を QEMU / 手元のハイパーバイザで起動する
- LXD など、systemd が動く VM に同じミドルウェアを入れる
