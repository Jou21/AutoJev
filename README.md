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
4. Enter the target URL, limits, and optional ephemeral test credentials.
5. Start the exploration and follow Playwright evidence in the live view.
6. Open the standalone report from the completed exploration.

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
