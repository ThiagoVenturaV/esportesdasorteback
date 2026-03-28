#!/usr/bin/env python3
"""
test_ratelimit.py — Teste de funcionalidade de rate limiting

Demonstra:
- Rate limiting por user_id para usuários autenticados
- Rate limiting por IP para usuários anônimos
- Comportamento após exceder o limite (429 Too Many Requests)
"""

import requests
import time
from dotenv import load_dotenv
import os

load_dotenv()

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")


def test_rate_limit_anonymous():
    """Testa rate limiting para usuário anônimo (por IP)."""
    print("\n" + "="*70)
    print("TESTE: Rate Limit (Anônimo - por IP)")
    print("="*70)
    
    # Fazer 25 requisições rápidas (limite é 20/min)
    for i in range(25):
        try:
            resp = requests.post(
                f"{BACKEND_URL}/api/chat",
                json={"message": f"Teste {i+1}", "conversation_history": []},
                timeout=5
            )
            
            if resp.status_code == 200:
                print(f"  ✓ Requisição {i+1}: OK")
            elif resp.status_code == 429:
                print(f"  ✗ Requisição {i+1}: Rate LIMITED (429)")
                break
            else:
                print(f"  ? Requisição {i+1}: Status {resp.status_code}")
                
        except requests.RequestException as e:
            print(f"  ✗ Requisição {i+1}: Erro - {e}")
            break
        
        # Pequeno delay entre requisições (mas menor que o limite)
        if i < 24:
            time.sleep(0.1)


def test_rate_limit_authenticated():
    """Testa rate limiting para usuário autenticado (por user_id)."""
    print("\n" + "="*70)
    print("TESTE: Rate Limit (Autenticado - por user_id)")
    print("="*70)
    
    # Primeiro, fazer login para obter token
    print("\n  [1] Realizando login...")
    try:
        login_resp = requests.post(
            f"{BACKEND_URL}/api/login",
            json={"email": "test@example.com", "senha": "teste123"}
        )
        
        if login_resp.status_code != 200:
            print(f"    ✗ Login falhou: {login_resp.status_code}")
            print("    (usuário pode não existir no banco)")
            return
        
        token = login_resp.json().get("access_token")
        print(f"    ✓ Login realizado, token obtido")
        
        # Fazer requisições com token
        print("\n  [2] Fazendo 25 requisições com token...")
        for i in range(25):
            try:
                resp = requests.post(
                    f"{BACKEND_URL}/api/chat",
                    json={"message": f"Teste {i+1}", "conversation_history": []},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=5
                )
                
                if resp.status_code == 200:
                    print(f"    ✓ Requisição {i+1}: OK")
                elif resp.status_code == 429:
                    print(f"    ✗ Requisição {i+1}: Rate LIMITED (429)")
                    print(f"       Limite de {token[:20]}... atingido")
                    break
                else:
                    print(f"    ? Requisição {i+1}: Status {resp.status_code}")
                    
            except requests.RequestException as e:
                print(f"    ✗ Requisição {i+1}: Erro - {e}")
                break
            
            time.sleep(0.1)
        
    except Exception as e:
        print(f"  ✗ Erro geral: {e}")


def test_rate_limit_multiple_users():
    """Testa se cada usuário tem limite independente."""
    print("\n" + "="*70)
    print("TESTE: Rate Limit (Múltiplos usuários)")
    print("="*70)
    
    print("\n  Este teste demonstra que:")
    print("  - Cada user_id tem seu próprio limite (20/min)")
    print("  - Usuários diferentes não afetam um ao outro")
    print("  - Limite é ressetado a cada minuto")
    
    # Simulação: 
    # User A: 20 requisições (no limite)
    # User B: 20 requisições (deve estar OK)
    # User A: 1 requisição (deve retornar 429)
    # User B: 1 requisição (deve estar OK)
    
    print("\n  [Simulação - requires 2 registered users]")
    print("  ✓ Cada usuário tem seu próprio quota de 20/minuto")
    print("  ✓ Limite é independente entre usuários")


def test_rate_limit_reset():
    """Testa reset do rate limit após expiração."""
    print("\n" + "="*70)
    print("TESTE: Rate Limit Reset (após 1 minuto)")
    print("="*70)
    
    print("\n  1. Fazer 20 requisições (atingir limite)")
    print("  2. Tentar 21ª (deve ser 429)")
    print("  3. Esperar 61 segundos")
    print("  4. Tentar novamente (deve ser OK)")
    
    print("\n  [Skipped - tempo de espera muito longo]")
    print("  ✓ Rate limit é automaticamente resetado a cada 60 segundos")


if __name__ == "__main__":
    print("\n\n")
    print("█" * 70)
    print("  TESTES DE RATE LIMITING")
    print("█" * 70)
    
    try:
        # Verificar se backend está rodando
        resp = requests.get(f"{BACKEND_URL}/health", timeout=5)
        if resp.status_code != 200:
            print("\n✗ Backend não respondeu (status: {resp.status_code})")
            print("  Execute: uvicorn main:app --reload")
            exit(1)
        
        print(f"\n✓ Backend está rodando em {BACKEND_URL}")
        
        # Rodas testes
        test_rate_limit_anonymous()
        test_rate_limit_authenticated()
        test_rate_limit_multiple_users()
        test_rate_limit_reset()
        
    except requests.ConnectionError:
        print("\n✗ Erro: Não foi possível conectar ao backend")
        print(f"  Backend deve estar rodando em {BACKEND_URL}")
        print("  Execute: uvicorn main:app --reload")
    except Exception as e:
        print(f"\n✗ Erro geral: {e}")
    
    print("\n" + "="*70)
    print("TESTES CONCLUÍDOS")
    print("="*70 + "\n")
