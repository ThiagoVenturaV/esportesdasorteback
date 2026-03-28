"""
analysis/router.py — Rotas de análise de partidas
"""

from fastapi import APIRouter, Depends
from analysis.service import get_saved_analysis
from odds.betsapi import fetch_live_matches


router = APIRouter(prefix="/api", tags=["analysis"])


def _safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _extract_live_fields(match: dict) -> dict:
    home_team = (
        match.get("home")
        or match.get("home_team")
        or match.get("home_team_name")
        or match.get("home_name")
        or match.get("team_home")
        or "Time Casa"
    )
    away_team = (
        match.get("away")
        or match.get("away_team")
        or match.get("away_team_name")
        or match.get("away_name")
        or match.get("team_away")
        or "Time Fora"
    )

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

    league_name = (
        match.get("league")
        or match.get("league_name")
        or match.get("competition")
        or match.get("tournament")
        or "Ao Vivo"
    )

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
        "league_name": str(league_name),
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
    
    return analysis


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
        analysis = saved if isinstance(saved, dict) else _default_analysis(
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
