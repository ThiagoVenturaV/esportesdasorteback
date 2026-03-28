import os
import json
import time
import requests
try:
    from groq import Groq
except Exception:
    Groq = None
from db_neon import get_db_connection
from dotenv import load_dotenv

load_dotenv()

# Configurações
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct").strip()
BETS_API_TOKEN = os.getenv("BETS_API_TOKEN")
if not BETS_API_TOKEN:
    raise RuntimeError("BETS_API_TOKEN não configurado")
RAG_DISABLE_WEB_FETCH = os.getenv("RAG_DISABLE_WEB_FETCH", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
EDSON_DB_ONLY_MODE = os.getenv("EDSON_DB_ONLY_MODE", "false").strip().lower() in {
    "1", "true", "yes", "on"
}

groq_client = Groq(api_key=GROQ_API_KEY) if (GROQ_API_KEY and Groq) else None
if GROQ_API_KEY and not Groq:
    print("[RAG] Pacote 'groq' não encontrado. Serviço seguirá em fallback DB-only quando necessário.")

# Cache p/ evitar flood na BetsAPI
_cache_live_matches = None
_cache_time = 0

def fetch_live_matches():
    global _cache_live_matches, _cache_time
    if RAG_DISABLE_WEB_FETCH:
        return []

    # Cache de 30 segundos
    if _cache_live_matches and (time.time() - _cache_time) < 30:
        return _cache_live_matches

    url = f"https://api.b365api.com/v3/events/inplay?sport_id=1&token={BETS_API_TOKEN}"
    try:
        req = requests.get(url, timeout=10)
        data = req.json()
        if data.get("success") == 1:
            _cache_live_matches = data.get("results", [])
            _cache_time = time.time()
            return _cache_live_matches
    except Exception as e:
        print(f"Erro BetsAPI: {e}")
    
    return []


_cache_upcoming_matches = None
_cache_upcoming_time = 0


def fetch_upcoming_matches(hours: int = 72):
    """
    Busca partidas futuras na BetsAPI para contexto de recomendações.
    """
    global _cache_upcoming_matches, _cache_upcoming_time
    if RAG_DISABLE_WEB_FETCH:
        return []

    if _cache_upcoming_matches and (time.time() - _cache_upcoming_time) < 60:
        return _cache_upcoming_matches

    url = f"https://api.b365api.com/v3/events/upcoming?sport_id=1&token={BETS_API_TOKEN}"
    try:
        req = requests.get(url, timeout=10)
        data = req.json() if req else {}
        if data.get("success") == 1:
            results = data.get("results", []) or []
            _cache_upcoming_matches = results
            _cache_upcoming_time = time.time()
            return results
    except Exception as e:
        print(f"Erro BetsAPI upcoming: {e}")

    return []

def get_historical_context(home_team, away_team):
    """
    Busca o histórico de confronto ou estatísticas no Neon DB.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Buscar jogos onde qualquer um dos dois times jogou (para montar média de xG ou desempenho passado)
            sql = """
                SELECT time_casa, time_fora, gols_casa, gols_fora, competicao, temporada 
                FROM tb_partida_historico 
                WHERE time_casa ILIKE %s OR time_fora ILIKE %s OR time_casa ILIKE %s OR time_fora ILIKE %s
                LIMIT 10
            """
            cur.execute(sql, (f"%{home_team}%", f"%{home_team}%", f"%{away_team}%", f"%{away_team}%"))
            historico = cur.fetchall()
            return historico
    except Exception as e:
        print(f"Erro ao buscar histórico: {e}")
        return []
    finally:
        if conn:
            conn.close()

def save_analysis(match_id, analysis_data, analysis_name: str = None):
    """
    Salva a análise no banco de dados Neon para consumo rápido e histórico.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            payload = json.dumps(analysis_data)
            nome = (analysis_name or f"Analise {match_id}").strip()[:255]

            # Compatibilidade com schemas antigos/novos de tb_analise.
            try:
                sql = "INSERT INTO tb_analise (nome, match_id, analise_json) VALUES (%s, %s, %s)"
                cur.execute(sql, (nome, match_id, payload))
            except Exception:
                conn.rollback()
                sql = "INSERT INTO tb_analise (match_id, analise_json) VALUES (%s, %s)"
                cur.execute(sql, (match_id, payload))
            conn.commit()
    except Exception as e:
        print(f"Erro ao salvar análise no banco: {e}")
    finally:
        if conn:
            conn.close()

ANALYSIS_TTL_LIVE_MIN = int(os.getenv("ANALYSIS_TTL_LIVE_MINUTES", "5"))
ANALYSIS_TTL_UPCOMING_H = int(os.getenv("ANALYSIS_TTL_UPCOMING_HOURS", "24"))

def get_saved_analysis(match_id: str, is_live: bool = True):
    """
    Verifica se a partida já tem análise gerada nos últimos minutos no DB.
    """
    ttl = f"{ANALYSIS_TTL_LIVE_MIN} minutes" if is_live else f"{ANALYSIS_TTL_UPCOMING_H} hours"
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            sql = """
                SELECT analise_json 
                FROM tb_analise 
                WHERE match_id = %s 
                AND criado_em > NOW() - CAST(%s AS interval)
                ORDER BY criado_em DESC 
                LIMIT 1
            """
            cur.execute(sql, (match_id, ttl))
            result = cur.fetchone()
            if result:
                # Retorna o JSONB parseado
                return result['analise_json']
    except Exception as e:
        print(f"Erro ao buscar análise salva: {e}")
    finally:
        if conn:
            conn.close()
    return None

def _build_prompt(match_id: str, target_match, historico_str: str, historico_exists: bool) -> str:
    return f"""
Você é o Edson, um assistente virtual ultra-avançado em análise de dados esportivos.
Sua missão é gerar um relatório estatístico e preditivo baseado em dados reais (BetsAPI) e históricos do banco.

Dados ao vivo da partida (BetsAPI):
{json.dumps(target_match) if target_match else "Sem dados BetsAPI disponíveis no momento. Assuma um cenário empatado 0x0 para fins de previsão."}

Dados Históricos (Banco Neon PostgreSQL):
{historico_str if historico_exists else "Sem amplo histórico. Baseie-se nas odds e no momento."}

Você DEVE retornar APENAS um objeto JSON válido, sem markdown e sem texto extra.

Estrutura obrigatória:
{{
  "matchId": "{match_id}",
  "winProbability": {{ "home": [Inteiro 0-100], "draw": [Inteiro 0-100], "away": [Inteiro 0-100] }},
  "goalProbabilityNextMinute": [Inteiro 0-100],
  "cardRiskHome": [Inteiro 0-100],
  "cardRiskAway": [Inteiro 0-100],
  "penaltyRisk": [Inteiro 0-100],
  "momentumHome": [Array com 15 inteiros 0-100],
  "momentumAway": [Array com 15 inteiros 0-100],
  "commentary": [Array com exatamente duas strings],
  "predictedWinner": "Nome do time com maior chance, ou 'Empate'",
  "confidenceScore": [Inteiro 0-100]
}}
"""


def _normalize_analysis_shape(match_id: str, home_name: str, away_name: str, analysis_data):
    if not isinstance(analysis_data, dict):
        return None

    analysis_data["matchId"] = str(analysis_data.get("matchId") or match_id)

    win = analysis_data.get("winProbability")
    if not isinstance(win, dict):
        win = {"home": 33, "draw": 33, "away": 34}
    for k in ("home", "draw", "away"):
        try:
            win[k] = int(win.get(k, 0))
        except Exception:
            win[k] = 0
        win[k] = max(0, min(100, win[k]))
    analysis_data["winProbability"] = win

    int_fields = ["goalProbabilityNextMinute", "cardRiskHome", "cardRiskAway", "penaltyRisk", "confidenceScore"]
    for field in int_fields:
        try:
            val = int(analysis_data.get(field, 0))
        except Exception:
            val = 0
        analysis_data[field] = max(0, min(100, val))

    for field in ("momentumHome", "momentumAway"):
        values = analysis_data.get(field)
        if not isinstance(values, list):
            values = [50] * 15
        normalized = []
        for v in values[:15]:
            try:
                normalized.append(max(0, min(100, int(v))))
            except Exception:
                normalized.append(50)
        while len(normalized) < 15:
            normalized.append(50)
        analysis_data[field] = normalized

    commentary = analysis_data.get("commentary")
    if not isinstance(commentary, list):
        commentary = []
    commentary = [str(c) for c in commentary if str(c).strip()]
    if len(commentary) < 2:
        commentary = commentary + [
            f"Análise gerada com dados históricos para {home_name} x {away_name}.",
            "Use o contexto da partida para ajustar a decisão de aposta em tempo real.",
        ]
    analysis_data["commentary"] = commentary[:2]

    predicted = analysis_data.get("predictedWinner")
    if not isinstance(predicted, str) or not predicted.strip():
        predicted = "Empate"
    analysis_data["predictedWinner"] = predicted

    return analysis_data


def analyze_match_with_ai(match_id: str, home_team: str = None, away_team: str = None):
    """
    Gera análise combinando dados ao vivo + histórico do banco + Groq.
    Retorna no formato exato que a AnalysisPage.jsx espera.
    """
    live_matches = fetch_live_matches()
    target_match = None

    for m in live_matches:
        if str(m.get("id")) == str(match_id):
            target_match = m
            break

    home_name = (
        target_match.get("home", {}).get("name")
        if target_match
        else (home_team or "Botafogo")
    )
    away_name = (
        target_match.get("away", {}).get("name")
        if target_match
        else (away_team or "Santos")
    )

    historico = get_historical_context(home_name, away_name)
    historico_str = json.dumps(historico, default=str)

    if EDSON_DB_ONLY_MODE:
        return build_db_only_analysis(match_id, home_name, away_name, historico)

    if not groq_client:
        return build_db_only_analysis(match_id, home_name, away_name, historico)

    prompt = _build_prompt(match_id, target_match, historico_str, bool(historico))

    try:
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": "Você é um analista esportivo e deve responder somente JSON válido."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=900,
            response_format={"type": "json_object"},
        )

        texto_limpo = ""
        if completion and completion.choices:
            choice = completion.choices[0]
            if choice and choice.message and choice.message.content:
                texto_limpo = str(choice.message.content).strip()

        texto_limpo = texto_limpo.replace('```json', '').replace('```', '').strip()
        analysis_data = json.loads(texto_limpo)

        analysis_data = _normalize_analysis_shape(match_id, home_name, away_name, analysis_data)
        if not analysis_data:
            return build_db_only_analysis(match_id, home_name, away_name, historico)

        save_analysis(match_id, analysis_data, analysis_name=f"{home_name} x {away_name}")

        return analysis_data

    except Exception as e:
        print(f"Erro na geração Groq/RAG: {e}")
        return build_db_only_analysis(match_id, home_name, away_name, historico)


