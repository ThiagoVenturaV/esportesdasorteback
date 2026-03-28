# Troubleshooting

## Erro de rede no frontend

Checklist:

1. Conferir VITE_BACKEND_URL
2. Confirmar backend acessivel por HTTPS
3. Validar CORS_ORIGINS no backend

## CORS preflight

Validar OPTIONS para /api/chat e checar Access-Control-Allow-Origin.

## Erro 500 em /api/chat

1. Verificar GROQ_API_KEY no ambiente
2. Consultar logs do processo uvicorn
3. Validar formato do payload JSON

## 404 em /health

Possiveis causas:

- app antigo em execucao
- service apontando para diretorio errado
- deploy nao atualizado

## Comandos uteis (Linux)

- journalctl -u seu_service -f
- systemctl status seu_service
- curl -i http://127.0.0.1:8000/health
