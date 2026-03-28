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


def build_chat_response(text: str, cta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cleaned = _sanitize_chat_output(text)
    return {
        "response": cleaned,
        "cta": cta or build_chat_cta(),
    }

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

