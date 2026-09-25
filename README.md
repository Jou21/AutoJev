# AutoJev

**AI-powered exploratory testing for web applications.**

AutoJev receives an application URL, asks a JEV model to choose one structured
action at a time, executes that action with a real Playwright browser, captures
evidence, and uses a separately configurable auxiliary LLM to document the
observed test scenario. At the end it produces a standalone HTML report.

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
uvicorn autojev.main:app --reload --host 127.0.0.1 --port 8000
```

Start the React interface in a second terminal:

```bash
npm install
npm run dev
```

Open `http://localhost:3000`. The API documentation is available at
`http://127.0.0.1:8000/docs`.

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
5. Set safety limits and, when necessary, ephemeral test credentials.
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
falsely reported as complete. After browser work ends, the live
**Gerando relatório** phase lets the auxiliary model document already captured
evidence without reducing the browser exploration budget.

TypeSafe JEV receives atomic candidate moves instead of separate action/target
questions. It ranks real Playwright observations using semantic DOM context,
coverage novelty, recent outcomes and browser errors; confidence is retained
with the decision. Safe form fields/selects and same-origin new tabs are also
covered, while password, upload, destructive and cross-origin interactions stay
outside the executable frontier.

Scenario verdicts come from Playwright evidence, not from model prose. A
successful action with verified assertions or an observable state change is
`PASS`; a failed assertion or ordinary execution failure is `FAIL`; a policy or
unavailable control is `BLOCKED`; and a successful action without verifiable
evidence is `SKIPPED`. Internal backtracking/replay actions do not become test
scenarios.

Prompt, completion and total tokens are accumulated for both models. The live
browser panel and React sidebar show the running usage; exploration views and
the standalone report retain the final total and provider-reported monetary
cost. When a provider does not return cost, AutoJev displays `—` instead of
estimating a price.

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

The current MVP is designed for a trusted local or internal environment. It has
no multi-user authentication and intentionally allows localhost/private target
URLs so it can test development applications. Put the API behind an
authenticated gateway and an isolated browser worker before a shared
deployment.
