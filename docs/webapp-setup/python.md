# Python 実装に切り替える

起動直後は Ruby の参考実装が動作しています。  
同時に起動できる実装は1つのため、Python を使うには Ruby を停止します。

## 切り替える

```bash
sudo systemctl stop isu-ruby
sudo systemctl disable isu-ruby

sudo systemctl start isu-python
sudo systemctl enable isu-python
```

`disable` と `enable` は、インスタンスを再起動したときにどちらが立ち上がるかを決めます。  
これを忘れると、停止・起動のたびに Ruby へ戻ります。

## 動作を確認する

```bash
systemctl status isu-python
```

ログは以下で追跡できます。

```bash
sudo journalctl -f -u isu-python
```

起動方法の詳細は `/etc/systemd/system/isu-python.service` に記述されています。  
Flask アプリケーションを gunicorn が `8080` で起動し、nginx がその前段に立ちます。

## ソースを変更した場合

Python はコンパイルが不要なため、`app.py` を編集したら再起動するだけで反映されます。

```bash
sudo systemctl restart isu-python
```

## 依存を変更した場合

依存管理には uv を使っています。  
`pyproject.toml` を変更したときは、仮想環境を更新してから再起動します。

```bash
sudo su - isucon
cd /home/isucon/private_isu/webapp/python
uv sync
sudo systemctl restart isu-python
```

`sudo su - isucon` が必要な理由は、アプリケーションのファイルが `isucon` ユーザーの所有だからです。  
SSM Session Manager でログインした直後は `ssm-user` のため、そのままでは書き込みに失敗します。

サービスは `.venv/bin/gunicorn` を直接起動しています。  
`uv sync` で `.venv` を更新しない限り、依存の変更は反映されません。
