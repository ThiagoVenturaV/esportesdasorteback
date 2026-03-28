"""
main.py — FastAPI app + include_router (< 80 linhas)

Toda a lógica de negócio foi extraída para módulos:
  auth/     — Autenticação, JWT, cadastro
  analysis/ — RAG, BetsAPI, Groq (análises JSON)
  chat/     — Edson chat conversacional (Maverick)
  odds/     — Sportingtech odds
  live/     — Background refresh worker
  db/       — Connection pooling (Neon PostgreSQL)
"""

import os
from dotenv import load_dotenv
load_dotenv()
# Groq configuration for chat and analysis
GROQ_MODEL_CHAT = os.getenv("GROQ_MODEL_CHAT", "openai/gpt-oss-120b")
GROQ_MODEL = os.getenv("GROQ_MODEL", "mixtral-8x7b-32768")


from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    HAS_SLOWAPI = True
except ImportError:
    HAS_SLOWAPI = False

from auth.router import router as auth_router
from auth.service import create_access_token, get_current_user
from analysis.router import router as analysis_router
from chat.router import router as chat_router
from odds.router import router as odds_router
from live.worker import start_live_refresh_worker
from db.neon import close_pool
from utils.ratelimit import get_rate_limit_key

# ── CORS ──────────────────────────────────────────────────────────────────────

raw_origins = os.getenv("CORS_ORIGINS", "")
cors_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

default_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://esportesdasorte.vercel.app",
    "https://esportesdasorte-production.up.railway.app",
]
for origin in default_origins:
    if origin not in cors_origins:
        cors_origins.append(origin)

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Assistente de Análise Esportiva (Edson)",
    description="Backend modular com PostgreSQL Neon, BetsAPI e Groq via RAG.",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate Limiting ─────────────────────────────────────────────────────────────

if HAS_SLOWAPI:
    # Usa user_id para usuários autenticados, IP para anônimos
    limiter = Limiter(key_func=get_rate_limit_key)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    from chat.router import set_limiter
    set_limiter(limiter)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(analysis_router)
app.include_router(chat_router)
app.include_router(odds_router)


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok"}


@app.get("/health/detailed", tags=["Health"])
def health_detailed():
    from db.neon import get_db_connection, release_connection
    checks: dict = {}
    try:
        conn = get_db_connection()
        conn.cursor().execute("SELECT 1")
        checks["database"] = "ok"
        release_connection(conn)
    except Exception as e:
        checks["database"] = f"error: {e}"

    checks["groq_configured"] = str(bool(os.getenv("GROQ_API_KEY")))
    checks["betsapi_configured"] = str(bool(os.getenv("BETS_API_TOKEN")))
    checks["jwt_configured"] = str(bool(os.getenv("JWT_SECRET")))
    return checks


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
def on_startup():
    from db.queries import ensure_edson_context_table
    ensure_edson_context_table()
    start_live_refresh_worker()


@app.on_event("shutdown")
def on_shutdown():
    close_pool()
