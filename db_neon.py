"""
db_neon.py — COMPATIBILIDADE RETROATIVA

Este arquivo é um wrapper fino sobre db.neon para que scripts antigos
(create_schema.py, cron_refresh_data.py, ingest_parquet_to_neon.py)
continuem funcionando sem modificações.
"""

from db.neon import get_db_connection, release_connection, get_pool, close_pool
