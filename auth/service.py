"""
auth/service.py — Lógica de autenticação e JWT
"""

import os
import re
import hmac
import base64
import secrets
import hashlib
import jwt
from datetime import datetime, timedelta
from fastapi import HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from models import Usuario

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise ValueError("JWT_SECRET não configurada. Gerar com: openssl rand -hex 32")

security = HTTPBearer()

PBKDF2_ALG = "sha256"
PBKDF2_ITERATIONS = int(os.getenv("AUTH_HASH_ITERATIONS", "120000"))


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def hash_password(password: str) -> str:
    raw = str(password or "")
    if not raw:
        raise ValueError("Senha vazia não pode ser hasheada")

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(PBKDF2_ALG, raw.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    salt_b64 = base64.b64encode(salt).decode("utf-8")
    digest_b64 = base64.b64encode(digest).decode("utf-8")
    return f"pbkdf2_{PBKDF2_ALG}${PBKDF2_ITERATIONS}${salt_b64}${digest_b64}"


def verify_password(plain_password: str, stored_password: str | None) -> bool:
    plain = str(plain_password or "")
    stored = str(stored_password or "")
    if not plain or not stored:
        return False

    # Compatibilidade com senhas legadas em texto plano.
    if "$" not in stored:
        return hmac.compare_digest(plain, stored)

    try:
        algo, iterations_str, salt_b64, digest_b64 = stored.split("$", 3)
        if not algo.startswith("pbkdf2_"):
            return False

        iterations = int(iterations_str)
        digest_name = algo.replace("pbkdf2_", "", 1)
        salt = base64.b64decode(salt_b64.encode("utf-8"))
        expected = base64.b64decode(digest_b64.encode("utf-8"))

        calculated = hashlib.pbkdf2_hmac(digest_name, plain.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(calculated, expected)
    except Exception:
        return False


def validate_signup_payload(novo_usuario: Usuario) -> str | None:
    nome = str(novo_usuario.nome_usuario or "").strip()
    email = str(novo_usuario.email_usuario or "").strip().lower()
    cpf = _only_digits(novo_usuario.cpf_usuario)
    telefone = _only_digits(novo_usuario.telefone_usuario)
    senha = str(novo_usuario.senha_usuario or "")

    if len(nome) < 3:
        return "Nome deve conter ao menos 3 caracteres."
    if "@" not in email or "." not in email.split("@")[-1]:
        return "E-mail inválido."
    if len(cpf) != 11:
        return "CPF deve conter 11 dígitos."
    if len(telefone) < 10 or len(telefone) > 11:
        return "Telefone deve conter 10 ou 11 dígitos."
    if len(senha) < 8:
        return "Senha deve conter no mínimo 8 caracteres."

    return None


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
