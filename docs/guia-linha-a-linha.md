# Guia Linha a Linha para Iniciantes

Este guia foi escrito para quem nunca programou.

Objetivo:

- explicar o projeto de forma simples
- mostrar o que cada arquivo principal faz
- explicar linha a linha os arquivos mais importantes
- mostrar como o restante dos arquivos se conecta

## 1. O que este projeto faz (em linguagem simples)

O sistema tem 2 partes:

1. Frontend (a tela que a pessoa usa)
2. Backend (o servidor que busca dados, fala com banco e responde para o frontend)

Fluxo simples:

1. Usuario abre a pagina.
2. Frontend pede dados para o backend.
3. Backend busca no banco/API externa/IA.
4. Backend devolve JSON.
5. Frontend mostra na tela.

## 2. Mapa completo do projeto

## 2.1 Frontend (pasta esportesdasorte)

Arquivos e pastas principais:

- src/main.jsx: ponto inicial da aplicacao React
- src/App.jsx: mapa de rotas (qual URL abre qual pagina)
- src/api/: funcoes que chamam o backend
- src/pages/: telas principais (Home, Live, Analise, Apostas, Login)
- src/components/: pecas visuais reutilizaveis (cards, menu, layout)
- src/services/: servicos de autenticacao, sessao e IA
- src/config/: rotas, URL do backend, configuracoes centrais
- src/styles/: estilos globais e tokens visuais

## 2.2 Backend (pasta esportesdasorteback)

Arquivos e pastas principais:

- main.py: inicia API FastAPI e registra rotas
- auth/: login, token JWT, conta do usuario
- analysis/: geracao e leitura de analises de partida
- chat/: endpoint do chat do Edson
- odds/: endpoints de odds e apostas
- live/: worker para atualizar partidas ao vivo
- db/: conexao e queries do banco Neon/PostgreSQL
- docs/: documentacao MkDocs

## 3. Explicacao linha a linha - Frontend

## 3.1 Arquivo src/main.jsx (entrada da aplicacao)

Codigo:

```jsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import '@/styles/global.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

Linha por linha:

1. `import React from 'react';`
   Importa a biblioteca React.
2. `import ReactDOM from 'react-dom/client';`
   Importa a parte que "desenha" React no HTML.
3. `import App from './App.jsx';`
   Importa o componente principal da aplicacao.
4. `import '@/styles/global.css';`
   Importa estilos globais (cores base, reset etc).
5. `ReactDOM.createRoot(document.getElementById('root')).render(...)`
   Encontra a div `root` no HTML e monta a aplicacao dentro dela.
6. `<React.StrictMode>`
   Modo de desenvolvimento para alertar problemas comuns.
7. `<App />`
   Renderiza o componente principal.

## 3.2 Arquivo src/App.jsx (roteamento)

O que esse arquivo faz:

- decide qual pagina abrir para cada URL
- protege paginas que exigem login
- controla exibicao de modal de autenticacao

Leitura simples do fluxo:

1. Importa BrowserRouter, Routes e Route.
2. Importa paginas (HomePage, LivePage, AnalysisPage etc).
3. Define funcao `AppRoutes()` que le a URL atual.
4. Monta lista de rotas publicas e privadas.
5. Se rota nao existir, redireciona para Home.
6. Renderiza `BetSlipModal` global.
7. Funcao `App()` embrulha tudo em `BetSlipProvider` + `BrowserRouter`.

## 3.3 Arquivo src/api/analysis.js (analise de partida)

O que esse arquivo faz:

- chama endpoint de analise no backend
- normaliza resposta para formato padrao
- aplica fallback se backend falhar

Linha a linha dos blocos principais:

1. Importa `fetchWithBackendFallback` e `getAuthHeaders`.
2. Funcao `toInt`: converte valor para numero inteiro com fallback.
3. Funcao `normalizeAnalysisPayload`: padroniza resposta antiga/atual.
4. Funcao `buildFallbackAnalysis`: cria analise minima para nao quebrar tela.
5. `getMatchAnalysis(matchId, matchContext)`:
   - monta query string com nomes dos times
   - chama `/api/analisar/{matchId}`
   - se erro HTTP, tenta analise salva
   - se tudo falhar, devolve fallback
6. `getSavedMatchAnalysis(matchId, matchContext)`:
   - chama `/api/analises-salvas/{matchId}`
   - retorna analise persistida para "first paint" rapido

## 4. Explicacao linha a linha - Backend

## 4.1 Arquivo main.py (entrada da API)

Codigo mental simplificado:

1. Carrega variaveis de ambiente (`load_dotenv`).
2. Define modelos de IA padrao (`GROQ_MODEL_CHAT`, `GROQ_MODEL`).
3. Importa FastAPI, CORS e roteadores de modulos.
4. Monta lista de origens CORS permitidas.
5. Cria app FastAPI com titulo e versao.
6. Adiciona middleware CORS.
7. Se `slowapi` estiver instalado, ativa rate limiting.
8. Registra routers:
   - auth
   - analysis
   - chat
   - odds
9. Cria endpoint `/health`.
10. Cria endpoint `/health/detailed` (inclui cheque de banco e chaves).
11. No startup:
    - garante tabela de contexto do Edson
    - inicia worker de refresh ao vivo
12. No shutdown:
    - fecha pool de conexoes do banco

## 4.2 Arquivo analysis/router.py (rotas de analise)

Objetivo do arquivo:

- entregar analise pronta ao frontend, com estabilidade

Blocos importantes explicados:

1. `_safe_int`: evita quebrar quando numero vem invalido.
2. `_display_name`: transforma dados confusos em nome legivel.
3. `_normalize_predicted_winner`: converte `home_win/draw/away_win` para texto.
4. `_extract_live_fields`: extrai dados padrao de partidas ao vivo.
5. `_default_analysis`: fallback minimo quando IA nao respondeu.
6. `_normalize_analysis_payload`: padroniza JSON final da analise.
7. `GET /api/analise/{match_id}`:
   - le analise salva respeitando TTL
8. `GET /api/analises-salvas/{match_id}`:
   - endpoint para pegar analise persistida
9. `GET /api/analisar/{match_id}`:
   - estrategia DB-first: busca cache, senao gera IA e salva
10. `GET /api/analises-ao-vivo`:

- devolve lista de partidas ao vivo com analises

## 4.3 Arquivo auth/service.py (JWT)

Linha a linha do essencial:

1. Le `JWT_SECRET` do ambiente.
2. Se nao existir segredo, interrompe app com erro (seguranca).
3. `create_access_token(user_id, email)`:
   - cria payload com `sub`, `email`, `exp`, `iat`
   - assina token com algoritmo HS256
4. `get_current_user(credentials)`:
   - le token Bearer do header
   - decodifica e valida
   - se expirado: erro 401
   - se invalido: erro 401

## 4.4 Arquivo db/neon.py (pool de conexoes)

Como entender este arquivo:

1. Define `_pool = None` (pool ainda nao criado).
2. `get_pool()`:
   - cria pool na primeira chamada (lazy init)
   - usa `NEON_URL`, `POOL_MIN_CONN`, `POOL_MAX_CONN`
3. `get_db_connection()`:
   - pega uma conexao livre do pool
4. `release_connection(conn)`:
   - devolve conexao para reutilizacao
5. `close_pool()`:
   - encerra todas as conexoes no shutdown
6. `get_pool_status()`:
   - retorna estado do pool para monitoramento

## 5. Como tudo conversa junto (passo a passo real)

Exemplo: abrir pagina de analise da partida.

1. Frontend abre rota `/analise/123`.
2. `AnalysisPage.jsx` chama `getSavedMatchAnalysis(123)`.
3. Backend rota `/api/analises-salvas/123` consulta banco.
4. Se nao achar, frontend chama `getMatchAnalysis(123)`.
5. Backend rota `/api/analisar/123`:
   - tenta cache no banco
   - se nao tiver, usa IA
   - salva no banco
6. Backend devolve JSON normalizado.
7. Frontend mostra grafico, comentarios e riscos.

## 6. Dicionario rapido para quem nunca programou

- API: endereco que devolve dados
- Endpoint: rota especifica da API (ex: `/api/login`)
- JSON: formato de texto para dados
- Frontend: parte visual
- Backend: servidor com regra de negocio
- Banco de dados: local onde dados ficam salvos
- Cache: copia temporaria para acelerar resposta
- Token JWT: comprovante digital de login
- Middleware: camada executada antes das rotas
- Fallback: plano B quando algo falha

## 7. Como continuar esta documentacao

Para cobrir literalmente 100% linha a linha de todos os arquivos, o ideal e continuar por lotes:

1. modulo `auth` completo
2. modulo `chat` completo
3. modulo `odds` completo
4. paginas do frontend uma por uma

Esse arquivo ja cobre os pontos centrais de arquitetura e execucao do sistema, com linguagem para iniciantes.
