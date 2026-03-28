"""
utils/ratelimit.py — Helpers para rate limiting com suporte a user_id
"""

import jwt
import os
from fastapi import Request
from slowapi.util import get_remote_address

JWT_SECRET = os.getenv("JWT_SECRET")


def get_rate_limit_key(request: Request) -> str:
    """
    Retorna a chave para rate limiting.
    
    Prioridade:
    1. user_id (extraído do JWT) se autenticado
    2. IP de origem se não autenticado ou token inválido
    
    Isso permite:
    - Usuários autenticados terem limite por conta (user_id)
    - Usuários anônimos terem limite por IP
    """
    # Tentar extrair token do header Authorization
    auth_header = request.headers.get("Authorization", "")
    
    if auth_header.startswith("Bearer "):
        token = auth_header.replace("Bearer ", "").strip()
        try:
            payload = jwt.decode(
                token,
                JWT_SECRET,
                algorithms=["HS256"]
            )
            user_id = payload.get("sub")
            if user_id:
                print(f"[RATELIMIT] Using user_id as key: {user_id}")
                return f"user:{user_id}"
        except jwt.InvalidTokenError:
            pass
        except Exception as e:
            print(f"[RATELIMIT] Erro ao decodificar token: {e}")
    
    # Fallback para IP
    ip = get_remote_address(request)
    print(f"[RATELIMIT] Using IP as key: {ip}")
    return f"ip:{ip}"
