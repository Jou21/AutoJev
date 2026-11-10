# AutoJev

**AI-powered exploratory testing for web applications.**

AutoJev receives an application URL, uses a deterministic policy for obvious
browser mechanics and asks a JEV model only when QA judgment is useful. A real
Playwright browser executes every validated action, captures evidence, and a
separately configurable auxiliary LLM documents the observed test scenario. At
the end it produces a standalone HTML report.

The important boundary is deliberate: **the JEV decides; Playwright executes;
the auxiliary LLM interprets evidence.** No model gets direct browser control.

## Project structure

```text
app/                 React/vinext interface
backend/autojev/     FastAPI service
  domain/            Models and replaceable ports
  adapters/          OpenAI-compatible, Playwright, SQLite, secrets, report
  services/          Exploration orchestration
  api/               HTTP schemas and routes
backend/tests/       Backend contract and security tests
ARCHITECTURE.md      Trust boundaries and status rules
```

## Requirements

- Node.js 22.13 or newer
- Python 3.11 or newer
- A reachable OpenAI-compatible endpoint with at least one model

## Run locally

Install and start the FastAPI backend in one terminal:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
AUTOJEV_HEADLESS=false uvicorn autojev.main:app --reload --host 127.0.0.1 --port 8000
```

On Windows, drop `--reload`: uvicorn then selects an asyncio loop without
subprocess support and Playwright cannot start the browser (the exploration is
cancelled as "browser closed" with an empty `NotImplementedError`).

Start the React interface in a second terminal:

```bash
npm install
npm run dev
```

Open `http://localhost:3000`. The API documentation is available at
`http://127.0.0.1:8000/docs`.

Keep `AUTOJEV_HEADLESS=false` for interactive local use: manual login requires
a visible browser window. A headless diagnostic backend must use a separate
temporary process/port and must not replace the everyday localhost backend.

The default FastAPI CORS policy also trusts the project's published Sites
origin, allowing that UI to reach a backend running on the user's machine.
Override `AUTOJEV_CORS_ORIGINS` with the exact origins used by other deployments.

The frontend uses `http://localhost:8000/api` by default. When embedding it in
another application, set this before the frontend build:

```bash
NEXT_PUBLIC_API_URL=https://your-autojev-api.example/api
```

## First exploration

1. Open **Provedores de IA** and add an OpenAI-compatible base URL.
2. Save the provider, discover or manually add its models, and test the
   connection.
3. Open **Nova exploração** and choose the JEV and auxiliary models separately.
4. Enter the target URL and choose **Aplicação inteira** or an optional focused
   screen/menu path such as `Configurações > Usuários > Permissões`.
5. Choose **Econômico · texto** (default) or **Visual · texto + imagem** for the
   auxiliary LLM and, when necessary, add ephemeral test credentials.
6. Start the exploration and follow Playwright evidence in the live view.
7. Open the standalone report from the completed exploration.

The coverage engine remembers semantic controls per route (including SPA query
and hash routes), exhausts each safe frontier, and backtracks to unfinished
parent screens. A frontier entry is an atomic move, not merely an element:
menus can expose separate `hover` and `click` moves, while a select contributes
up to eight safe option values. When a modal/dialog is visible, only controls
inside the active dialog are eligible, and close/back controls are left until
the useful controls on that surface have been exercised.

SPA states such as open menus, tabs and dialogs may share the same URL. AutoJev
stores how those states were reached as selector-free semantic transition
recipes. To resume an unfinished branch it resolves the control again from a
fresh observation and replays the safe interaction; stale Playwright refs and
CSS selectors are never durable coverage identity. Replay attempts and depth
are bounded, and an unrestorable branch is reported as blocked rather than
silently counted as covered.

Limits still bound dynamic or effectively infinite applications. A run that
hits its action, duration or planning bound, cannot restore every safe branch,
or cannot find the requested focus is labeled **Cobertura parcial**, never
falsely reported as complete. Each verified action is persisted immediately as
a local CT while browser exploration continues; one bounded auxiliary worker
documents those CTs in parallel without reducing the JEV/Playwright exploration
budget. After browser work ends, only any small remaining queue and the final
report are completed.

