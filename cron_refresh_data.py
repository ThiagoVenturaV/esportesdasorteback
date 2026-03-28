"""
Cron job para Railway.

Executa em sequência:
1) Garante schema no Neon
2) Faz ingest incremental de parquets (quando disponíveis)
3) Limpa cache antigo de análises

Projetado para ser idempotente: pode rodar várias vezes sem duplicar partidas,
pois o ingest usa ON CONFLICT (id_partida) DO NOTHING.
"""

import os
from datetime import datetime, UTC

from db_neon import get_db_connection
from create_schema import create_schema
from ingest_parquet_to_neon import load_matches_to_neon


def cleanup_old_analysis_cache(retention_days: int = 7) -> int:
    """
    Remove análises antigas para reduzir custo e manter cache recente.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM tb_analise
                WHERE criado_em < NOW() - (%s || ' days')::interval
                """,
                (str(retention_days),),
            )
            removed = cur.rowcount or 0
        conn.commit()
        return removed
    except Exception as e:
        print(f"[CRON] Erro ao limpar cache tb_analise: {e}")
        if conn:
            conn.rollback()
        return 0
    finally:
        if conn:
            conn.close()


def run_cron_job() -> int:
    limit_files = int(os.getenv("INGEST_LIMIT_FILES", "2"))
    retention_days = int(os.getenv("ANALYSIS_CACHE_RETENTION_DAYS", "7"))

    print(f"[CRON] Iniciado em {datetime.now(UTC).isoformat()}")
    print(f"[CRON] Config: INGEST_LIMIT_FILES={limit_files}, ANALYSIS_CACHE_RETENTION_DAYS={retention_days}")

    create_schema()

    processed_rows = load_matches_to_neon(limit_files=limit_files)
    removed_cache_rows = cleanup_old_analysis_cache(retention_days=retention_days)

    print(
        f"[CRON] Finalizado. Linhas processadas no ingest: {processed_rows}. "
        f"Registros removidos do cache: {removed_cache_rows}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cron_job())
