#!/bin/bash
# この台の「残すファイル」を、リポジトリの backup/isucon14/<名前>/ に写します。
#
#   使い方（この台で）:
#     sudo /home/isucon/private-isu-terraform/backup/isucon14/snapshot.sh s1
#
# 写したあとは、リポジトリで git add / git commit / git push します。
# 別の台に入れるときは restore.sh を使います。
#
# 写す対象は「自分で書き換えるもの」だけです（allowlist）。
# /etc を丸ごと持っていかないので、shadow やホスト鍵は入りません。
set -euo pipefail

name=${1:?使い方: snapshot.sh <s1|s2|s3>}
script_dir=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$script_dir/../.." && pwd)
dst="$repo/backup/isucon14/$name"

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

mkdir -p "$dst"
for f in "${files[@]}"; do
  [ -e "$f" ] || continue
  mkdir -p "$dst$(dirname "$f")"
  cp -a "$f" "$dst$f"
done
for d in "${dirs[@]}"; do
  [ -d "$d" ] || continue
  mkdir -p "$dst$d"
  cp -a "$d/." "$dst$d/"
done

# ビルドで作られるものは入りません
find "$dst" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$dst" -name '*.pyc' -delete

if id -u isucon >/dev/null 2>&1; then
  chown -R isucon:isucon "$dst"
fi

echo "wrote $dst"
find "$dst" -type f | sort | sed "s|^$repo/||"
