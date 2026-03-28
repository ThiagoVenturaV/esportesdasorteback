"""
db/queries.py — Queries auxiliares
"""

from db.neon import get_db_connection, release_connection


def ensure_edson_context_table():
    """Garante que a tabela tb_edson_context existe."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        sql = """
            CREATE TABLE IF NOT EXISTS tb_edson_context (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER REFERENCES tb_usuario(id_usuario) ON DELETE CASCADE,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                match_id TEXT,
                tokens_used INTEGER DEFAULT 0,
                criado_em TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_edson_ctx_user ON tb_edson_context (usuario_id, criado_em DESC);
            CREATE INDEX IF NOT EXISTS idx_edson_ctx_session ON tb_edson_context (session_id);
            CREATE INDEX IF NOT EXISTS idx_edson_ctx_criado_em ON tb_edson_context (criado_em);
        """
        
        cursor.execute(sql)
        conn.commit()
        print("[DB] Tabela tb_edson_context garantida")
        
    except Exception as e:
        print(f"[DB] Erro ao criar tabela tb_edson_context: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            release_connection(conn)


def get_edson_context(usuario_id: int, limit: int = 10) -> list:
    """Busca os últimos N turnos do usuário para enriquecer o RAG."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            sql = """
                SELECT role, content
                FROM tb_edson_context
                WHERE usuario_id = %s
                ORDER BY criado_em DESC
                LIMIT %s
            """
            cur.execute(sql, (usuario_id, limit))
            rows = cur.fetchall()
            return list(reversed(rows))
    except Exception as e:
        print(f"[DB] Erro ao buscar contexto do Edson: {e}")
        return []
    finally:
        if conn:
            release_connection(conn)
