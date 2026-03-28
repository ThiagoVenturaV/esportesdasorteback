"""
Authentication and user management routes.
"""
import psycopg2
from fastapi import APIRouter

from db_neon import get_db_connection
from models import LoginDados, Usuario
from auth.service import (
    verify_password,
    hash_password,
    create_access_token,
    validate_signup_payload,
    _only_digits,
)


router = APIRouter(tags=["Usuários"])

@router.post("/api/login")
def validar_login(credenciais: LoginDados):
    conn = None
    try:
        email_normalized = str(credenciais.email_usuario or "").strip().lower()
        conn = get_db_connection()
        with conn.cursor() as cur:
            sql = """SELECT id_usuario, nome_usuario, email_usuario, cpf_usuario, telefone_usuario, senha_usuario
                     FROM tb_usuario
                     WHERE email_usuario = %s"""
            cur.execute(sql, (email_normalized,))
            usuario = cur.fetchone()

            if usuario and verify_password(credenciais.senha_usuario, usuario.get("senha_usuario")):
                # Migra senha legada em texto plano para hash sem interromper login.
                if "$" not in str(usuario.get("senha_usuario") or ""):
                    try:
                        new_hash = hash_password(credenciais.senha_usuario)
                        cur.execute(
                            "UPDATE tb_usuario SET senha_usuario = %s WHERE id_usuario = %s",
                            (new_hash, usuario["id_usuario"]),
                        )
                        conn.commit()
                    except Exception as e:
                        print(f"Falha ao migrar hash de senha legada: {e}")

                safe_user = {
                    "id_usuario": usuario["id_usuario"],
                    "nome_usuario": usuario["nome_usuario"],
                    "email_usuario": usuario["email_usuario"],
                    "cpf_usuario": usuario.get("cpf_usuario"),
                    "telefone_usuario": usuario.get("telefone_usuario"),
                }
                token = create_access_token(usuario["id_usuario"], usuario["email_usuario"])
                return {
                    "sucesso": True, 
                    "mensagem": f"Bem-vindo(a), {usuario['nome_usuario']}!", 
                    "usuario": safe_user,
                    "access_token": token,
                    "token_type": "bearer"
                }
            return {"sucesso": False, "erro": "E-mail ou senha incorretos."}
    except Exception as erro:
        return {"sucesso": False, "erro": f"Erro no servidor: {str(erro)}"}
    finally:
        if conn: conn.close()


@router.post("/api/usuarios")
def criar_usuario(novo_usuario: Usuario):
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
        with conn.cursor() as cur:
            sql = """INSERT INTO tb_usuario 
                     (nome_usuario, email_usuario, cpf_usuario, dataNac_usuario, endereco_usuario, telefone_usuario, senha_usuario)
                     VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id_usuario"""
            valores = (
                novo_usuario.nome_usuario, email_normalized, cpf_normalized,
                novo_usuario.dataNac_usuario, novo_usuario.endereco_usuario,
                phone_normalized, senha_hash
            )
            cur.execute(sql, valores)
            id_gerado = cur.fetchone()['id_usuario']
            conn.commit()
            return {"sucesso": True, "mensagem": "Usuário cadastrado com sucesso!", "id_gerado": id_gerado}
    except psycopg2.IntegrityError:
        return {"sucesso": False, "erro": "E-mail ou CPF já cadastrado."}
    except Exception as erro:
        return {"sucesso": False, "erro": str(erro)}
    finally:
        if conn: conn.close()


@router.get("/api/usuarios")
def listar_usuarios():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id_usuario, nome_usuario, email_usuario, criado_em FROM tb_usuario ORDER BY id_usuario DESC")
            usuarios = cur.fetchall()
            return {"sucesso": True, "quantidade": len(usuarios), "usuarios": usuarios}
    except Exception as erro:
        return {"sucesso": False, "erro": str(erro)}
    finally:
        if conn: conn.close()
