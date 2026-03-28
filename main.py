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

class Usuario(BaseModel):
    nome_usuario: str
    email_usuario: str
    cpf_usuario: str
    dataNac_usuario: str
    endereco_usuario: str = ""
    telefone_usuario: str
    senha_usuario: str

class LoginDados(BaseModel):
    email_usuario: str
    senha_usuario: str

class ChatRequest(BaseModel):
    message: str
    history: List[Dict[str, Any]] = []


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


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _only_digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${PASSWORD_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def _verify_password(input_password: str, stored_password: str) -> bool:
    stored = str(stored_password or "")
    if not stored:
        return False

    # Compatibilidade: contas antigas salvas em texto plano.
    if "$" not in stored:
        return hmac.compare_digest(stored, str(input_password or ""))

    try:
        algo, iters, salt, stored_digest = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            str(input_password or "").encode("utf-8"),
            salt.encode("utf-8"),
            int(iters),
        )
        return hmac.compare_digest(digest.hex(), stored_digest)
    except Exception:
        return False


def _validate_signup_payload(novo_usuario: Usuario) -> Optional[str]:
    if len(str(novo_usuario.nome_usuario or "").strip()) < 3:
        return "Nome deve ter pelo menos 3 caracteres."

    email = str(novo_usuario.email_usuario or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return "E-mail inválido."

    cpf_digits = _only_digits(novo_usuario.cpf_usuario)
    if len(cpf_digits) != 11:
        return "CPF deve conter 11 dígitos."

    phone_digits = _only_digits(novo_usuario.telefone_usuario)
    if len(phone_digits) not in (10, 11):
        return "Telefone deve conter 10 ou 11 dígitos."

    password = str(novo_usuario.senha_usuario or "")
    if len(password) < 8:
        return "Senha deve ter no mínimo 8 caracteres."

    return None


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


def _is_brazil_intent(user_message: str) -> bool:
    return _contains_any(user_message, ["campeonato brasileiro", "brasileirao", "brasileirão", "serie a", "série a", "brasil"])


def _is_brazil_row(row: Dict[str, Any]) -> bool:
    hay = " ".join(
        [
            _norm(row.get("competition")),
            _norm(row.get("home")),
            _norm(row.get("away")),
            _norm(row.get("home_team")),
            _norm(row.get("away_team")),
        ]
    )
    return _contains_any(hay, ["brasil", "brasile", "serie a", "série a", "brazil"])


def _score_market_name(market_name: str, user_message: str) -> int:
    name = _norm(market_name)
    msg = _norm(user_message)

    # Prioriza mercado citado explicitamente na pergunta.
    scored = 0
    keyword_groups = [
        (["resultado", "vencedor", "vence", "ganha", "ganhar", "1x2", "empate"], ["resultado", "1x2", "match odds"]),
        (["gol", "gols", "over", "under", "2.5", "1.5", "3.5"], ["gols", "total", "over", "under", "mais/menos"]),
        (["ambas", "ambos marcam", "btts"], ["ambas marcam", "both teams"]),
        (["escanteio", "corner"], ["escanteio", "corner"]),
        (["cartão", "cartao", "cartoes", "cartões"], ["cart", "booking"]),
        (["handicap", "hcp"], ["handicap"]),
    ]

    for msg_terms, market_terms in keyword_groups:
        if any(t in msg for t in msg_terms):
            if any(mt in name for mt in market_terms):
                scored += 6

    # Sinal fraco para manter mercados populares quando não há intenção clara.
    if any(t in name for t in ["resultado", "gols", "ambas marcam", "escanteio", "cart", "handicap"]):
        scored += 1

    return scored


def _pick_selection_for_market(market_name: str, selections: List[Dict[str, Any]], user_message: str, home_name: str, away_name: str) -> Optional[Dict[str, Any]]:
    if not selections:
        return None

    msg = _norm(user_message)
    parsed_line_match = re.search(r"(\d+[\.,]?\d*)", msg)
    parsed_line = parsed_line_match.group(1).replace(",", ".") if parsed_line_match else ""

    scored = []
    for sel in selections:
        label = str(sel.get("label") or "").strip()
        odd = sel.get("odd")
        try:
            odd = float(odd)
        except Exception:
            continue

        label_norm = _norm(label)
        score = 0

        # Resultado final direcionado por time/empate.
        if _norm(home_name) and _norm(home_name) in msg and _norm(home_name) in label_norm:
            score += 7
        if _norm(away_name) and _norm(away_name) in msg and _norm(away_name) in label_norm:
            score += 7
        if any(k in msg for k in ["empate", "draw", "x"]) and any(k in label_norm for k in ["empate", "draw", "x"]):
            score += 7

        # Over/under orientado por linha mencionada.
        if parsed_line and parsed_line in label_norm:
            score += 5
        if any(k in msg for k in ["over", "mais de", "acima de"]) and any(k in label_norm for k in ["over", "mais de"]):
            score += 4
        if any(k in msg for k in ["under", "menos de", "abaixo de"]) and any(k in label_norm for k in ["under", "menos de"]):
            score += 4

        # Odds mais próximas de zona comum de valor ganham leve prioridade.
        score -= abs(odd - 1.95)

        scored.append((score, {"label": label, "odd": odd}))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _pick_offer_from_markets(match: Dict[str, Any], user_message: str) -> Optional[Dict[str, Any]]:
    markets = match.get("markets") or {}
    if not isinstance(markets, dict) or not markets:
        return None

    home_name = str(match.get("home") or "Casa")
    away_name = str(match.get("away") or "Fora")

    ranked_markets = sorted(
        markets.items(),
        key=lambda x: _score_market_name(str(x[0]), user_message),
        reverse=True,
    )

    # Evita monotonia: se não há intenção de mercado explícita, alterna entre top mercados.
    msg = _norm(user_message)
    has_market_intent = any(
        t in msg
        for t in ["resultado", "vencedor", "vence", "ganha", "ganhar", "gols", "over", "under", "ambas", "escanteio", "cart", "handicap"]
    )
    if not has_market_intent and len(ranked_markets) > 1:
        rotation = sum(ord(c) for c in msg) % min(3, len(ranked_markets))
        ranked_markets = ranked_markets[rotation:] + ranked_markets[:rotation]

    for market_name, selections in ranked_markets:
        picked = _pick_selection_for_market(
            market_name=str(market_name),
            selections=selections if isinstance(selections, list) else [],
            user_message=user_message,
            home_name=home_name,
            away_name=away_name,
        )
        if picked:
            return {
                "market": str(market_name),
                "pick": picked["label"],
                "odd": float(picked["odd"]),
            }

    return None


def _build_dynamic_cta_from_live_context(user_message: str = "") -> Optional[Dict[str, Any]]:
    """
    Fallback dinâmico para o botão do Edson quando não houver odds completas de mercado.
    """
    try:
        live_rows = get_live_matches_context(user_message, limit=1)
        if not live_rows:
            return None

        row = live_rows[0]
        match_id = str(row.get("match_id") or "").strip()
        home = str(row.get("home_team") or "Time Casa").strip()
        away = str(row.get("away_team") or "Time Fora").strip()
        if not match_id or match_id == "0":
            return None

        pick = home
        odd_value = 1.95

        try:
            saved_raw = rag_service.get_saved_analysis(match_id)
            saved = _coerce_analysis_dict(saved_raw)
            if isinstance(saved, dict):
                predicted = str(saved.get("predictedWinner") or "").strip()
                if predicted:
                    pick = predicted

                win = saved.get("winProbability") or {}
                probs = []
                for key in ("home", "draw", "away"):
                    try:
                        probs.append(float(win.get(key, 0)))
                    except Exception:
                        continue
                if probs:
                    max_prob = max(probs)
                    if max_prob > 0:
                        odd_value = round(max(1.05, 100.0 / max_prob), 2)
        except Exception:
            pass

        params = urlencode(
            {
                "market": "Resultado Final",
                "pick": pick,
                "odd": f"{odd_value:.2f}",
            }
        )

        return {
            "label": f"Apostar em {pick} @ {odd_value:.2f}",
            "href": f"/apostar/{match_id}?{params}",
            "variant": "bet",
            "matchId": match_id,
            "market": "Resultado Final",
            "pick": pick,
            "odd": odd_value,
            "home": home,
            "away": away,
        }
    except Exception as e:
        print(f"Erro no fallback de CTA dinâmico: {e}")
        return None


def _get_live_odds_matches(ttl_seconds: int = 30) -> List[Dict[str, Any]]:
    now = time.time()
    cached = _ODDS_CACHE.get("data")
    ts = float(_ODDS_CACHE.get("ts") or 0.0)
    if cached and (now - ts) <= ttl_seconds:
        return cached

    headers = {
        "Accept": "application/json, text/plain, */*",
        "languageid": "23",
        "device": "d",
        "customorigin": "https://esportesdasorte.bet.br",
        "bragiurl": "https://bragi.sportingtech.com/",
        "Origin": "https://esportesdasorte.bet.br",
        "Referer": "https://esportesdasorte.bet.br/",
    }
    params = {"languageId": "23", "deviceType": "d"}
    matches: List[Dict[str, Any]] = []

    try:
        response = requests.get(
            SPORTINGTECH_POPULAR_ODDS_URL,
            headers=headers,
            params=params,
            timeout=6,
        )
        response.raise_for_status()
        payload = response.json() or {}
        rows = payload.get("data") or []

        grouped: Dict[str, Dict[str, Any]] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue

            fid = str(item.get("fId") or "").strip()
            if not fid:
                continue

            fixture_info = str(item.get("fixtureInfo") or "")
            if " vs. " in fixture_info:
                home_name, away_name = fixture_info.split(" vs. ", 1)
            elif " vs " in fixture_info:
                home_name, away_name = fixture_info.split(" vs ", 1)
            else:
                home_name, away_name = "Time Casa", "Time Fora"

            market_name = str(item.get("betTypeGroupName") or item.get("betTypeName") or "").strip() or "Mercado"

            try:
                odd = float(item.get("odd"))
            except Exception:
                continue

            selection_name = str(item.get("selectionName") or "").strip()
            selection_norm = _norm(selection_name)

            entry = grouped.setdefault(
                fid,
                {
                    "id": fid,
                    "home": home_name.strip() or "Time Casa",
                    "away": away_name.strip() or "Time Fora",
                    "markets": {},
                },
            )

            market_bucket = entry["markets"].setdefault(market_name, [])
            if selection_name and not any(_norm(s.get("label")) == selection_norm for s in market_bucket):
                market_bucket.append({"label": selection_name, "odd": odd})

        matches = [m for m in grouped.values() if m.get("markets")]
    except Exception as e:
        print(f"Erro ao buscar odds no endpoint genérico da Sportingtech: {e}")

    # Fallback para API-v2 de live-fixture quando o endpoint genérico falhar/não retornar mercados.
    if not matches:
        try:
            encoded = _encode_sportingtech_body({"sportSelfUrlKey": "soccer", "timeRangeInHours": 24})
            live_url = f"https://esportesdasorte.bet.br/api-v2/live-fixture/d/23/esportesdasortevip/{encoded}"
            live_response = requests.get(
                live_url,
                headers={**headers, "encodedbody": encoded, "User-Agent": "Mozilla/5.0"},
                timeout=8,
            )
            live_response.raise_for_status()
            live_payload = live_response.json() or {}

            parsed_matches: List[Dict[str, Any]] = []
            sports = live_payload.get("data") or []
            for sport in sports:
                if not isinstance(sport, dict):
                    continue
                for cat in sport.get("cs") or []:
                    competition = str((cat or {}).get("cN") or "").strip()
                    for season in (cat or {}).get("sns") or []:
                        for fixture in (season or {}).get("fs") or []:
                            if not isinstance(fixture, dict):
                                continue

                            fid = str(fixture.get("fId") or "").strip()
                            if not fid:
                                continue

                            fixture_info = str(fixture.get("fixtureInfo") or "")
                            home_name = str(fixture.get("hcN") or "").strip()
                            away_name = str(fixture.get("acN") or "").strip()
                            if not home_name or not away_name:
                                if " vs. " in fixture_info:
                                    home_name, away_name = fixture_info.split(" vs. ", 1)
                                elif " vs " in fixture_info:
                                    home_name, away_name = fixture_info.split(" vs ", 1)

                            markets: Dict[str, List[Dict[str, Any]]] = {}
                            for btg in fixture.get("btgs") or []:
                                if not isinstance(btg, dict):
                                    continue
                                market_name = str(btg.get("btgN") or "Mercado").strip() or "Mercado"
                                selections: List[Dict[str, Any]] = []
                                for odd in btg.get("fos") or []:
                                    if not isinstance(odd, dict):
                                        continue
                                    label = str(odd.get("hSh") or odd.get("oN") or "").strip()
                                    if not label:
                                        continue
                                    try:
                                        odd_value = float(odd.get("hO"))
                                    except Exception:
                                        continue
                                    if not any(_norm(x.get("label")) == _norm(label) for x in selections):
                                        selections.append({"label": label, "odd": odd_value})

                                if selections:
                                    markets[market_name] = selections

                            if markets:
                                parsed_matches.append(
                                    {
                                        "id": fid,
                                        "home": home_name.strip() or "Time Casa",
                                        "away": away_name.strip() or "Time Fora",
                                        "competition": competition,
                                        "markets": markets,
                                    }
                                )

            if parsed_matches:
                matches = parsed_matches
        except Exception as e:
            print(f"Erro ao buscar odds no fallback live-fixture da Sportingtech: {e}")

    if matches:
        _ODDS_CACHE["ts"] = now
        _ODDS_CACHE["data"] = matches
        return matches

    return cached or []


def _select_match_for_cta(
    live_matches: List[Dict[str, Any]],
    user_message: str,
    db_context: List[Dict[str, Any]],
    upcoming_context: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    if not live_matches:
        return None

    msg = _norm(user_message)
    team_hint = " ".join(
        [
            _norm(row.get("time_casa")) + " " + _norm(row.get("time_fora"))
            for row in (db_context or [])[:4]
            if isinstance(row, dict)
        ]
    )
    upcoming_hint = " ".join(
        [
            _norm(row.get("home_team")) + " " + _norm(row.get("away_team"))
            for row in (upcoming_context or [])[:6]
            if isinstance(row, dict)
        ]
    )

    scored = []
    for match in live_matches:
        hay = f"{_norm(match.get('home'))} {_norm(match.get('away'))} {_norm(match.get('competition'))}"
        score = 0
        for token in re.findall(r"[a-z0-9]{4,}", msg):
            if token in hay:
                score += 3
        for token in re.findall(r"[a-z0-9]{4,}", team_hint):
            if token in hay:
                score += 1
        for token in re.findall(r"[a-z0-9]{4,}", upcoming_hint):
            if token in hay:
                score += 2
        scored.append((score, match))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored else live_matches[0]


def build_chat_cta(
    user_message: str = "",
    db_context: Optional[List[Dict[str, Any]]] = None,
    upcoming_context: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    default_cta: Dict[str, Any] = {
        "label": "Apostar Agora",
        "href": "/ao-vivo",
        "variant": "bet",
    }

    try:
        live_matches = _get_live_odds_matches()
        if not live_matches:
            # fallback com IDs compatíveis do frontend (detail-card por fixture)
            live_matches = _build_upcoming_sporting_matches_with_markets(upcoming_context or [], max_items=6)

        if _is_brazil_intent(user_message):
            br_matches = [m for m in live_matches if _is_brazil_row(m)]
            if br_matches:
                live_matches = br_matches
            else:
                # Evita CTA irrelevante para pedido específico de Brasileirão.
                return default_cta

        best_match = _select_match_for_cta(live_matches, user_message, db_context or [], upcoming_context or [])
        if not best_match:
            dynamic_fallback = _build_dynamic_cta_from_live_context(user_message)
            return dynamic_fallback or default_cta

        offer = _pick_offer_from_markets(best_match, user_message)
        if not offer:
            dynamic_fallback = _build_dynamic_cta_from_live_context(user_message)
            return dynamic_fallback or default_cta

        odd_value = float(offer["odd"])
        params = urlencode(
            {
                "market": offer["market"],
                "pick": offer["pick"],
                "odd": f"{odd_value:.2f}",
            }
        )

        return {
            "label": f"Apostar em {offer['pick']} @ {odd_value:.2f}",
            "href": f"/apostar/{best_match['id']}?{params}",
            "variant": "bet",
            "matchId": best_match["id"],
            "market": offer["market"],
            "pick": offer["pick"],
            "odd": odd_value,
        }
    except Exception as e:
        print(f"Erro ao montar CTA com odds ao vivo: {e}")
        return default_cta


def _encode_sportingtech_body(body: Dict[str, Any]) -> str:
    payload = json.dumps({"requestBody": body}, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(payload).decode("utf-8")


def _extract_fixture_from_detail_payload(payload: Dict[str, Any], fixture_id: str) -> Optional[Dict[str, Any]]:
    data = payload.get("data")
    wanted = str(fixture_id)

    if isinstance(data, dict):
        fixtures = data.get("fixtures") or data
        if isinstance(fixtures, list):
            for f in fixtures:
                if isinstance(f, dict) and str(f.get("fId")) == wanted:
                    return f
        elif isinstance(fixtures, dict):
            if str(fixtures.get("fId")) == wanted:
                return fixtures

    if isinstance(data, list):
        for sport in data:
            if not isinstance(sport, dict):
                continue
            for cat in sport.get("cs") or []:
                competition = str((cat or {}).get("cN") or "").strip()
                for season in (cat or {}).get("sns") or []:
                    for f in (season or {}).get("fs") or []:
                        if isinstance(f, dict) and str(f.get("fId")) == wanted:
                            f = dict(f)
                            if competition:
                                f["__competition"] = competition
                            return f

    return None


def get_sportingtech_fixture_match_with_markets(fixture_id: str, ttl_seconds: int = 120) -> Optional[Dict[str, Any]]:
    fid = str(fixture_id or "").strip()
    if not fid:
        return None

    now = time.time()
    cached = _FIXTURE_DETAIL_CACHE.get(fid)
    if cached and (now - float(cached.get("ts") or 0.0)) <= ttl_seconds:
        return cached.get("data")

    headers = {
        "Accept": "application/json, text/plain, */*",
        "languageid": "23",
        "device": "d",
        "customorigin": "https://esportesdasorte.bet.br",
        "bragiurl": "https://bragi.sportingtech.com/",
        "Origin": "https://esportesdasorte.bet.br",
        "Referer": "https://esportesdasorte.bet.br/",
        "User-Agent": "Mozilla/5.0",
    }

    try:
        fixture_id_int = int(fid)
    except Exception:
        fixture_id_int = fid

    encoded = _encode_sportingtech_body({"fixtureIds": [fixture_id_int]})
    url = f"https://esportesdasorte.bet.br/api-v2/detail-card/d/23/esportesdasortevip/{fid}/{encoded}"

    try:
        response = requests.get(url, headers={**headers, "encodedbody": encoded}, timeout=8)
        response.raise_for_status()
        payload = response.json() or {}
        fixture = _extract_fixture_from_detail_payload(payload, fid)
        if not fixture:
            return None

        fixture_info = str(fixture.get("fixtureInfo") or "")
        home_name = str(fixture.get("hcN") or "").strip()
        away_name = str(fixture.get("acN") or "").strip()
        if not home_name or not away_name:
            if " vs. " in fixture_info:
                home_name, away_name = fixture_info.split(" vs. ", 1)
            elif " vs " in fixture_info:
                home_name, away_name = fixture_info.split(" vs ", 1)

        markets: Dict[str, List[Dict[str, Any]]] = {}
        for btg in fixture.get("btgs") or []:
            if not isinstance(btg, dict):
                continue
            market_name = str(btg.get("btgN") or "Mercado").strip() or "Mercado"
            selections: List[Dict[str, Any]] = []
            for odd in btg.get("fos") or []:
                if not isinstance(odd, dict):
                    continue
                label = str(odd.get("hSh") or odd.get("oN") or "").strip()
                if not label:
                    continue
                try:
                    odd_value = float(odd.get("hO"))
                except Exception:
                    continue
                selections.append({"label": label, "odd": odd_value})

            if selections:
                markets[market_name] = selections

        if not markets:
            return None

        match_data = {
            "id": fid,
            "home": home_name.strip() or "Time Casa",
            "away": away_name.strip() or "Time Fora",
            "competition": str(fixture.get("__competition") or "").strip(),
            "markets": markets,
        }
        _FIXTURE_DETAIL_CACHE[fid] = {"ts": now, "data": match_data}
        return match_data
    except Exception as e:
        print(f"Erro ao buscar odds por fixture (Sportingtech detail-card {fid}): {e}")
        return None


def _build_upcoming_sporting_matches_with_markets(upcoming_context: List[Dict[str, Any]], max_items: int = 6) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for row in (upcoming_context or [])[: max_items * 2]:
        fid = str((row or {}).get("match_id") or "").strip()
        if not fid or not fid.isdigit():
            continue
        detail = get_sportingtech_fixture_match_with_markets(fid)
        if not detail:
            continue
        if not detail.get("competition"):
            detail["competition"] = str(row.get("competition") or "").strip()
        matches.append(detail)
        if len(matches) >= max_items:
            break
    return matches


def get_upcoming_matches_context(user_message: str, limit: int = 8, ttl_seconds: int = 90) -> List[Dict[str, Any]]:
    """
    Busca jogos futuros da mesma API do front para orientar recomendações de próximas partidas.
    """
    now = time.time()
    cached = _UPCOMING_CACHE.get("data")
    ts = float(_UPCOMING_CACHE.get("ts") or 0.0)
    if cached and (now - ts) <= ttl_seconds:
        all_rows = cached
    else:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "languageid": "23",
            "device": "d",
            "customorigin": "https://esportesdasorte.bet.br",
            "bragiurl": "https://bragi.sportingtech.com/",
            "Origin": "https://esportesdasorte.bet.br",
            "Referer": "https://esportesdasorte.bet.br/",
            "User-Agent": "Mozilla/5.0",
        }

        encoded = _encode_sportingtech_body({"sportSelfUrlKey": None})
        url = f"{SPORTINGTECH_UPCOMING_BASE_URL}/{encoded}"

        try:
            response = requests.get(url, headers={**headers, "encodedbody": encoded}, timeout=8)
            response.raise_for_status()
            payload = response.json() or {}

            parsed_rows: List[Dict[str, Any]] = []
            sports = payload.get("data") or []
            for sport in sports:
                sport_name = str((sport or {}).get("stN") or "Futebol").strip()
                categories = (sport or {}).get("cs") or []
                for cat in categories:
                    competition = str((cat or {}).get("cN") or "Competição").strip()
                    seasons = (cat or {}).get("sns") or []
                    for sn in seasons:
                        season_name = str((sn or {}).get("snN") or "Temporada").strip()
                        fixtures = (sn or {}).get("fs") or []
                        for f in fixtures:
                            if not isinstance(f, dict):
                                continue

                            fixture_info = str(f.get("fixtureInfo") or "")
                            home_name = str(f.get("hcN") or "").strip()
                            away_name = str(f.get("acN") or "").strip()
                            if not home_name or not away_name:
                                if " vs. " in fixture_info:
                                    home_name, away_name = fixture_info.split(" vs. ", 1)
                                elif " vs " in fixture_info:
                                    home_name, away_name = fixture_info.split(" vs ", 1)

                            parsed_rows.append(
                                {
                                    "match_id": str(f.get("fId") or "").strip(),
                                    "home_team": home_name.strip() or "Time Casa",
                                    "away_team": away_name.strip() or "Time Fora",
                                    "competition": competition,
                                    "season": season_name,
                                    "sport": sport_name,
                                    "kickoff": _format_kickoff(f.get("fDat") or f.get("fsd")),
                                }
                            )

            _UPCOMING_CACHE["ts"] = now
            _UPCOMING_CACHE["data"] = parsed_rows
            all_rows = parsed_rows
        except Exception as e:
            print(f"Erro ao buscar jogos futuros (Sportingtech): {e}")
            all_rows = cached or []

        # Fallback: BetsAPI upcoming quando Sportingtech não retornar jogos
        if not all_rows:
            try:
                bets_rows = rag_service.fetch_upcoming_matches() or []
                parsed_rows = []
                for row in bets_rows:
                    if not isinstance(row, dict):
                        continue
                    home = ((row.get("home") or {}).get("name") or row.get("home_name") or "Time Casa")
                    away = ((row.get("away") or {}).get("name") or row.get("away_name") or "Time Fora")
                    league = str((row.get("league") or {}).get("name") or row.get("league_name") or "Competição").strip()
                    kickoff = str(row.get("time") or row.get("time_str") or row.get("start_time") or "").strip()
                    parsed_rows.append(
                        {
                            "match_id": str(row.get("id") or row.get("FI") or "").strip(),
                            "home_team": str(home).strip(),
                            "away_team": str(away).strip(),
                            "competition": league,
                            "season": "2026",
                            "sport": "Futebol",
                            "kickoff": _format_kickoff(kickoff),
                        }
                    )

                all_rows = parsed_rows
                _UPCOMING_CACHE["ts"] = now
                _UPCOMING_CACHE["data"] = parsed_rows
            except Exception as e:
                print(f"Erro no fallback de jogos futuros (BetsAPI): {e}")

    if not all_rows:
        return []

    msg = _norm(user_message)

    scored = []
    for row in all_rows:
        score = _score_match_row(row, user_message, is_live=False)
        scored.append((score, row))

    # Se a pergunta mencionar "hoje", prioriza partidas com data de hoje.
    if _contains_any(msg, ["hoje"]):
        today = datetime.now().date()
        today_rows = [
            row
            for _, row in scored
            if _to_datetime(row.get("kickoff")) and _to_datetime(row.get("kickoff")).date() == today
        ]
        if today_rows:
            scored = [(_score_match_row(r, user_message, is_live=False) + 10, r) for r in today_rows]

    scored.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in scored[:limit]]


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


def _is_generic_no_data_reply(text: str) -> bool:
    t = _norm(text)
    if not t:
        return True
    patterns = [
        "nao tenho inform",
        "não tenho inform",
        "nao temos acesso",
        "não temos acesso",
        "verifique o calendario",
        "consulte o calendario",
        "sem informacoes",
        "sem informações",
    ]
    return any(p in t for p in patterns)


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


def build_chat_response(text: str, cta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cleaned = _sanitize_chat_output(text)
    return {
        "response": cleaned,
        "cta": cta or build_chat_cta(),
    }


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


def shorten_chat_text(text: str, max_chars: int = 420) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned

    truncated = cleaned[:max_chars].rsplit(" ", 1)[0].strip()
    if not truncated:
        truncated = cleaned[:max_chars]
    return f"{truncated}..."


def frontend_history_to_groq_messages(history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    role_map = {
        "user": "user",
        "assistant": "assistant",
        "model": "assistant",
        "system": "system",
    }
    messages = []

    for item in history or []:
        if not isinstance(item, dict):
            continue

        role = role_map.get(str(item.get("role", "user")).strip().lower(), "user")
        parts_raw = item.get("parts", [])

        if isinstance(parts_raw, str):
            content = parts_raw.strip()
        elif isinstance(parts_raw, list):
            chunks = []
            for part in parts_raw:
                if isinstance(part, dict):
                    text = part.get("text", "")
                    if text:
                        chunks.append(str(text))
                elif part is not None:
                    chunks.append(str(part))
            content = " ".join(chunks).strip()
        else:
            content = str(parts_raw).strip() if parts_raw is not None else ""

        if content:
            messages.append({"role": role, "content": content})

    return messages


def get_fallback_live_matches() -> List[Dict[str, Any]]:
    return [
        {"id": "802107412", "home": {"name": "Flamengo"}, "away": {"name": "Palmeiras"}, "ss": "1-1", "timer": {"tm": 65}},
        {"id": "673291882", "home": {"name": "Real Madrid"}, "away": {"name": "Barcelona"}, "ss": "2-0", "timer": {"tm": 40}},
        {"id": "992123512", "home": {"name": "Vasco da Gama"}, "away": {"name": "Botafogo"}, "ss": "0-1", "timer": {"tm": 85}},
    ]


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


@app.on_event("startup")
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


def get_chat_db_context(user_message: str, limit: int = 8) -> List[Dict[str, Any]]:
    """
    Busca contexto de partidas no Neon com base em termos da mensagem.
    Se não encontrar termos relevantes, retorna partidas recentes como fallback.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            terms = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ0-9]{4,}", user_message or "")]
            terms = list(dict.fromkeys(terms))[:6]

            if terms:
                like_terms = [f"%{t}%" for t in terms]
                sql = """
                    SELECT id_partida, competicao, temporada, time_casa, time_fora, gols_casa, gols_fora
                    FROM tb_partida_historico
                    WHERE LOWER(time_casa) LIKE ANY(%s)
                       OR LOWER(time_fora) LIKE ANY(%s)
                       OR LOWER(competicao) LIKE ANY(%s)
                    ORDER BY id_partida DESC
                    LIMIT %s
                """
                cur.execute(sql, (like_terms, like_terms, like_terms, limit))
            else:
                sql = """
                    SELECT id_partida, competicao, temporada, time_casa, time_fora, gols_casa, gols_fora
                    FROM tb_partida_historico
                    ORDER BY id_partida DESC
                    LIMIT %s
                """
                cur.execute(sql, (limit,))

            return cur.fetchall() or []
    except Exception as e:
        print(f"Erro ao montar contexto de chat no DB: {e}")
        return []
    finally:
        if conn:
            conn.close()


def get_fbref_db_context(user_message: str, season: str = "2025/2026", limit: int = 8) -> List[Dict[str, Any]]:
    """
    Busca contexto de jogadores/times no dataset FBref importado no Neon.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            terms = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ0-9]{3,}", user_message or "")]
            terms = list(dict.fromkeys(terms))[:8]

            if terms:
                like_terms = [f"%{t}%" for t in terms]
                sql = """
                    SELECT player, squad, competition, season, position, goals, assists, minutes
                    FROM tb_fbref_player_stats
                    WHERE season = %s
                      AND (
                           LOWER(player) LIKE ANY(%s)
                        OR LOWER(squad) LIKE ANY(%s)
                        OR LOWER(competition) LIKE ANY(%s)
                        OR LOWER(COALESCE(position, '')) LIKE ANY(%s)
                      )
                    ORDER BY COALESCE(goals, 0) DESC, COALESCE(assists, 0) DESC, COALESCE(minutes, 0) DESC
                    LIMIT %s
                """
                cur.execute(sql, (season, like_terms, like_terms, like_terms, like_terms, limit))
            else:
                sql = """
                    SELECT player, squad, competition, season, position, goals, assists, minutes
                    FROM tb_fbref_player_stats
                    WHERE season = %s
                    ORDER BY COALESCE(goals, 0) DESC, COALESCE(assists, 0) DESC, COALESCE(minutes, 0) DESC
                    LIMIT %s
                """
                cur.execute(sql, (season, limit))

            return cur.fetchall() or []
    except Exception as e:
        print(f"Erro ao montar contexto FBref no DB: {e}")
        return []
    finally:
        if conn:
            conn.close()


def get_web_context(user_message: str, limit: int = 5) -> List[Dict[str, str]]:
    """
    Busca contexto público na web (DuckDuckGo + Wikipedia) para perguntas fora do banco.
    """
    query = (user_message or "").strip()
    if not query:
        return []

    results: List[Dict[str, str]] = []
    seen = set()
    web_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    # Fonte 1: DuckDuckGo Instant Answer API
    try:
        ddg_resp = requests.get(
            "https://api.duckduckgo.com/",
            params={
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
                "kl": "br-pt",
            },
            headers=web_headers,
            timeout=6,
        )
        ddg_resp.raise_for_status()
        payload = ddg_resp.json() or {}

        abstract = str(payload.get("AbstractText") or "").strip()
        abstract_url = str(payload.get("AbstractURL") or "").strip()
        heading = str(payload.get("Heading") or "DuckDuckGo").strip() or "DuckDuckGo"
        if abstract:
            key = (heading.lower(), abstract.lower())
            if key not in seen:
                seen.add(key)
                results.append({"title": heading, "snippet": abstract, "url": abstract_url or "https://duckduckgo.com"})

        related = payload.get("RelatedTopics") or []
        for item in related:
            if len(results) >= limit:
                break

            text = ""
            url = ""
            title = "DuckDuckGo"
            if isinstance(item, dict):
                if isinstance(item.get("Topics"), list):
                    for sub in item.get("Topics"):
                        if len(results) >= limit:
                            break
                        if not isinstance(sub, dict):
                            continue
                        text = str(sub.get("Text") or "").strip()
                        url = str(sub.get("FirstURL") or "").strip()
                        if text:
                            title = text.split(" - ", 1)[0][:90]
                            key = (title.lower(), text.lower())
                            if key not in seen:
                                seen.add(key)
                                results.append({"title": title, "snippet": text, "url": url or "https://duckduckgo.com"})
                else:
                    text = str(item.get("Text") or "").strip()
                    url = str(item.get("FirstURL") or "").strip()
                    if text:
                        title = text.split(" - ", 1)[0][:90]
                        key = (title.lower(), text.lower())
                        if key not in seen:
                            seen.add(key)
                            results.append({"title": title, "snippet": text, "url": url or "https://duckduckgo.com"})
    except Exception as e:
        print(f"Erro no fallback web (DuckDuckGo): {e}")

    # Fonte 1.1: DuckDuckGo HTML (fallback quando API não trouxer conteúdo)
    if len(results) < limit:
        try:
            html_resp = requests.get(
                "https://duckduckgo.com/html/",
                params={"q": query, "kl": "br-pt"},
                headers=web_headers,
                timeout=6,
            )
            html_resp.raise_for_status()
            html = html_resp.text or ""

            pattern = re.compile(
                r'<a[^>]*class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
                re.IGNORECASE | re.DOTALL,
            )

            for match in pattern.finditer(html):
                if len(results) >= limit:
                    break
                raw_title = re.sub(r"<.*?>", "", match.group("title") or "").strip()
                raw_snippet = re.sub(r"<.*?>", "", match.group("snippet") or "").strip()
                raw_url = (match.group("url") or "").strip()
                if not raw_title or not raw_snippet:
                    continue

                key = (raw_title.lower(), raw_snippet.lower())
                if key in seen:
                    continue
                seen.add(key)
                results.append({"title": raw_title[:120], "snippet": raw_snippet, "url": raw_url or "https://duckduckgo.com"})
        except Exception as e:
            print(f"Erro no fallback web (DuckDuckGo HTML): {e}")

    # Fonte 2: Wikipedia search + summary
    if len(results) < limit:
        try:
            search_resp = requests.get(
                "https://pt.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": 3,
                    "format": "json",
                },
                headers=web_headers,
                timeout=6,
            )
            search_resp.raise_for_status()
            search_payload = search_resp.json() or {}
            search_items = (((search_payload.get("query") or {}).get("search")) or [])[:3]

            for item in search_items:
                if len(results) >= limit:
                    break
                title = str(item.get("title") or "").strip()
                if not title:
                    continue

                summary_resp = requests.get(
                    f"https://pt.wikipedia.org/api/rest_v1/page/summary/{title}",
                    headers=web_headers,
                    timeout=6,
                )
                if not summary_resp.ok:
                    continue
                summary_payload = summary_resp.json() or {}
                snippet = str(summary_payload.get("extract") or "").strip()
                url = str((((summary_payload.get("content_urls") or {}).get("desktop") or {}).get("page")) or "").strip()
                if not snippet:
                    continue

                key = (title.lower(), snippet.lower())
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    "title": title,
                    "snippet": snippet,
                    "url": url or f"https://pt.wikipedia.org/wiki/{title.replace(' ', '_')}",
                })
        except Exception as e:
            print(f"Erro no fallback web (Wikipedia): {e}")

    # Fonte 3: Wikipedia em inglês como último fallback
    if len(results) < limit:
        try:
            search_resp_en = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": 3,
                    "format": "json",
                },
                headers=web_headers,
                timeout=6,
            )
            search_resp_en.raise_for_status()
            search_payload_en = search_resp_en.json() or {}
            search_items_en = (((search_payload_en.get("query") or {}).get("search")) or [])[:3]

            for item in search_items_en:
                if len(results) >= limit:
                    break
                title = str(item.get("title") or "").strip()
                if not title:
                    continue

                summary_resp_en = requests.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
                    headers=web_headers,
                    timeout=6,
                )
                if not summary_resp_en.ok:
                    continue
                summary_payload_en = summary_resp_en.json() or {}
                snippet = str(summary_payload_en.get("extract") or "").strip()
                url = str((((summary_payload_en.get("content_urls") or {}).get("desktop") or {}).get("page")) or "").strip()
                if not snippet:
                    continue

                key = (title.lower(), snippet.lower())
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    "title": title,
                    "snippet": snippet,
                    "url": url or f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                })
        except Exception as e:
            print(f"Erro no fallback web (Wikipedia EN): {e}")

    return results[:limit]