def analyze_match_with_gemini(match_id: str, home_team: str = None, away_team: str = None):
    """Alias de compatibilidade retroativa."""
    return analyze_match_with_ai(match_id, home_team=home_team, away_team=away_team)


def build_db_only_analysis(match_id: str, home_name: str, away_name: str, historico):
    """
    Gera análise determinística apenas com histórico do banco (sem LLM e sem web).
    """
    sample = historico or []
    n = len(sample)

    home_wins = 0
    away_wins = 0
    draws = 0
    total_goals = 0

    for r in sample:
        tc = (r.get("time_casa") or "").strip().lower()
        tf = (r.get("time_fora") or "").strip().lower()
        gc = int(r.get("gols_casa") or 0)
        gf = int(r.get("gols_fora") or 0)
        total_goals += gc + gf

        if gc == gf:
            draws += 1
            continue

        if home_name.strip().lower() == tc:
            if gc > gf:
                home_wins += 1
            else:
                away_wins += 1
        elif home_name.strip().lower() == tf:
            if gf > gc:
                home_wins += 1
            else:
                away_wins += 1
        else:
            # Sem identificação direta do mandante, conta tendência global como neutra
            if gc > gf:
                home_wins += 1
            else:
                away_wins += 1

    # Laplace smoothing para evitar 0%
    den = (home_wins + draws + away_wins) + 3
    p_home = int(round(((home_wins + 1) / den) * 100))
    p_draw = int(round(((draws + 1) / den) * 100))
    p_away = max(0, 100 - p_home - p_draw)

    avg_goals = (total_goals / n) if n else 0.0
    goal_probability = min(95, max(5, int(round(15 + (avg_goals * 12)))))
    card_home = min(90, max(10, int(round(35 + (p_away - p_home) * 0.15))))
    card_away = min(90, max(10, int(round(35 + (p_home - p_away) * 0.15))))
    penalty_risk = min(70, max(5, int(round(8 + avg_goals * 5))))

    predicted = "Empate"
    if p_home > max(p_draw, p_away):
        predicted = home_name
    elif p_away > max(p_draw, p_home):
        predicted = away_name

    confidence = min(92, max(25, int(round(25 + n * 6))))

    momentum_home = [max(5, min(95, p_home + ((i % 5) - 2) * 2)) for i in range(15)]
    momentum_away = [max(5, min(95, p_away + (2 - (i % 5)) * 2)) for i in range(15)]

    return {
        "matchId": str(match_id),
        "winProbability": {"home": p_home, "draw": p_draw, "away": p_away},
        "goalProbabilityNextMinute": goal_probability,
        "cardRiskHome": card_home,
        "cardRiskAway": card_away,
        "penaltyRisk": penalty_risk,
        "momentumHome": momentum_home,
        "momentumAway": momentum_away,
        "commentary": [
            f"Modo estrito de banco ativo: análise calculada com {n} jogos históricos no Neon para {home_name} e {away_name}.",
            "Sem busca web e sem geração livre da IA; resultado derivado apenas de estatísticas históricas armazenadas.",
        ],
        "predictedWinner": predicted,
        "confidenceScore": confidence,
    }
