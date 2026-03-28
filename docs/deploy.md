# Deploy

## Backend (Railway)

Checklist:

1. Configurar variaveis de ambiente no projeto
2. Garantir que requirements.txt esteja atualizado
3. Definir start command com uvicorn main:app
4. Validar /health apos deploy

## Frontend (Vercel)

Variaveis sugeridas:

- VITE_BACKEND_URL: backend principal
- VITE_BACKEND_URL_FALLBACK: backend secundario

## Documentacao (GitHub Pages)

Para publicar docs com MkDocs:

    mkdocs build
    mkdocs gh-deploy --clean

URL esperada:

https://thiagoventurav.github.io/esportesdasorteback/
