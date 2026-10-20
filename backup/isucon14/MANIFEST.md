# backup/isucon14 の中身

各ディレクトリは `snapshot.sh` と同じ形（`<名前>/<絶対パス>`）で、
`restore.sh <名前>` がそのまま使えます。

- `base` … 0010 の直後（AMI のまま）。正本は各台の `*.orig` / `*.pre-split` と未変更ファイル
- `s1` … `app-1` のいまの状態（この教材で残した変更が全部入っている）
- `s2` … `app-2` のいまの状態
- `s3` … `app-3` のいまの状態

中身は `snapshot.sh` の対象パスに、アプリのソース一式
（`pyproject.toml` / `uv.lock` / `noxfile.py` / `.python-version` / `.gitignore` /
`.pre-commit-config.yaml`）を足したものです。後者は `restore.sh` の対象パスには
入っていないので、`restore.sh` は触りません（読むための控えです）。

`__pycache__` / `*.pyc` / `.venv` は入れていません。
`app/*.orig` は各台に残っていた控えをそのまま置いています。
検証中に作った使い捨ての控え（`*.pre0080` / `*.pre0090` / `*.broken_step4` /
`*.pre_step4`）だけは外しました。

`home/isucon/webapp/sql/3-initial-data.sql.gz` は中身が 821 KB あります。3 台で md5 `c417f619e44a25b1149e384ea6254ac7` まで一致しています。

## ファイルと出どころ

### base（30 files）

| ファイル | 出どころ | md5 |
| --- | --- | --- |
| `base/etc/mysql/mysql.conf.d/mysqld.cnf` | app1 mysqld.cnf.orig | `d017734c688c` |
| `base/etc/nginx/sites-available/isuride.conf` | app1 isuride.conf.orig | `9e6b18156eff` |
| `base/etc/systemd/system/isuride-go.service` | app3 live (未変更) | `a8804b0c5660` |
| `base/etc/systemd/system/isuride-matcher.service` | app3 live (未変更) | `a6efa91cce29` |
| `base/etc/systemd/system/isuride-payment_mock.service` | app3 live (未変更) | `64a1e8c88d07` |
| `base/etc/systemd/system/isuride-python.service` | app3 live (未変更) | `3d29f8f1d81a` |
| `base/home/isucon/env.sh` | app1 env.sh.orig | `7cf13656bc4e` |
| `base/home/isucon/webapp/python/.gitignore` | app3 live (未変更) | `e5e81fdb76c0` |
| `base/home/isucon/webapp/python/.pre-commit-config.yaml` | app3 live (未変更) | `6f5918e7f07b` |
| `base/home/isucon/webapp/python/.python-version` | app3 live (未変更) | `e746a954a026` |
| `base/home/isucon/webapp/python/app/__init__.py` | app3 live (未変更) | `d41d8cd98f00` |
| `base/home/isucon/webapp/python/app/app_handlers.py` | app1 app_handlers.py.orig | `7a61b53779d2` |
| `base/home/isucon/webapp/python/app/chair_handlers.py` | 公式リポジトリと一致を確認 | `7e29e50a8a0c` |
| `base/home/isucon/webapp/python/app/internal_handlers.py` | app1 internal_handlers.py.orig | `2904f88c7cc8` |
| `base/home/isucon/webapp/python/app/main.py` | app3 live (未変更) | `1ac7e44f8d0b` |
| `base/home/isucon/webapp/python/app/middlewares.py` | 公式リポジトリと一致を確認 | `7edf19a87735` |
| `base/home/isucon/webapp/python/app/models.py` | app3 live (未変更) | `63144e481ace` |
| `base/home/isucon/webapp/python/app/owner_handlers.py` | 公式リポジトリと一致を確認 | `e4f479b12bd2` |
| `base/home/isucon/webapp/python/app/payment_gateway.py` | app1 payment_gateway.py.orig | `b495521d9b0e` |
| `base/home/isucon/webapp/python/app/sql.py` | app3 live (未変更) | `0ad6c299230c` |
| `base/home/isucon/webapp/python/app/utils.py` | app3 live (未変更) | `5bb86b7c87b2` |
| `base/home/isucon/webapp/python/gunicorn.conf.py` | app2 gunicorn.conf.py.orig | `a48ec7addb51` |
| `base/home/isucon/webapp/python/noxfile.py` | app3 live (未変更) | `120096585b55` |
| `base/home/isucon/webapp/python/pyproject.toml` | app3 live (未変更) | `db64fb1eb385` |
| `base/home/isucon/webapp/python/uv.lock` | app3 live (未変更) | `82b3df41378d` |
| `base/home/isucon/webapp/sql/0-init.sql` | app3 live (未変更) | `3522c35d4563` |
| `base/home/isucon/webapp/sql/1-schema.sql` | app3 live (未変更) | `d4aff74edbfb` |
| `base/home/isucon/webapp/sql/2-master-data.sql` | app3 live (未変更) | `d9db1e982fd9` |
| `base/home/isucon/webapp/sql/init.sh` | app1 init.sh.orig | `09fd86678c3e` |
| `home/isucon/webapp/sql/3-initial-data.sql.gz` | app1/app2/app3 live (未変更) | `c417f619e44a` |

