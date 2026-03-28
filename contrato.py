# ============================================================
# contrato.py — DICIONÁRIO DE CAMPOS COMPARTILHADOS
# ============================================================
# Este arquivo é a "fonte da verdade" do projeto.
# Define os contratos de dados entre módulos.
#
# REGRA: se precisar mudar um campo, mude AQUI.
# ============================================================


# ----------------------------------------------------------
# CONTRATO 1 — Dados de jogo ao vivo (BetsAPI / Sportingtech)
# ----------------------------------------------------------

CONTRATO_JOGO_AO_VIVO = {
    "event_id":       None,   # str   — ID único do jogo
    "time_a":         None,   # str   — Nome do time da casa
    "time_b":         None,   # str   — Nome do time visitante
    "gols_a":         0,      # int   — Gols do time A
    "gols_b":         0,      # int   — Gols do time B
    "minuto":         0,      # int   — Minuto atual da partida
    "liga":           None,   # str   — Nome da liga/campeonato
    "pais":           None,   # str   — País da liga (código ISO, ex: "BR")
    "odds_vitoria_a": None,   # float — Odd de vitória do time A (decimal)
    "odds_empate":    None,   # float — Odd de empate
    "odds_vitoria_b": None,   # float — Odd de vitória do time B
    "status":         None,   # str   — "ao_vivo" | "intervalo" | "encerrado"
}


# ----------------------------------------------------------
# CONTRATO 2 — Análise gerada pela IA (Groq / Edson)
# ----------------------------------------------------------

CONTRATO_ANALISE = {
    "matchId":                   None,   # str   — ID da partida
    "winProbability":            None,   # dict  — {"home": int, "draw": int, "away": int}
    "goalProbabilityNextMinute": 0,      # int   — % de chance de gol no próximo minuto
    "cardRiskHome":              0,      # int   — % de risco de cartão (casa)
    "cardRiskAway":              0,      # int   — % de risco de cartão (fora)
    "penaltyRisk":               0,      # int   — % de risco de pênalti
    "momentumHome":              None,   # list  — 15 inteiros 0-100
    "momentumAway":              None,   # list  — 15 inteiros 0-100
    "commentary":                None,   # list  — 2 strings de comentário
    "predictedWinner":           None,   # str   — Nome do time favorito ou "Empate"
    "confidenceScore":           0,      # int   — 0-100 confiança da análise
}


# ----------------------------------------------------------
# CONTRATO 3 — Campos da tabela tb_usuario (Neon PostgreSQL)
# ----------------------------------------------------------

CAMPOS_USUARIO = [
    "nome_usuario",       # str  — Nome completo
    "email_usuario",      # str  — E-mail único (chave de login)
    "cpf_usuario",        # str  — CPF (11 dígitos, sem formatação)
    "dataNac_usuario",    # date — Data de nascimento (YYYY-MM-DD)
    "endereco_usuario",   # str  — Endereço completo (opcional)
    "telefone_usuario",   # str  — Telefone com DDD (10/11 dígitos)
    "senha_usuario",      # str  — Hash PBKDF2-SHA256
]


# ----------------------------------------------------------
# SQL DE CRIAÇÃO — usar via create_schema.py
# ----------------------------------------------------------

SQL_CRIAR_TABELA_USUARIO = """
CREATE TABLE IF NOT EXISTS tb_usuario (
    id_usuario       SERIAL PRIMARY KEY,
    nome_usuario     VARCHAR(255)  NOT NULL,
    email_usuario    VARCHAR(255)  NOT NULL UNIQUE,
    cpf_usuario      VARCHAR(11)   UNIQUE,
    dataNac_usuario  DATE,
    endereco_usuario TEXT,
    telefone_usuario VARCHAR(20),
    senha_usuario    VARCHAR(255)  NOT NULL,
    criado_em        TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
);
"""


# ----------------------------------------------------------
# DADOS MOCK — para desenvolvimento local sem APIs
# ----------------------------------------------------------

JOGO_MOCK = {
    "event_id":       "mock_001",
    "time_a":         "Arsenal",
    "time_b":         "Chelsea",
    "gols_a":         1,
    "gols_b":         1,
    "minuto":         67,
    "liga":           "Premier League",
    "pais":           "GB",
    "odds_vitoria_a": 2.10,
    "odds_empate":    3.40,
    "odds_vitoria_b": 3.20,
    "status":         "ao_vivo",
}