TypeSafe JEV receives atomic candidate moves instead of separate action/target
questions. It ranks real Playwright observations using semantic DOM context,
coverage novelty, recent outcomes and browser errors; confidence is retained
with the decision. Safe form fields/selects and same-origin new tabs are also
covered, while password, upload, destructive and cross-origin interactions stay
outside the executable frontier.

The invocation policy does not spend a JEV call to repeat a clearly identified
button/link/field, fill a conventional form, follow known navigation, replay a
known route, or confirm an HTTP error already observed by Playwright. It
escalates duplicate/ambiguous controls, a new complex state, an ambiguous
business control, a state with no safe deterministic move, or two attempts
without progress. The timeline records whether each choice came from the local
policy or from JEV.

In the default text mode, the auxiliary LLM receives compact accessible DOM,
visible text and verified Playwright results; screenshots remain local and are
still embedded in the report. Visual mode additionally sends the masked
low-detail screenshot. Explicitly rejected image input is recorded as visual
analysis unavailable; it is not silently presented as a text-only visual check.
Authentication and exhausted request budgets are not retried.

The auxiliary request budget is local and finite (120 HTTP requests by default),
independent from the number of CTs. Retries and updated evidence consume requests;
exact cached evidence does not. New executions expose this budget in the form and
use up to three review attempts, including the first, for retryable failures.
When a completed visual run has pending reviews, **Retomar revisões visuais
pendentes** reuses its saved captures without reopening the browser or repeating
the exploration. It requires an explicit cost confirmation and a new finite
request allowance. Reviews can be stopped independently of the completed run.
Older runs may lack the original semantic text context; the UI reports that
limitation instead of inventing missing evidence. An unavailable visual review
does not invalidate recorded local checks or mean that the CT was never executed,
but it must never be counted as visual approval.

Scenario verdicts come from Playwright evidence, not from model prose. A
successful action with verified assertions or an observable state change is
`PASS`; a failed assertion or ordinary execution failure is `FAIL`; a policy or
unavailable control is `BLOCKED`. A successful no-op without verifiable
evidence is recorded in the activity timeline but does not become a test case;
`SKIPPED` remains supported only for historical/imported reports. Internal
backtracking/replay actions never become test scenarios.

Each CT has an expandable evidence timeline in the exploration view and HTML
report: actions, assertions, before/after captures, retries and classified
technical signals. External telemetry and isolated request cancellations do
not automatically fail a functional interaction. Same-origin HTTP/network
failures and JavaScript exceptions remain technical checks, not automatically
confirmed business defects. Unassociated startup/restoration signals appear in
a separate, collapsible **Observações da execução** section, outside CT counts
and filters. Observations identify the recorded page, time and request endpoint;
legacy capture-time pages are not presented as event-time pages or loading phases.
Historical verdicts are preserved and
explained rather than silently changed. Repeated signals remain local evidence
without repeatedly enlarging model prompts.

Prompt, completion and total tokens are accumulated for both models. The live
browser panel and React sidebar show the running usage; exploration views and
the standalone report retain provider-reported monetary cost as **Custo JEV**,
**Custo LLM** (auxiliary) and **Custo total**, in USD. Attribution follows the
caller's role even when both roles use the same model. When a provider does not
return cost, AutoJev displays `—` instead of estimating a price; incomplete sums
are marked partial and an explicitly reported zero remains zero. Historical
runs that stored only a combined total retain it without an invented split.

Provider API keys are encrypted by FastAPI and never returned to React. Test
credentials exist only in the active process memory and are discarded when the
run ends. See [ARCHITECTURE.md](./ARCHITECTURE.md) and
[backend/README.md](./backend/README.md) for contracts, safety boundaries, and
production considerations.

## Validation

```bash
npm run lint
npm run build
npm test

cd backend
pytest
```

See the [coverage validation record](./docs/coverage-validation-2026-10-06.md)
for the real text/visual Gemini runs, independent Playwright MCP checks,
resolved recovery gaps and remaining application-level dependencies.

