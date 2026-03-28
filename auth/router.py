"""
auth/router.py — Rotas de autenticação
"""

from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer
from pydantic import BaseModel
import psycopg2
import os

# Importar funções de JWT
from auth.service import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
    validate_signup_payload,
    _only_digits,
)
from models import Usuario

# Importar conexão com banco
try:
    from db.neon import get_db_connection, release_connection
except ImportError:
    from db_neon import get_db_connection, release_connection

router = APIRouter(prefix="/api", tags=["auth"])
security = HTTPBearer()


class LoginRequest(BaseModel):
    """Modelo de requisição de login."""
    email: str | None = None
    senha: str | None = None
    email_usuario: str | None = None
    senha_usuario: str | None = None


class LoginResponse(BaseModel):
    """Modelo de resposta do login."""
    sucesso: bool = True
    mensagem: str | None = None
    usuario: dict
    access_token: str
    token_type: str = "bearer"


class ContaUserResponse(BaseModel):
    """Modelo de resposta da conta do usuário."""
    id: int
    nome: str
    email: str
    cpf: str | None = None
    telefone: str | None = None
    endereco: str | None = None
    data_nascimento: str | None = None


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest):
    """
    Endpoint de login.
    
    Valida email e senha contra tb_usuario no Neon,
    retorna dados do usuário + JWT token.
    """
    conn = None
    try:
        email_raw = payload.email_usuario or payload.email or ""
        senha_raw = payload.senha_usuario or payload.senha or ""

        email_normalized = str(email_raw).strip().lower()
        senha_input = str(senha_raw)

        if not email_normalized or not senha_input:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Informe e-mail e senha.",
            )

        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Buscar usuário por email
        cursor.execute(
            """
            SELECT id_usuario, nome_usuario, email_usuario, cpf_usuario, telefone_usuario, senha_usuario
            FROM tb_usuario
            WHERE email_usuario = %s
            """,
            (email_normalized,)
        )
        user = cursor.fetchone()
        
        if not user or not verify_password(senha_input, user[5]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email ou senha inválidos"
            )

        # Migração automática para hash se senha legada estiver em texto plano.
        if "$" not in str(user[5] or ""):
            try:
                new_hash = hash_password(senha_input)
                cursor.execute(
                    "UPDATE tb_usuario SET senha_usuario = %s WHERE id_usuario = %s",
                    (new_hash, user[0]),
                )
                conn.commit()
            except Exception as migration_error:
                print(f"[AUTH] Falha ao migrar hash de senha legada: {migration_error}")
        
        # Montar resposta
        usuario_dict = {
            "id": user[0],
            "id_usuario": user[0],
            "nome": user[1],
            "nome_usuario": user[1],
            "email": user[2],
            "email_usuario": user[2],
            "cpf_usuario": user[3],
            "telefone_usuario": user[4],
        }
        
        # Gerar token JWT
        token = create_access_token(user[0], user[2])

        return {
            "sucesso": True,
            "mensagem": f"Bem-vindo(a), {user[1]}!",
            "usuario": usuario_dict,
            "access_token": token,
            "token_type": "bearer",
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[AUTH] Erro no login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro interno do servidor"
        )
    finally:
        if conn:
            release_connection(conn)


@router.post("/usuarios")
async def criar_usuario(novo_usuario: Usuario):
    """Cadastro de usuário com persistência no Neon e senha em hash PBKDF2."""
    conn = None
    try:
        validation_error = validate_signup_payload(novo_usuario)
        if validation_error:
            return {"sucesso": False, "erro": validation_error}

        email_normalized = str(novo_usuario.email_usuario or "").strip().lower()
        cpf_normalized = _only_digits(novo_usuario.cpf_usuario)
        phone_normalized = _only_digits(novo_usuario.telefone_usuario)
        senha_hash = hash_password(novo_usuario.senha_usuario)

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO tb_usuario
                (nome_usuario, email_usuario, cpf_usuario, dataNac_usuario, endereco_usuario, telefone_usuario, senha_usuario)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id_usuario
            """,
            (
                novo_usuario.nome_usuario,
                email_normalized,
                cpf_normalized,
                novo_usuario.dataNac_usuario,
                novo_usuario.endereco_usuario,
                phone_normalized,
                senha_hash,
            ),
        )
        created = cursor.fetchone()
        conn.commit()

        return {
            "sucesso": True,
            "mensagem": "Usuário cadastrado com sucesso!",
            "id_gerado": created[0] if created else None,
        }
    except psycopg2.IntegrityError:
        if conn:
            conn.rollback()
        return {"sucesso": False, "erro": "E-mail ou CPF já cadastrado."}
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"[AUTH] Erro no cadastro: {e}")
        return {"sucesso": False, "erro": "Erro interno ao cadastrar usuário."}
    finally:
        if conn:
            release_connection(conn)


# ─────────────────────────────────────────────────────────────────────────────
# ROTAS PROTEGIDAS
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/usuarios/{user_id}/conta", response_model=ContaUserResponse)
async def get_conta(user_id: int, user=Depends(get_current_user)):
    """
    Obtém dados da conta do usuário (rota protegida).
    
    O usuário só pode acessar sua própria conta (validação by user_id do token).
    """
    # Validar que o usuário está acessando sua própria conta
    if int(user.get("sub")) != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado"
        )
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Buscar dados da conta
        cursor.execute(
            """
            SELECT 
                id_usuario, 
                nome_usuario, 
                email_usuario, 
                cpf_usuario, 
                telefone_usuario, 
                endereco_usuario,
                dataNac_usuario
            FROM tb_usuario 
            WHERE id_usuario = %s
            """,
            (user_id,)
        )
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Usuário não encontrado"
            )
        
        return ContaUserResponse(
            id=row[0],
            nome=row[1],
            email=row[2],
            cpf=row[3],
            telefone=row[4],
            endereco=row[5],
            data_nascimento=str(row[6]) if row[6] else None,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[AUTH] Erro ao buscar conta: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro interno do servidor"
        )
    finally:
        if conn:
            release_connection(conn)


@router.put("/usuarios/{user_id}/conta")
async def update_conta(
    user_id: int, 
    updates: ContaUserResponse,
    user=Depends(get_current_user)
):
    """
    Atualiza dados da conta do usuário (rota protegida).
    
    O usuário só pode atualizar sua própria conta.
    """
    # Validar que o usuário está acessando sua própria conta
    if int(user.get("sub")) != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado"
        )
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Atualizar apenas campos não-nulos
        update_fields = []
        values = []
        
        if updates.nome:
            update_fields.append("nome_usuario = %s")
            values.append(updates.nome)
        if updates.cpf:
            update_fields.append("cpf_usuario = %s")
            values.append(updates.cpf)
        if updates.telefone:
            update_fields.append("telefone_usuario = %s")
            values.append(updates.telefone)
        if updates.endereco:
            update_fields.append("endereco_usuario = %s")
            values.append(updates.endereco)
        if updates.data_nascimento:
            update_fields.append("dataNac_usuario = %s")
            values.append(updates.data_nascimento)
        
        if not update_fields:
            return {"mensagem": "Nenhum campo para atualizar"}
        
        values.append(user_id)
        
        sql = f"UPDATE tb_usuario SET {', '.join(update_fields)} WHERE id_usuario = %s"
        cursor.execute(sql, values)
        conn.commit()
        
        return {"mensagem": "Conta atualizada com sucesso"}
        
    except Exception as e:
        print(f"[AUTH] Erro ao atualizar conta: {e}")
        if conn:
            conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro interno do servidor"
        )
    finally:
        if conn:
            release_connection(conn)
