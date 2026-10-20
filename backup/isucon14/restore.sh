#!/bin/bash
# backup/isucon14/<名前>/ の内容を、この台に戻します。
#
#   使い方（この台で）:
#     sudo /home/isucon/private-isu-terraform/backup/isucon14/restore.sh s1
#
# 戻したあとは、触ったサービスを再起動します（最後にコマンドを表示します）。
set -euo pipefail

name=${1:?使い方: restore.sh <s1|s2|s3>}
script_dir=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$script_dir/../.." && pwd)
src="$repo/backup/isucon14/$name"

[ -d "$src" ] || { echo "ありません: $src" >&2; exit 1; }

files=(
  /home/isucon/env.sh
  /home/isucon/webapp/python/gunicorn.conf.py
  /etc/nginx/sites-available/isuride.conf
  /etc/nginx/conf.d/isucon14-ltsv.conf
  /etc/nginx/conf.d/upstream.conf
  /etc/systemd/system/isuride-python.service
  /etc/systemd/system/isuride-matcher.service
  /etc/systemd/system/isuride-go.service
  /etc/systemd/system/isuride-payment_mock.service
  /etc/mysql/mysql.conf.d/mysqld.cnf
)
dirs=(
  /home/isucon/webapp/python/app
  /home/isucon/webapp/sql
)

for f in "${files[@]}"; do
  [ -f "$src$f" ] || continue
  mkdir -p "$(dirname "$f")"
  cp -a "$src$f" "$f"
done
for d in "${dirs[@]}"; do
  [ -d "$src$d" ] || continue
  mkdir -p "$d"
  cp -a "$src$d/." "$d/"
done

if id -u isucon >/dev/null 2>&1; then
  chown -R isucon:isucon /home/isucon/webapp/python/app /home/isucon/webapp/sql 2>/dev/null || true
  chown isucon:isucon /home/isucon/env.sh /home/isucon/webapp/python/gunicorn.conf.py 2>/dev/null || true
fi

echo "restored $name from $src"
echo "反映するには再起動します:"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl restart isuride-python isuride-matcher nginx mysql"
