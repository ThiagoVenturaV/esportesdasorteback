"""
analysis/router.py — Rotas de análise de partidas
"""

from fastapi import APIRouter, Depends
from analysis.service import get_saved_analysis

router = APIRouter(prefix="/api", tags=["analysis"])


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
