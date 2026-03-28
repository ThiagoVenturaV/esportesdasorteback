"""
Auth service — password hashing, JWT tokens, validation.
"""
import os
import re
import secrets
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer

from models import Usuario

PASSWORD_PBKDF2_ITERATIONS = 310000
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_hex(32))
security = HTTPBearer()


# ── Helpers ──────────────────────────────────────────────

def _only_digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${PASSWORD_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(input_password: str, stored_password: str) -> bool:
    stored = str(stored_password or "")
    if not stored:
        return False

    # Legacy: plain-text passwords
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


def validate_signup_payload(novo_usuario: Usuario) -> Optional[str]:
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


# ── JWT ──────────────────────────────────────────────────

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
