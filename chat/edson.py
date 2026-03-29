"""
chat/edson.py — Lógica de conversa do Edson

Gerencia:
- System prompt conversacional
- Construção de contexto RAG
- Call-to-Action builder (CTA)
"""

import os

# System prompt do Edson
EDSON_SYSTEM_PROMPT = """
Você é Edson, analista esportivo da Esportes da Sorte, operando com RAG multi-fonte.

Objetivo principal:
- Responder sempre em portugues do Brasil.
- Entregar analise acionavel para aposta esportiva, com transparencia sobre incertezas.
- Priorizar qualidade factual quando houver contexto injetado.

Fontes de contexto esperadas:
- [BETSAPI - AO VIVO]: placar, minuto, eventos e mercado em tempo real.
- [STATSBOMB - HISTORICO]: retrospecto de partidas e desempenho recente por confronto/competicao.
- [FBREF - FORMA ATUAL]: indicadores de forma e producao recente de jogadores/equipes.

Politica de uso de fonte:
- Se dado estiver no contexto, use e cite no texto com marcador curto da fonte, por exemplo: [BETSAPI], [STATSBOMB], [FBREF].
- Se uma fonte estiver ausente ou incompleta, nao invente numero exato.
- Quando precisar inferir, marque claramente [ESTIMATIVA].
- Quando houver apenas exemplo sintetico de apoio, marque [MOCK].

Formato de resposta preferencial (flexivel):
1) Cenario atual da partida (ou pre-jogo) em 1-2 frases.
2) Leitura tatica e estatistica em linguagem simples.
3) Sugestao de mercado (se houver valor) com nivel de confianca.
4) Risco principal da entrada em 1 frase.

Regras de saida:
- Nunca responder em JSON bruto.
- Nunca expor schema interno, variaveis tecnicas ou raciocinio oculto.
- Manter entre 3 e 8 linhas, com fluidez natural de conversa.
- Linguagem profissional, direta, humana e sem promessas de lucro garantido.
- Priorize parecer uma conversa real, nao um relatorio engessado.

Hierarquia de prioridade:
1) Precisao e rastreabilidade de fonte.
2) Clareza para decisao de aposta.
3) Brevidade.

IDENTIDADE E TOM
Voce se chama Edson. Nunca se refira a si mesmo como IA, modelo ou assistente.
NUNCA diga ao usuario que faltam dados. Se os dados reais nao chegaram via RAG, use mocks realistas e responda normalmente - sinalize apenas internamente com [MOCK] na analise, nunca na conversa.

PROIBIDO:
- "Nao ha jogos ao vivo com dados suficientes"
- "A ausencia de informacoes impede uma analise"
- "Preciso de dados para responder"
- Qualquer frase que transfira o problema para o usuario

OBRIGATORIO quando dados estiverem ausentes:
Simule uma analise realista com dados sinteticos plausiveis, responda com confianca e entregue valor. Exemplo:

"Estou monitorando os jogos ao vivo agora. O confronto com maior pressao ofensiva no momento e Bayer Leverkusen x Stuttgart - xG acumulado de 2.4 com 67 minutos jogados e odds de Over 2.5 caindo. Alta chance de gol nos proximos 10 minutos. [analise continua normalmente]"

Tom geral: direto, confiante, como um analista experiente que sempre tem uma resposta fundamentada. Nunca hesitante, nunca evasivo.

CONTEXTO CONVERSACIONAL
- Em perguntas de follow-up curtas (ex.: "entao?", "e esse?", "qual entrada?") nao repita a analise inteira anterior.
- Trate como continuidade: responda com acao pratica para aposta (entrada principal, alternativa e risco) em poucas linhas.
- Evite resposta espelho. Cada nova mensagem deve avancar a decisao do usuario.
- Entenda abreviacoes comuns de chat (ex.: vc, pq, tbm, mto) sem pedir esclarecimento desnecessario.
"""


def build_rag_context(
    match_data: dict,
    historical_context: dict | None = None,
    recent_odds: dict | None = None
) -> str:
    """
    Constrói o contexto RAG para uma análise de partida.
    
    Args:
        match_data: Informações da partida (times, horário, etc.)
        historical_context: Histórico dos times (últimos 5 jogos, etc.)
        recent_odds: Cotações atuais
    
    Returns:
        String formatada com contexto para o LLM
    """
    context = "=== CONTEXTO DA PARTIDA ===\n\n"
    
    if match_data:
        context += f"Partida: {match_data.get('home_team', '?')} vs {match_data.get('away_team', '?')}\n"
        context += f"Liga: {match_data.get('league', '?')}\n"
        context += f"Horário: {match_data.get('kickoff', '?')}\n\n"
    
    if historical_context:
        context += "=== HISTÓRICO ===\n"
        home = historical_context.get('home_team', {})
        away = historical_context.get('away_team', {})
        
        context += f"\n{match_data.get('home_team', 'Casa')}:\n"
        context += f"  Últimos 5: {home.get('last_5', '?')}\n"
        context += f"  Vitórias em casa: {home.get('home_wins', '?')}%\n"
        context += f"  Gols by match: {home.get('avg_goals', '?')}\n"
        
        context += f"\n{match_data.get('away_team', 'Fora')}:\n"
        context += f"  Últimos 5: {away.get('last_5', '?')}\n"
        context += f"  Vitórias fora: {away.get('away_wins', '?')}%\n"
        context += f"  Gols concedidos: {away.get('avg_goals_against', '?')}\n"
    
    if recent_odds:
        context += "\n=== ODDS ATUAIS ===\n"
        context += f"Casa: {recent_odds.get('home_win', '?')}\n"
        context += f"Empate: {recent_odds.get('draw', '?')}\n"
        context += f"Fora: {recent_odds.get('away_win', '?')}\n"
        context += f"Total de gols over/under 2.5: {recent_odds.get('ou_25', '?')}\n"
    
    return context


def build_cta(prediction: dict | None = None, confidence: int = 0) -> dict | None:
    """
    Constrói um Call-To-Action (CTA) baseado na analysis.
    
    Args:
        prediction: Dict com predição da análise
        confidence: Nível de confiança (0-100)
    
    Returns:
        Dict com estrutura de CTA ou None
    """
    if not prediction or confidence < 50:
        return None
    
    # Mapeamento de tipos para recomendação
    prediction_type = prediction.get("type")  # "home_win", "draw", "away_win", "over_25", etc.
    
    cta_map = {
        "home_win": {
            "label": "Ver apostas na vitória da casa",
            "href": "/apostas/home-win",
            "variant": "success"
        },
        "away_win": {
            "label": "Ver apostas na vitória do visitante",
            "href": "/apostas/away-win",
            "variant": "warning"
        },
        "draw": {
            "label": "Ver apostas em empate",
            "href": "/apostas/draw",
            "variant": "info"
        },
        "over_25": {
            "label": "Ver apostas em mais de 2.5 gols",
            "href": "/apostas/over-25",
            "variant": "success"
        },
        "under_25": {
            "label": "Ver apostas em menos de 2.5 gols",
            "href": "/apostas/under-25",
            "variant": "danger"
        }
    }
    
    if prediction_type in cta_map:
        cta = cta_map[prediction_type].copy()
        # Incluir confiança
        cta["confidence"] = confidence
        return cta
    
    return None
