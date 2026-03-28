"""
odds/betsapi.py — Integração com BetsAPI para odds e partidas
"""

import os
import requests
from datetime import datetime

BETS_API_TOKEN = os.getenv("BETS_API_TOKEN")
BETS_API_BASE = "https://api.betsapi.com/v2"

# Headers para BetsAPI
BETS_API_HEADERS = {
    "X-API-KEY": BETS_API_TOKEN,
    "Content-Type": "application/json"
}


def fetch_live_matches(sport_id: int = 1) -> list | None:
    """
    Busca partidas ao vivo no BetsAPI.
    
    Args:
        sport_id: 1 = Soccer, 2 = Basketball, etc.
    
    Returns:
        Lista de partidas ou None se erro
    """
    try:
        url = f"{BETS_API_BASE}/events/inplay"
        params = {
            "sport_id": sport_id,
            "token": BETS_API_TOKEN
        }
        
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        
        data = resp.json()
        if data.get("success"):
            matches = data.get("results", [])
            print(f"[BETSAPI] {len(matches)} partidas ao vivo encontradas")
            return matches
        else:
            print(f"[BETSAPI] Erro na resposta: {data}")
            return None
            
    except requests.RequestException as e:
        print(f"[BETSAPI] Erro ao buscar live matches: {e}")
        return None


def fetch_upcoming_matches(sport_id: int = 1, days: int = 7) -> list | None:
    """
    Busca partidas futuras no BetsAPI.
    
    Args:
        sport_id: 1 = Soccer
        days: Quantos dias no futuro buscar
    
    Returns:
        Lista de partidas ou None se erro
    """
    try:
        url = f"{BETS_API_BASE}/events/upcoming"
        params = {
            "sport_id": sport_id,
            "token": BETS_API_TOKEN,
            "days": days
        }
        
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        
        data = resp.json()
        if data.get("success"):
            matches = data.get("results", [])
            print(f"[BETSAPI] {len(matches)} partidas futuras encontradas")
            return matches
        else:
            print(f"[BETSAPI] Erro na resposta: {data}")
            return None
            
    except requests.RequestException as e:
        print(f"[BETSAPI] Erro ao buscar upcoming matches: {e}")
        return None


def get_odds_for_match(event_id: str) -> dict | None:
    """
    Busca odds para uma partida específica.
    
    Args:
        event_id: ID da partida no BetsAPI
    
    Returns:
        Dict com odds ou None se erro
    """
    try:
        url = f"{BETS_API_BASE}/event/odds"
        params = {
            "event_id": event_id,
            "token": BETS_API_TOKEN
        }
        
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        
        data = resp.json()
        if data.get("success"):
            odds = data.get("results", {})
            print(f"[BETSAPI] Odds obtidas para evento {event_id}")
            return odds
        else:
            print(f"[BETSAPI] Erro ao buscar odds: {data}")
            return None
            
    except requests.RequestException as e:
        print(f"[BETSAPI] Erro ao buscar odds: {e}")
        return None
