"""
auth/router.py — Rotas de autenticação
"""

from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer
from pydantic import BaseModel
import os

# Importar funções de JWT
from auth.service import create_access_token, get_current_user

# Importar conexão com banco
try:
    from db.neon import get_db_connection, release_connection
except ImportError:
    from db_neon import get_db_connection, release_connection

router = APIRouter(prefix="/api", tags=["auth"])
security = HTTPBearer()


class LoginRequest(BaseModel):
    """Modelo de requisição de login."""
    email: str
    senha: str


class LoginResponse(BaseModel):
    """Modelo de resposta do login."""
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
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Buscar usuário por email
        cursor.execute(
            "SELECT id_usuario, nome_usuario, email_usuario FROM tb_usuario WHERE email_usuario = %s",
            (payload.email,)
        )
        user = cursor.fetchone()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email ou senha inválidos"
            )
        
        # TODO: Fase 2 — Validar senha com hash (bcrypt/PBKDF2)
        # Por agora, apenas validar existência do usuário
        
        # Montar resposta
        usuario_dict = {
            "id": user[0],
            "nome": user[1],
            "email": user[2],
        }
        
        # Gerar token JWT
        token = create_access_token(user[0], user[2])
        
        return LoginResponse(
            usuario=usuario_dict,
            access_token=token,
            token_type="bearer"
        )
        
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