The current MVP is designed for a trusted local or internal environment. It has
no multi-user authentication and intentionally allows localhost/private target
URLs so it can test development applications. Put the API behind an
authenticated gateway and an isolated browser worker before a shared
deployment.

### Compartilhar relatórios entre instalações

Use **Baixar relatório** no relatório existente e envie somente esse HTML.
Em **Meus testes**, use **Exportar relatório** na linha da base e selecione uma execução encerrada (inicial, rodada ou vinculada). **Baixar HTML** exporta somente essa execução, não o consolidado, preservando evidências e metadados portáteis. Rascunhos não aparecem na seleção; execuções em andamento ou sem testes ficam indisponíveis. Exportar não executa CTs. Em navegadores com `showSaveFilePicker`, a escolha do arquivo abre no clique, antes de solicitar o relatório; depois, a resposta é gravada em fluxo, sem acumular todo o HTML na memória do navegador. Cancelar essa escolha não solicita relatório. Nos demais navegadores, o download usa o anexo nativo: a pasta e a exibição do diálogo dependem das configurações do navegador. O modal mostra apenas etapa e uma barra: inicialmente indeterminada, depois baseada nos CTs realmente renderizados (incluindo suas imagens), consultados por identificador efêmero da exportação selecionada. Preparação ocupa 0–90%, bytes recebidos 90–99% e somente o fechamento confirmado do arquivo chega a 100%. Esses pesos são fases, não previsão de duração. Cache válido pula a renderização sem reutilizar telemetria de outra exportação. Cancelar interrompe a transferência e solicita cancelamento cooperativo da preparação; nenhuma geração parcial é publicada no cache. No download nativo, o modal informa apenas a entrega ao navegador, sem alegar gravação concluída.

Na outra instalação, em **Meus testes**, use **Importar relatório HTML**.
Não há arquivo JSON separado nem botão de exportação por base.
A importação grava uma nova base SQLite de forma atômica, com identificação e
horário locais, no topo da lista. Não inicia navegador nem executa testes.
Abra a base, selecione os CTs, valide a preparação e confirme uma nova execução.
Receitas determinísticas e jornadas completas são preservadas quando disponíveis;
a validação de estado continua obrigatória a cada passo. Credenciais precisam ser
informadas novamente na instalação de destino; provedores e configurações não
são importados.

Um bloco não executável de metadados `autojev-report` v1 embutido no HTML
(com caracteres de fechamento de script escapados) transporta definições, resultados,
conferências e evidências **textuais**; não transporta imagens, arquivos locais,
cookies ou cofre de credenciais. O navegador envia o arquivo diretamente, sem
convertê-lo inteiro em texto ou JSON. O backend decodifica UTF-8 incrementalmente,
descarta apresentação e imagens e retém apenas o bloco inerte de metadados;
nunca executa, renderiza ou segue referências do HTML recebido. Não há teto de
bytes para o HTML ou seus metadados na importação/exportação. Continuam valendo
os limites de complexidade: 500 CTs, listas/profundidade/textos limitados,
marcação com até 16 KiB por tag após compactação dos valores longos de atributos,
URLs HTTP(S) sem credenciais (URLs principais
sem query/fragmento). Campos desconhecidos e receitas inválidas são rejeitados,
sem salvar parte da base. Limites físicos de memória (metadados), CPU, disco e
configurações de proxy/servidor ainda se aplicam; não há promessa de recursos
ilimitados. Revise textos livres antes de compartilhar: filtros de
estrutura não conseguem identificar todo dado privado escrito em prosa.

**HTML antigo não é importável**: o relatório de apresentação remove receitas
privadas intencionalmente. Não é possível reconstruir ações executáveis a partir
dele. Baixe novamente o HTML na instalação original atualizada. Um HTML novo pode
conter testes históricos sem receita; eles continuam visíveis, mas indisponíveis
para reexecução automática até uma nova exploração/gravação apropriada.

Relatórios vazios ou com dados fora do esquema seguro continuam disponíveis para
leitura, mas exibem aviso de que não podem ser importados; não recebem metadados
parciais. A prévia online continua leve e baixa o HTML completo pelo botão existente.
