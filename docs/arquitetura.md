# Arquitetura

## Visao geral

O backend segue arquitetura modular por dominio:

- auth: autenticacao e JWT
- analysis: analises com cache TTL
- chat: conversa com Edson
- odds: integracoes de odds
- live: refresh em background
- db: pool e queries

## Fluxos principais

### Login

1. Cliente chama POST /api/login
2. Backend valida credenciais
3. Backend retorna access_token

### Analise

1. Cliente chama endpoint de analise
2. Backend busca cache valido por TTL
3. Em miss, gera analise com IA e persiste

### Chat

1. Cliente envia mensagem e historico
2. Backend monta contexto
3. Groq gera resposta
