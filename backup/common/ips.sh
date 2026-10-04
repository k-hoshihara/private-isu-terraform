# s1 のプライベート IP（静的の rsync 用。後で使う）
WEB_PRIV='10.42.0.21'
# s2 のプライベート IP（重いアプリ、HTTP）
APP_PRIV='10.42.0.181'
# 共有 memcached。既定は s2 なので APP_PRIV と同じ
MC_PRIV='10.42.0.181'
# s3 のプライベート IP
DB_PRIV='10.42.0.168'
SOCK='/home/isucon/tmp/gunicorn.sock'
SITE='/etc/nginx/sites-enabled/isucon.conf'
