"""
analysis/router.py — Rotas de análise de partidas
"""

import ast

from fastapi import APIRouter, Depends
from analysis.service import get_saved_analysis, analyze_match_with_ai, save_analysis
from odds.betsapi import fetch_live_matches


router = APIRouter(prefix="/api", tags=["analysis"])


def _safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _display_name(value, fallback: str) -> str:
    """Converte dicts/strings serializadas em nomes legíveis para o frontend."""
    if value is None:
        return fallback

    if isinstance(value, dict):
        return str(value.get("name") or value.get("short_name") or value.get("title") or fallback)

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return fallback

        if (stripped.startswith("{") and stripped.endswith("}")) or (
            stripped.startswith("[") and stripped.endswith("]")
        ):
            try:
                parsed = ast.literal_eval(stripped)
                if isinstance(parsed, dict):
                    return str(parsed.get("name") or parsed.get("short_name") or parsed.get("title") or fallback)
            except Exception:
                return stripped

        return stripped

    return str(value)


def _normalize_predicted_winner(value, home_team: str, away_team: str) -> str:
    """Converte tokens técnicos (home_win/draw/away_win) para texto legível."""
    normalized = _display_name(value, home_team).strip().lower()

    if normalized in {"home", "home_win", "mandante", "casa", "1"}:
        return home_team
    if normalized in {"away", "away_win", "visitante", "fora", "2"}:
        return away_team
    if normalized in {"draw", "empate", "x"}:
        return "Empate"

    return _display_name(value, home_team)


def _extract_live_fields(match: dict) -> dict:
    home_team_raw = (
        match.get("home")
        or match.get("home_team")
        or match.get("home_team_name")
        or match.get("home_name")
        or match.get("team_home")
        or "Time Casa"
    )
    away_team_raw = (
        match.get("away")
        or match.get("away_team")
        or match.get("away_team_name")
        or match.get("away_name")
        or match.get("team_away")
        or "Time Fora"
    )
    home_team = _display_name(home_team_raw, "Time Casa")
    away_team = _display_name(away_team_raw, "Time Fora")

    # BetsAPI frequentemente usa "ss" = "1-0"
    ss = str(match.get("ss") or "")
    home_score = match.get("home_score")
    away_score = match.get("away_score")
    if ss and "-" in ss and (home_score is None or away_score is None):
        parts = ss.split("-")
        if len(parts) == 2:
            home_score = _safe_int(parts[0].strip(), 0)
            away_score = _safe_int(parts[1].strip(), 0)

    timer = match.get("timer") or {}
    minute = match.get("minute")
    if minute is None:
        minute = timer.get("tm")

    league_name_raw = (
        match.get("league")
        or match.get("league_name")
        or match.get("competition")
        or match.get("tournament")
        or "Ao Vivo"
    )
    league_name = _display_name(league_name_raw, "Ao Vivo")

    match_id = (
        match.get("id")
        or match.get("match_id")
        or match.get("event_id")
        or match.get("fixture_id")
        or "0"
    )

    return {
        "match_id": str(match_id),
        "home_team": str(home_team),
        "away_team": str(away_team),
        "league_name": league_name,
        "live_data": {
            "home_score": _safe_int(home_score, 0),
            "away_score": _safe_int(away_score, 0),
            "minute": _safe_int(minute, 0),
        },
    }


def _default_analysis(home_team: str, away_team: str) -> dict:
    return {
        "winProbability": {"home": 34, "draw": 32, "away": 34},
        "confidenceScore": 52,
        "predictedWinner": home_team,
        "commentary": [
            f"{home_team} x {away_team}: dados ao vivo coletados."
        ],
        "goalProbabilityNextMinute": 42,
        "cardRiskHome": 38,
        "cardRiskAway": 36,
        "penaltyRisk": 18,
        "momentumHome": 51,
        "momentumAway": 49,
    }


def _normalize_analysis_payload(raw: dict | None, home_team: str, away_team: str) -> dict:
    if not isinstance(raw, dict):
        return _default_analysis(home_team, away_team)

    win = raw.get("winProbability") or raw.get("win_probability") or {}
    home = _safe_int(win.get("home") if isinstance(win, dict) else 0, 34)
    draw = _safe_int(win.get("draw") if isinstance(win, dict) else 0, 32)
    away = _safe_int(win.get("away") if isinstance(win, dict) else 0, 34)

    commentary = raw.get("commentary")
    if isinstance(commentary, str):
        commentary = [commentary]
    if not isinstance(commentary, list) or not commentary:
        commentary = [f"Análise de {home_team} x {away_team} baseada no contexto disponível."]

    predicted_raw = (
        raw.get("predictedWinner")
        or raw.get("prediction")
        or home_team
    )
    predicted = _normalize_predicted_winner(predicted_raw, home_team, away_team)

    return {
        "winProbability": {"home": home, "draw": draw, "away": away},
        "confidenceScore": _safe_int(raw.get("confidenceScore") or raw.get("confidence"), 52),
        "predictedWinner": predicted,
        "commentary": [str(x) for x in commentary[:4]],
        "goalProbabilityNextMinute": _safe_int(raw.get("goalProbabilityNextMinute"), 42),
        "cardRiskHome": _safe_int(raw.get("cardRiskHome"), 38),
        "cardRiskAway": _safe_int(raw.get("cardRiskAway") or raw.get("cardRisskAway"), 36),
        "penaltyRisk": _safe_int(raw.get("penaltyRisk"), 18),
        "momentumHome": _safe_int(raw.get("momentumHome"), 51),
        "momentumAway": _safe_int(raw.get("momentumAway"), 49),
    }


