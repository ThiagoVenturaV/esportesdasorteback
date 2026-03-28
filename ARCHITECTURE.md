"""
Backend Architecture - Sporting das Sorte

Estrutura modular do backend FastAPI com separação clara de responsabilidades.

```
esportesdasorteback/
│
├── main.py                          # FastAPI app + include_router (< 80 linhas)
│
├── auth/                            # Autenticação & JWT
│   ├── __init__.py
│   ├── router.py                   # POST /api/login, POST /api/usuarios
│   └── service.py                  # hash, verify, JWT, validate_payload
│
├── analysis/                        # Análises de partidas com cache TTL
│   ├── __init__.py
│   ├── router.py                   # GET /api/analise/{id}, /api/analises-salvas/{id}
│   └── service.py                  # get_saved_analysis, save_analysis, TTL logic
│
├── chat/                            # Conversa com Edson (assistente)
│   ├── __init__.py
│   ├── router.py                   # POST /api/chat
│   └── edson.py                    # system prompt, RAG builder, CTA builder
│
├── odds/                            # Odds e apostas ao vivo
│   ├── __init__.py
│   ├── sportingtech.py             # _get_live_odds_matches + fallbacks
│   ├── betsapi.py                  # fetch_live, fetch_upcoming, get_odds
│   └── cache.py                    # _ODDS_CACHE, _UPCOMING_CACHE, TTLs
│
├── live/                            # Background refresh worker
│   ├── __init__.py
│   └── worker.py                   # start_live_refresh_worker, background thread
│
├── db/                              # Database layer
│   ├── __init__.py
│   ├── neon.py                     # get_pool, get_connection, release_connection
│   └── queries.py                  # SQL centralizados como funções
│
├── rag_service.py                  # COMPATIBILIDADE: wrapper sobre analysis.service
├── db_neon.py                       # COMPATIBILIDADE: wrapper sobre db.neon
│
├── create_schema.py                # Setup inicial do schema no Neon
├── cron_refresh_data.py            # Manutenção periódica
├── ingest_parquet_to_neon.py       # Import de dados históricos
├── import_fbref_csv_to_neon.py     # Import de dados de jogadores
│
└── requirements.txt                # Dependências Python


PADRÕES & RESPONSABILIDADES
═════════════════════════════════════════════════════════════════════════════

1. AUTH / Autenticação
   - service.py: JWT generation/validation
   - router.py: POST /api/login, POST /api/usuarios
   - Dependency: get_current_user para rotas protegidas

2. ANALYSIS / Análises
   - service.py: get_saved_analysis (com TTL), save_analysis
   - router.py: GET endpoints para buscar/salvar análises
   - Cache: banco de dados (tb_analise), respeitando TTL

3. CHAT / Conversa com Edson
   - edson.py: System prompt, RAG context builder, CTA builder
   - router.py: POST /api/chat (conversação conversacional)
   - Integração com analysis.service + RAG

4. ODDS / Apostas & Cotações
   - betsapi.py: Integração com BetsAPI
   - sportingtech.py: Integração com Sportingtech + fallback
   - cache.py: Cache em memória com TTL

5. LIVE / Atualização em Tempo Real
   - worker.py: Thread background para refresh de partidas ao vivo
   - Integração: odds + analysis

6. DB / Camada de dados
   - neon.py: Connection pool PostgreSQL (Neon)
   - queries.py: Funções SQL centralizadas

7. MAIN / Orquestração
   - < 80 linhas
   - CORS middleware
   - Rate limiting (slowapi)
   - Include routers
   - Lifecycle (startup/shutdown)


FLUXOS PRINCIPAIS
═════════════════════════════════════════════════════════════════════════════

1. Login → JWT → Acesso a rotas protegidas
   → auth/router POST /api/login
   → auth/service create_access_token
   → Outras rotas use get_current_user dependency

2. Análise de partida
   → analysis/router GET /api/analise/{match_id}
   → analysis/service get_saved_analysis (com TTL)
   → Se miss/expired: chamada ao RAG + Groq
   → analysis/service save_analysis
   → Retorno para frontend

3. Chat com Edson
   → chat/router POST /api/chat
   → chat/edson build_rag_context
   → Groq API para geração
   → chat/edson build_cta
   → Retorno: resposta + CTA

4. Refresh ao vivo
   → live/worker background thread
   → odds/sportingtech ou betsapi fetch_live_matches
   → odds/cache cache management
   → analysis/service salva análises recentes


VARIÁVEIS DE AMBIENTE IMPORTANTES
═════════════════════════════════════════════════════════════════════════════

# Database
NEON_URL=postgresql://...

# Auth
JWT_SECRET=... (openssl rand -hex 32)

# Análises - TTL
ANALYSIS_TTL_LIVE_MINUTES=5
ANALYSIS_TTL_UPCOMING_HOURS=24

# Odds - TTL
ODDS_CACHE_TTL_MINUTES=5
UPCOMING_ODDS_CACHE_TTL_HOURS=24

# APIs externas
GROQ_API_KEY=...
BETS_API_TOKEN=...
SPORTINGTECH_API_KEY=...

# CORS
CORS_ORIGINS=https://esportesdasorte.vercel.app,http://localhost:5173
"""

# Este é apenas um docstring - o arquivo de documentação real seria:
# ARCHITECTURE.md (em markdown)
```
