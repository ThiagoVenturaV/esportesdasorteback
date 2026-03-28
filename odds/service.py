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

