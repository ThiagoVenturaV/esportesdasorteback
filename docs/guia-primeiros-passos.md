# Primeiros Passos

## Requisitos

- Python 3.11+
- Git
- Conta no Neon (PostgreSQL)

## Setup local

1. Clonar repositorio
2. Criar ambiente virtual
3. Instalar dependencias
4. Configurar arquivo .env

Exemplo de comandos:

    python -m venv .venv
    .venv\\Scripts\\activate
    pip install -r requirements.txt

## Banco de dados

Crie as tabelas iniciais:

    python create_schema.py

Importacao opcional de dados historicos:

    python import_fbref_csv_to_neon.py
    python ingest_parquet_to_neon.py

## Rodar API

    uvicorn main:app --reload

Aplicacao local:

- API: http://localhost:8000
- Swagger: http://localhost:8000/docs
