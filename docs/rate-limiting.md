# Rate Limiting

## Objetivo

Proteger endpoints sensiveis contra abuso e controlar custos com provedores externos.

## Regra atual

- POST /api/chat: 20 requisicoes por minuto

## Chave de controle

- Usuario autenticado: user:{user_id}
- Usuario anonimo: ip:{ip}

## Resposta quando excede

- HTTP 429 Too Many Requests

## Ajustes futuros

- Mover armazenamento para Redis em ambiente multi-instancia
- Expor headers de rate limit para o frontend
