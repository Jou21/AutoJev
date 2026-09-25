# AutoJev backend

FastAPI service for evidence-based exploratory testing. JEV is the decision maker, but it never receives a browser object and cannot execute browser code. Only the Playwright adapter performs actions. The auxiliary LLM receives completed evidence and documents test scenarios; it cannot request browser actions or assign `PASS`.

## Run locally

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
uvicorn autojev.main:app --reload --host 127.0.0.1 --port 8000
```

If Google Chrome or Microsoft Edge is already installed, set
`AUTOJEV_BROWSER=chrome` or `AUTOJEV_BROWSER=msedge`; those values use the
corresponding Playwright channel instead of a downloaded browser.

Environment variables are read directly by the process. If you use a `.env` file, launch Uvicorn with `--env-file .env`.

OpenAPI is available at `http://127.0.0.1:8000/docs`; the frontend normally uses `http://127.0.0.1:8000/api`.

## Architecture and trust boundaries

```text
FastAPI API -> ExplorationManager -> JEV adapter -> JSON AgentDecision
                                      |                 |
                                      |                 v
                                      |          PlaywrightExecutor -> Browser
                                      |                 |
                                      v                 v
                              auxiliary LLM <- verified evidence
                                      |
                                      v
                         SQLite + standalone HTML report
```

- `domain/` contains models and ports without FastAPI or Playwright dependencies.
- `adapters/openai_compatible.py` implements OpenAI-compatible model discovery, connectivity checks, schema-validated JEV JSON decisions and auxiliary documentation. The auxiliary model receives the redacted DOM plus a masked screenshot when it supports vision; incompatible models automatically fall back to textual evidence.
- `adapters/playwright_executor.py` is the only browser executor. Element targets are observation refs such as `e4`, not model-supplied selectors or JavaScript.
- `services/exploration.py` owns state, limits, cancellation, deduplication and loop protection.
- `adapters/repository.py` stores providers, models, runs, events, actions, evidence, scenarios, issues and artifact metadata in SQLite.
- `adapters/report.py` creates an offline HTML report with embedded CSS/JS and base64 screenshots. Every model/site string is HTML-escaped.

## API contract

All endpoints use `/api` (not `/api/v1`). Input accepts the documented snake_case names and common React camelCase aliases. Responses use snake_case.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Service health/version |
| GET/POST | `/api/providers` | List/create providers |
| GET/PATCH/PUT/DELETE | `/api/providers/{id}` | Provider CRUD |
| POST | `/api/providers/{id}/discover-models` | Fetch `/v1/models`, merge and persist new models |
| POST | `/api/providers/{id}/test` | Test model JSON response, or model listing when the body is empty |
| GET | `/api/models` | Flatten enabled provider/model choices |
| POST | `/api/explorations` | Create a real background exploration (HTTP 202) |
| GET | `/api/explorations` | Recent explorations |
| GET | `/api/explorations/{id}` | Live state, events, actions, evidence and scenarios |
| POST | `/api/explorations/{id}/cancel` | Request cooperative cancellation |
| GET | `/api/explorations/{id}/report` | Standalone HTML report |
| GET | `/api/explorations/{id}/screenshots/{file}` | Validated screenshot artifact |

Example exploration:

```json
{
  "app_url": "http://localhost:3000",
  "agent": {"provider_id": "provider-uuid", "model_id": "jev-model"},
  "auxiliary_llm": {"provider_id": "provider-uuid", "model_id": "claude-sonnet"},
  "options": {
    "mode": "full",
    "max_steps": 50,
    "max_duration_seconds": 900,
    "capture_screenshots": true,
    "capture_console_errors": true,
    "capture_network_errors": true,
    "generate_test_cases": true,
    "generate_html_report": true
  },
  "credentials": {"username": "qa-user", "password": "secret"}
}
```

Credential aliases—not their values—are shown to JEV. The executor resolves the alias in memory. Credentials are discarded at the end, redacted from actions/errors, and never inserted into SQLite. Inputs filled from credentials are masked in subsequent screenshots.

`max_duration_seconds` is enforced as a deadline across browser startup, JEV calls, Playwright actions and auxiliary-model calls. Repeated action/state fingerprints and actual URL transitions provide separate loop protection for SPAs.

## Evidence and status

- `PASS`: the Playwright action succeeded and every explicit supported assertion was observed.
- `FAIL`: execution or a supported assertion failed.
- `BLOCKED`: a safety policy or unavailable dependency prevented execution.
- `SKIPPED`: an action succeeded but no verifiable assertion exists. The model cannot turn this into `PASS`.

Captured browser evidence includes console/page errors, failed requests and HTTP 4xx/5xx responses. Reports only describe actions actually returned by Playwright.

## Security notes

- Provider keys are encrypted at rest with Fernet and are write-only over the API. Set `AUTOJEV_SECRET_KEY` in production. When omitted, a mode-0600 local key is created under the data directory.
- Only absolute `http` and `https` URLs without user-info are accepted. `file:`, `javascript:`, `data:` and similar schemes are rejected. Sensitive query/fragment keys are rejected at API input and browser-observed URLs/hrefs are sanitized before persistence or model egress.
- Localhost and private-network targets are intentionally allowed because AutoJev is designed to test local/internal apps. That is an explicit SSRF tradeoff for this local MVP. In a shared deployment, run the browser worker in an isolated network and add an organization allowlist/DNS revalidation before exposing this API.
- Top-level cross-host navigation and destructive/transactional clicks are blocked by default. Third-party subresources remain possible so normal sites can load assets.
- Page text is untrusted prompt data. The system prompt forbids following page instructions, and the executor independently validates the action/ref allowlist.
- This MVP has no multi-user authentication. Bind to loopback unless it sits behind an authenticated gateway.
- `asyncio.create_task` is appropriate for the local MVP but is not a durable production queue. A process restart marks in-flight runs failed; production should move workers to a queue and isolated browser containers.

## Tests

```bash
cd backend
pytest
```

Tests cover encrypted/write-only secrets, in-memory credentials, redaction, provider CRUD, exploration request secrecy, evidence-derived statuses, repository summaries and report XSS escaping/base64 embedding.

The standalone report includes duration, status filters, full-text search,
expandable scenario cards and embedded screenshots, so `report.html` can be
opened without the API running.
