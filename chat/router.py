"""
chat/router.py — Rotas de chat conversacional
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
import os
import json
try:
    from groq import Groq
except ImportError:
    Groq = None

from chat.edson import EDSON_SYSTEM_PROMPT

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


CHAT_HISTORY_MAX_TURNS = int(os.getenv("CHAT_HISTORY_MAX_TURNS", "12"))


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
        return raw

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
            return " ".join(lines)
        return "Análise concluída, mas sem detalhes legíveis no momento."
    except Exception:
        return raw


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
    if not Groq:
        raise HTTPException(status_code=503, detail="SDK Groq não está instalada no backend")
    
    try:
        from main import GROQ_MODEL_CHAT

        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            raise HTTPException(status_code=503, detail="GROQ_API_KEY não configurada")

        groq_client = Groq(api_key=groq_api_key)

        # Build messages list for conversation (normaliza formatos Gemini/OpenAI)
        messages = [{"role": "system", "content": EDSON_SYSTEM_PROMPT}]
        history = payload.conversation_history or payload.history
        if history:
            messages.extend(_trim_history(_normalize_history(history)))
        messages.append({
            "role": "user",
            "content": payload.message.strip()
        })
        
        # Call Groq API with chat model
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL_CHAT,
            temperature=0.7,
            max_tokens=600,
            messages=messages
        )
        
        response_text = _coerce_to_natural_ptbr(completion.choices[0].message.content)
        
        return {
            "response": response_text,
            "cta": None
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[CHAT] Erro ao chamar Groq: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao processar chat")


