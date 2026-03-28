# Runbook Vercel (Frontend)

Guia operacional para deploy seguro do frontend no Vercel.

## 1. Pre-deploy checklist

- [ ] Branch `main` com mudancas revisadas.
- [ ] `VITE_BACKEND_URL` apontando para o backend de producao.
- [ ] `VITE_BACKEND_URL_FALLBACK` configurado (opcional, recomendado).
- [ ] Fluxos criticos revisados: login, Ask AI, tela de analise, ao vivo.

## 2. Variaveis de ambiente (Vercel)

No projeto do frontend, configure:

```env
VITE_BACKEND_URL=https://esportesdasorteback-production-7ace.up.railway.app
VITE_BACKEND_URL_FALLBACK=https://esportesdasorteback.onrender.com
VITE_EDSON_MAX_HISTORY=8
VITE_TYPEWRITER_SPEED=18
```

Importante:

- Nao colocar `GEMINI_API_KEY` no Vercel.
- Chaves de IA ficam somente no Railway (backend).

## 3. Deploy padrao

1. Fazer commit e push para `main`.
2. Vercel cria novo deployment automaticamente.
3. Aguardar status `Ready`.
4. Abrir URL do deployment e validar smoke test.

## 4. Smoke test pos-deploy (manual)

### Acesso e autenticacao

- [ ] Abrir Home sem erros de console.
- [ ] Fazer login com usuario valido.
- [ ] Confirmar nome abaixo do avatar no estado logado.

### Edson (Ask AI)

- [ ] Abrir modal do Edson.
- [ ] Confirmar fundo do modal sem transparencia.
- [ ] Enviar pergunta curta e receber resposta.

### Analise de partida

- [ ] Abrir card de analise na Home/Ao Vivo.
- [ ] Confirmar carregamento da pagina de analise sem tela em branco.
- [ ] Confirmar exibicao de probabilidade, commentary e momentum.

### Ao Vivo

- [ ] Conferir se lista de jogos ao vivo carrega.
- [ ] Confirmar fallback quando provider principal falha.

## 5. Rollback rapido no Vercel

1. Vercel > Deployments.
2. Selecionar ultimo deployment estavel.
3. Promover deployment estavel para producao.
4. Rodar smoke test novamente.

## 6. Erros comuns e acao

### Front sem dados de API

- Verificar `VITE_BACKEND_URL`.
- Confirmar CORS no backend (`CORS_ORIGINS`).

### Tela de analise nao abre

- Validar backend `/api/analisar/{id}` e `/api/analises-salvas/{id}`.
- Confirmar fallback de payload no frontend.

### Ask AI lento ou falhando

- Validar latencia do `/api/chat` no backend.
- Verificar status do Gemini/Groq no Railway.

## 7. Definicao de pronto

Release frontend considerado pronto quando:

- [ ] Build Vercel `Ready`
- [ ] Login/cadastro funcionando
- [ ] Nome aparece abaixo do avatar logado
- [ ] Ask AI responde
- [ ] Tela de analise abre sem erro
- [ ] Fluxo Ao Vivo carregando normalmente
