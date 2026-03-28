# Backend Estrutura - Sporting das Sorte

## Visão Geral

Backend modular em **FastAPI** com separação clara de responsabilidades. Cada módulo tem um propósito específico e comunica via imports/dependencies.

## Estrutura de Diretórios

```
esportesdasorteback/
├── main.py                    # FastAPI app (< 80 linhas)
│
├── auth/                      # Autenticação & JWT
│   ├── router.py             # POST /api/login, POST /api/usuarios
│   └── service.py            # JWT, hash, verify
│
├── analysis/                  # Análises com cache TTL
│   ├── router.py             # GET /api/analise/{id}
│   └── service.py            # get_saved_analysis, save_analysis
│
├── chat/                      # Chat com Edson
│   ├── router.py             # POST /api/chat
│   └── edson.py              # System prompt, RAG, CTA
│
├── odds/                      # Odds & apostas
│   ├── sportingtech.py       # API wrapper
│   ├── betsapi.py            # BetsAPI wrapper
│   └── cache.py              # Cache em memória com TTL
│
├── live/                      # Background worker
│   └── worker.py             # Thread de refresh ao vivo
│
├── db/                        # Database layer
│   ├── neon.py               # Connection pool
│   └── queries.py            # SQL helpers
│
└── [compatibilidade]
    ├── rag_service.py        # Wrapper (legacy)
    └── db_neon.py            # Wrapper (legacy)
```

## Módulos Principais

### 1. AUTH - Autenticação

**Router:** `auth/router.py`

- `POST /api/login` - Autenticação com email/senha
- `POST /api/usuarios` - Cadastro de novo usuário
- `GET /api/usuarios/{user_id}/conta` - Dados da conta (protegido)
- `PUT /api/usuarios/{user_id}/conta` - Atualizar conta (protegido)

**Service:** `auth/service.py`

- `create_access_token(user_id, email)` - Gera JWT
- `get_current_user(token)` - Valida token (dependency)
- `JWT_SECRET` - Carregado de `.env`

### 2. ANALYSIS - Análises

**Router:** `analysis/router.py`

- `GET /api/analise/{match_id}` - Busca análise (com cache TTL)

**Service:** `analysis/service.py`

- `get_saved_analysis(match_id, is_live)` - Busca com filtro de TTL
- `save_analysis(match_id, analysis_json)` - Salva no `tb_analise`

**TTL:**

- Partidas ao vivo: 5 minutos (configurável via `ANALYSIS_TTL_LIVE_MINUTES`)
- Partidas futuras: 24 horas (configurável via `ANALYSIS_TTL_UPCOMING_HOURS`)

### 3. CHAT - Conversa com Edson

**Router:** `chat/router.py`

- `POST /api/chat` - Conversa conversacional

**Edson:** `chat/edson.py`

- `EDSON_SYSTEM_PROMPT` - System prompt genérico
- `build_rag_context(match_data, historical, odds)` - Contexto para LLM
- `build_cta(prediction, confidence)` - Call-to-action

### 4. ODDS - Odds & Apostas

**BetsAPI:** `odds/betsapi.py`

- `fetch_live_matches(sport_id)` - Partidas ao vivo
- `fetch_upcoming_matches(sport_id, days)` - Partidas futuras
- `get_odds_for_match(event_id)` - Odds de uma partida

**Sportingtech:** `odds/sportingtech.py`

- Fallback para BetsAPI se chave não configurada
- Mesmo interface que BetsAPI

**Cache:** `odds/cache.py`

- `get_live_odds(match_id)` - Cache de 5 min
- `set_live_odds(match_id, data)`
- `get_upcoming_odds(match_id)` - Cache de 24h
- `set_upcoming_odds(match_id, data)`

### 5. LIVE - Background Worker

**Worker:** `live/worker.py`

- `start_live_refresh_worker()` - Inicia thread
- Periodicamente atualiza odds/análises ao vivo

### 6. DB - Database Layer

**Neon:** `db/neon.py`

