"""
chat/router.py — Rotas de chat conversacional
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
import os
try:
    from groq import Groq
except ImportError:
    Groq = None

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

        # Build messages list for conversation
        messages = []
        history = payload.conversation_history or payload.history
        if history:
            messages.extend(history)
        messages.append({
            "role": "user",
            "content": payload.message
        })
        
        # Call Groq API with chat model
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL_CHAT,
            temperature=0.7,
            max_tokens=600,
            messages=messages
        )
        
        response_text = completion.choices[0].message.content
        
        return {
            "response": response_text,
            "cta": None
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[CHAT] Erro ao chamar Groq: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao processar chat: {str(e)}")


