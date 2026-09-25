# AutoJev architecture

AutoJev keeps reasoning, browser execution, interpretation, and reporting behind
separate contracts. The central rule is that a language model never receives a
browser handle and can never claim that an action ran: only the Playwright
adapter can execute actions and produce observations.

```text
React UI
   |
   v
FastAPI -> Exploration Engine -> JEV agent (decision only)
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
  deduplication, cancellation, status evaluation, and lifecycle state.

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
assertion results, timestamp, and screenshot. Scenario status is derived from
that evidence:

- `PASS`: the action ran and every explicit assertion was observed.
- `FAIL`: the action ran but at least one assertion did not match reality.
- `BLOCKED`: Playwright could not perform or observe the action.
- `SKIPPED`: reserved for scenarios intentionally not executed.

The auxiliary model may improve titles and descriptions, but it cannot promote
a scenario to `PASS`.

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
standalone report is generated.
