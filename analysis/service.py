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


def _coerce_analysis_dict(analysis: Any) -> Optional[Dict[str, Any]]:
    if isinstance(analysis, str):
        try:
            analysis = json.loads(analysis)
        except Exception:
            return None
    return analysis if isinstance(analysis, dict) else None

def _extract_live_match_fields(match_data: Dict[str, Any]) -> Dict[str, Any]:
    mid = str(match_data.get("id") or match_data.get("FI") or "0").strip() or "0"
    home = str(((match_data.get("home") or {}).get("name") or match_data.get("home_name") or "Time Casa")).strip() or "Time Casa"
    away = str(((match_data.get("away") or {}).get("name") or match_data.get("away_name") or "Time Fora")).strip() or "Time Fora"
    league_name = str(
        ((match_data.get("league") or {}).get("name"))
        or match_data.get("league_name")
        or match_data.get("competition")
        or "Partida em Andamento"
    ).strip() or "Partida em Andamento"

    return {
        "match_id": mid,
        "home_team": home,
        "away_team": away,
        "league_name": league_name,
        "live_data": parse_live_data(match_data),
    }

def ensure_analysis_shape(analysis: Any, mid: str, home: str, away: str, live_data: Dict[str, int]) -> Dict[str, Any]:
    if isinstance(analysis, str):
        try:
            analysis = json.loads(analysis)
        except Exception:
            analysis = None

    if not isinstance(analysis, dict):
        analysis = build_default_analysis(mid, home, away, live_data)

    required_fields = ["cardRiskHome", "cardRiskAway", "penaltyRisk", "momentumHome", "momentumAway"]
    for field in required_fields:
        if field not in analysis:
            analysis[field] = [50] * 15 if "momentum" in field else 0

    return analysis

def build_default_analysis(mid: str, home: str, away: str, live_data: Dict[str, int]) -> Dict[str, Any]:
    home_score = live_data.get("home_score", 0)
    away_score = live_data.get("away_score", 0)
    minute = live_data.get("minute", 45)

    predicted = "Empate"
    if home_score > away_score:
        predicted = home
    elif away_score > home_score:
        predicted = away

    return {
        "matchId": mid,
        "winProbability": {"home": 45, "draw": 30, "away": 25},
        "goalProbabilityNextMinute": 15,
        "cardRiskHome": 35,
        "cardRiskAway": 25,
        "penaltyRisk": 5,
        "momentumHome": [50, 55, 60, 45, 40, 65, 70, 75, 50, 45, 55, 60, 65, 70, 80],
        "momentumAway": [50, 45, 40, 55, 60, 35, 30, 25, 50, 55, 45, 40, 35, 30, 20],
        "predictedWinner": predicted,
        "confidenceScore": 80,
        "commentary": [
            f"Partida bastante equilibrada até o minuto {minute}.",
            f"A equipe do {home} tem as melhores chances de definir o resultado agora com base na pressão ofensiva.",
        ],
    }

def parse_live_data(match_data: Dict[str, Any]) -> Dict[str, int]:
    ss = str(match_data.get("ss", "0-0"))
    parts = ss.split("-")
    hs = parts[0].strip() if len(parts) > 0 else "0"
    as_ = parts[1].strip() if len(parts) > 1 else "0"
    home_score = int(hs) if hs.isdigit() else 0
    away_score = int(as_) if as_.isdigit() else 0

    minute = 45
    timer = match_data.get("timer")
    if isinstance(timer, dict):
        tm = timer.get("tm", 45)
        try:
            minute = int(tm)
        except Exception:
            minute = 45

    return {"home_score": home_score, "away_score": away_score, "minute": minute}

def get_fallback_live_matches() -> List[Dict[str, Any]]:
    return [
        {"id": "802107412", "home": {"name": "Flamengo"}, "away": {"name": "Palmeiras"}, "ss": "1-1", "timer": {"tm": 65}},
        {"id": "673291882", "home": {"name": "Real Madrid"}, "away": {"name": "Barcelona"}, "ss": "2-0", "timer": {"tm": 40}},
        {"id": "992123512", "home": {"name": "Vasco da Gama"}, "away": {"name": "Botafogo"}, "ss": "0-1", "timer": {"tm": 85}},
    ]

def _analysis_needs_refresh(analysis: Any) -> bool:
    if not isinstance(analysis, dict):
        return True

    mh = analysis.get("momentumHome")
    ma = analysis.get("momentumAway")
    if isinstance(mh, list) and isinstance(ma, list):
        flat_mh = len(mh) > 0 and len(set(mh)) == 1
        flat_ma = len(ma) > 0 and len(set(ma)) == 1
        if flat_mh and flat_ma:
            return True

    goal_prob = analysis.get("goalProbabilityNextMinute", 0)
    try:
        goal_prob = int(goal_prob)
    except Exception:
        goal_prob = 0

    comments = analysis.get("commentary") or []
    comments_text = " ".join(str(c).lower() for c in comments if c)
    if goal_prob <= 12 and ("indispon" in comments_text or "erro" in comments_text):
        return True

    return False

def get_live_matches_context(user_message: str = "", limit: int = 8) -> List[Dict[str, Any]]:
    """
    Busca jogos ao vivo em modo DB-first para resposta rápida no chat.
    """
    live_rows = []
    try:
        live_matches = _get_live_matches_from_db(limit=max(limit * 2, limit))
        if not live_matches:
            _sync_live_matches_cache_from_api()
            live_matches = _get_live_matches_from_db(limit=max(limit * 2, limit), max_age_seconds=600)

        for row in live_matches:
            if not isinstance(row, dict):
                continue

            fields = _extract_live_match_fields(row)
            score_data = fields.get("live_data") or {}
            score = f"{score_data.get('home_score', 0)}-{score_data.get('away_score', 0)}"
            minute_text = str(score_data.get("minute", "N/D"))

            timer = row.get("timer") or {}
            if isinstance(timer, dict) and timer.get("tm") is not None:
                minute_text = str(timer.get("tm"))

            live_rows.append(
                {
                    "match_id": str(fields.get("match_id") or "").strip(),
                    "home_team": str(fields.get("home_team") or "Time Casa").strip(),
                    "away_team": str(fields.get("away_team") or "Time Fora").strip(),
                    "score": score,
                    "minute": minute_text,
                    "competition": str(fields.get("league_name") or "Competição").strip(),
                }
            )

        if live_rows:
            ranked = sorted(
                live_rows,
                key=lambda r: _score_match_row(r, user_message, is_live=True),
                reverse=True,
            )
            return ranked[:limit]
    except Exception as e:
        print(f"Erro ao montar contexto de jogos ao vivo (DB-first): {e}")

    # Fallback local quando API estiver fora
    fallback = []
    for m in get_fallback_live_matches()[:limit]:
        parsed = parse_live_data(m)
        fallback.append(
            {
                "match_id": str(m.get("id") or "0"),
                "home_team": str((m.get("home") or {}).get("name") or "Time Casa"),
                "away_team": str((m.get("away") or {}).get("name") or "Time Fora"),
                "score": f"{parsed.get('home_score', 0)}-{parsed.get('away_score', 0)}",
                "minute": str(parsed.get("minute", "N/D")),
                "competition": "Ao vivo",
            }
        )
    return fallback