### s1（30 files）

| ファイル | 出どころ | md5 |
| --- | --- | --- |
| `s1/etc/mysql/mysql.conf.d/mysqld.cnf` | live | `8397b0072c98` |
| `s1/etc/nginx/conf.d/isucon14-ltsv.conf` | live | `ccb5a02fe11c` |
| `s1/etc/nginx/sites-available/isuride.conf` | live | `5eb05af1c321` |
| `s1/etc/systemd/system/isuride-go.service` | live | `a8804b0c5660` |
| `s1/etc/systemd/system/isuride-matcher.service` | live | `a6efa91cce29` |
| `s1/etc/systemd/system/isuride-payment_mock.service` | live | `64a1e8c88d07` |
| `s1/etc/systemd/system/isuride-python.service` | live | `3d29f8f1d81a` |
| `s1/home/isucon/env.sh` | live | `92dc49f06867` |
| `s1/home/isucon/webapp/python/.gitignore` | live | `e5e81fdb76c0` |
| `s1/home/isucon/webapp/python/.python-version` | live | `e746a954a026` |
| `s1/home/isucon/webapp/python/app/__init__.py` | live | `d41d8cd98f00` |
| `s1/home/isucon/webapp/python/app/app_handlers.py` | live | `6e602813269f` |
| `s1/home/isucon/webapp/python/app/chair_handlers.py` | live | `99bd8777d6cd` |
| `s1/home/isucon/webapp/python/app/internal_handlers.py` | live | `bb4dbf342689` |
| `s1/home/isucon/webapp/python/app/main.py` | live | `1ac7e44f8d0b` |
| `s1/home/isucon/webapp/python/app/middlewares.py` | live | `776c1263fde2` |
| `s1/home/isucon/webapp/python/app/models.py` | live | `2e77bf6e7663` |
| `s1/home/isucon/webapp/python/app/owner_handlers.py` | live | `857c3de102f0` |
| `s1/home/isucon/webapp/python/app/payment_gateway.py` | live | `9ce4fc234e64` |
| `s1/home/isucon/webapp/python/app/sql.py` | live | `4d4457b41465` |
| `s1/home/isucon/webapp/python/app/utils.py` | live | `5bb86b7c87b2` |
| `s1/home/isucon/webapp/python/gunicorn.conf.py` | live | `afd62eaaa43a` |
| `s1/home/isucon/webapp/python/noxfile.py` | live | `120096585b55` |
| `s1/home/isucon/webapp/python/pyproject.toml` | live | `db64fb1eb385` |
| `s1/home/isucon/webapp/python/uv.lock` | live | `82b3df41378d` |
| `s1/home/isucon/webapp/sql/0-init.sql` | live | `3522c35d4563` |
| `s1/home/isucon/webapp/sql/1-index.sql` | live | `33a08fed99d8` |
| `s1/home/isucon/webapp/sql/1-schema.sql` | live | `d4aff74edbfb` |
| `s1/home/isucon/webapp/sql/2-master-data.sql` | live | `d9db1e982fd9` |
| `s1/home/isucon/webapp/sql/init.sh` | live | `d29f8c839fc4` |

### s2（30 files）

