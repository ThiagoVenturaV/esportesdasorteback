# ============================================================
# contrato.py — DICIONÁRIO DE CAMPOS COMPARTILHADOS
# ============================================================
# Este arquivo é a "fonte da verdade" do projeto.
# M3, M4 e M5 importam daqui — ninguém inventa nome de campo.
#
# REGRA: se precisar mudar um campo, mude AQUI e avise no Discord.
# Nenhum membro altera este arquivo sem alinhar com os outros.
# ============================================================


# ----------------------------------------------------------
# CONTRATO 1 — O que M3 (betsapi.py) entrega para M4
# ----------------------------------------------------------
# M3 se compromete a sempre devolver um dicionário com estes campos.
# Se a BetsAPI não retornar algum dado, M3 usa o valor padrão abaixo.

CONTRATO_JOGO_AO_VIVO = {
    "event_id":       None,   # str   — ID único do jogo na BetsAPI
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
# CONTRATO 2 — O que M5 (analyst.py) recebe e devolve
# ----------------------------------------------------------
# M5 RECEBE: os campos de CONTRATO_JOGO_AO_VIVO + dados de jogadores
# M5 DEVOLVE: um dicionário com estes campos exatos.

CONTRATO_ANALISE = {
    "chance_gol_a":        0.0,   # float — % de chance de gol do time A (0–100)
    "chance_gol_b":        0.0,   # float — % de chance de gol do time B (0–100)
    "vencedor_provavel":   None,  # str   — Nome do time favorito ou "Equilíbrio"
    "confianca_vencedor":  None,  # str   — "Alta" | "Média" | "Baixa"
    "risco_cartao":        None,  # dict  — {"nome": str, "risco": float}
    "narrativa_ia":        None,  # str   — Texto gerado pelo Groq/Llama
    "movimento_destaque":  None,  # str   — Variação notável de odds, ou None
    "alerta":              None,  # str   — Insight especial, ou None
    "probabilidades": {
        "time_a": None,           # str   — ex: "58% — favorito moderado"
        "empate": None,           # str   — ex: "22% — improvável mas real"
        "time_b": None,           # str   — ex: "20% — azarão com chance"
    }
}


# ----------------------------------------------------------
# CONTRATO 3 — Campos da tabela tb_usuario no MySQL
# ----------------------------------------------------------
# M4 usa esses campos para validar os dados antes de salvar.
# A ordem aqui é a mesma ordem do INSERT no banco.

CAMPOS_USUARIO = [
    "nome_usuario",       # str  — Nome completo
    "email_usuario",      # str  — E-mail único (chave de login)
    "cpf_usuario",        # str  — CPF (ex: "123.456.789-00")
    "dataNac_usuario",    # date — Data de nascimento (YYYY-MM-DD)
    "endereco_usuario",   # str  — Endereço completo (opcional)
    "telefone_usuario",   # str  — Telefone com DDD
    "senha_usuario",      # str  — Senha (hash em produção real)
]


# ----------------------------------------------------------
# SQL DE CRIAÇÃO DA TABELA — rode no Railway para criar o banco
# ----------------------------------------------------------
# Como usar:
#   1. Acesse Railway → seu serviço MySQL → aba "Query"
#   2. Cole o SQL abaixo e execute (botão Run)
#   3. A tabela tb_usuario será criada. Faça isso só uma vez.

SQL_CRIAR_TABELA_USUARIO = """
CREATE TABLE IF NOT EXISTS tb_usuario (
    id_usuario       INT AUTO_INCREMENT PRIMARY KEY,
    nome_usuario     VARCHAR(150)  NOT NULL,
    email_usuario    VARCHAR(150)  NOT NULL UNIQUE,
    cpf_usuario      VARCHAR(14)   NOT NULL UNIQUE,
    dataNac_usuario  DATE          NOT NULL,
    endereco_usuario VARCHAR(255),
    telefone_usuario VARCHAR(20)   NOT NULL,
    senha_usuario    VARCHAR(255)  NOT NULL,
    criado_em        TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
);
"""


# ----------------------------------------------------------
# DADOS MOCK — use quando quiser testar sem a BetsAPI
# ----------------------------------------------------------
# Substitui uma chamada real de API durante o desenvolvimento.
# M4 pode retornar JOGO_MOCK direto na rota /api/jogos
# se quiser testar o front-end sem depender de M3.

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
