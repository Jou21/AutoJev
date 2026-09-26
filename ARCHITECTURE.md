# AutoJev architecture

AutoJev keeps reasoning, browser execution, interpretation, and reporting behind
separate contracts. The central rule is that a language model never receives a
browser handle and can never claim that an action ran: only the Playwright
adapter can execute actions and produce observations.

```text
React UI
   |
   v
FastAPI -> Exploration Engine -> Coverage planner -> JEV agent (priority/decision)
                                               |
                                               v
                              -> Playwright executor -> real browser
                                      |
                                      v
                           DOM, URL, console, network, screenshots
                                      |
                                      v
                               Auxiliary LLM
                                      |
                                      v
                         Evidence-backed test scenario
                                      |
                                      v
                           Standalone HTML report
```

## Core contracts

- `AgentPort`: receives a sanitized observation and returns one structured,
  allow-listed action plus explicit assertions.
- `BrowserExecutorPort`: resolves a target from the observed page, performs the
  action in Playwright, and returns the before/after evidence.
- `AuxiliaryLLMPort`: turns an executed action and its evidence into a test-case
  description. It does not execute actions or decide the final status.
- `ReportPort`: renders stored exploration evidence into an offline HTML file.
- The exploration engine owns step limits, duration limits, loop detection,
  deduplication, cancellation, usage accounting, status evaluation, and
  lifecycle state.

## Coverage and navigation

- State identity preserves path, query and hash and combines them with a stable
  semantic signature of safe controls. Volatile refs such as `e0` are never
  durable identity.
- Coverage is tracked per atomic move (`semantic element + action + value`),
  rather than once per element. Popup/menu controls may therefore expose both
  `hover` and `click`; selects expose up to eight enabled, non-destructive
  values. At most 30 pending moves are sent in one JEV decision request.
- When one or more dialogs are visible, Playwright observes only the active
  modal (a native `:modal` dialog first, otherwise the last visible dialog).
  Background controls cannot enter the frontier or receive a stale action.
  Close, cancel and back controls are deliberately ordered after non-exit
  controls on the active surface.
- The coverage planner, not the model, owns the pending frontier and route
  stack. A premature `finish` or `go_back` is replaced with the next untried
  safe control. Already-classified parent moves propagate into a child while
  still-pending controls that remain mounted can be exercised directly in the
  live DOM. Backtracking happens only while a remote frontier remains; an
  exhausted run does not unwind browser history just to finish. Same-route
  parents use their known URL instead of browser history, and failed restores
  are bounded and reported as partial coverage.
- Repeated labels are disambiguated by bounded row/card/form/dialog context,
  including common `data-test*` containers. Empty anchor URLs are normalized
  consistently before stale-element validation. After manual authentication,
  the first authenticated observation becomes the coverage root and browser
  backtracking is prevented from crossing into the login document.
- A route URL is not enough to restore a DOM-only state such as an expanded
  submenu, tab or modal. Successful safe transitions are stored as
  selector-free recipes containing a semantic fingerprint and atomic action.
  Restoration resolves a fresh ref in the current observation and replays the
  recipe. Replay depth, steps and retries are bounded; an unreachable frontier
  is marked blocked, not covered. A focused DOM-only root retains an external
  entry anchor solely for restoration—controls on that anchor do not become
  eligible inside the focused scope.
- The optional `focus_area` is a human-readable menu/screen path. Before it is
  reached, matching controls are prioritized. After it is reached, that screen
  becomes the root and unrelated global links are excluded. Omitting the field
  means whole-application coverage.
- Completion means the discovered frontier and return path are empty. Safety
  bounds that stop earlier produce lifecycle status `partial`.
- Safe text/search/email/number/telephone/URL fields join the frontier with one
  deterministic synthetic value. Safe select values are separate moves.
  Passwords, uploads, hidden/read-only fields and destructive choices are
  excluded.
- Same-origin tabs opened by a control are adopted by the executor and placed
  on its page stack. Backtracking closes the tab and restores its exact parent;
  unresolved or cross-origin popups are blocked.