| ファイル | 出どころ | md5 |
| --- | --- | --- |
| `s2/etc/mysql/mysql.conf.d/mysqld.cnf` | live | `950d9fcd3f65` |
| `s2/etc/nginx/conf.d/isucon14-ltsv.conf` | live | `a4459c98e835` |
| `s2/etc/nginx/sites-available/isuride.conf` | live | `6280d4bfd132` |
| `s2/etc/systemd/system/isuride-go.service` | live | `a8804b0c5660` |
| `s2/etc/systemd/system/isuride-matcher.service` | live | `a6efa91cce29` |
| `s2/etc/systemd/system/isuride-payment_mock.service` | live | `64a1e8c88d07` |
| `s2/etc/systemd/system/isuride-python.service` | live | `3d29f8f1d81a` |
| `s2/home/isucon/env.sh` | live | `92dc49f06867` |
| `s2/home/isucon/webapp/python/.gitignore` | live | `e5e81fdb76c0` |
| `s2/home/isucon/webapp/python/.python-version` | live | `e746a954a026` |
| `s2/home/isucon/webapp/python/app/__init__.py` | live | `d41d8cd98f00` |
| `s2/home/isucon/webapp/python/app/app_handlers.py` | live | `6e602813269f` |
| `s2/home/isucon/webapp/python/app/chair_handlers.py` | live | `99bd8777d6cd` |
| `s2/home/isucon/webapp/python/app/internal_handlers.py` | live | `bb4dbf342689` |
| `s2/home/isucon/webapp/python/app/main.py` | live | `1ac7e44f8d0b` |
| `s2/home/isucon/webapp/python/app/middlewares.py` | live | `776c1263fde2` |
| `s2/home/isucon/webapp/python/app/models.py` | live | `2e77bf6e7663` |
| `s2/home/isucon/webapp/python/app/owner_handlers.py` | live | `857c3de102f0` |
| `s2/home/isucon/webapp/python/app/payment_gateway.py` | live | `9ce4fc234e64` |
| `s2/home/isucon/webapp/python/app/sql.py` | live | `4d4457b41465` |
| `s2/home/isucon/webapp/python/app/utils.py` | live | `5bb86b7c87b2` |
| `s2/home/isucon/webapp/python/gunicorn.conf.py` | live | `e5535f7961e9` |
| `s2/home/isucon/webapp/python/noxfile.py` | live | `120096585b55` |
| `s2/home/isucon/webapp/python/pyproject.toml` | live | `db64fb1eb385` |
| `s2/home/isucon/webapp/python/uv.lock` | live | `82b3df41378d` |
| `s2/home/isucon/webapp/sql/0-init.sql` | live | `3522c35d4563` |
| `s2/home/isucon/webapp/sql/1-index.sql` | live | `33a08fed99d8` |
| `s2/home/isucon/webapp/sql/1-schema.sql` | live | `d4aff74edbfb` |
| `s2/home/isucon/webapp/sql/2-master-data.sql` | live | `d9db1e982fd9` |
| `s2/home/isucon/webapp/sql/init.sh` | live | `d29f8c839fc4` |

### s3（30 files）

| ファイル | 出どころ | md5 |
| --- | --- | --- |
| `s3/etc/mysql/mysql.conf.d/mysqld.cnf` | live | `3c3dc5ad0d5e` |
| `s3/etc/nginx/conf.d/isucon14-ltsv.conf` | live | `a4459c98e835` |
| `s3/etc/nginx/sites-available/isuride.conf` | live | `6280d4bfd132` |
| `s3/etc/systemd/system/isuride-go.service` | live | `a8804b0c5660` |
| `s3/etc/systemd/system/isuride-matcher.service` | live | `a6efa91cce29` |
| `s3/etc/systemd/system/isuride-payment_mock.service` | live | `64a1e8c88d07` |
| `s3/etc/systemd/system/isuride-python.service` | live | `3d29f8f1d81a` |
| `s3/home/isucon/env.sh` | live | `73725dfa43dd` |
| `s3/home/isucon/webapp/python/.gitignore` | live | `e5e81fdb76c0` |
| `s3/home/isucon/webapp/python/.python-version` | live | `e746a954a026` |
| `s3/home/isucon/webapp/python/app/__init__.py` | live | `d41d8cd98f00` |
| `s3/home/isucon/webapp/python/app/app_handlers.py` | live | `6e602813269f` |
| `s3/home/isucon/webapp/python/app/chair_handlers.py` | live | `4f5186cc3234` |
| `s3/home/isucon/webapp/python/app/internal_handlers.py` | live | `bb4dbf342689` |
| `s3/home/isucon/webapp/python/app/main.py` | live | `1ac7e44f8d0b` |
| `s3/home/isucon/webapp/python/app/middlewares.py` | live | `776c1263fde2` |
| `s3/home/isucon/webapp/python/app/models.py` | live | `2e77bf6e7663` |
| `s3/home/isucon/webapp/python/app/owner_handlers.py` | live | `857c3de102f0` |
| `s3/home/isucon/webapp/python/app/payment_gateway.py` | live | `79c9d121d9d7` |
| `s3/home/isucon/webapp/python/app/sql.py` | live | `4d4457b41465` |
| `s3/home/isucon/webapp/python/app/utils.py` | live | `5bb86b7c87b2` |
| `s3/home/isucon/webapp/python/gunicorn.conf.py` | live | `e5535f7961e9` |
| `s3/home/isucon/webapp/python/noxfile.py` | live | `120096585b55` |
| `s3/home/isucon/webapp/python/pyproject.toml` | live | `db64fb1eb385` |
| `s3/home/isucon/webapp/python/uv.lock` | live | `82b3df41378d` |
| `s3/home/isucon/webapp/sql/0-init.sql` | live | `3522c35d4563` |
| `s3/home/isucon/webapp/sql/1-index.sql` | live | `33a08fed99d8` |
| `s3/home/isucon/webapp/sql/1-schema.sql` | live | `d4aff74edbfb` |
| `s3/home/isucon/webapp/sql/2-master-data.sql` | live | `d9db1e982fd9` |
| `s3/home/isucon/webapp/sql/init.sh` | live | `d29f8c839fc4` |
