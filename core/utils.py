import os
HOME_LIVE_ANALYSES_LIMIT = max(1, int(os.getenv('HOME_LIVE_ANALYSES_LIMIT', '8')))
LIVE_ANALYSIS_REFRESH_SECONDS = max(30, int(os.getenv('LIVE_ANALYSIS_REFRESH_SECONDS', '60')))
LIVE_MATCHES_DB_MAX_AGE_SECONDS = max(30, int(os.getenv('LIVE_MATCHES_DB_MAX_AGE_SECONDS', '120')))
LIVE_ANALYSIS_BACKGROUND_REFRESH = os.getenv('LIVE_ANALYSIS_BACKGROUND_REFRESH', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
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


def get_user_key(request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    try:
        from main import JWT_SECRET
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload.get("sub", get_remote_address(request))
    except Exception:
        return get_remote_address(request)

def health_check():
    return {"status": "ok"}

def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())

def _contains_any(text: str, terms: List[str]) -> bool:
    t = _norm(text)
    return any(_norm(term) in t for term in terms)

def _to_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    # Epoch em segundos ou milissegundos
    if raw.isdigit():
        ts = int(raw)
        if ts > 10_000_000_000:
            ts = ts // 1000
        try:
            return datetime.fromtimestamp(ts)
        except Exception:
            return None

    # ISO ou datas comuns
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(raw[:19], fmt)
        except Exception:
            continue

    return None

def _format_kickoff(value: Any) -> str:
    dt = _to_datetime(value)
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")

def _score_match_row(row: Dict[str, Any], user_message: str, is_live: bool = False) -> int:
    msg = _norm(user_message)
    tokens = re.findall(r"[a-z0-9]{3,}", msg)

    hay = " ".join(
        [
            _norm(row.get("home_team")),
            _norm(row.get("away_team")),
            _norm(row.get("competition")),
            _norm(row.get("season")),
            _norm(row.get("sport")),
        ]
    )

    score = 0
    for token in tokens:
        if token in hay:
            score += 2

    if _contains_any(msg, ["campeonato brasileiro", "brasileirao", "brasileirão", "serie a", "série a", "brasil"]):
        if _contains_any(hay, ["brasil", "brasile", "serie a", "série a"]):
            score += 12

    if _contains_any(msg, ["uefa", "champions", "europa", "conference"]):
        if _contains_any(hay, ["uefa", "champions", "europa", "conference"]):
            score += 10

    if _contains_any(msg, ["copa", "world cup", "fifa"]):
        if _contains_any(hay, ["copa", "world cup", "fifa"]):
            score += 10

    if _contains_any(msg, ["ao vivo", "agora", "live"]) and is_live:
        score += 6

    if _contains_any(msg, ["proximo", "próximo", "futuro", "amanha", "amanhã", "hoje"]):
        if not is_live:
            score += 5

    kickoff = _to_datetime(row.get("kickoff"))
    if kickoff and _contains_any(msg, ["hoje"]):
        if kickoff.date() == datetime.now().date():
            score += 10

    return score

def _build_contextual_quick_reply(
    user_message: str,
    live_rows: List[Dict[str, Any]],
    upcoming_rows: List[Dict[str, Any]],
    cta: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    msg = _norm(user_message)
    today = datetime.now().strftime("%d/%m/%Y")
    brazil_intent = _is_brazil_intent(msg)

    if brazil_intent:
        live_rows = [r for r in (live_rows or []) if _is_brazil_row(r)]
        upcoming_rows = [r for r in (upcoming_rows or []) if _is_brazil_row(r)]

    prefers_live = _contains_any(msg, ["ao vivo", "live", "agora"])
    prefers_next = _contains_any(msg, ["proximo", "próximo", "futuro", "amanha", "amanhã", "hoje"])

    best_live = (live_rows or [None])[0]
    best_next = (upcoming_rows or [None])[0]

    cta_line = ""
    if isinstance(cta, dict) and cta.get("pick") and cta.get("odd"):
        cta_line = f" Sugestão direta: {cta.get('pick')} @ {float(cta.get('odd')):.2f}."

    if brazil_intent and not best_live and not best_next:
        return shorten_chat_text(
            f"Hoje é {today}. No feed atual, não encontrei jogos do Campeonato Brasileiro com odds disponíveis neste momento. "
            "Se abrir mercado nas próximas horas, eu te trago a melhor entrada com odd e link direto para o bilhete."
        )

    if prefers_live and best_live:
        return shorten_chat_text(
            f"Hoje ({today}), no ao vivo, destaque: {best_live.get('home_team')} x {best_live.get('away_team')} "
            f"({best_live.get('score')} aos {best_live.get('minute')}') em {best_live.get('competition')}.{cta_line}"
        )

    if prefers_next and best_next:
        kickoff = best_next.get("kickoff") or "horário a confirmar"
        return shorten_chat_text(
            f"Hoje é {today}. Próximo jogo relevante: {best_next.get('home_team')} x {best_next.get('away_team')} "
            f"em {best_next.get('competition')} (início: {kickoff}).{cta_line}"
        )

    if best_live:
        return shorten_chat_text(
            f"Hoje ({today}), no ao vivo: {best_live.get('home_team')} x {best_live.get('away_team')} "
            f"({best_live.get('score')} aos {best_live.get('minute')}').{cta_line}"
        )

    if best_next:
        kickoff = best_next.get("kickoff") or "horário a confirmar"
        return shorten_chat_text(
            f"Hoje é {today}. Jogo futuro em foco: {best_next.get('home_team')} x {best_next.get('away_team')} "
            f"em {best_next.get('competition')} (início: {kickoff}).{cta_line}"
        )

    return None

def _sanitize_chat_output(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    # Remove markdown de destaque que chega do modelo e quebra o visual do chat.
    cleaned = cleaned.replace("*", "")

    # Normalize bullets simples para melhorar legibilidade.
    cleaned = re.sub(r"(?m)^\s*[-•]\s*", "- ", cleaned)

    # Colapsa linhas em excesso.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()

def create_access_token(user_id: int, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.utcnow() + timedelta(hours=24),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

