# Runbook Railway

Guia operacional para deploy seguro do backend no Railway.

## 1. Pre-deploy checklist

- [ ] Branch `main` atualizado e sem conflitos.
- [ ] Variaveis de ambiente revisadas (`GEMINI_API_KEY`, `GROQ_API_KEY`, `NEON_URL`, `JWT_SECRET`).
- [ ] Endpoints criticos validados localmente (`/health`, `/api/chat`, `/api/analisar`).
- [ ] Mudancas de schema avaliadas antes do release.

## 2. Deploy padrao

1. Fazer commit e push para `main`.
2. Abrir o servico no Railway.
3. Confirmar novo deployment em andamento.
4. Aguardar status `SUCCESS`.
5. Ver logs de startup e confirmar sem erro de import/config.

## 3. Smoke test pos-deploy

Use este script PowerShell:

```powershell
$base='https://esportesdasorteback-production-7ace.up.railway.app'

Write-Host '--- health ---'
(Invoke-WebRequest -Uri "$base/health" -UseBasicParsing -TimeoutSec 30).StatusCode

Write-Host '--- chat ---'
$body = @{ message='resuma risco principal de Flamengo x Palmeiras'; history=@(); conversation_history=@() } | ConvertTo-Json -Depth 6
(Invoke-WebRequest -Uri "$base/api/chat" -Method POST -ContentType 'application/json' -Body $body -UseBasicParsing -TimeoutSec 30).StatusCode

Write-Host '--- analisar ---'
(Invoke-WebRequest -Uri "$base/api/analisar/123?home_team=Flamengo&away_team=Palmeiras" -UseBasicParsing -TimeoutSec 30).StatusCode

Write-Host '--- analises ao vivo ---'
(Invoke-WebRequest -Uri "$base/api/analises-ao-vivo?limit=2" -UseBasicParsing -TimeoutSec 30).StatusCode
```

## 4. Rollback rapido

Se o deploy atual quebrar:

1. Railway > Deployments.
2. Selecionar ultimo deployment estavel.
3. Acionar rollback/redeploy da versao estavel.
4. Rodar smoke test novamente.

## 5. Alertas comuns e acao

### Erro 503 em `/api/chat`

- Verificar `GEMINI_API_KEY` e `GROQ_API_KEY`.
- Conferir se fallback esta ativo (Gemini primario, Groq fallback).

### Erro 404 em `/api/analisar/{id}`

- Confirmar roteador de `analysis` incluido no `main.py`.
- Confirmar que o deploy novo foi realmente aplicado.

### Latencia alta

- Verificar cache DB-first.
- Reduzir `CHAT_HISTORY_MAX_TURNS` e `ANALYSIS_MODEL_MAX_TOKENS` se necessario.

## 6. Variaveis recomendadas (producao)

```env
GEMINI_MODEL_CHAT=gemini-2.5-flash-lite
GEMINI_MODEL_ANALYSIS=gemini-2.5-flash-lite
CHAT_HISTORY_MAX_TURNS=8
CHAT_RESPONSE_MAX_TOKENS=220
ANALYSIS_MODEL_MAX_TOKENS=260
ANALYSIS_TTL_LIVE_MINUTES=5
ANALYSIS_TTL_UPCOMING_HOURS=24
```

## 7. Definicao de pronto

Release considerado pronto quando:

- [ ] `/health` retorna 200
- [ ] `/api/chat` retorna 200
- [ ] `/api/analisar/{id}` retorna 200
- [ ] Tela de analise abre no frontend sem erro
- [ ] Logs sem excecoes criticas nos primeiros minutos
