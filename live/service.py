from core.utils import *
import os
import re
import json
import time
import threading
import base64
import hashlib
import hmac
import secrets
import requests
import psycopg2
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
import jwt
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from urllib.parse import urlencode
from dotenv import load_dotenv
try:
    from groq import Groq
except Exception:
    Groq = None

from db_neon import get_db_connection
import rag_service

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct").strip()
GROQ_MODEL_CHAT = os.getenv("GROQ_MODEL_CHAT", "meta-llama/llama-4-maverick-17b-128e-instruct").strip()
groq_client = Groq(api_key=GROQ_API_KEY) if (GROQ_API_KEY and Groq) else None
if GROQ_API_KEY and not Groq:
    print("[Startup] Pacote 'groq' não encontrado. Chat seguirá em modo fallback até instalar dependências.")

EDSON_DB_ONLY_MODE = os.getenv("EDSON_DB_ONLY_MODE", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
CHAT_DB_ONLY_MODE = os.getenv("CHAT_DB_ONLY_MODE", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
EDSON_WEB_FALLBACK_ENABLED = os.getenv("EDSON_WEB_FALLBACK_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "on"
}

raw_origins = os.getenv("CORS_ORIGINS", "")
cors_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

# Adicionar origens padrão para desenvolvimento e produção se não estiverem presentes
default_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://esportesdasorte.vercel.app",
    "https://esportesdasorte-production.up.railway.app"
]

for origin in default_origins:
    if origin not in cors_origins:
        cors_origins.append(origin)

app = FastAPI(
    title="Assistente de Análise Esportiva (Edson)",
    description="Backend estruturado com PostgreSQL Neon, BetsAPI e Groq via RAG.",
    version="2.0.0"
)

# Configuração robusta de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def _ensure_live_matches_table() -> None:
    global _LIVE_MATCHES_TABLE_READY
    with _LIVE_MATCHES_TABLE_LOCK:
        if _LIVE_MATCHES_TABLE_READY:
            return

        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tb_live_match_cache (
                        match_id VARCHAR(50) PRIMARY KEY,
                        home_team VARCHAR(150) NOT NULL,
                        away_team VARCHAR(150) NOT NULL,
                        league_name VARCHAR(180) NOT NULL,
                        home_score INT NOT NULL DEFAULT 0,
                        away_score INT NOT NULL DEFAULT 0,
                        minute INT NOT NULL DEFAULT 0,
                        raw_payload JSONB,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_live_match_cache_updated_at ON tb_live_match_cache (updated_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_live_match_cache_league_name ON tb_live_match_cache (league_name)"
                )
            conn.commit()
            _LIVE_MATCHES_TABLE_READY = True
        except Exception as e:
            print(f"[LiveCache] Erro ao garantir tabela de cache ao vivo: {e}")
        finally:
            if conn:
                conn.close()

def _sync_live_matches_cache_from_api() -> List[Dict[str, Any]]:
    """
    Busca jogos ao vivo da API e persiste no banco para servir o frontend em modo DB-first.
    """
    _ensure_live_matches_table()

    try:
        live_matches = rag_service.fetch_live_matches() or []
    except Exception as e:
        print(f"[LiveCache] Erro ao buscar partidas ao vivo na API: {e}")
        return []

    if not live_matches:
        return []

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            upsert_sql = """
                INSERT INTO tb_live_match_cache (
                    match_id, home_team, away_team, league_name,
                    home_score, away_score, minute, raw_payload, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
                ON CONFLICT (match_id)
                DO UPDATE SET
                    home_team = EXCLUDED.home_team,
                    away_team = EXCLUDED.away_team,
                    league_name = EXCLUDED.league_name,
                    home_score = EXCLUDED.home_score,
                    away_score = EXCLUDED.away_score,
                    minute = EXCLUDED.minute,
                    raw_payload = EXCLUDED.raw_payload,
                    updated_at = NOW()
            """

            for match in live_matches:
                if not isinstance(match, dict):
                    continue

                fields = _extract_live_match_fields(match)
                mid = str(fields.get("match_id") or "0")
                if not mid or mid == "0":
                    continue

                live_data = fields.get("live_data") or {}
                try:
                    home_score = int(live_data.get("home_score", 0))
                except Exception:
                    home_score = 0
                try:
                    away_score = int(live_data.get("away_score", 0))
                except Exception:
                    away_score = 0
                try:
                    minute = int(live_data.get("minute", 0))
                except Exception:
                    minute = 0

                cur.execute(
                    upsert_sql,
                    (
                        mid,
                        str(fields.get("home_team") or "Time Casa"),
                        str(fields.get("away_team") or "Time Fora"),
                        str(fields.get("league_name") or "Partida em Andamento"),
                        home_score,
                        away_score,
                        minute,
                        json.dumps(match, ensure_ascii=False),
                    ),
                )

            # Limpa lixo antigo para manter a tabela leve.
            cur.execute("DELETE FROM tb_live_match_cache WHERE updated_at < NOW() - INTERVAL '6 hours'")

        conn.commit()
    except Exception as e:
        print(f"[LiveCache] Erro ao persistir partidas ao vivo no banco: {e}")
    finally:
        if conn:
            conn.close()

    return live_matches

def _get_live_matches_from_db(
    limit: int = 0,
    league_filter: str = "",
    max_age_seconds: int = LIVE_MATCHES_DB_MAX_AGE_SECONDS,
) -> List[Dict[str, Any]]:
    _ensure_live_matches_table()

    rows: List[Dict[str, Any]] = []
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            if league_filter:
                if limit > 0:
                    sql = """
                        SELECT match_id, home_team, away_team, league_name, home_score, away_score, minute, updated_at
                        FROM tb_live_match_cache
                        WHERE updated_at >= NOW() - (%s * INTERVAL '1 second')
                          AND LOWER(league_name) LIKE %s
                        ORDER BY minute DESC, updated_at DESC
                        LIMIT %s
                    """
                    cur.execute(sql, (max_age_seconds, f"%{league_filter}%", limit))
                else:
                    sql = """
                        SELECT match_id, home_team, away_team, league_name, home_score, away_score, minute, updated_at
                        FROM tb_live_match_cache
                        WHERE updated_at >= NOW() - (%s * INTERVAL '1 second')
                          AND LOWER(league_name) LIKE %s
                        ORDER BY minute DESC, updated_at DESC
                    """
                    cur.execute(sql, (max_age_seconds, f"%{league_filter}%"))
            else:
                if limit > 0:
                    sql = """
                        SELECT match_id, home_team, away_team, league_name, home_score, away_score, minute, updated_at
                        FROM tb_live_match_cache
                        WHERE updated_at >= NOW() - (%s * INTERVAL '1 second')
                        ORDER BY minute DESC, updated_at DESC
                        LIMIT %s
                    """
                    cur.execute(sql, (max_age_seconds, limit))
                else:
                    sql = """
                        SELECT match_id, home_team, away_team, league_name, home_score, away_score, minute, updated_at
                        FROM tb_live_match_cache
                        WHERE updated_at >= NOW() - (%s * INTERVAL '1 second')
                        ORDER BY minute DESC, updated_at DESC
                    """
                    cur.execute(sql, (max_age_seconds,))

            rows = cur.fetchall() or []
    except Exception as e:
        print(f"[LiveCache] Erro ao ler partidas ao vivo do banco: {e}")
        rows = []
    finally:
        if conn:
            conn.close()

    matches: List[Dict[str, Any]] = []
    for row in rows:
        try:
            hs = int(row.get("home_score", 0))
        except Exception:
            hs = 0
        try:
            aw = int(row.get("away_score", 0))
        except Exception:
            aw = 0
        try:
            minute = int(row.get("minute", 0))
        except Exception:
            minute = 0

        matches.append(
            {
                "id": str(row.get("match_id") or "0"),
                "home": {"name": str(row.get("home_team") or "Time Casa")},
                "away": {"name": str(row.get("away_team") or "Time Fora")},
                "league_name": str(row.get("league_name") or "Partida em Andamento"),
                "ss": f"{hs}-{aw}",
                "timer": {"tm": minute},
            }
        )

    return matches

def _refresh_live_analyses_once() -> int:
    """
    Recalcula e persiste análises de partidas ao vivo para leitura rápida no frontend.
    """
    live_matches = _sync_live_matches_cache_from_api()
    if not live_matches:
        # Fallback: usa cache de partidas recentes do banco para manter o refresh das análises.
        live_matches = _get_live_matches_from_db(limit=0, max_age_seconds=600)

    if not live_matches:
        return 0

    refreshed = 0
    for m in live_matches:
        if not isinstance(m, dict):
            continue

        fields = _extract_live_match_fields(m)
        mid = fields.get("match_id")
        if not mid or mid == "0":
            continue

        try:
            rag_service.analyze_match_with_ai(
                mid,
                home_team=fields.get("home_team"),
                away_team=fields.get("away_team"),
            )
            refreshed += 1
        except Exception as e:
            print(f"[LiveRefresh] Erro ao atualizar análise {mid}: {e}")

    return refreshed

def _live_analyses_refresh_loop() -> None:
    while True:
        started_at = time.time()
        try:
            refreshed = _refresh_live_analyses_once()
            if refreshed:
                print(f"[LiveRefresh] Análises atualizadas: {refreshed}")
        except Exception as e:
            print(f"[LiveRefresh] Falha inesperada no loop: {e}")

        elapsed = time.time() - started_at
        sleep_seconds = max(5, LIVE_ANALYSIS_REFRESH_SECONDS - int(elapsed))
        time.sleep(sleep_seconds)

def _startup_live_refresh_worker() -> None:
    _ensure_live_matches_table()

    # Warm cache once on startup to reduce first-request latency.
    try:
        _sync_live_matches_cache_from_api()
    except Exception as e:
        print(f"[LiveRefresh] Falha no warmup inicial do cache ao vivo: {e}")

    if not LIVE_ANALYSIS_BACKGROUND_REFRESH:
        print("[LiveRefresh] Worker desativado por configuração.")
        return

    global _LIVE_ANALYSIS_REFRESH_STARTED
    with _LIVE_ANALYSIS_REFRESH_LOCK:
        if _LIVE_ANALYSIS_REFRESH_STARTED:
            return

        _LIVE_ANALYSIS_REFRESH_STARTED = True
        worker = threading.Thread(
            target=_live_analyses_refresh_loop,
            name="live-analyses-refresh",
            daemon=True,
        )
        worker.start()
        print(f"[LiveRefresh] Worker iniciado (intervalo={LIVE_ANALYSIS_REFRESH_SECONDS}s).")

