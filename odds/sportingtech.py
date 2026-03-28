"""
odds/sportingtech.py — Integração com Sportingtech para odds

Nota: Sportingtech é uma alternativa a BetsAPI com fallback.
Para this version, retorna dados mock enquanto se integra com a API real.
"""

import os
from datetime import datetime

SPORTINGTECH_API_KEY = os.getenv("SPORTINGTECH_API_KEY")
SPORTINGTECH_BASE = os.getenv("SPORTINGTECH_BASE_URL", "https://api.sportingtech.com")


def get_live_odds_matches(sport: str = "soccer") -> list | None:
    """
    Busca partidas ao vivo com odds do Sportingtech.
    
    Args:
        sport: Esporte (soccer, basketball, etc.)
    
    Returns:
        Lista de partidas com odds ou None se erro
    """
    # TODO: Implementar integração real com Sportingtech
    # Por agora, retorna mock ou fallback para BetsAPI
    
    print(f"[SPORTINGTECH] Buscando live matches para {sport}")
    
    if not SPORTINGTECH_API_KEY:
        print("[SPORTINGTECH] Chave não configurada, usando fallback (BetsAPI)")
        from odds.betsapi import fetch_live_matches
        return fetch_live_matches()
    
    try:
        # Placeholder para implementação futura
        return []
    except Exception as e:
        print(f"[SPORTINGTECH] Erro: {e}, fazendo fallback para BetsAPI")
        from odds.betsapi import fetch_live_matches
        return fetch_live_matches()


def get_upcoming_odds_matches(sport: str = "soccer", days: int = 7) -> list | None:
    """
    Busca partidas futuras com odds do Sportingtech.
    
    Args:
        sport: Esporte
        days: Dias no futuro
    
    Returns:
        Lista de partidas com odds ou None se erro
    """
    print(f"[SPORTINGTECH] Buscando upcoming matches ({days} dias)")
    
    if not SPORTINGTECH_API_KEY:
        print("[SPORTINGTECH] Chave não configurada, usando fallback (BetsAPI)")
        from odds.betsapi import fetch_upcoming_matches
        return fetch_upcoming_matches(days=days)
    
    try:
        # Placeholder para implementação futura
        return []
    except Exception as e:
        print(f"[SPORTINGTECH] Erro: {e}, fazendo fallback para BetsAPI")
        from odds.betsapi import fetch_upcoming_matches
        return fetch_upcoming_matches(days=days)


def get_sportingtech_fixture_match_with_markets(fixture_id: str) -> dict | None:
    """
    Retorna dados de odds por fixture.

    Estratégia atual:
    - Se Sportingtech não estiver configurado, faz fallback para BetsAPI.
    - Quando a integração Sportingtech estiver pronta, substituir a implementação.
    """
    if not fixture_id:
        return None

    if not SPORTINGTECH_API_KEY:
        from odds.betsapi import get_odds_for_match

        odds = get_odds_for_match(fixture_id)
        if not odds:
            return None

        return {
            "fixture_id": str(fixture_id),
            "provider": "betsapi",
            "markets": odds,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
        }

    try:
        # TODO: integrar endpoint real da Sportingtech aqui.
        # Mantido fallback seguro para não quebrar o frontend.
        from odds.betsapi import get_odds_for_match

        odds = get_odds_for_match(fixture_id)
        if not odds:
            return None

        return {
            "fixture_id": str(fixture_id),
            "provider": "sportingtech-fallback-betsapi",
            "markets": odds,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as e:
        print(f"[SPORTINGTECH] Erro ao buscar fixture {fixture_id}: {e}")
        return None
