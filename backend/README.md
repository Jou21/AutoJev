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
AUTOJEV_HEADLESS=false uvicorn autojev.main:app --reload --host 127.0.0.1 --port 8000
```

If Google Chrome or Microsoft Edge is already installed, set
`AUTOJEV_BROWSER=chrome` or `AUTOJEV_BROWSER=msedge`; those values use the
corresponding Playwright channel instead of a downloaded browser.

Environment variables are read directly by the process. If you use a `.env` file, launch Uvicorn with `--env-file .env`.

Interactive local runs need `AUTOJEV_HEADLESS=false`, especially when login
requires user intervention. The explicit launch setting prevents a headless
value inherited from diagnostics from hiding the window. Run headless
diagnostics in a separate temporary backend process/port and stop that process
after validation; do not leave the interactive localhost backend headless.

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
- `adapters/openai_compatible.py` implements OpenAI-compatible model discovery, connectivity checks, schema-validated JEV JSON decisions and auxiliary documentation. Text mode receives compact redacted text/DOM. In visual mode the CT description and executed checks are documented locally; the model receives text plus masked captures and returns only grounded visual findings, without rewriting the CT. Missing captures or explicitly unsupported vision are marked unavailable without a redundant text-only request.
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

Supplying both `username` and `password` enables one deterministic automatic
login attempt by Playwright, independent of whether the UI credentials section
is expanded. Only an unambiguous same-origin login form is accepted; no LLM is
called for authentication. Password whitespace is preserved. Rejection,
CAPTCHA/MFA, unsafe requests, changed fields or ambiguous forms fall back to
manual intervention, with no automatic resubmission during the run. The first
confirmed authenticated page becomes the coverage/history root. Omit both
values for manual login; other credential aliases remain available to the
executor but are never guessed as login credentials.

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
masked low-detail image is attached to the auxiliary-model request. Model-only
previews are JPEGs (quality 75), aspect-preserving and at most 768 pixels on the
long edge; the original screenshots in the CT timeline are not rewritten.
Auxiliary documentation is bounded to 3,072 output tokens. Optional OpenRouter
reasoning is disabled only when declared supported; mandatory reasoning uses
the lowest declared effort. JEV planning requests keep their separate policy.

Visual mode requests **only** a compact structured visual assessment; CT
wording and executed expectations/results come from local browser evidence,
not a model rewrite. Documentation and visual review have separate provenance.
Only an accepted review counts as visual enrichment; malformed/missing replies
remain unavailable even when the provider returned JSON successfully.
When available, masked before/after captures are
sent at low detail. Accepted findings carry a screenshot, normalized region,
visible identity/expectation, category and model-declared confidence (minimum
0.90). AutoJev admits only grounded negative findings from the after image; the
model cannot assign a status or erase a local business failure. A visual-only
failure is labelled **FAIL · INDÍCIO VISUAL / revisão necessária**, not a
confirmed functional defect. No design baseline is supplied: an empty findings
list is not a blanket visual PASS. Missing images, invalid assessments,
timeouts and provider limits are exposed per CT as analysis unavailable, with a
prominent reviewed/pending/unavailable summary in the dashboard and new reports.
An unavailable after capture prevents sending a before-only image payload or
making a redundant text-only request; the before capture remains in the local
CT timeline for context. Exact-cache and no-request paths do not add artificial
free-model pacing. Actual review latency and HTTP-attempt count are recorded
separately from the run's cumulative request count.
The visual prompt uses one complete response contract. For OpenRouter models
declaring `structured_outputs` in the public catalog, the same request uses a
strict JSON Schema and routes only to endpoints supporting its parameters. The
public capability lookup is cached per client/run; no model names are used to
guess support and no additional inference is used to repair omitted fields.
Other compatible providers retain the explicit prompt contract and local
validation. Malformed findings are unavailable, not a successful empty review;
sent screenshot references remain preserved even when review is unavailable.
Findings attributed to the AutoJev panel are rejected locally, not merely by
the model prompt. Application-owned overlays remain eligible for review.
Text mode never attaches images or admits visual-model assertions.
HTTP 429 honors `Retry-After` (60-second fallback); local CTs continue without
provider calls during it. Authentication rejection stops auxiliary calls for
the run; explicit vision incompatibility is remembered after its first rejected
request, with no text retry and no repeated unusable vision calls.
The local auxiliary HTTP-request allowance is finite and independent of CT count.
`options.auxiliary_max_requests` overrides the configured default for a new run;
`auxiliary_review_max_attempts` defaults to 3 total attempts, including the first.
Exact evidence cache hits remain available after the request allowance is spent.
Retryable visual failures use bounded retries; authentication rejection and
budget exhaustion never trigger retry loops. Rapid revisions of one CT are
coalesced before review, while its verified actions and prior findings remain
preserved in the timeline.

For a terminal visual run, `POST /api/explorations/{id}/visual-reviews/resume`
accepts a finite `{ "max_requests": 120, "max_attempts": 3 }` allowance for the
pending saved-evidence reviews only. It does not reopen a browser, rerun local
assertions or ask JEV to explore again. `POST .../visual-reviews/cancel` stops
only that review batch. Detail payloads expose `visual_review_resume` for progress
and independent cancellation; GET detail/report never starts paid requests.
The frontend requires cost consent before submitting a new batch. Legacy CTs
without the original semantic review context retain an explicit limitation;
missing captures or insufficient evidence are not fabricated or marked reviewed.
Only an explicit JSON-format rejection permits one format compatibility retry;
strict schema, generic request, auth, timeout and rate-limit errors do not
trigger silent format downgrades or a request burst.

The local frontend disables recursive browser-error forwarding over HMR.
Screenshot loading errors have a clear placeholder and manual retry, separate
from the exploration result; retry does not call the model or change history.

## Result-based checks and form recovery

`services/business_checks.py` checks observable semantic before/after facts:
field value retention and other-field preservation, selected option and actual
product sort order, selected product identity/price in the detail, cart count,
membership/control transitions and declared required-form validation/progress.
Missing semantics do not manufacture a business PASS. Generic state deltas
remain explicitly labelled technical interactions when no specific oracle is
available. Field comparisons use per-run HMACs; private values/credentials are
not saved in these semantic facts or sent to the auxiliary model.
Removal is permitted only for a semantically recognized product's reversible
cart control with the exact Remove/Remove from cart label (or Portuguese
equivalent). The executor rechecks the live DOM; account/data deletion,
payments and other transactional actions stay blocked by the existing policy.

The coverage planner completes safe fields and submits before header/navigation
links, uses distinct synthetic field values, and resolves restoration moves in
the fresh DOM. A different live cart/menu variant is not automatically marked
covered. Blank previously exercised fields may be refilled once as setup for
an untried submit. A verified failed business outcome is still an exercised
candidate, so it is recorded as FAIL and exploration continues without buying
the same diagnosis repeatedly. Limits, blocked actions and unavailable returns
remain honest coverage restrictions, not approved tests.

Required-field negatives run before a departing happy-path submit once the
ordinary non-secret fields have been exercised. Preparation retries are bounded
per target and peer. Broken input behavior does not blanket-block other fields:
each target receives an actual submission when preparation permits. A response
attributable only to a broken peer leaves that target BLOCKED, with the observed
dependency, instead of guessing its rule. Probe titles/results stay grounded in
the attempted target even when an auxiliary model summarizes another error.
Planner-owned probes/replays do not consume the separate repeated-JEV-action
budget. Authentication forms remain excluded from these synthetic negatives.

An explicitly harmless button inside visible form-owned validation feedback is
tested while it exists, before a later submission removes it. Its checks verify
that the message closes and observed field tokens stay unchanged. A matching
label outside this DOM-owned error, a reset/submit, or an order cancellation
does not receive that priority or permission.

Restoration prefers fresh observed same-origin navigation or learned safe
semantic paths. Exhausted logical parents are skipped without navigating the
browser back through covered screens. Restriction records are immutable history
events: a later successful execution of the exact same semantic move on the
same route may resolve temporary restoration debt, with its step and capture.
Merely reaching the route, matching a label, or running a different product's
control cannot resolve it. API/UI/offline reports distinguish active gaps from
explicit recovered history; a business FAIL still stays FAIL. Failed inverse
cart toggles can explain missing setup without resetting application data or
bypassing the site. This finite observed frontier is not proof of every possible
input, user profile, or combination of application states.
Route shortcuts never skip a known business prerequisite anywhere in the
chosen replay path (for example, adding the expected item before entering its
cart, even if another route comes first). Replay selects a
shorter learned safe path only if its first semantic move exists in the current
DOM; reaching a URL still does not approve a scenario. Unique observed DOM
markers stabilize existing controls across sibling text changes, while entity
owners and ambiguous-marker fallbacks keep distinct product/row actions separate.

Placeholder JavaScript links (`href="#"`) are replayed only after their actual
same-origin destination has been observed; their labels never invent a URL.
A known checkout continuation may be used as a route prerequisite only after
native `form_progress` passed and planner-generated field setup is available.
Fresh blank fields are filled before Continue, without replaying credentials.
Cancel is exercised in both the information form and the order review, for
empty and populated carts. Review cancellation may return to the catalog;
selection/count are checked there, and contents/prices are checked when the
actual cart is subsequently opened. A drawer already open on a newly reached
route can be closed as bounded local setup for its still-untested opener; the
setup itself never approves that opener or its remaining work.
The information form's actual Cart link is also exercised before its pending
positive submit departs that empty/populated lane. Cancel is a separate handler
and never serves as proof that the Cart link worked. Completed form setup is
not resurrected in an old state after that route's submit was actually tried;
genuinely untried submits and blocked fields remain distinguishable.

A product card opening the wrong detail remains a business FAIL. Its actually
observed same-origin destination may nevertheless provide a bounded native
recovery edge for testing the controls on the page that opened. A divergent
anchor needs an explicit learned wrong-product edge; its label or declared href
cannot invent another destination. Browser-operation failure, authentication,
external destinations and failed form/explicit URL contracts remain excluded.
This recovery does not approve the intended product or modify earlier verdicts.

Offline regressions in `tests/test_business_checks.py` reproduce the observable
mutations in the official [Sauce Labs sample app](https://github.com/saucelabs/sample-app-web):
no-op add/remove and wrong product links (`InventoryListItem.jsx`), ineffective
sorting (`Inventory.jsx`), and overwritten/cleared Last Name (`CheckOutStepOne.jsx`).
Real headless Chromium fixtures verify the complete standard checkout versus
these failure variants, including a usable SPA document returned with HTTP 404.
`tests/test_visual_checks.py` validates visual evidence admission, status merge,
timeline/report output and provider cooldown with fixture responses, without
paid calls. These fixtures do not claim to evaluate a real vision model's
accuracy or to retrospectively rerun stored explorations.

Before every paid JEV request, the orchestrator applies an explicit invocation
policy. Clear atomic controls, conventional forms, known navigation and graph
restoration execute deterministically. Duplicate labels, complex new states,
ambiguous business controls, missing safe moves and two attempts without
progress are escalated to JEV. Console/HTTP errors are first recorded by
Playwright and do not need a model merely to confirm that they occurred.

## Evidence and status

- `PASS`: the Playwright action succeeded, at least one verifiable assertion
  exists, and every assertion passed. An observable URL/DOM/control-state delta
  can supply basic interaction evidence, but is not a business outcome check.
- `FAIL`: ordinary execution failed or a supported assertion failed. An
  attributable same-origin HTTP/network failure or page exception can fail the
  technical-health check, even if the screen changes. This is distinguished
  from a confirmed functional defect. External telemetry, isolated cancelled
  requests and signals outside the action window do not independently fail a CT.
- `BLOCKED`: an executor policy/unavailable control prevented execution, or the
  action was dispatched but its API result remained pending beyond a bounded
  settling window. A pending API outcome is neither a verified PASS nor a
  proven business failure.
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

For asynchronous actions, the executor waits up to five seconds for same-origin
XHR/fetch requests started by that action, then a short stable DOM window.
Pre-existing requests and third-party telemetry are not a global network-idle
gate. A still-pending result is explicitly unavailable, without replaying the
click or asking a model to guess its business outcome.

`tests/test_exploration_integration.py` runs the complete explorer with real
Chromium and isolated SQLite/artifacts in both evidence modes. In-process HTTP
fixtures and fake model clients verify that business failures are preserved,
other flows continue, and CT documentation runs beside browser work. No user
provider credentials, production history or credits are used.

Exploration lifecycle status is separate from scenario status. `completed`
means the discovered frontier and return stack were exhausted. `partial` means
a time/action/planning bound was reached, the requested focus could not be
found, or blocked candidates left the frontier incomplete. `postprocessing` is
a live, cancelable phase after browser work that drains only any remaining
auxiliary queue and generates the report; only then is the terminal status
published. A backend restart marks `queued`, `running`, `waiting_input` and
`postprocessing` runs as failed rather than leaving them orphaned. Visible-browser
screenshots capture only the viewport, avoiding full-page scroll oscillation.
The AutoJev overlay may remain visible in the capture; it is never hidden,
removed or restyled for screenshots. Sensitive password, username, token,
API-key and OTP fields are masked with Playwright's screenshot compositor,
including values filled manually rather than supplied through the vault.

Token usage is accumulated for both JEV and auxiliary calls as prompt,
completion and total tokens. Monetary cost is persisted only when the provider
returns it; unknown cost remains `—` rather than being estimated. The live
browser panel and React sidebar show the running total, and exploration history,
detail payloads and the standalone report retain the final values. New runs
persist `usage.by_role.jev` and `usage.by_role.auxiliary`, with the same token
fields plus `cost`, `cost_known` and `cost_complete`. Attribution uses the call's
role, not its model ID. Exploration detail and the report show **Custo JEV**,
**Custo LLM** and **Custo total** in USD. Known zero-cost calls show zero;
incomplete known sums are marked partial. Migrated historical rows keep their
combined total with an empty role breakdown, never an estimated split.

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
