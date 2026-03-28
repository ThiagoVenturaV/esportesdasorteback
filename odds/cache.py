"""
odds/cache.py — Cache de odds com TTL
"""

import os
from datetime import datetime, timedelta

# TTLs para cache de odds
ODDS_CACHE_TTL_MINUTES = int(os.getenv("ODDS_CACHE_TTL_MINUTES", "5"))
UPCOMING_ODDS_CACHE_TTL_HOURS = int(os.getenv("UPCOMING_ODDS_CACHE_TTL_HOURS", "24"))

# Caches em memória
_ODDS_CACHE = {}  # {match_id: {"data": {...}, "timestamp": datetime}}
_UPCOMING_CACHE = {}  # {match_id: {"data": {...}, "timestamp": datetime}}


def is_cache_valid(timestamp: datetime, ttl_minutes: int) -> bool:
    """Verifica se um cache ainda é válido."""
    expiration = timestamp + timedelta(minutes=ttl_minutes)
    return datetime.utcnow() < expiration


def get_live_odds(match_id: str) -> dict | None:
    """Obtém odds ao vivo do cache se ainda válido."""
    if match_id in _ODDS_CACHE:
        cache_data = _ODDS_CACHE[match_id]
        if is_cache_valid(cache_data["timestamp"], ODDS_CACHE_TTL_MINUTES):
            print(f"[ODDS] Cache HIT para live odds: {match_id}")
            return cache_data["data"]
        else:
            del _ODDS_CACHE[match_id]
            print(f"[ODDS] Cache EXPIRED para live odds: {match_id}")
    
    print(f"[ODDS] Cache MISS para live odds: {match_id}")
    return None


def set_live_odds(match_id: str, odds_data: dict) -> None:
    """Salva odds ao vivo no cache."""
    _ODDS_CACHE[match_id] = {
        "data": odds_data,
        "timestamp": datetime.utcnow()
    }
    print(f"[ODDS] Salvo em cache: {match_id} (TTL: {ODDS_CACHE_TTL_MINUTES} min)")


def get_upcoming_odds(match_id: str) -> dict | None:
    """Obtém odds de partida futura do cache se ainda válido."""
    if match_id in _UPCOMING_CACHE:
        cache_data = _UPCOMING_CACHE[match_id]
        if is_cache_valid(cache_data["timestamp"], UPCOMING_ODDS_CACHE_TTL_HOURS * 60):
            print(f"[ODDS] Cache HIT para upcoming odds: {match_id}")
            return cache_data["data"]
        else:
            del _UPCOMING_CACHE[match_id]
            print(f"[ODDS] Cache EXPIRED para upcoming odds: {match_id}")
    
    print(f"[ODDS] Cache MISS para upcoming odds: {match_id}")
    return None


def set_upcoming_odds(match_id: str, odds_data: dict) -> None:
    """Salva odds de partida futura no cache."""
    _UPCOMING_CACHE[match_id] = {
        "data": odds_data,
        "timestamp": datetime.utcnow()
    }
    print(f"[ODDS] Salvo em cache: {match_id} (TTL: {UPCOMING_ODDS_CACHE_TTL_HOURS} h)")


def clear_expired_caches() -> None:
    """Limpa caches expirados (pode ser chamado periodicamente)."""
    expired_live = [
        k for k, v in _ODDS_CACHE.items()
        if not is_cache_valid(v["timestamp"], ODDS_CACHE_TTL_MINUTES)
    ]
    expired_upcoming = [
        k for k, v in _UPCOMING_CACHE.items()
        if not is_cache_valid(v["timestamp"], UPCOMING_ODDS_CACHE_TTL_HOURS * 60)
    ]
    
    for k in expired_live:
        del _ODDS_CACHE[k]
    for k in expired_upcoming:
        del _UPCOMING_CACHE[k]
    
    if expired_live or expired_upcoming:
        print(f"[ODDS] Limpeza de cache: {len(expired_live)} live, {len(expired_upcoming)} upcoming")
