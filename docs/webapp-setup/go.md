# Go 実装に切り替える

起動直後は Ruby の参考実装が動作しています。  
同時に起動できる実装は1つのため、Go を使うには Ruby を停止します。

## 切り替える

```bash
sudo systemctl stop isu-ruby
sudo systemctl disable isu-ruby

sudo systemctl start isu-go
sudo systemctl enable isu-go
```

`disable` と `enable` は、インスタンスを再起動したときにどちらが立ち上がるかを決めます。  
これを忘れると、停止・起動のたびに Ruby へ戻ります。

## 動作を確認する

```bash
systemctl status isu-go
```

ログは以下で追跡できます。

```bash
sudo journalctl -f -u isu-go
```

起動方法の詳細は `/etc/systemd/system/isu-go.service` に記述されています。  
アプリケーションは `127.0.0.1:8080` を listen し、nginx がその前段に立ちます。

## ソースを変更したらビルドする

Go はコンパイル言語のため、`app.go` を編集しただけでは動作に反映されません。  
`systemctl restart` の前にビルドします。

```bash
sudo su - isucon
cd /home/isucon/private_isu/webapp/golang
go build -o app
sudo systemctl restart isu-go
```

`sudo su - isucon` が必要な理由は、アプリケーションのファイルが `isucon` ユーザーの所有だからです。  
SSM Session Manager でログインした直後は `ssm-user` のため、そのままビルドすると書き込みに失敗します。

`Makefile` が用意されているので、`go build -o app` の代わりに `make` でも同じ結果になります。

private-isu のデフォルトは Ruby のため、言語を切り替えた直後はビルドを省略しやすい箇所です。  
変更が反映されない場合は、ビルドを実行したか確認してください。
