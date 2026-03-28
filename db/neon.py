"""
db/neon.py — Connection pooling com PostgreSQL Neon (ThreadedConnectionPool)

Gerencia um pool de conexões reutilizáveis para evitar:
- Overhead de criar nova conexão TCP a cada requisição
- Atingir limite de conexões simultâneas do Neon
- Timeouts por esgotamento de conexões

Configuração:
- minconn=2: Número mínimo de conexões mantidas abertas
- maxconn=10: Número máximo de conexões (ajustar conforme plano Neon)
- DSN: Obtido de NEON_URL
"""

import os
import psycopg2
from psycopg2 import pool

_pool = None
POOL_MIN_CONN = int(os.getenv("DB_POOL_MIN_CONN", "2"))
POOL_MAX_CONN = int(os.getenv("DB_POOL_MAX_CONN", "10"))


def get_pool():
    """
    Obtém ou cria o pool de conexões ThreadedConnectionPool.
    
    O pool é lazy-initialized na primeira chamada.
    Thread-safe para uso em contexto multi-threaded (FastAPI).
    """
    global _pool
    
    if _pool is None:
        neon_url = os.getenv("NEON_URL")
        if not neon_url:
            raise ValueError("NEON_URL não configurada no .env")
        
        try:
            _pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=POOL_MIN_CONN,
                maxconn=POOL_MAX_CONN,
                dsn=neon_url
            )
            print(f"[DB] Pool criado: {POOL_MIN_CONN} min, {POOL_MAX_CONN} max conexões")
        except Exception as e:
            print(f"[DB] Erro ao criar pool: {e}")
            raise
    
    return _pool


def get_db_connection():
    """
    Obtém uma conexão do pool.
    
    Returns:
        psycopg2.extensions.connection: Conexão reutilizável do pool
    
    Raises:
        pool.PoolError: Se todas as conexões estão em uso (exceção rara)
    """
    try:
        conn = get_pool().getconn()
        # Validar conexão antes de retornar
        conn.isolation_level  # Trigger connection test
        return conn
    except pool.PoolError:
        print(f"[DB] Pool cheio: todas as {POOL_MAX_CONN} conexões em uso")
        raise


def release_connection(conn):
    """
    Retorna uma conexão ao pool para reutilização.
    
    CRÍTICO: Sempre chamar em finally block para garantir liberação.
    
    Args:
        conn: Conexão a liberar (ou None)
    """
    if conn:
        try:
            get_pool().putconn(conn)
        except Exception as e:
            print(f"[DB] Erro ao liberar conexão: {e}")
            # Fechar conexão quebrada
            try:
                conn.close()
            except:
                pass


def close_pool():
    """
    Fecha todas as conexões do pool (chamado no shutdown da app).
    
    Garante:
    - Cleanup adequado de recursos
    - Desconexão apropriada do Neon
    """
    global _pool
    
    if _pool is not None:
        try:
            _pool.closeall()
            print("[DB] Pool fechado, todas as conexões desconectadas")
        except Exception as e:
            print(f"[DB] Erro ao fechar pool: {e}")
        finally:
            _pool = None


def get_pool_status():
    """
    Retorna status do pool (para monitoramento).
    
    Returns:
        dict: {"available": n, "closed": bool, "max": POOL_MAX_CONN}
    """
    if _pool is None:
        return {"status": "not_initialized"}
    
    try:
        return {
            "available": _pool._available.__len__(),
            "in_use": POOL_MAX_CONN - _pool._available.__len__(),
            "max": POOL_MAX_CONN,
            "closed": _pool.closed
        }
    except:
        return {"status": "error"}

