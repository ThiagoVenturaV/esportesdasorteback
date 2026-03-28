# Fluxo de Analise (DB-first)

Esta pagina descreve o fluxo mais rapido para manter as telas de analise estaveis.

## Visao geral

O endpoint `GET /api/analisar/{match_id}` segue a politica DB-first:

1. tenta cache live (`is_live=true`)
2. tenta cache de partidas futuras (`is_live=false`)
3. gera analise com IA (Gemini primario, Groq fallback)
4. persiste no banco para reaproveitamento

## Por que DB-first

- menor latencia para o frontend
- menor custo de token
- resiliencia quando IA externa oscila

## Contrato esperado

Payload normalizado para o frontend:

- `winProbability.home|draw|away`
- `confidenceScore`
- `predictedWinner`
- `commentary[]`
- `goalProbabilityNextMinute`
- `cardRiskHome`
- `cardRiskAway`
- `penaltyRisk`
- `momentumHome`
- `momentumAway`

Compatibilidade legada:

- backend aceita `cardRisskAway` e converte para `cardRiskAway`

## Garantia de tela

Mesmo sem analise de IA disponivel, a API retorna fallback minimo para evitar tela quebrada.

No frontend, existe normalizacao adicional para evitar regressao com payload antigo.

## Smoke test

```powershell
$base='https://esportesdasorteback-production-7ace.up.railway.app'
Invoke-WebRequest -Uri "$base/api/analisar/123?home_team=Flamengo&away_team=Palmeiras" -UseBasicParsing
Invoke-WebRequest -Uri "$base/api/analises-salvas/123?home_team=Flamengo&away_team=Palmeiras" -UseBasicParsing
Invoke-WebRequest -Uri "$base/api/analises-ao-vivo?limit=2" -UseBasicParsing
```
