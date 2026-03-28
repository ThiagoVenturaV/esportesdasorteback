# Configuracao

## Variaveis de ambiente essenciais

### Banco

- NEON_URL: string de conexao PostgreSQL
- DB_POOL_MIN_CONN: minimo de conexoes no pool
- DB_POOL_MAX_CONN: maximo de conexoes no pool

### Autenticacao

- JWT_SECRET: segredo para assinar tokens
- JWT_EXPIRATION_HOURS: expiracao do token

### IA e provedores

- GROQ_API_KEY
- GROQ_MODEL
- GROQ_MODEL_CHAT
- BETS_API_TOKEN
- SPORTINGTECH_API_KEY

### CORS e frontend

- CORS_ORIGINS: lista separada por virgula com origens permitidas

### TTL e limpeza

- ANALYSIS_TTL_LIVE_MINUTES
- ANALYSIS_TTL_UPCOMING_HOURS
- ANALYSIS_CACHE_RETENTION_DAYS
- EDSON_CONTEXT_RETENTION_DAYS

## Exemplo de .env

Veja o arquivo .env.example na raiz do backend para um modelo completo.