- `get_db_connection()` - Obtém conexão PostgreSQL
- `release_connection(conn)` - Fecha conexão
- `close_pool()` - Cleanup no shutdown

**Queries:** `db/queries.py`

- `ensure_edson_context_table()` - Garante tabelas
- Funções SQL centralizadas

## Fluxos Principais

### Login & Autenticação

```
1. Cliente: POST /api/login
   → email, senha

2. Backend: auth/router
   → valida email/senha contra tb_usuario
   → auth/service.create_access_token()
   → retorna {usuario, access_token, token_type}

3. Cliente: localStorage.setItem("access_token", token)

4. Requisições autenticadas:
   * Header: Authorization: Bearer {token}
   * auth/service.get_current_user(Depends)
```

### Análise de Partida

```
1. Cliente: GET /api/analise/{match_id}?is_live=true

2. Backend: analysis/router
   → analysis/service.get_saved_analysis(match_id, is_live=True)

3. Check de TTL (5 min se live, 24h se upcoming)
   → se válido: retorna análise do cache
   → se expirado/missing: MISS

4. Se MISS:
   → RAG builder junta contexto
   → Groq API gera análise
   → analysis/service.save_analysis() salva
   → retorna análise nova
```

### Chat com Edson

```
1. Cliente: POST /api/chat
   → {message, conversation_history}

2. Backend: chat/router
   → chat/edson.build_rag_context()
   → analysis/service.get_saved_analysis()
   → Groq API com system prompt + RAG

3. chat/edson.build_cta(prediction, confidence)
   → CTA = Call-To-Action para apostar

4. Retorna:
   {
     "response": "análise conversacional",
     "cta": {
       "label": "Ver apostas em...",
       "href": "/apostas/...",
       "confidence": 85
     }
   }
```

## Variáveis de Ambiente

```bash
# Database
NEON_URL=postgresql://user:password@host/dbname?sslmode=require

# Auth
JWT_SECRET=<openssl rand -hex 32>
JWT_EXPIRATION_HOURS=24

# APIs
GROQ_API_KEY=<sua chave>
BETS_API_TOKEN=<sua chave>
SPORTINGTECH_API_KEY=<opcional>

# TTL
ANALYSIS_TTL_LIVE_MINUTES=5
ANALYSIS_TTL_UPCOMING_HOURS=24
ODDS_CACHE_TTL_MINUTES=5
UPCOMING_ODDS_CACHE_TTL_HOURS=24

# CORS
CORS_ORIGINS=https://esportesdasorte.vercel.app,http://localhost:5173
```

## Como Executar

## Validação automática pós-push (Painel QA)

Para disparar checklist automaticamente após cada `git push`:

```bash
git config core.hooksPath .githooks
```

Execução manual (quando quiser):

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/post_push_panel.ps1
```

O relatório é salvo em `deploy/reports/`.

### Setup Inicial

```bash
# 1. Criar schema no Neon
python create_schema.py

# 2. Importar dados históricos (opcional)
python import_fbref_csv_to_neon.py
python ingest_parquet_to_neon.py

# 3. Iniciar servidor
uvicorn main:app --reload
```

### Testes

```bash
# Teste de TTL
python test_ttl.py

# Health check
curl http://localhost:8000/health
curl http://localhost:8000/health/detailed
```

## Dependências Instaladas

Ver `requirements.txt`:

- FastAPI 0.135.1
- PyJWT >= 2.8.0 (auth)
- psycopg2-binary (Neon)
- Groq 1.1.1 (AI)
- requests 2.32.5 (APIs)
- slowapi (rate limiting)
- python-dotenv (env vars)

## Próximos Passos (Fase 2)

- [ ] Implementar hash de senha (bcrypt)
- [ ] Implement full Sportingtech integration
- [ ] Live worker background refresh
- [ ] WebSocket para chat em tempo real
- [ ] Estatísticas de usuário & histórico

## Documentação

- [ARCHITECTURE.md](./ARCHITECTURE.md) - Arquitetura detalhada
- [.env.example](./.env.example) - Template de variáveis
