"""
analysis/service.py — Análises de partidas com TTL

Gerencia cache de análises com diferentes TTLs:
- Partidas ao vivo: 5 minutos (análises precisam ser atualizadas frequentemente)
- Partidas futuras: 24 horas (análises mudam menos frequentemente)
"""

import os
import json
from datetime import datetime, UTC
try:
    from groq import Groq
except ImportError:
    Groq = None


# TTL configurável por variáveis de ambiente
ANALYSIS_TTL_LIVE_MIN = int(os.getenv("ANALYSIS_TTL_LIVE_MINUTES", "5"))
ANALYSIS_TTL_UPCOMING_H = int(os.getenv("ANALYSIS_TTL_UPCOMING_HOURS", "24"))

try:
    from db.neon import get_db_connection, release_connection
except ImportError:
    from db_neon import get_db_connection, release_connection


def get_saved_analysis(match_id: str, is_live: bool = True) -> dict | None:
    """
    Recupera uma análise salva do cache, respeitando o TTL.
    
    Args:
        match_id: ID da partida (match_id do BetsAPI)
        is_live: True se é partida ao vivo (TTL curto), False se futuro (TTL longo)
    
    Returns:
        dict com análise ou None se expirada/não encontrada
    """
    # Determinar TTL baseado no tipo de partida
    ttl_minutes = ANALYSIS_TTL_LIVE_MIN if is_live else (ANALYSIS_TTL_UPCOMING_H * 60)
    ttl_interval = f"{ANALYSIS_TTL_LIVE_MIN} minutes" if is_live else f"{ANALYSIS_TTL_UPCOMING_H} hours"
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Query com filtro de TTL
        sql = """
            SELECT analise_json, criado_em
            FROM tb_analise
            WHERE match_id = %s
            AND criado_em > NOW() - INTERVAL %s
            ORDER BY criado_em DESC
            LIMIT 1
        """
        
        cursor.execute(sql, (match_id, ttl_interval))
        row = cursor.fetchone()
        
        if row:
            analysis_data = row[0]
            created_at = row[1]
            
            # Log de hit do cache
            match_type = "LIVE" if is_live else "UPCOMING"
            print(f"[ANALYSIS] Cache HIT para {match_id} ({match_type}), criada em {created_at}")
            
            # Parse JSON se necessário
            if isinstance(analysis_data, str):
                return json.loads(analysis_data)
            return analysis_data
        
        # Log de miss do cache
        match_type = "LIVE" if is_live else "UPCOMING"
        print(f"[ANALYSIS] Cache MISS para {match_id} ({match_type}), TTL={ttl_interval}")
        return None
        
    except Exception as e:
        print(f"[ANALYSIS] Erro ao buscar análise em cache: {e}")
        return None
    finally:
        if conn:
            release_connection(conn)


def save_analysis(match_id: str, analysis_json: dict | str) -> bool:
    """
    Salva uma análise no cache.
    
    Args:
        match_id: ID da partida
        analysis_json: Análise em formato JSON (dict ou string)
    
    Returns:
        True se salva com sucesso, False caso contrário
    """
    conn = None
    try:
        # Converter para JSON se necessário
        if isinstance(analysis_json, dict):
            analysis_str = json.dumps(analysis_json, ensure_ascii=False)
        else:
            analysis_str = analysis_json
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # INSERT ON CONFLICT para atualizar se já existe
        sql = """
            INSERT INTO tb_analise (match_id, analise_json, criado_em)
            VALUES (%s, %s, NOW())
            ON CONFLICT (match_id) DO UPDATE
            SET analise_json = EXCLUDED.analise_json,
                criado_em = NOW()
        """
        
        cursor.execute(sql, (match_id, analysis_str))
        conn.commit()
        
        print(f"[ANALYSIS] Análise salva para {match_id}")
        return True
        
    except Exception as e:
        print(f"[ANALYSIS] Erro ao salvar análise: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            release_connection(conn)


def fetch_live_matches(*args, **kwargs):
    """Placeholder para compatibilidade com rag_service.py"""
    raise NotImplementedError("fetch_live_matches não implementado")


def fetch_upcoming_matches(*args, **kwargs):
    """Placeholder para compatibilidade com rag_service.py"""
    raise NotImplementedError("fetch_upcoming_matches não implementado")


def get_historical_context(*args, **kwargs):
    """Placeholder para compatibilidade com rag_service.py"""
    raise NotImplementedError("get_historical_context não implementado")


def analyze_match_with_ai(match_data: dict, prompt: str = None) -> dict | None:
    """
    Análise de partida com Groq (JSON estruturado).
    Temperature=0.2, max_tokens=900, response_format json_object.
    """
    if not Groq:
        return None
    
    try:
        import os
        groq_model = os.getenv("GROQ_MODEL", "mixtral-8x7b-32768")
        groq_api_key = os.getenv("GROQ_API_KEY")
        
        if not groq_api_key:
            return None
        
        groq_client = Groq(api_key=groq_api_key)
        
        if not prompt:
            prompt = f"""Analyze match data and return JSON:
-prediction: home_win|draw|away_win
- confidence: 0-100
- key_factors: list
- recommended_bet: string

Match: {json.dumps(match_data, ensure_ascii=False)}"""
        
        completion = groq_client.chat.completions.create(
            model=groq_model,
            temperature=0.2,
            max_tokens=900,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}]
        )
        
        return json.loads(completion.choices[0].message.content)
    except Exception as e:
        print(f"[ANALYSIS] Groq erro: {e}")
        return None


def analyze_match_with_gemini(*args, **kwargs):
    """Placeholder para compatibilidade com rag_service.py"""
    raise NotImplementedError("analyze_match_with_gemini não implementado")


def build_db_only_analysis(*args, **kwargs):
    """Placeholder para compatibilidade com rag_service.py"""
    raise NotImplementedError("build_db_only_analysis não implementado")
