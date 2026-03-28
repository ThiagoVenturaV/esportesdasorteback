"""
odds/router.py — Rotas de odds e apostas
"""

from fastapi import APIRouter, HTTPException

from odds.betsapi import fetch_live_matches, fetch_upcoming_matches
from odds.sportingtech import get_sportingtech_fixture_match_with_markets


router = APIRouter(prefix="/api", tags=["odds"])


@router.get("/apostas")
async def get_apostas():
    """Retorna apostas disponíveis (abertas) para o frontend."""
    upcoming = fetch_upcoming_matches() or []
    return {"apostas": upcoming}


@router.get("/apostas/abertas")
async def get_apostas_abertas():
    """Lista de partidas/apostas abertas."""
    upcoming = fetch_upcoming_matches() or []
    return {"apostas": upcoming}


@router.get("/apostas/finalizadas")
async def get_apostas_finalizadas():
    """Lista de apostas finalizadas.

    Enquanto não há integração com histórico de tickets, retorna lista vazia.
    """
    return {"apostas": []}


@router.get("/odds/{fixture_id}")
async def get_odds_fixture(fixture_id: str):
    """Retorna odds de uma fixture específica."""
    match = get_sportingtech_fixture_match_with_markets(fixture_id)
    if not match:
        raise HTTPException(status_code=404, detail="Odds não encontradas")
    return match


@router.get("/odds/live")
async def get_odds_live():
    """Odds de partidas ao vivo para consumo no frontend."""
    live = fetch_live_matches() or []
    return {"matches": live}
