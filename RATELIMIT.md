# Rate Limiting - Sporting das Sorte

## Visão Geral

O backend implementa rate limiting com **slowapi** para proteger against abuse e controlar custos de APIs externas (Groq, BetsAPI, etc.).

## Configuração

### Instalação

```bash
pip install slowapi>=0.1.9
```

### Setup em main.py

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from utils.ratelimit import get_rate_limit_key

# Criar limiter com chave customizada (user_id ou IP)
limiter = Limiter(key_func=get_rate_limit_key)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

## Estratégia de Rate Limiting

### Chave de Rate Limit

A chave para rate limiting é determinada em `utils/ratelimit.py`:

| Caso                             | Chave             | Limite     |
| -------------------------------- | ----------------- | ---------- |
| Usuário autenticado (JWT válido) | `user:{user_id}`  | 20 req/min |
| Usuário anônimo                  | `ip:{ip_address}` | 20 req/min |

### Fluxo

```python
# 1. Extrair token do header Authorization
Authorization: Bearer {JWT_TOKEN}

# 2. Decodificar JWT
payload = jwt.decode(token, JWT_SECRET)
user_id = payload.get("sub")

# 3. Usar user_id como chave
# Resultado: "user:123" (limite por conta)

# Se falhar ou não houver token:
# Resultado: "ip:192.168.1.1" (limite por IP)
```

## Endpoints com Rate Limit

### POST /api/chat

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {token}" \
  -d '{"message": "Análise Flamengo x Vasco", "conversation_history": []}'
```

**Limite:** 20 mensagens/minuto

**Respostas:**

- `200 OK` - Requisição processada
- `429 Too Many Requests` - Limite excedido

```json
{
  "detail": "20 per 1 minute"
}
```

## Comportamento Padrão

### Limite Atingido

```
Cliente: GET /api/chat (requisição 21 em 60 segundos)
↓
Backend: Valida rate limit
↓
Status: 429 Too Many Requests
Response: "20 per 1 minute"
```

### Reset Automático

O limite é automaticamente resetado a cada 60 segundos (1 minuto).

```
:00 - Primeira requisição (contador = 1)
:30 - Décima requisição (contador = 10)
:59 - Vigésima requisição (contador = 20)
:60 - Vigésima-primeira requisição → 429 (excedido)
:01 - Contador resetado (contador = 1)
```

## Casos de Uso

### 1. Usuário Autenticado

```python
# Cliente faz login
POST /api/login
← access_token: "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."

# Requisições subsequentes
POST /api/chat
Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
```

**Resultado:**

- Todas as requisições do User A são contadas contra seu limite (user:123)
- User B tem um limite separado (user:456)
- Limite: 20 req/min por user

### 2. Usuário Anônimo

```python
# Sem token
POST /api/chat
```

**Resultado:**

- Requisição é contada por IP
- Qualquer pessoa no mesmo IP(proxy) compartilha o limite
- Limite: 20 req/min por IP

## Proteção Contra Abuse

### Cenário

```
Atacante tenta fazer 1000 requisições/min

:00 - Requisições 1-20 → 200 OK
:01 - Requisição 21 → 429 Too Many Requests
:01 - Requisições 22-40 → 429 Too Many Requests
:02 - Requisição 41 → 429 Too Many Requests
```

### Custo Previsto

```
Sem rate limit:
- 1000 req/min × 0.005 USD/req (Groq) = 5 USD/min = 7200 USD/dia 💸

Com rate limit (20/min):
- 20 req/min × 0.005 USD/req = 0.1 USD/min = 144 USD/dia ✓
```

## Customização

### Aumentar Limite

```python
# em main.py
@limiter.limit("50/minute")  # 50 por minuto
@app.post("/api/chat")
async def chat(...):
    ...
```

### Limite por Rota

```python
# Diferentes limites para diferentes rotas
@limiter.limit("100/minute")  # Chat: mais permissivo
@app.post("/api/chat")
async def chat(...):
    ...

@limiter.limit("5/minute")    # Análise: mais restritivo
@app.post("/api/analisar")
async def analisar(...):
    ...
```

### Desabilitar Rate Limit

```python
# Remover decorator
async def health_check():
    return {"status": "ok"}
```

## Monitoramento

### Logs

```
[RATELIMIT] Using user_id as key: 123
[RATELIMIT] Using IP as key: 192.168.1.1
```

### Métricas (Implementação Futura)

- Requisições por usuário/IP
- Taxa de rejeição (429s)
- Picos de uso

## Teste

```bash
# Executar teste de rate limiting
python test_ratelimit.py
```

Verifica:
✓ Rate limit anônimo (por IP)
✓ Rate limit autenticado (por user_id)
✓ Usuários múltiplos independentes
✓ Reset após expiração

## FAQ

### P: Posso detectar quando vou atingir o limite?

R: slowapi não retorna headers de rate limit por padrão. Para implementar em Fase 2:

```python
from slowapi.util import get_remote_address
from slowapi import Limiter

# Headers customizados
response.headers["X-RateLimit-Limit"] = "20"
response.headers["X-RateLimit-Remaining"] = "5"
response.headers["X-RateLimit-Reset"] = "3600"
```

### P: E se eu tiver um proxy reverso ou load balancer?

R: `get_remote_address()` pode retornar IP do proxy. Configure:

```python
# em .env
TRUSTED_PROXIES=10.0.0.0/8

# em utils/ratelimit.py
from fastapi import Request
request.client.host  # Já trata proxies
```

### P: Como resetar o limite manualmente?

R: Não é possível com slowapi in-memory. Use Redis para Fase 2:

```python
from slowapi.stores import RedisStore

store = RedisStore("redis://localhost:6379")
limiter = Limiter(key_func=..., store=store)
```

## Referências

- [slowapi](https://github.com/laurentS/slowapi)
- [FastAPI Rate Limiting](https://fastapi.tiangolo.com/tutorial/security/)
- [OWASP: Rate Limiting](https://owasp.org/www-community/attacks/Rate-limiting_attack)
