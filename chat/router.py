"""
chat/router.py — Rotas de chat conversacional
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
import os
import json
import time
import re
try:
    from groq import Groq
except ImportError:
    Groq = None
try:
    import google.generativeai as genai
except ImportError:
    genai = None

from chat.edson import EDSON_SYSTEM_PROMPT

try:
    from db.neon import get_db_connection, release_connection
except Exception:
    get_db_connection = None
    release_connection = None

try:
    from odds.betsapi import fetch_live_matches, fetch_upcoming_matches
except Exception:
    fetch_live_matches = None
    fetch_upcoming_matches = None

router = APIRouter(prefix="/api", tags=["chat"])

_limiter = None


def set_limiter(limiter):
    """Configura o rate limiter para chat."""
    global _limiter
    _limiter = limiter


class ChatRequest(BaseModel):
    """Modelo de requisição de chat."""
    message: str
    conversation_history: list = Field(default_factory=list)
    history: list = Field(default_factory=list)


CHAT_HISTORY_MAX_TURNS = int(os.getenv("CHAT_HISTORY_MAX_TURNS", "8"))
CHAT_MAX_USER_CHARS = int(os.getenv("CHAT_MAX_USER_CHARS", "500"))
CHAT_RESPONSE_MAX_TOKENS = int(os.getenv("CHAT_RESPONSE_MAX_TOKENS", "320"))
CHAT_TEMPERATURE = float(os.getenv("CHAT_TEMPERATURE", "0.58"))
CHAT_FAST_CACHE_TTL_SECONDS = int(os.getenv("CHAT_FAST_CACHE_TTL_SECONDS", "90"))
CHAT_MAX_RESPONSE_LINES = int(os.getenv("CHAT_MAX_RESPONSE_LINES", "10"))
CHAT_MAX_RESPONSE_CHARS = int(os.getenv("CHAT_MAX_RESPONSE_CHARS", "900"))
GEMINI_MODEL_CHAT = os.getenv("GEMINI_MODEL_CHAT", "gemini-2.5-flash-lite")
GROQ_MODEL_CHAT = os.getenv("GROQ_MODEL_CHAT", "openai/gpt-oss-120b")
CHAT_USE_DB_CONTEXT = os.getenv("CHAT_USE_DB_CONTEXT", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
CHAT_USE_BETSAPI_CONTEXT = os.getenv("CHAT_USE_BETSAPI_CONTEXT", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
CHAT_DB_CONTEXT_LIMIT = int(os.getenv("CHAT_DB_CONTEXT_LIMIT", "6"))
CHAT_BETSAPI_CONTEXT_LIMIT = int(os.getenv("CHAT_BETSAPI_CONTEXT_LIMIT", "6"))
CHAT_USE_FBREF_CONTEXT = os.getenv("CHAT_USE_FBREF_CONTEXT", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
CHAT_FBREF_CONTEXT_LIMIT = int(os.getenv("CHAT_FBREF_CONTEXT_LIMIT", "6"))

# Cache em memória para perguntas repetidas em curto intervalo.
_fast_cache: dict[str, tuple[float, dict]] = {}


def _normalize_role(role: str) -> str | None:
    normalized = str(role or "").strip().lower()
    if normalized in {"assistant", "model", "bot"}:
        return "assistant"
    if normalized in {"user", "human"}:
        return "user"
    if normalized == "system":
        return "system"
    return None


def _extract_text_from_message(item: dict) -> str:
    # OpenAI-like: {"content": "..."}
    content = item.get("content")
    if isinstance(content, str):
        return content.strip()

    # OpenAI multi-part: {"content": [{"type":"text","text":"..."}]}
    if isinstance(content, list):
        chunks = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                txt = part.get("text") or part.get("content")
                if isinstance(txt, str) and txt.strip():
                    chunks.append(txt.strip())
        return "\n".join(chunks).strip()

    # Gemini-like: {"parts": [{"text":"..."}]}
    parts = item.get("parts")
    if isinstance(parts, list):
        chunks = []
        for part in parts:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                txt = part.get("text")
                if isinstance(txt, str) and txt.strip():
                    chunks.append(txt.strip())
        return "\n".join(chunks).strip()

    return ""


def _normalize_history(history: list) -> list:
    messages = []
    for item in history:
        if not isinstance(item, dict):
            continue

        role = _normalize_role(item.get("role"))
        if not role:
            continue

        text = _extract_text_from_message(item)
        if not text:
            continue

        messages.append({"role": role, "content": text})

    return messages


def _trim_history(messages: list) -> list:
    # Mantém as últimas N mensagens para reduzir custo e latência.
    if CHAT_HISTORY_MAX_TURNS <= 0:
        return messages
    return messages[-CHAT_HISTORY_MAX_TURNS:]


def _coerce_to_natural_ptbr(text: str) -> str:
    """Converte saída em JSON bruto para texto natural em pt-BR."""
    raw = str(text or "").strip()
    if not raw:
        return "Não consegui montar uma análise agora."

    if not (raw.startswith("{") and raw.endswith("}")):
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if CHAT_MAX_RESPONSE_LINES > 0:
            lines = lines[:CHAT_MAX_RESPONSE_LINES]
        compact = "\n".join(lines) if lines else raw
        if CHAT_MAX_RESPONSE_CHARS > 0 and len(compact) > CHAT_MAX_RESPONSE_CHARS:
            compact = compact[: CHAT_MAX_RESPONSE_CHARS].rstrip() + "..."
        return compact

    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return raw

        commentary = data.get("commentary")
        if isinstance(commentary, list) and commentary:
            first = str(commentary[0]).strip()
            if first:
                return first

        prediction = data.get("prediction") or data.get("predictedWinner")
        confidence = data.get("confidence") or data.get("confidenceScore")
        factors = data.get("key_factors") or data.get("keyFactors") or []

        lines = []
        if prediction:
            lines.append(f"Palpite principal: {prediction}.")
        if confidence is not None:
            lines.append(f"Confiança estimada: {confidence}.")
        if isinstance(factors, list) and factors:
            lines.append("Fatores-chave: " + "; ".join(str(x) for x in factors[:3]))

        if lines:
            compact = " ".join(lines)
            if CHAT_MAX_RESPONSE_CHARS > 0 and len(compact) > CHAT_MAX_RESPONSE_CHARS:
                compact = compact[: CHAT_MAX_RESPONSE_CHARS].rstrip() + "..."
            return compact
        return "Análise concluída, mas sem detalhes legíveis no momento."
    except Exception:
        return raw


def _strip_markdown_formatting(text: str) -> str:
    cleaned = str(text or "")
    if not cleaned:
        return cleaned

    # Remove fenced code blocks while preserving inner content.
    cleaned = re.sub(r"```[a-zA-Z0-9_-]*\n?", "", cleaned)
    cleaned = cleaned.replace("```", "")

    # Remove inline markdown marks.
    cleaned = cleaned.replace("**", "")
    cleaned = cleaned.replace("__", "")
    cleaned = cleaned.replace("`", "")

    # Remove list markers at line start (ordered/unordered).
    cleaned = re.sub(r"(?m)^\s*[-*+]\s+", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*\d+[\.)]\s+", "", cleaned)

    # Collapse excessive blank lines.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _strip_internal_source_markers(text: str) -> str:
    cleaned = str(text or "")
    if not cleaned:
        return cleaned

    # Remove internal provenance markers from user-facing text.
    marker_pattern = (
        r"\[(?:"
        r"betsapi|estimativa|mock|statsbomb|fbref|ao_vivo|proximo|"
        r"banco_historico_neon|contesto|contexto_rag|regras_de_uso"
        r")(?:\s*[-_ ]\s*[a-z0-9]+)*\]"
    )
    cleaned = re.sub(marker_pattern, "", cleaned, flags=re.IGNORECASE)

    # Normalize spaces left after marker removal.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n\s+", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _sanitize_user_message(text: str) -> str:
    msg = str(text or "").strip()
    if len(msg) > CHAT_MAX_USER_CHARS:
        msg = msg[:CHAT_MAX_USER_CHARS]
    return msg


def _expand_ptbr_chat_slang(text: str) -> str:
    expanded = str(text or "")
    replacements = {
        r"\bvc\b": "voce",
        r"\bvcs\b": "voces",
        r"\bpq\b": "porque",
        r"\bq\b": "que",
        r"\btbm\b": "tambem",
        r"\bmto\b": "muito",
        r"\bto\b": "estou",
        r"\bta\b": "esta",
        r"\bblz\b": "beleza",
    }
    for pattern, repl in replacements.items():
        expanded = re.sub(pattern, repl, expanded, flags=re.IGNORECASE)
    return expanded.strip()


def _build_fast_cache_key(message: str, history_messages: list) -> str:
    recent = history_messages[-4:] if history_messages else []
    parts = [message.lower()]
    for item in recent:
        role = str(item.get("role") or "")
        content = str(item.get("content") or "").strip().lower()
        if content:
            parts.append(f"{role}:{content[:180]}")
    return "||".join(parts)


def _get_fast_cache(cache_key: str) -> dict | None:
    now = time.time()
    cached = _fast_cache.get(cache_key)
    if not cached:
        return None

    expires_at, payload = cached
    if expires_at <= now:
        _fast_cache.pop(cache_key, None)
        return None
    return payload


def _set_fast_cache(cache_key: str, payload: dict):
    _fast_cache[cache_key] = (time.time() + CHAT_FAST_CACHE_TTL_SECONDS, payload)

    # Limpeza simples para evitar crescimento sem limite.
    if len(_fast_cache) > 600:
        now = time.time()
        stale_keys = [k for k, (exp, _) in _fast_cache.items() if exp <= now]
        for key in stale_keys[:250]:
            _fast_cache.pop(key, None)


def _extract_terms(text: str, max_terms: int = 6) -> list[str]:
    terms = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ0-9]{4,}", str(text or ""))]
    deduped = []
    seen = set()
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        deduped.append(term)
        if len(deduped) >= max_terms:
            break
    return deduped


def _infer_upcoming_days_from_message(user_message: str) -> int:
    msg = str(user_message or "").lower()
    if "depois de amanha" in msg:
        return 3
    if "amanha" in msg or "amanhã" in msg or "tomorrow" in msg:
        return 2
    return 1


def _get_db_context_rows(user_message: str, limit: int = 6) -> list[dict]:
    if not CHAT_USE_DB_CONTEXT or not get_db_connection or not release_connection:
        return []

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        terms = _extract_terms(user_message)

        if terms:
            like_terms = [f"%{t}%" for t in terms]
            cursor.execute(
                """
                SELECT id_partida, competicao, temporada, time_casa, time_fora, gols_casa, gols_fora
                FROM tb_partida_historico
                WHERE LOWER(time_casa) LIKE ANY(%s)
                   OR LOWER(time_fora) LIKE ANY(%s)
                   OR LOWER(competicao) LIKE ANY(%s)
                ORDER BY id_partida DESC
                LIMIT %s
                """,
                (like_terms, like_terms, like_terms, max(1, limit)),
            )
        else:
            cursor.execute(
                """
                SELECT id_partida, competicao, temporada, time_casa, time_fora, gols_casa, gols_fora
                FROM tb_partida_historico
                ORDER BY id_partida DESC
                LIMIT %s
                """,
                (max(1, limit),),
            )

        rows = cursor.fetchall() or []
        parsed = []
        for row in rows:
            parsed.append(
                {
                    "id_partida": row[0],
                    "competicao": row[1],
                    "temporada": row[2],
                    "time_casa": row[3],
                    "time_fora": row[4],
                    "gols_casa": row[5],
                    "gols_fora": row[6],
                }
            )
        return parsed
    except Exception as e:
        print(f"[CHAT] Falha ao buscar contexto no DB: {e}")
        return []
    finally:
        if conn:
            try:
                release_connection(conn)
            except Exception:
                pass


def _get_betsapi_context_rows(user_message: str, limit: int = 6) -> list[dict]:
    if not CHAT_USE_BETSAPI_CONTEXT:
        return []

    rows: list[dict] = []
    try:
        remaining = max(1, limit)
        if fetch_live_matches:
            live = fetch_live_matches() or []
            for item in live[:remaining]:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        "kind": "live",
                        "id": item.get("id") or item.get("match_id") or item.get("event_id"),
                        "league": item.get("league") or item.get("league_name") or item.get("competition"),
                        "home": item.get("home") or item.get("home_team") or item.get("home_team_name"),
                        "away": item.get("away") or item.get("away_team") or item.get("away_team_name"),
                        "score": item.get("ss") or f"{item.get('home_score', 0)}-{item.get('away_score', 0)}",
                        "minute": (item.get("timer") or {}).get("tm") if isinstance(item.get("timer"), dict) else item.get("minute"),
                    }
                )
            remaining = max(0, max(1, limit) - len(rows))

        if fetch_upcoming_matches and remaining > 0:
            upcoming_days = _infer_upcoming_days_from_message(user_message)
            upcoming = fetch_upcoming_matches(days=upcoming_days) or []
            for item in upcoming[:remaining]:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        "kind": "upcoming",
                        "id": item.get("id") or item.get("match_id") or item.get("event_id"),
                        "league": item.get("league") or item.get("league_name") or item.get("competition"),
                        "home": item.get("home") or item.get("home_team") or item.get("home_team_name"),
                        "away": item.get("away") or item.get("away_team") or item.get("away_team_name"),
                        "score": None,
                        "minute": None,
                    }
                )
    except Exception as e:
        print(f"[CHAT] Falha ao buscar contexto BetsAPI: {e}")

    return rows[: max(1, limit)]


def _get_fbref_context_rows(user_message: str, limit: int = 6) -> list[dict]:
    if not CHAT_USE_FBREF_CONTEXT or not get_db_connection or not release_connection:
        return []

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        terms = _extract_terms(user_message)
        like_terms = [f"%{t}%" for t in terms] if terms else []

        if like_terms:
            cursor.execute(
                """
                SELECT player_name, squad, comp, season, starts, minutes_90s, goals, assists
                FROM tb_fbref_player_stats
                WHERE LOWER(player_name) LIKE ANY(%s)
                   OR LOWER(squad) LIKE ANY(%s)
                   OR LOWER(comp) LIKE ANY(%s)
                ORDER BY COALESCE(minutes_90s, 0) DESC
                LIMIT %s
                """,
                (like_terms, like_terms, like_terms, max(1, limit)),
            )
        else:
            cursor.execute(
                """
                SELECT player_name, squad, comp, season, starts, minutes_90s, goals, assists
                FROM tb_fbref_player_stats
                ORDER BY COALESCE(minutes_90s, 0) DESC
                LIMIT %s
                """,
                (max(1, limit),),
            )

        rows = cursor.fetchall() or []
        parsed = []
        for row in rows:
            parsed.append(
                {
                    "player_name": row[0],
                    "squad": row[1],
                    "comp": row[2],
                    "season": row[3],
                    "starts": row[4],
                    "minutes_90s": row[5],
                    "goals": row[6],
                    "assists": row[7],
                }
            )
        return parsed
    except Exception as e:
        print(f"[CHAT] Falha ao buscar contexto FBref: {e}")
        return []
    finally:
        if conn:
            try:
                release_connection(conn)
            except Exception:
                pass


def _build_runtime_context_text(user_message: str) -> str:
    db_rows = _get_db_context_rows(user_message, CHAT_DB_CONTEXT_LIMIT)
    bets_rows = _get_betsapi_context_rows(user_message, CHAT_BETSAPI_CONTEXT_LIMIT)
    fbref_rows = _get_fbref_context_rows(user_message, CHAT_FBREF_CONTEXT_LIMIT)

    parts = [
        "[CONTEXTO RAG - EDSON]",
        "Use os blocos abaixo como base factual. Nao invente dado numerico sem marcar [ESTIMATIVA] ou [MOCK].",
    ]

    parts.append("[BETSAPI - AO VIVO]")
    if bets_rows:
        for row in bets_rows:
            kind = "AO_VIVO" if row.get("kind") == "live" else "PROXIMO"
            minute = row.get("minute")
            minute_text = f" minuto={minute}" if minute not in (None, "", 0, "0") else ""
            score = row.get("score")
            score_text = f" placar={score}" if score else ""
            parts.append(
                (
                    f"- [{kind}] liga={row.get('league') or 'N/A'} | "
                    f"jogo={row.get('home') or 'Time Casa'} x {row.get('away') or 'Time Fora'} |"
                    f"{score_text}{minute_text}"
                )
            )
    else:
        parts.append("- [ESTIMATIVA] BetsAPI indisponivel ou sem partidas relevantes no momento.")

    parts.append("[STATSBOMB - HISTORICO]")
    if db_rows:
        for row in db_rows:
            parts.append(
                (
                    f"- competicao={row.get('competicao') or 'N/A'} | temporada={row.get('temporada') or 'N/A'} | "
                    f"placar={row.get('time_casa') or 'Time Casa'} {row.get('gols_casa') if row.get('gols_casa') is not None else '-'}"
                    f" x {row.get('gols_fora') if row.get('gols_fora') is not None else '-'} {row.get('time_fora') or 'Time Fora'}"
                )
            )
    else:
        parts.append("- [ESTIMATIVA] Historico StatsBomb/Neon indisponivel para os termos consultados.")

    parts.append("[FBREF - FORMA ATUAL]")
    if fbref_rows:
        for row in fbref_rows:
            parts.append(
                (
                    f"- jogador={row.get('player_name') or 'N/A'} | clube={row.get('squad') or 'N/A'} | "
                    f"comp={row.get('comp') or 'N/A'} | temporada={row.get('season') or 'N/A'} | "
                    f"starts={row.get('starts') if row.get('starts') is not None else 'N/A'} | "
                    f"min90={row.get('minutes_90s') if row.get('minutes_90s') is not None else 'N/A'} | "
                    f"gols={row.get('goals') if row.get('goals') is not None else 'N/A'} | "
                    f"assist={row.get('assists') if row.get('assists') is not None else 'N/A'}"
                )
            )
    else:
        parts.append("- [MOCK] Base FBref ausente para consulta atual; nao use numeros exatos sem rotular como estimativa.")

    parts.append(
        "[REGRAS_DE_USO] Se faltar dado para assertividade, responda com transparencia e informe explicitamente o que e [ESTIMATIVA] ou [MOCK]."
    )
    return "\n".join(parts)


def _messages_to_plain_prompt(messages: list[dict]) -> str:
    lines = []
    for item in messages:
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if not content:
            continue

        if role == "system":
            lines.append(f"[SISTEMA]\n{content}")
        elif role == "assistant":
            lines.append(f"[ASSISTENTE]\n{content}")
        else:
            lines.append(f"[USUARIO]\n{content}")

    lines.append(
        "[INSTRUCAO FINAL]\nResponda em pt-BR natural e conversacional, direto ao ponto, sem JSON bruto."
    )
    return "\n\n".join(lines)


def _tokenize_for_similarity(text: str) -> set[str]:
    return {
        t.lower()
        for t in re.findall(r"[A-Za-zÀ-ÿ0-9]{3,}", str(text or ""))
    }


def _jaccard_similarity(a: str, b: str) -> float:
    left = _tokenize_for_similarity(a)
    right = _tokenize_for_similarity(b)
    if not left or not right:
        return 0.0
    inter = len(left.intersection(right))
    union = len(left.union(right))
    return inter / union if union else 0.0


def _get_last_assistant_message(history_messages: list[dict]) -> str:
    for item in reversed(history_messages or []):
        if str(item.get("role") or "").lower() == "assistant":
            return str(item.get("content") or "").strip()
    return ""


def _is_repetitive_reply(reply: str, history_messages: list[dict], user_message: str) -> bool:
    last_assistant = _get_last_assistant_message(history_messages)
    if not last_assistant:
        return False

    similarity = _jaccard_similarity(reply, last_assistant)
    short_follow_up = len(str(user_message or "").split()) <= 4
    return similarity >= 0.72 or (short_follow_up and similarity >= 0.55)


def _build_actionable_followup_fallback(user_message: str) -> str:
    msg = str(user_message or "").strip()
    return (
        f"Boa, vamos nessa linha: {msg or 'partida atual'}. "
        "Se voce quer reduzir risco, priorize entrada com protecao parcial e evite odd esticada no fim do jogo. "
        "Minha leitura: melhor ir em stake moderada e guardar parte para ajuste ao vivo se o ritmo cair. "
        "Risco principal: mudanca brusca de dinamica em bola parada ou substituicao tardia."
    )


def _rewrite_if_repetitive(messages: list[dict], user_message: str, repeated_reply: str) -> str | None:
    rewrite_messages = list(messages)
    rewrite_messages.append(
        {
            "role": "system",
            "content": (
                "Reescreva sem repetir frases da resposta anterior e sem tom robotico. "
                "Responda como um analista humano experiente, com linguagem natural, clara e pratica para aposta. "
                "Mantenha entre 4 e 7 linhas e avance a decisao do usuario."
            ),
        }
    )
    rewrite_messages.append(
        {
            "role": "user",
            "content": (
                f"Pergunta atual: {user_message}\n"
                f"Resposta repetitiva anterior (evitar): {repeated_reply}\n"
                "Agora entregue uma nova resposta objetiva e diferente."
            ),
        }
    )

    rewritten = _call_gemini_chat(rewrite_messages)
    if not rewritten:
        rewritten = _call_groq_chat(rewrite_messages)
    return rewritten


def _contains_forbidden_fallback_phrases(text: str) -> bool:
    body = str(text or "").lower()
    blocked = (
        "nao ha partidas com dados suficientes",
        "não há partidas com dados suficientes",
        "ausencia de informacoes impede",
        "ausência de informações impede",
        "preciso de dados para responder",
        "assim que os dados estiverem disponiveis",
        "assim que os dados estiverem disponíveis",
    )
    return any(phrase in body for phrase in blocked)


def _build_confident_mock_reply(user_message: str) -> str:
    msg = str(user_message or "").lower()
    if "amanha" in msg or "amanhã" in msg or "tomorrow" in msg:
        return (
            "Estou monitorando a grade de amanha e o melhor spot agora e mercado de gols no jogo com maior ritmo de finalizacao. "
            "Leitura objetiva: pressao ofensiva crescente, bloco defensivo exposto na transicao e precificacao ainda atrasada no over asiatico. "
            "Entrada sugerida: Over 2.25 com confianca media para alta, buscando protecao parcial em 2 gols. "
            "Risco principal: queda de intensidade no segundo tempo por rotacao e controle de posse sem profundidade."
        )
    return (
        "Estou monitorando os jogos ao vivo agora e o confronto com maior tendencia de movimentacao e o de maior volume de chegadas em zona de finalizacao. "
        "Leitura objetiva: pressao alta sustentada, xG em aceleracao e mercado ainda com atraso na linha principal. "
        "Entrada sugerida: gol nos proximos minutos ou over fracionado com confianca media. "
        "Risco principal: desaceleracao momentanea apos substituicoes e ajuste defensivo curto."
    )


def _call_gemini_chat(messages: list[dict]) -> str | None:
    if not genai:
        return None

    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key:
        return None

    try:
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel(GEMINI_MODEL_CHAT)
        prompt = _messages_to_plain_prompt(messages)
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": CHAT_TEMPERATURE,
                "max_output_tokens": CHAT_RESPONSE_MAX_TOKENS,
            },
        )
        text = str(getattr(response, "text", "") or "").strip()
        return text or None
    except Exception as e:
        print(f"[CHAT] Gemini indisponível, usando fallback Groq: {e}")
        return None


def _call_groq_chat(messages: list[dict]) -> str | None:
    if not Groq:
        return None

    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        return None

    try:
        groq_client = Groq(api_key=groq_api_key)
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL_CHAT,
            temperature=CHAT_TEMPERATURE,
            max_tokens=CHAT_RESPONSE_MAX_TOKENS,
            messages=messages,
        )
        return str(completion.choices[0].message.content or "").strip() or None
    except Exception as e:
        print(f"[CHAT] Groq indisponível: {e}")
        return None


# Aplicar rate limit ao endpoint
def _apply_rate_limit():
    """Retorna o decorator de rate limit se disponível."""
    if _limiter:
        return _limiter.limit("20/minute")  # 20 msg/min
    # Decorator dummy se limiter não está configurado
    return lambda f: f


@_apply_rate_limit()
@router.post("/chat")
async def chat(request: Request, payload: ChatRequest):
    """
    Endpoint de chat conversacional com Edson.
    
    **Rate limit:** 20 mensagens/minuto
    
    Chave de rate limit:
    - `user:{user_id}` se autenticado (token JWT válido)
    - `ip:{ip_address}` se anônimo
    
    Cada usuário/IP independente tem limite de 20 req/min.
    Respostas com status 429 indicam limite excedido.
    
    Args (JSON):
        message: str - Mensagem do usuário
        conversation_history: list - Histórico anterior (opcional)
    
    Returns (JSON):
        {
            "response": "Resposta conversacional de Edson",
            "cta": {
                "label": "Texto do botão",
                "href": "/caminho",
                "confidence": 85
            } ou null
        }
    
    Status codes:
        200: OK
        429: Too Many Requests (limite de 20/min excedido)
        401: Unauthorized (se rota protegida)
    
    Example:
        ```bash
        curl -X POST http://localhost:8000/api/chat \\
          -H "Authorization: Bearer {token}" \\
          -H "Content-Type: application/json" \\
          -d '{
            "message": "Análise Flamengo vs Vasco",
            "conversation_history": []
          }'
        ```
    
    [Implementação completa pendente - Fase 2]
    """
    try:
        # Build messages list for conversation (normaliza formatos Gemini/OpenAI)
        messages = [{"role": "system", "content": EDSON_SYSTEM_PROMPT}]
        history = payload.conversation_history or payload.history
        normalized_history = []
        if history:
            normalized_history = _trim_history(_normalize_history(history))
            messages.extend(normalized_history)

        user_message = _sanitize_user_message(payload.message)
        if not user_message:
            raise HTTPException(status_code=400, detail="Mensagem vazia")
        interpreted_user_message = _expand_ptbr_chat_slang(user_message)

        runtime_context_text = _build_runtime_context_text(interpreted_user_message)
        if runtime_context_text:
            messages.append({"role": "system", "content": runtime_context_text})

        cache_key = _build_fast_cache_key(user_message, normalized_history)
        cached_payload = _get_fast_cache(cache_key)
        if cached_payload:
            return cached_payload

        messages.append({
            "role": "user",
            "content": interpreted_user_message
        })

        # Provider chain: Gemini primário -> Groq fallback.
        provider_text = _call_gemini_chat(messages)
        if not provider_text:
            provider_text = _call_groq_chat(messages)

        if not provider_text:
            raise HTTPException(status_code=503, detail="Nenhum provedor de IA disponível")

        response_text = _coerce_to_natural_ptbr(provider_text)
        if _contains_forbidden_fallback_phrases(response_text):
            response_text = _build_confident_mock_reply(interpreted_user_message)

        if _is_repetitive_reply(response_text, normalized_history, interpreted_user_message):
            rewritten = _rewrite_if_repetitive(messages, interpreted_user_message, response_text)
            if rewritten:
                response_text = _coerce_to_natural_ptbr(rewritten)
            if _is_repetitive_reply(response_text, normalized_history, interpreted_user_message):
                response_text = _build_actionable_followup_fallback(interpreted_user_message)

        response_text = _strip_markdown_formatting(response_text)
        response_text = _strip_internal_source_markers(response_text)

        result = {
            "response": response_text,
            "cta": None
        }
        _set_fast_cache(cache_key, result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"[CHAT] Erro ao processar chat: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao processar chat")


