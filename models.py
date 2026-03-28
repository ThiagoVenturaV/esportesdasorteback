"""
Pydantic models for request/response validation.
"""
from pydantic import BaseModel
from typing import List, Dict, Any


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
