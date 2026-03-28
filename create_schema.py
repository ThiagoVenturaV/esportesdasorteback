"""
Script para criar as tabelas principais no Neon PostgreSQL
Execute isso uma única vez para setup inicial
"""

from db_neon import get_db_connection, release_connection

CREATE_SCHEMA_SQL = """
-- Tabela de histórico de partidas (StatsBomb)
CREATE TABLE IF NOT EXISTS tb_partida_historico (
    id_partida INTEGER PRIMARY KEY,
    competicao VARCHAR(255),
    temporada VARCHAR(100),
    time_casa VARCHAR(255),
    time_fora VARCHAR(255),
    gols_casa INTEGER DEFAULT 0,
    gols_fora INTEGER DEFAULT 0,
    dados_extras JSONB,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de usuários (para login/registro)
CREATE TABLE IF NOT EXISTS tb_usuario (
    id_usuario SERIAL PRIMARY KEY,
    nome_usuario VARCHAR(255) NOT NULL,
    email_usuario VARCHAR(255) UNIQUE NOT NULL,
    cpf_usuario VARCHAR(11) UNIQUE,
    dataNac_usuario DATE,
    endereco_usuario TEXT,
    telefone_usuario VARCHAR(20),
    senha_usuario VARCHAR(255) NOT NULL,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Índices para performance
CREATE INDEX IF NOT EXISTS idx_partida_competicao ON tb_partida_historico(competicao);
CREATE INDEX IF NOT EXISTS idx_partida_temporada ON tb_partida_historico(temporada);
CREATE INDEX IF NOT EXISTS idx_usuario_email ON tb_usuario(email_usuario);

-- Tabela de histórico de chats (opcional para RAG/contexto)
CREATE TABLE IF NOT EXISTS tb_chat_historico (
    id_chat SERIAL PRIMARY KEY,
    id_usuario INTEGER REFERENCES tb_usuario(id_usuario),
    id_partida INTEGER REFERENCES tb_partida_historico(id_partida),
    pergunta TEXT,
    resposta TEXT,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_usuario ON tb_chat_historico(id_usuario);
CREATE INDEX IF NOT EXISTS idx_chat_partida ON tb_chat_historico(id_partida);

-- Tabela de cache/histórico de análises geradas pela IA
CREATE TABLE IF NOT EXISTS tb_analise (
    id_analise SERIAL PRIMARY KEY,
    match_id VARCHAR(50),
    analise_json JSONB,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Migração para cenários em que tb_analise já existe com colunas legadas
ALTER TABLE tb_analise ADD COLUMN IF NOT EXISTS match_id VARCHAR(50);
ALTER TABLE tb_analise ADD COLUMN IF NOT EXISTS analise_json JSONB;
ALTER TABLE tb_analise ADD COLUMN IF NOT EXISTS criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_analise_match_id ON tb_analise(match_id);

-- Contexto persistente do Edson
CREATE TABLE IF NOT EXISTS tb_edson_context (
    id_context SERIAL PRIMARY KEY,
    user_id VARCHAR(50) NOT NULL,
    session_id VARCHAR(100),
    context_data JSONB NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_edson_context_user_id ON tb_edson_context(user_id);

COMMIT;
"""

def create_schema():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Executa o SQL do schema
        cursor.execute(CREATE_SCHEMA_SQL)
        conn.commit()
        
        print("✓ Schema criado com sucesso!")
        print("✓ Tabelas criadas:")
        print("  - tb_partida_historico (para dados StatsBomb)")
        print("  - tb_usuario (para autenticação)")
        print("  - tb_chat_historico (para RAG/contexto)")
        
    except Exception as e:
        print(f"✗ Erro ao criar schema: {e}")
        if conn:
            conn.rollback()
    finally:
        release_connection(conn)

if __name__ == "__main__":
    create_schema()
