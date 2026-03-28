# IA Providers

Esta pagina documenta como o backend escolhe o provedor de IA em producao.

## Estrategia atual

- Primario: Gemini (`gemini-2.5-flash-lite`)
- Fallback: Groq (modelo configurado via `GROQ_MODEL_CHAT` / `GROQ_MODEL`)

Objetivo:

- reduzir indisponibilidade
- manter resposta da API mesmo quando um provedor falha
- economizar tokens com respostas curtas e cache rapido

## Variaveis obrigatorias (Railway)

```env
GEMINI_API_KEY=...
GEMINI_MODEL_CHAT=gemini-2.5-flash-lite
GEMINI_MODEL_ANALYSIS=gemini-2.5-flash-lite
GROQ_API_KEY=...
GROQ_MODEL_CHAT=openai/gpt-oss-120b
GROQ_MODEL=mixtral-8x7b-32768
```

## Regras de roteamento

### Chat (`/api/chat`)

1. tenta Gemini
2. se Gemini falhar, usa Groq
3. se ambos falharem, retorna `503`

### Analise (`/api/analisar/{match_id}`)

1. busca no banco (cache DB-first)
2. se nao encontrar, tenta Gemini
3. se Gemini falhar, tenta Groq
4. se tudo falhar, usa fallback deterministico para nao quebrar a tela

## Boas praticas

- manter as duas chaves (Gemini e Groq) ativas em producao
- nao colocar `GEMINI_API_KEY` no frontend (Vercel)
- monitorar latencia e taxa de erro por endpoint
