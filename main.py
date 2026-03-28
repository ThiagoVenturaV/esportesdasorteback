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


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok"}


# ==========================================
# MODELOS PYDANTIC
# ==========================================

from models import Usuario, LoginDados, ChatRequest


SPORTINGTECH_POPULAR_ODDS_URL = os.getenv(
    "SPORTINGTECH_POPULAR_ODDS_URL",
    "https://esportesdasorte.bet.br/api-generic/sportbet/getPopularOdds",
).strip()
SPORTINGTECH_UPCOMING_BASE_URL = os.getenv(
    "SPORTINGTECH_UPCOMING_BASE_URL",
    "https://esportesdasorte.bet.br/api-v2/upcoming-events/d/23/esportesdasortevip/null",
).strip()
_ODDS_CACHE: Dict[str, Any] = {"ts": 0.0, "data": []}
_UPCOMING_CACHE: Dict[str, Any] = {"ts": 0.0, "data": []}
_FIXTURE_DETAIL_CACHE: Dict[str, Any] = {}
HOME_LIVE_ANALYSES_LIMIT = max(1, int(os.getenv("HOME_LIVE_ANALYSES_LIMIT", "8")))
LIVE_ANALYSIS_REFRESH_SECONDS = max(30, int(os.getenv("LIVE_ANALYSIS_REFRESH_SECONDS", "60")))
LIVE_MATCHES_DB_MAX_AGE_SECONDS = max(30, int(os.getenv("LIVE_MATCHES_DB_MAX_AGE_SECONDS", "120")))
LIVE_ANALYSIS_BACKGROUND_REFRESH = os.getenv("LIVE_ANALYSIS_BACKGROUND_REFRESH", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
_LIVE_ANALYSIS_REFRESH_LOCK = threading.Lock()
_LIVE_ANALYSIS_REFRESH_STARTED = False
_LIVE_MATCHES_TABLE_LOCK = threading.Lock()
_LIVE_MATCHES_TABLE_READY = False
PASSWORD_PBKDF2_ITERATIONS = 310000


# --- Auth Helpers migrados para auth.service ---
from services import *
from auth.routes import router as auth_router
app.include_router(auth_router)


# ==========================================
# ROTAS DO ASSISTENTE EDSON E RAG
# ==========================================

@app.get("/api/analises-salvas", tags=["Edson RAG"])
def analises_salvas():
    return {"sucesso": False, "analises": []}


@app.get("/api/analises-salvas/{match_id}", tags=["Edson RAG"])
def analise_salva_por_partida(match_id: str, home_team: Optional[str] = None, away_team: Optional[str] = None):
    home = (home_team or "Time Casa").strip() or "Time Casa"
    away = (away_team or "Time Fora").strip() or "Time Fora"
    live_data = {"home_score": 0, "away_score": 0, "minute": 45}

    try:
        saved_raw = rag_service.get_saved_analysis(match_id)
        saved = _coerce_analysis_dict(saved_raw)
        if not saved:
            return {"sucesso": False, "analise": None}

        normalized = ensure_analysis_shape(saved, str(match_id), home, away, live_data)
        return {"sucesso": True, "analise": normalized}
    except Exception as e:
        print(f"Erro ao buscar analise salva por partida ({match_id}): {e}")
        return {"sucesso": False, "analise": None}


@app.get("/api/analises-ao-vivo", tags=["Edson RAG"])
def analises_ao_vivo(limit: int = HOME_LIVE_ANALYSES_LIMIT, league: Optional[str] = None):
    effective_limit = int(limit) if isinstance(limit, int) else HOME_LIVE_ANALYSES_LIMIT
    if effective_limit < 0:
        effective_limit = HOME_LIVE_ANALYSES_LIMIT

    league_filter = _norm(league) if league else ""

    def build_payload(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        resultado: List[Dict[str, Any]] = []

        for m in matches:
            try:
                fields = _extract_live_match_fields(m)
                league_name = fields.get("league_name") or "Partida em Andamento"

                if league_filter and league_filter not in _norm(league_name):
                    continue

                mid = str(fields.get("match_id") or "0")
                home = str(fields.get("home_team") or "Time Casa")
                away = str(fields.get("away_team") or "Time Fora")
                live_data = fields.get("live_data") or {"home_score": 0, "away_score": 0, "minute": 45}

                saved = None
                try:
                    saved_raw = rag_service.get_saved_analysis(mid)
                    saved = _coerce_analysis_dict(saved_raw)
                except Exception as e:
                    print(f"Erro ao buscar analise salva para {mid}: {e}")

                # Endpoint de leitura rápida: usa análise já persistida no DB.
                analysis = ensure_analysis_shape(saved, mid, home, away, live_data)

                resultado.append(
                    {
                        "match_id": mid,
                        "home_team": home,
                        "away_team": away,
                        "league_name": league_name,
                        "live_data": live_data,
                        "analysis": analysis,
                    }
                )

                if effective_limit > 0 and len(resultado) >= effective_limit:
                    break
            except Exception as item_error:
                print(f"Erro ao montar item de analise ao vivo: {item_error}")

        return resultado

    fallback_matches = get_fallback_live_matches()

    try:
        live_matches = _get_live_matches_from_db(limit=0, league_filter=league_filter)
        if not live_matches:
            _sync_live_matches_cache_from_api()
            live_matches = _get_live_matches_from_db(limit=0, league_filter=league_filter, max_age_seconds=600)

        if not live_matches:
            live_matches = fallback_matches

        resultado = build_payload(live_matches)
        if not resultado:
            resultado = build_payload(fallback_matches)

        return {"sucesso": True, "analises": resultado}
    except Exception as e:
        print(f"Erro em analises_ao_vivo: {e}")
        return {"sucesso": True, "analises": build_payload(fallback_matches)}


@app.get("/api/analisar/{match_id}", tags=["Edson RAG"])
def analisar_partida(
    match_id: str,
    home_team: Optional[str] = None,
    away_team: Optional[str] = None,
    refresh: bool = False,
):
    """
    Acionado pela AnalysisPage.jsx. Retorna a previsão robusta baseada em Parquet/BetsAPI.
    """
    home = (home_team or "Time Casa").strip() or "Time Casa"
    away = (away_team or "Time Fora").strip() or "Time Fora"
    live_data = {"home_score": 0, "away_score": 0, "minute": 45}

    try:
        # Prioriza leitura do cache persistido no Neon para resposta rápida.
        if not refresh:
            try:
                saved_raw = rag_service.get_saved_analysis(match_id)
                saved = _coerce_analysis_dict(saved_raw)
                if saved:
                    return ensure_analysis_shape(saved, str(match_id), home, away, live_data)
            except Exception as cache_error:
                print(f"Erro ao ler cache de analise ({match_id}): {cache_error}")

        resultado = rag_service.analyze_match_with_ai(
            match_id,
            home_team=home_team,
            away_team=away_team,
        )
        if not resultado:
            # Fallback seguro com dados completos se a IA falhar
            resultado = {
                "matchId": match_id,
                "winProbability": { "home": 50, "draw": 25, "away": 25 },
                "goalProbabilityNextMinute": 20,
                "cardRiskHome": 30, "cardRiskAway": 30, "penaltyRisk": 10,
                "momentumHome": [50]*15, "momentumAway": [50]*15,
                "commentary": ["Analise temporariamente indisponível.", "Consulte novamente em instantes."],
                "predictedWinner": home_team or "Empate",
                "confidenceScore": 70
            }

        return ensure_analysis_shape(resultado, str(match_id), home, away, live_data)
    except Exception as e:
        print(f"Erro em analisar_partida: {e}")
        return {
            "matchId": match_id,
            "winProbability": { "home": 33, "draw": 33, "away": 34 },
            "goalProbabilityNextMinute": 10,
            "cardRiskHome": 0, "cardRiskAway": 0, "penaltyRisk": 0,
            "momentumHome": [50]*15, "momentumAway": [50]*15,
            "commentary": ["Ocorreu um erro ao processar a análise.", str(e)],
            "predictedWinner": "Indefinido",
            "confidenceScore": 0
        }


@app.post("/api/chat", tags=["Edson Chat"])
@limiter.limit("20/minute")
def edson_chat(fastapi_req: Request, request: ChatRequest):
    """
    Substitui a chamada direta do frontend para o provedor de LLM.
    Injeta contexto do Neon DB e usa Groq no backend.
    """
    db_context = get_chat_db_context(request.message)
    fbref_context = get_fbref_db_context(request.message)
    live_bets_context = get_live_matches_context(request.message, limit=8)
    upcoming_context = get_upcoming_matches_context(request.message)
    should_try_web = EDSON_WEB_FALLBACK_ENABLED and (not db_context and not fbref_context and not live_bets_context and not upcoming_context)
    web_context = get_web_context(request.message) if should_try_web else []
    live_matches = _get_live_odds_matches()
    if not live_matches:
        live_matches = _build_upcoming_sporting_matches_with_markets(upcoming_context, max_items=6)
    live_match_for_prompt = _select_match_for_cta(live_matches, request.message, db_context, upcoming_context)
    cta = build_chat_cta(request.message, db_context, upcoming_context)
    context_lines = []
    for row in db_context:
        context_lines.append(
            f"[{row.get('id_partida')}] {row.get('time_casa')} {row.get('gols_casa')} x {row.get('gols_fora')} {row.get('time_fora')} | "
            f"{row.get('competicao')} {row.get('temporada')}"
        )

    fbref_lines = []
    for row in fbref_context:
        fbref_lines.append(
            f"{row.get('player')} | {row.get('squad')} | {row.get('competition')} {row.get('season')} | "
            f"Pos: {row.get('position') or '-'} | Gols: {row.get('goals') or 0} | Ast: {row.get('assists') or 0} | Min: {row.get('minutes') or 0}"
        )

    web_lines = []
    for item in web_context:
        web_lines.append(
            f"{item.get('title')} | {item.get('snippet')} | Fonte: {item.get('url')}"
        )

    upcoming_lines = []
    for row in upcoming_context[:8]:
        upcoming_lines.append(
            f"[{row.get('match_id')}] {row.get('home_team')} vs {row.get('away_team')} | {row.get('competition')} | {row.get('season')} | Início: {row.get('kickoff') or 'N/D'}"
        )

    live_bets_lines = []
    for row in live_bets_context[:8]:
        live_bets_lines.append(
            f"[{row.get('match_id')}] {row.get('home_team')} {row.get('score')} {row.get('away_team')} | Min {row.get('minute')} | {row.get('competition')}"
        )

    odds_lines = []
    if live_match_for_prompt and isinstance(live_match_for_prompt.get("markets"), dict):
        odds_lines.append(
            f"Partida-alvo: {live_match_for_prompt.get('home')} vs {live_match_for_prompt.get('away')} (match_id={live_match_for_prompt.get('id')})"
        )
        for market_name, selections in list(live_match_for_prompt.get("markets", {}).items())[:8]:
            if not isinstance(selections, list) or not selections:
                continue
            formatted = []
            for sel in selections[:3]:
                label = str(sel.get("label") or "").strip()
                odd = sel.get("odd")
                try:
                    odd_text = f"{float(odd):.2f}"
                except Exception:
                    continue
                if label:
                    formatted.append(f"{label} ({odd_text})")
            if formatted:
                odds_lines.append(f"{market_name}: {' | '.join(formatted)}")

    prompt_sistema = (
        "Você é Edson, um assistente virtual ultra-avançado em análise de dados esportivos. "
        "Você atua como conselheiro principal da plataforma de apostas de elite.\n"
        f"Data atual de referência: {datetime.now().strftime('%Y-%m-%d')} (ano {datetime.now().year}).\n"
        "Considere explicitamente que estamos em 2026 e priorize recomendações para jogos futuros quando o usuário pedir próximos jogos, UEFA, Copa ou calendário.\n"
        "Quando houver contexto de jogos ao vivo/futuros abaixo, use-o diretamente; não diga que não há acesso a esses dados.\n"
        "Sua principal fonte de verdade são os dados estatísticos abaixo, extraídos do banco de dados (Neon DB). "
        "Avalie as informações rigorosamente. Se não houver muitos dados exatos, forneça sua melhor estimativa "
        "baseada no seu vasto conhecimento sobre histórico do futebol.\n"
        "Seja sempre assertivo, inspirador e com tom de especialista em apostas. Sugira mercados promissores "
        "com base nos dados e nas odds da API. Varie os mercados entre resultado final, ambas marcam, over/under de gols, escanteios, cartões e handicap. "
        "Nunca diga que não temos acesso às odds da API quando houver CONTEXTO_ODDS_API. "
        "Responda em texto puro, sem markdown (não use **, *, listas markdown ou blocos de código). "
        "Evite repetir sempre a mesma linha (como 'Mais de 2.5 gols') quando não for a melhor opção. "
        "Elabore textos diretos, de fácil leitura, sem jargões excessivos como 'eu sou uma IA' ou afins.\n"
        "Quando não houver dados do banco, você pode usar o contexto web fornecido, citando a fonte de forma curta quando relevante.\n"
        "Seja natural e amigável. Responda em no máximo 2 parágrafos curtos e até 420 caracteres.\n\n"
        f"CONTEXTO_PARTIDAS_DB:\n{os.linesep.join(context_lines) if context_lines else 'Sem contexto específico de partidas no momento.'}\n\n"
        f"CONTEXTO_FBREF_DB:\n{os.linesep.join(fbref_lines) if fbref_lines else 'Sem contexto específico de jogadores no momento.'}\n\n"
        f"CONTEXTO_JOGOS_AO_VIVO_BETSAPI:\n{os.linesep.join(live_bets_lines) if live_bets_lines else 'Sem jogos ao vivo no momento.'}\n\n"
        f"CONTEXTO_JOGOS_FUTUROS_API:\n{os.linesep.join(upcoming_lines) if upcoming_lines else 'Sem jogos futuros disponíveis no momento.'}\n\n"
        f"CONTEXTO_WEB_FALLBACK:\n{os.linesep.join(web_lines) if web_lines else 'Sem contexto web necessário no momento.'}\n\n"
        f"CONTEXTO_ODDS_API:\n{os.linesep.join(odds_lines) if odds_lines else 'Sem odds ao vivo disponíveis no momento.'}"
    )

    if CHAT_DB_ONLY_MODE:
        if not db_context and not fbref_context:
            return build_chat_response(
                (
                    "Não encontrei dados suficientes no banco para responder com segurança. "
                    "Informe times, jogadores, campeonato ou temporada para eu consultar o Neon/FBref."
                ),
                cta,
            )

        resumo_partidas = "; ".join(context_lines[:3]) if context_lines else "sem partidas relevantes"
        resumo_fbref = "; ".join(fbref_lines[:3]) if fbref_lines else "sem jogadores relevantes"
        return build_chat_response(
            (
                "Resposta em modo estrito de banco (sem busca web). "
                f"Partidas: {resumo_partidas}. "
                f"FBref: {resumo_fbref}. "
                "Use esses dados como referência estatística para sua decisão."
            ),
            cta,
        )
    
    if not groq_client:
        if db_context or fbref_context or live_bets_context or upcoming_context or web_context:
            resumo_partidas = "; ".join(context_lines[:2]) if context_lines else "sem partidas"
            resumo_fbref = "; ".join(fbref_lines[:2]) if fbref_lines else "sem FBref"
            resumo_live = "; ".join(live_bets_lines[:2]) if live_bets_lines else "sem ao vivo"
            resumo_upcoming = "; ".join(upcoming_lines[:2]) if upcoming_lines else "sem próximos jogos"
            resumo_web = "; ".join(web_lines[:2]) if web_lines else "sem web"
            return build_chat_response(
                (
                    "Estou sem conexão com o provedor de IA no momento, mas encontrei contexto útil. "
                    f"Partidas: {resumo_partidas}. FBref: {resumo_fbref}. Ao vivo: {resumo_live}. Próximos jogos: {resumo_upcoming}. Web: {resumo_web}. "
                    "Tente novamente em alguns segundos para uma análise completa."
                ),
                cta,
            )

        return build_chat_response(
            (
                "No momento estou sem conexão com o provedor de IA. "
                "Tente novamente em alguns segundos."
            ),
            cta,
        )

    history_messages = frontend_history_to_groq_messages(request.history)
    messages = [{"role": "system", "content": prompt_sistema}] + history_messages + [
        {"role": "user", "content": request.message}
    ]

    try:
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL_CHAT,
            messages=messages,
            temperature=0.7,
            max_tokens=600,
        )

        resposta_texto = ""
        if completion and completion.choices:
            choice = completion.choices[0]
            if choice and choice.message and choice.message.content:
                resposta_texto = str(choice.message.content).strip()

        if not resposta_texto:
            resposta_texto = "O Edson está processando muitas informações agora. Pode repetir a pergunta de outra forma?"

        if odds_lines and _contains_any(
            resposta_texto,
            [
                "não temos as odds",
                "nao temos as odds",
                "sem odds oficiais",
                "não temos acesso às odds",
                "nao temos acesso as odds",
            ],
        ):
            resumo_odds = "; ".join(odds_lines[:2])
            resposta_texto = (
                "Com base nas odds atuais da API da Esportes da Sorte, "
                f"os melhores mercados são: {resumo_odds}."
            )

        if _is_generic_no_data_reply(resposta_texto) and (live_bets_context or upcoming_context):
            quick_reply = _build_contextual_quick_reply(
                request.message,
                live_bets_context,
                upcoming_context,
                cta,
            )
            if quick_reply:
                resposta_texto = quick_reply

        return build_chat_response(shorten_chat_text(resposta_texto), cta)
    except Exception as e:
        print(f"Erro no chat Groq: {e}")
        return build_chat_response(
            "Desculpe, ocorreu um erro na nuvem neural do Edson. Tente novamente em alguns segundos.",
            cta,
        )
