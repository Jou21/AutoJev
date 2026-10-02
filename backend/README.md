# AutoJev backend

FastAPI service for evidence-based exploratory testing. JEV is the decision maker, but it never receives a browser object and cannot execute browser code. Only the Playwright adapter performs actions. The auxiliary LLM receives verified evidence and documents test scenarios in parallel with later browser actions; it cannot request browser actions or assign `PASS`.

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
- `adapters/openai_compatible.py` implements OpenAI-compatible model discovery, connectivity checks, schema-validated JEV JSON decisions and auxiliary documentation. The auxiliary model receives compact redacted text/DOM by default; visual mode additionally attaches a masked screenshot, with a text fallback only after an explicit vision-capability rejection.
- `adapters/playwright_executor.py` is the only browser executor. Element targets are observation refs such as `e4`, not model-supplied selectors or JavaScript.
- `services/coverage.py` owns route/state identity, semantic control memory,
  the atomic pending frontier, selector-free DOM-state replay and verified
  backtracking. `services/exploration.py` owns lifecycle, limits, cancellation,
  usage accounting and evidence orchestration.
- TypeSafe JEV receives one native `Choice` over complete atomic moves rather
  than unrelated action and target questions. Parallel QA-focus and issue
  judgments, returned probability/confidence, recent Playwright state deltas,
  console/network errors and frontier metrics make each next-step decision
  evidence-based while code still owns safety and completion.
- Coverage is keyed by `semantic element + action + value`: menu controls can
  contribute separate `hover` and `click` moves, and a select contributes up to
  eight enabled, non-destructive option moves. JEV receives at most 30 moves per
  decision. Text-like fields use one deterministic synthetic value.
- When a dialog is open, only the active modal's controls are observed; page
  controls behind it cannot enter the frontier. Exit controls are attempted
  only after the remaining useful controls on that surface.
- DOM-only states (open submenus, tabs and modals that keep the same URL) are
  restored from bounded selector-free recipes. The planner re-resolves a fresh
  semantic ref and replays the safe interaction; an unrestorable branch is
  marked blocked. Same-origin popups are adopted and backtracked through an
  executor-owned page stack.
- Repeated controls are separated by bounded card/row/form context, including
  `data-test*` containers. Classified parent moves propagate across live
  same-route states, so a cart/filter toggle does not force an obsolete DOM
  restore. Manual login is excluded from the coverage graph: the authenticated
  page becomes the root and history backtracking cannot reopen the login form.
- Passwords, uploads, hidden/read-only fields, destructive choices and
  cross-origin top-level navigation are excluded or blocked.
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
| POST | `/api/explorations/{id}/resume` | Resume after a manual-login intervention |
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
    "focus_area": "Configurações > Usuários > Permissões",
    "max_steps": 100,
    "max_duration_seconds": 1800,
    "capture_screenshots": true,
    "auxiliary_evidence_mode": "text",
    "capture_console_errors": true,
    "capture_network_errors": true,
    "generate_test_cases": true,
    "generate_html_report": true
  },
  "credentials": {"username": "qa-user", "password": "secret"}
}
```

Credential aliases—not their values—are shown to JEV. The executor resolves the alias in memory. Credentials are discarded at the end, redacted from actions/errors, and never inserted into SQLite. Inputs filled from credentials are masked in subsequent screenshots.

`focus_area` is optional. Without it, the planner covers every safe reachable
same-origin branch it can discover. With it, navigation may traverse the menus
needed to reach the requested screen, then the selected route becomes the
backtracking root and unrelated global navigation is excluded. If that root is
a same-URL submenu or modal, its entry state is retained only as a replay anchor;
background controls remain out of scope.

`max_duration_seconds` is enforced across browser startup, JEV decisions and
Playwright actions. Each deterministic local CT is persisted immediately and a
single bounded auxiliary worker enriches it in parallel, so provider latency
cannot reduce browser coverage; local evidence remains the fallback when the
documentation queue reaches its own bound. Route fingerprints keep
path, query and hash distinct for SPAs. `max_steps` counts real browser actions;
invalid/rejected model responses use a separate bounded planning budget.

`auxiliary_evidence_mode` accepts `text` (default/economical) or `visual`.
Screenshots are still captured and retained locally when
`capture_screenshots=true` in either mode; the option controls only whether a
masked low-detail image is attached to the auxiliary-model request.

Before every paid JEV request, the orchestrator applies an explicit invocation
policy. Clear atomic controls, conventional forms, known navigation and graph
restoration execute deterministically. Duplicate labels, complex new states,
ambiguous business controls, missing safe moves and two attempts without
progress are escalated to JEV. Console/HTTP errors are first recorded by
Playwright and do not need a model merely to confirm that they occurred.

## Evidence and status

- `PASS`: the Playwright action succeeded, at least one verifiable assertion
  exists, and every assertion passed. An observable URL/DOM/control-state delta
  can supply deterministic evidence.
- `FAIL`: ordinary execution failed or a supported assertion failed. An
  attributable same-origin HTTP/network failure or page exception can fail the
  technical-health check, even if the screen changes. This is distinguished
  from a confirmed functional defect. External telemetry, isolated cancelled
  requests and signals outside the action window do not independently fail a CT.
- `BLOCKED`: an executor policy or unavailable control prevented execution.
- A successful action without verifiable evidence is logged as a no-op and is
  omitted from the CT inventory. `SKIPPED` remains readable for historical or
  imported scenarios and is presented as **Sem validação**, never as `PASS`.

Captured browser evidence includes console/page errors, failed requests and HTTP 4xx/5xx responses. Reports only describe actions actually returned by Playwright.

Each CT exposes `evidence.timeline`, `technical_signals` and an explanatory
`verdict`. Timelines retain before/after screenshots and earlier retry attempts
when the same semantic case is recovered. Signals are presented inside that
timeline, not in a separate final issue list. Unlinked setup/restoration
occurrences appear in `scenario_contexts`, which are not validated CTs and do
not change scenario totals. Historical results stay unchanged; the report
endpoint renders their new presentation to a separate atomic cache artifact.
Only new, relevant signatures enter model prompts; repeated occurrences remain
fully available in local evidence.

Exploration lifecycle status is separate from scenario status. `completed`
means the discovered frontier and return stack were exhausted. `partial` means
a time/action/planning bound was reached, the requested focus could not be
found, or blocked candidates left the frontier incomplete. `postprocessing` is
a live, cancelable phase after browser work that drains only any remaining
auxiliary queue and generates the report; only then is the terminal status
published. A backend restart marks `queued`, `running`, `waiting_input` and
`postprocessing` runs as failed rather than leaving them orphaned. Visible-browser
screenshots capture only the viewport and mask the AutoJev overlay without
toggling it, avoiding full-page scroll oscillation.

Token usage is accumulated for both JEV and auxiliary calls as prompt,
completion and total tokens. Monetary cost is persisted only when the provider
returns it; unknown cost remains `—` rather than being estimated. The live
browser panel and React sidebar show the running total, and exploration history,
detail payloads and the standalone report retain the final values.

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

Tests cover encrypted/write-only secrets, in-memory credentials, redaction,
provider CRUD, exploration request secrecy, semantic SPA state identity,
atomic move variants, frontier/backtracking rules, selector-free DOM replay,
focused-scope and active-modal isolation, stable screenshots, evidence-derived
statuses, lifecycle recovery, usage persistence, repository summaries and
report XSS escaping/base64 embedding.

The standalone report includes scope, terminal lifecycle status/reason,
duration, tokens, provider-reported cost, scenario filters, full-text search,
expandable cards and embedded screenshots, so `report.html` can be opened
without the API running.
