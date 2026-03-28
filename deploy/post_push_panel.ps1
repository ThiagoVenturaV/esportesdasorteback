param(
  [string]$BackendUrl = "https://esportesdasorteback-production-7ace.up.railway.app"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
  $ts = Get-Date -Format "yyyyMMdd-HHmmss"
  $reportDir = Join-Path $PSScriptRoot "reports"
  if (-not (Test-Path $reportDir)) {
    New-Item -ItemType Directory -Path $reportDir | Out-Null
  }

  $commit = (git rev-parse --short HEAD).Trim()
  $branch = (git rev-parse --abbrev-ref HEAD).Trim()

  function Get-StatusCode([string]$url) {
    try {
      return (Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 25).StatusCode
    } catch {
      if ($_.Exception.Response) {
        return [int]$_.Exception.Response.StatusCode.value__
      }
      return -1
    }
  }

  $health = Get-StatusCode "$BackendUrl/health"
  $analisar = Get-StatusCode "$BackendUrl/api/analisar/123?home_team=Flamengo&away_team=Palmeiras"
  $salvas = Get-StatusCode "$BackendUrl/api/analises-salvas/123?home_team=Flamengo&away_team=Palmeiras"
  $live = Get-StatusCode "$BackendUrl/api/analises-ao-vivo?limit=2"
  $chat = Get-StatusCode "$BackendUrl/api/chat"

  $reportPath = Join-Path $reportDir ("panel-report-" + $ts + ".md")

  $content = @"
# Relatorio Pos-Push - Backend

Data: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Branch: $branch
Commit: $commit
Backend URL: $BackendUrl

## Smoke Tests de Producao
- GET /health: $health
- GET /api/analisar/{id}: $analisar
- GET /api/analises-salvas/{id}: $salvas
- GET /api/analises-ao-vivo: $live
- POST /api/chat (sem payload): $chat

## Gate rapido (passa/falha)
- API basica no ar: $(if ($health -eq 200) { "PASS" } else { "FAIL" })
- Analise frontend contrato: $(if ($analisar -eq 200) { "PASS" } else { "FAIL" })
- Analises ao vivo: $(if ($live -eq 200) { "PASS" } else { "FAIL" })

## Painel QA v3 (preenchimento rapido)
- BLOCO 1 Runtime [CTO+QA]: atualizar score e bugs com arquivo:linha
- BLOCO 2 Repositorio [Code Reviewer]: atualizar red flags e consistencia
- BLOCO 3 Aderencia ao desafio [Idealizador]: atualizar interpretacao vs exibicao
- BLOCO 4 Produto [CEO]: atualizar custo/risco/regulatorio
- BLOCO 5 Dados [Data Engineer]: atualizar fundamentacao estatistica
- BLOCO 6 Score Hackathon: consolidar 6 criterios
- BLOCO 7 Plano de melhorias: Grupo A/B/C com impacto na nota
- BLOCO 8 Veredicto final: nota atual, nota possivel e ordem de execucao

## Proximas acoes sugeridas automaticamente
1. Se algum endpoint != 200, investigar deploy/env no Railway antes de demo.
2. Se chat != 200 esperado, validar contrato de payload do frontend.
3. Atualizar este arquivo com os 8 blocos completos antes da apresentacao.
"@

  Set-Content -Path $reportPath -Value $content -Encoding UTF8
  Write-Host "[post-push-panel] Relatorio gerado: $reportPath"
} finally {
  Pop-Location
}
