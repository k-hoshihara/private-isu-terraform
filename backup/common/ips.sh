# s1 のプライベート IP（静的の rsync 用。後で使う）
WEB_PRIV='10.42.0.90'
# s2 のプライベート IP（重いアプリ、HTTP）
APP_PRIV='10.42.0.171'
# 共有 memcached。既定は s2 なので APP_PRIV と同じ
MC_PRIV='10.42.0.171'
# s3 のプライベート IP
DB_PRIV='10.42.0.186'
SOCK='/home/isucon/tmp/gunicorn.sock'
SITE='/etc/nginx/sites-enabled/isucon.conf'
