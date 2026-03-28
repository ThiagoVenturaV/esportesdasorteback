"""
Gestão de conexões com o Neon DB (Connection Pooling).
"""
import os
import psycopg2
from psycopg2 import pool
from dotenv import load_dotenv

load_dotenv()

_pool = None

def get_pool():
    global _pool
    if _pool is None:
        try:
            _pool = pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=10,
                dsn=os.getenv("NEON_URL")
            )
        except Exception as e:
            print(f"Erro ao inicializar db pool: {e}")
            raise e
    return _pool

class PooledConnWrapper:
    def __init__(self, conn, pool_ref):
        self._conn = conn
        self._pool_ref = pool_ref

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        if self._conn and self._pool_ref:
            self._pool_ref.putconn(self._conn)

def get_db_connection():
    p = get_pool()
    conn = p.getconn()
    if conn:
        conn.autocommit = False
    return PooledConnWrapper(conn, p)

def release_connection(conn):
    if hasattr(conn, "close"):
        conn.close()
