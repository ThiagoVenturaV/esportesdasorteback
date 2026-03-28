"""
auth/service.py — Lógica de autenticação e JWT
"""

import os
import jwt
from datetime import datetime, timedelta
from fastapi import HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise ValueError("JWT_SECRET não configurada. Gerar com: openssl rand -hex 32")

security = HTTPBearer()


def create_access_token(user_id: int, email: str) -> str:
    """Cria um JWT token com expiração de 24h."""
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.utcnow() + timedelta(hours=24),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


async def get_current_user(credentials: HTTPAuthorizationCredentials = security):
    """
    Valida e decodifica o JWT token do header Authorization.
    
    Uso: async def my_route(user=Depends(get_current_user)):
    
    Retorna o payload do JWT contendo 'sub' (user_id) e 'email'.
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token, JWT_SECRET, algorithms=["HS256"]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )
