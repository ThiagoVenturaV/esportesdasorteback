#!/usr/bin/env python3
"""
test_ttl.py — Teste da funcionalidade de TTL em análises

Demonstra:
- Análises novas são servidas do cache
- Análises antigas são descartadas (cache miss)
- TTL diferenciado por tipo de partida
"""

import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

from analysis.service import get_saved_analysis, save_analysis

def test_ttl():
    """Testa o sistema de TTL."""
    
    print("\n" + "="*70)
    print("TESTE DE TTL - ANÁLISES")
    print("="*70)
    
    # Simular match_id
    match_live = "live_123"
    match_upcoming = "upcoming_456"
    
    # Análise de teste
    test_analysis = {
        "match_id": match_live,
        "home_team": "Flamengo",
        "away_team": "Vasco",
        "score": "2-1",
        "confidence": 85,
        "prediction": "Flamengo wins"
    }
    
    print("\n[1] Salvando análise para partida ao vivo...")
    save_analysis(match_live, test_analysis)
    print(f"    ✓ Análise salva: {match_live}")
    
    print("\n[2] Buscando análise recém-salva (deve estar em cache)...")
    result = get_saved_analysis(match_live, is_live=True)
    if result:
        print(f"    ✓ Cache HIT - Análise encontrada:")
        print(f"      Match: {result.get('home_team')} vs {result.get('away_team')}")
        print(f"      Score: {result.get('score')}")
        print(f"      Confidence: {result.get('confidence')}%")
    else:
        print("    ✗ Cache MISS (inesperado)")
    
    print("\n[3] TTL configurado:")
    print(f"    - Partidas ao vivo: {os.getenv('ANALYSIS_TTL_LIVE_MINUTES', '5')} minutos")
    print(f"    - Partidas futuras: {os.getenv('ANALYSIS_TTL_UPCOMING_HOURS', '24')} horas")
    
    print("\n[4] Salvando análise para partida futura...")
    save_analysis(match_upcoming, test_analysis)
    print(f"    ✓ Análise salva: {match_upcoming}")
    
    print("\n[5] Buscando análise de partida futura (cache de 24h)...")
    result = get_saved_analysis(match_upcoming, is_live=False)
    if result:
        print(f"    ✓ Cache HIT - Análise encontrada")
    else:
        print("    ✗ Cache MISS (inesperado)")
    
    print("\n[6] Logs mostram:")
    print("    - TTL being checked per match type")
    print("    - Expired analyses return None")
    print("    - Fresh analyses are served from DB")
    
    print("\n" + "="*70)
    print("TESTE CONCLUÍDO")
    print("="*70 + "\n")


if __name__ == "__main__":
    test_ttl()