# ==========================================
# ROTAS DE USUÁRIOS
# ==========================================

JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_hex(32))
security = HTTPBearer()

def create_access_token(user_id: int, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.utcnow() + timedelta(hours=24),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

async def get_current_user(token=Depends(security)):
    try:
        payload = jwt.decode(token.credentials, JWT_SECRET, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Token inválido")

@app.post("/api/login", tags=["Usuários"])
def validar_login(credenciais: LoginDados):
    conn = None
    try:
        email_normalized = str(credenciais.email_usuario or "").strip().lower()
        conn = get_db_connection()
        with conn.cursor() as cur:
            sql = """SELECT id_usuario, nome_usuario, email_usuario, cpf_usuario, telefone_usuario, senha_usuario
                     FROM tb_usuario
                     WHERE email_usuario = %s"""
            cur.execute(sql, (email_normalized,))
            usuario = cur.fetchone()

            if usuario and _verify_password(credenciais.senha_usuario, usuario.get("senha_usuario")):
                # Migra senha legada em texto plano para hash sem interromper login.
                if "$" not in str(usuario.get("senha_usuario") or ""):
                    try:
                        new_hash = _hash_password(credenciais.senha_usuario)
                        cur.execute(
                            "UPDATE tb_usuario SET senha_usuario = %s WHERE id_usuario = %s",
                            (new_hash, usuario["id_usuario"]),
                        )
                        conn.commit()
                    except Exception as e:
                        print(f"Falha ao migrar hash de senha legada: {e}")

                safe_user = {
                    "id_usuario": usuario["id_usuario"],
                    "nome_usuario": usuario["nome_usuario"],
                    "email_usuario": usuario["email_usuario"],
                    "cpf_usuario": usuario.get("cpf_usuario"),
                    "telefone_usuario": usuario.get("telefone_usuario"),
                }
                token = create_access_token(usuario["id_usuario"], usuario["email_usuario"])
                return {
                    "sucesso": True, 
                    "mensagem": f"Bem-vindo(a), {usuario['nome_usuario']}!", 
                    "usuario": safe_user,
                    "access_token": token,
                    "token_type": "bearer"
                }
            return {"sucesso": False, "erro": "E-mail ou senha incorretos."}
    except Exception as erro:
        return {"sucesso": False, "erro": f"Erro no servidor: {str(erro)}"}
    finally:
        if conn: conn.close()


@app.post("/api/usuarios", tags=["Usuários"])
def criar_usuario(novo_usuario: Usuario):
    conn = None
    try:
        validation_error = _validate_signup_payload(novo_usuario)
        if validation_error:
            return {"sucesso": False, "erro": validation_error}

        email_normalized = str(novo_usuario.email_usuario or "").strip().lower()
        cpf_normalized = _only_digits(novo_usuario.cpf_usuario)
        phone_normalized = _only_digits(novo_usuario.telefone_usuario)
        senha_hash = _hash_password(novo_usuario.senha_usuario)

        conn = get_db_connection()
        with conn.cursor() as cur:
            sql = """INSERT INTO tb_usuario 
                     (nome_usuario, email_usuario, cpf_usuario, dataNac_usuario, endereco_usuario, telefone_usuario, senha_usuario)
                     VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id_usuario"""
            valores = (
                novo_usuario.nome_usuario, email_normalized, cpf_normalized,
                novo_usuario.dataNac_usuario, novo_usuario.endereco_usuario,
                phone_normalized, senha_hash
            )
            cur.execute(sql, valores)
            id_gerado = cur.fetchone()['id_usuario']
            conn.commit()
            return {"sucesso": True, "mensagem": "Usuário cadastrado com sucesso!", "id_gerado": id_gerado}
    except psycopg2.IntegrityError:
        return {"sucesso": False, "erro": "E-mail ou CPF já cadastrado."}
    except Exception as erro:
        return {"sucesso": False, "erro": str(erro)}
    finally:
        if conn: conn.close()


@app.get("/api/usuarios", tags=["Usuários"])
def listar_usuarios():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id_usuario, nome_usuario, email_usuario, criado_em FROM tb_usuario ORDER BY id_usuario DESC")
            usuarios = cur.fetchall()
            return {"sucesso": True, "quantidade": len(usuarios), "usuarios": usuarios}
    except Exception as erro:
        return {"sucesso": False, "erro": str(erro)}
    finally:
        if conn: conn.close()


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
