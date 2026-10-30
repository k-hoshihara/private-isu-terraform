import os

import sqlalchemy

host = os.getenv("ISUCON_DB_HOST", "127.0.0.1")
port = int(os.getenv("ISUCON_DB_PORT", "3306"))
user = os.getenv("ISUCON_DB_USER", "isucon")
password = os.getenv("ISUCON_DB_PASSWORD", "isucon")
dbname = os.getenv("ISUCON_DB_NAME", "isuride")

engine = sqlalchemy.create_engine(
    f"mysql+pymysql://{user}:{password}@{host}:{port}/{dbname}",
    # 0150: worker ごとの接続プール。6 worker x 5 = 30 接続まで（max_connections = 151 に対して余裕）
    pool_size=5,
    max_overflow=5,
    pool_timeout=10,
    pool_recycle=300,
    # 0150: 接続の生存確認クエリは打たない（1 リクエストあたり 1 往復ぶん増える）
    pool_pre_ping=False,
    # 0150: DB が詰まってもリクエストが無限に待たないようにする
    connect_args={
        "connect_timeout": 5,
        "read_timeout": 30,
        "write_timeout": 30,
    },
)