- The auxiliary LLM documents captured evidence only after browser navigation,
  so provider latency cannot consume the exploration deadline. This work runs
  under the live, cancelable `postprocessing` lifecycle state and falls back to
  deterministic local evidence on cancellation, provider failure or its own
  bounded documentation window.

## JEV decision quality

- OpenAI-compatible chat agents return a schema-validated `AgentDecision`.
  TypeSafe JEV uses its native Decisions API: AutoJev supplies one `Choice`
  whose options are complete atomic moves (`action + target + safe value`), a
  parallel QA-focus `Choice`, and a concrete-issue `Noul` judgment.
- Each candidate carries its accessible name, role/type, destination, semantic
  context, viewport position, scope, coverage novelty, recent outcomes and
  browser errors. Action and target are never selected independently.
- JEV chooses the most valuable safe frontier move. Application code retains
  the safety, scope, backtracking and completion invariants. Choice confidence
  and probability are preserved in the rationale and scenario evidence.
- After Playwright acts, the next decision receives the observed state delta,
  result message, assertions, URL transition, new console/network errors and
  remaining coverage. A browser command with no real state change is reported
  as such instead of being treated as new coverage.

## Model configuration

Agent and auxiliary selections are stored independently:

```json
{
  "agent": {
    "provider_id": "provider-id",
    "model_id": "jev-model"
  },
  "auxiliary_llm": {
    "provider_id": "provider-id",
    "model_id": "claude-sonnet"
  }
}
```

Both selections use the same OpenAI-compatible provider adapter today. New
provider and model adapters can be introduced without changing Playwright or
the exploration engine.

## Evidence and status

An action result records its URL before and after execution, success/failure,
assertion results, timestamp, and screenshot. A verified route, DOM or control
state change adds deterministic evidence; when browser-error capture is active,
the correlated console/network result is evaluated with it. Scenario status is
derived from that evidence:

- `PASS`: the action succeeded, at least one verifiable assertion exists, and
  every assertion passed.
- `FAIL`: an ordinary execution failed, or at least one assertion did not match
  reality.
- `BLOCKED`: an executor safety policy or unavailable control prevented the
  action.
- `SKIPPED`: the action succeeded but produced no verifiable assertion.

The auxiliary model may improve titles and descriptions, but it cannot promote
a scenario to `PASS`. Internal backtracking and selector-free restoration steps
are orchestration, so they are not materialized as test scenarios.

Exploration lifecycle is independent from scenario verdicts:

- `queued`, `running` and `waiting_input` describe browser preparation,
  autonomous execution and manual-login intervention.
- `postprocessing` is a live/cancelable phase for auxiliary documentation and
  report generation after browser work has stopped.
- `completed` is used only when the reachable safe frontier and return path are
  exhausted without blocked candidates.
- `partial` records bounded or incomplete coverage: action/duration/planning
  limits, a missing requested focus, loop-protection exhaustion, or blocked
  frontier candidates.
- `failed` and `cancelled` represent infrastructure/model failure and explicit
  cancellation. A process restart converts every nonterminal phase to `failed`.

## Usage accounting

Each provider response is drained into persisted `prompt_tokens`,
`completion_tokens` and `total_tokens` counters for the JEV and auxiliary
roles. A monetary total is accumulated only when the provider returns a cost;
AutoJev never fabricates model pricing. Live usage appears in the visible
Playwright panel and React sidebar, then remains available in exploration
history, detail views and the standalone report. Unknown cost is rendered as
`—` while token totals remain exact to the provider response.

## Secrets and sensitive data

- Provider keys are encrypted by the FastAPI server and API responses expose
  only `has_api_key`.
- Test credentials are held only for the active in-memory execution and are not
  persisted in history or reports.
- Secret values are resolved after the JEV decision and redacted before action
  logs are stored.
- Prompts receive only credential aliases and sanitized page observations.

## Persistence

FastAPI owns the SQLite database and run artifacts. Provider metadata,
exploration state, actions, scenarios, issues, and structured logs are durable.
Screenshots are stored per exploration and embedded as data URLs when the
standalone report is generated. The report also records whole-application or
focused scope, terminal lifecycle status and reason, duration, token totals and
provider-reported cost.
