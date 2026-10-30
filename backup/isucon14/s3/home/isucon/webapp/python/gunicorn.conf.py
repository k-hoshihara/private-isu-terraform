wsgi_app = "app.main:app"
bind = "127.0.0.1:8080"
workers = 8
worker_class = "uvicorn.workers.UvicornWorker"
accesslog = None  # 0130: リクエストごとの journald 書き込みを止める
