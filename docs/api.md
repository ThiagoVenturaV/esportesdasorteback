# Endpoints API

## Health

- GET /health
- GET /health/detailed

## Auth

- POST /api/login
- POST /api/usuarios
- GET /api/usuarios/{user_id}/conta
- PUT /api/usuarios/{user_id}/conta

## Chat

- POST /api/chat

Payload esperado:

- message: string
- conversation_history: lista

## Analise

- GET /api/analise/{match_id}

## Odds e apostas

- GET /api/apostas
- GET /api/apostas/abertas
- GET /api/apostas/finalizadas
- GET /api/odds/{fixture_id}

## Swagger

Em ambiente local:

http://localhost:8000/docs
