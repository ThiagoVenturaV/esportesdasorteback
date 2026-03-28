"""
rag_service.py — COMPATIBILIDADE RETROATIVA

Este arquivo é um wrapper fino sobre analysis.service para que scripts
e referências antigas continuem funcionando.
"""

import os

# Importar configurações de TTL
ANALYSIS_TTL_LIVE_MIN = int(os.getenv("ANALYSIS_TTL_LIVE_MINUTES", "5"))
ANALYSIS_TTL_UPCOMING_H = int(os.getenv("ANALYSIS_TTL_UPCOMING_HOURS", "24"))

from analysis.service import (
    fetch_live_matches,
    fetch_upcoming_matches,
    get_historical_context,
    save_analysis,
    get_saved_analysis,
    analyze_match_with_ai,
    analyze_match_with_gemini,
    build_db_only_analysis,
)