@router.get("/analise/{match_id}")
async def get_analysis(match_id: str, is_live: bool = True):
    """
    Obtém análise de uma partida (com cache TTL).
    
    - Partidas ao vivo: cache de 5 minutos
    - Partidas futuras: cache de 24 horas
    """
    analysis = get_saved_analysis(match_id, is_live=is_live)
    
    if not analysis:
        return {
            "erro": "Análise não encontrada ou expirada",
            "match_id": match_id
        }
    
    return _normalize_analysis_payload(analysis, "Time Casa", "Time Fora")


@router.get("/analises-salvas/{match_id}")
async def get_saved_analysis_front(match_id: str, home_team: str = "Time Casa", away_team: str = "Time Fora"):
    """Endpoint compatível com o frontend para leitura de análise já persistida."""
    # DB-first: tenta cache live e, se não houver, cache de partidas futuras.
    saved = get_saved_analysis(match_id, is_live=True)
    if not saved:
        saved = get_saved_analysis(match_id, is_live=False)
    if not saved:
        return {"sucesso": False, "analise": None}

    return {
        "sucesso": True,
        "analise": _normalize_analysis_payload(saved, home_team, away_team),
    }


@router.get("/analisar/{match_id}")
async def analyze_match_front(match_id: str, home_team: str = "Time Casa", away_team: str = "Time Fora"):
    """Endpoint compatível com o frontend para gerar análise sob demanda."""
    # DB-first para economizar tokens e reduzir latência.
    saved = get_saved_analysis(match_id, is_live=True)
    if not saved:
        saved = get_saved_analysis(match_id, is_live=False)
    if saved:
        return _normalize_analysis_payload(saved, home_team, away_team)

    # Só chama o modelo se não houver cache útil no banco.
    generated = analyze_match_with_ai(
        {
            "match_id": match_id,
            "home_team": home_team,
            "away_team": away_team,
            "league": "Futebol",
        }
    )

    if not isinstance(generated, dict):
        # Fallback determinístico para nunca quebrar a tela de análise.
        generated = _default_analysis(home_team, away_team)

    normalized = _normalize_analysis_payload(generated, home_team, away_team)
    save_analysis(match_id, normalized)
    return normalized


@router.get("/analises-ao-vivo")
async def get_live_analyses(limit: int = 8):
    """Retorna partidas ao vivo em formato pronto para o frontend."""
    live_matches = fetch_live_matches() or []

    analyses = []
    for raw_match in live_matches:
        if not isinstance(raw_match, dict):
            continue

        base = _extract_live_fields(raw_match)
        if base["match_id"] == "0":
            continue

        saved = get_saved_analysis(base["match_id"], is_live=True)
        raw_analysis = saved

        # Se não houver cache válido, tenta gerar análise real com IA.
        if not raw_analysis:
            generated = analyze_match_with_ai(
                {
                    "match_id": base["match_id"],
                    "home_team": base["home_team"],
                    "away_team": base["away_team"],
                    "league": base["league_name"],
                    "live_data": base["live_data"],
                }
            )
            if isinstance(generated, dict):
                normalized_generated = _normalize_analysis_payload(
                    generated,
                    base["home_team"],
                    base["away_team"],
                )
                save_analysis(base["match_id"], normalized_generated)
                raw_analysis = normalized_generated

        # Evita retornar insights mockados quando não existe análise real.
        if not raw_analysis:
            continue

        analysis = _normalize_analysis_payload(
            raw_analysis,
            base["home_team"],
            base["away_team"],
        )

        analyses.append(
            {
                "match_id": base["match_id"],
                "home_team": base["home_team"],
                "away_team": base["away_team"],
                "league_name": base["league_name"],
                "live_data": base["live_data"],
                "analysis": analysis,
            }
        )

        if limit > 0 and len(analyses) >= limit:
            break

    return {
        "sucesso": True,
        "analises": analyses,
        "total": len(analyses),
    }
