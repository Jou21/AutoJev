from __future__ import annotations

from dataclasses import asdict
from inspect import getfile
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool

from autojev import __version__
from autojev.adapters.openai_compatible import (
    OpenAICompatibleClient,
    ProviderConnectionError,
)
from autojev.adapters.repository import NotFoundError, SQLiteRepository
from autojev.domain.models import (
    ExplorationOptions,
    ExplorationRequest,
    ModelSelection,
    Provider,
    ProviderModel,
)
from autojev.services.scenario_timeline import prepare_scenario_timelines
from autojev.services.coverage_ledger import coverage_restriction_ledger
from autojev.services.execution_summary import execution_duration_summary, report_snapshot_key
from autojev.services.report_localization import REPORT_LOCALE_VERSION, check_label, localize_report_presentation
from autojev.adapters.report_premium import ASSETS as REPORT_ASSETS, REPORT_LAYOUT_VERSION, render_premium_report
from autojev.services.visual_checks import visual_review_counts
from autojev.services.verification_summary import verification_summary
from autojev.services.visual_review_resume import visual_review_resume_summary
from autojev.services.rerun_projection import without_rerun_recipes

from .schemas import (
    ConnectionTestInput,
    ExplorationCreate,
    ProviderCreate,
    ProviderUpdate,
    ScenarioRerunInput,
    VisualReviewResumeInput,
)

router = APIRouter(prefix="/api")


def _repository(request: Request) -> SQLiteRepository:
    return request.app.state.repository


def _provider_payload(provider: Provider) -> dict[str, Any]:
    return {
        "id": provider.id,
        "name": provider.name,
        "base_url": provider.base_url,
        "has_api_key": provider.has_api_key,
        "enabled": provider.enabled,
        "models": [asdict(model) for model in provider.models],
        "created_at": provider.created_at,
        "updated_at": provider.updated_at,
    }


def _phase_payload(result: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe browser work and post-processing as distinct user-facing phases."""

    status_value = str(result.get("status") or "queued")
    raw_max_steps = result.get("options", {}).get("max_steps")
    max_steps = max(int(raw_max_steps), 1) if raw_max_steps is not None else None
    actions = int(result.get("steps_completed", 0))
    browser_percentage = (
        min(round((actions / max_steps) * 100), 100)
        if max_steps is not None
        else None
    )
    browser_progress = {
        "current": actions,
        "total": max_steps,
        "percentage": browser_percentage,
    }

    event_types = [str(event.get("type") or "") for event in events]
    phase = "preparing"
    label = "Preparando a exploração"
    description = "Configurando o navegador e os modelos selecionados."
    current = 0
    total = 1
    percentage = 0

    if status_value in {"running", "waiting_input"}:
        phase = "waiting_input" if status_value == "waiting_input" else "exploring"
        label = (
            "Aguardando sua ação no navegador"
            if phase == "waiting_input"
            else "Explorando a aplicação"
        )
        description = (
            "Conclua a etapa solicitada no navegador para o JEV continuar."
            if phase == "waiting_input"
            else (
                "O JEV decide e o Playwright valida cada interação enquanto "
                "os CTs são preservados e aprimorados em paralelo."
            )
        )
        current, total, percentage = actions, max_steps, browser_percentage
    elif status_value == "postprocessing":
        report_started = "report_generation_started" in event_types
        if report_started:
            phase = "reporting"
            label = "Montando o relatório final"
            description = "A navegação e os casos de teste já terminaram."
            report_ready = "report_ready" in event_types
            current, total, percentage = (1, 1, 100) if report_ready else (0, 1, 50)
        else:
            phase = "documenting"
            visual_mode = result.get("options", {}).get("auxiliary_evidence_mode") == "visual"
            label = "Analisando as capturas dos CTs" if visual_mode else "Revisando as evidências dos CTs"
            description = (
                "Os CTs e verificações locais já foram preservados. A LLM auxiliar "
                "está analisando as capturas; não reexecuta testes nem controla o navegador."
                if visual_mode else
                "Os CTs locais já foram preservados. A LLM auxiliar está revisando "
                "a documentação das evidências sem controlar o navegador."
            )
            progress_event = next(
                (
                    event
                    for event in reversed(events)
                    if event.get("type")
                    in {
                        "scenario_enriched",
                        "scenario_enrichment_skipped",
                        "scenario_enrichment_started",
                        "scenario_enrichment_deferred",
                        "scenario_enrichment_waiting_provider",
                        "scenario_preparation_started",
                        "scenario_documentation_fallback",
                        "scenario_created",
                    }
                ),
                None,
            )
            payload = (progress_event or {}).get("payload") or {}
            total = max(
                int(payload.get("total") or 0),
                len(result.get("scenarios") or []),
                1,
            )
            current = min(int(payload.get("current") or 0), total)
            percentage = min(round((current / total) * 100), 100)
    elif status_value in {"completed", "partial", "failed", "cancelled"}:
        phase = "complete"
        label = {
            "completed": "Exploração concluída",
            "partial": "Exploração concluída com cobertura parcial",
            "failed": "A exploração foi interrompida por um erro",
            "cancelled": "Exploração cancelada",
        }[status_value]
        description = "Os resultados disponíveis foram preservados."
        current, total, percentage = 1, 1, 100

    enrichment_event = next(
        (
            event
            for event in reversed(events)
            if event.get("type")
            in {
                "scenario_postprocessing_completed",
                "scenario_enriched",
                "scenario_enrichment_skipped",
                "scenario_enrichment_backlog_full",
                "scenario_enrichment_worker_unavailable",
                "scenario_enrichment_deferred",
                "scenario_enrichment_retrying",
                "scenario_enrichment_waiting_provider",
                "scenario_draft_created",
                "scenario_draft_updated",
                "scenario_draft_removed",
                "scenario_parallel_started",
            }
        ),
        None,
    )
    enrichment = (enrichment_event or {}).get("payload") or {}
    # Draft/backlog events from older runs do not carry every queue counter.
    # Preserve the latest measured value instead of showing zero between
    # provider updates. Explicit zero values still mean an empty queue.
    counter_keys = ("pending", "deferred", "provider_requests", "requests")
    for key in counter_keys:
        if key not in enrichment:
            for event in reversed(events):
                event_payload = event.get("payload") or {}
                if key in event_payload and str(event.get("type", "")).startswith("scenario_"):
                    enrichment = {**enrichment, key: event_payload[key]}
                    break
    legacy_enriched = sum(1 for event in events if event.get("type") == "scenario_created")
    legacy_local = sum(
        1 for event in events if event.get("type") == "scenario_documentation_fallback"
    )
    enriched_count = int(enrichment.get("enriched") or legacy_enriched)
    local_count = int(enrichment.get("local_evidence") or legacy_local)
    postprocessing = {
        "current": current if phase == "documenting" else int(enrichment.get("current") or 0),
        "total": max(
            int(enrichment.get("total") or 0),
            len(result.get("scenarios") or []),
        ),
        "enriched": enriched_count,
        "local_evidence": local_count,
        "stage": enrichment.get("stage"),
        "reason": enrichment.get("reason"),
        "pending": enrichment.get("pending", 0),
        "deferred": enrichment.get("deferred", 0),
        "requests": enrichment.get("provider_requests", enrichment.get("requests", 0)),
    }

    completion_event = next(
        (
            event
            for event in reversed(events)
            if event.get("type")
            in {
                "limit_reached",
                "coverage_partial",
                "coverage_scope_not_found",
                "coverage_complete",
                "coverage_complete_with_restrictions",
                "coverage_stalled",
                "failed",
                "cancelled",
                "browser_closed",
            }
        ),
        None,
    )
    if (
        status_value in {"cancelled", "canceled"}
        and completion_event is not None
        and completion_event.get("type") not in {"cancelled", "browser_closed"}
    ):
        # Older runs could be cancelled during post-processing after a
        # coverage-complete event but before a canonical cancellation event.
        # Do not present that stale event as their terminal outcome.
        completion_event = None
    if completion_event is None and isinstance(result.get("completion_event"), dict):
        completion_event = result["completion_event"]
        if (
            status_value in {"cancelled", "canceled"}
            and completion_event.get("type") not in {"cancelled", "browser_closed"}
        ):
            completion_event = None
    completion_payload = (completion_event or {}).get("payload") or {}
    completion = {
        "type": (completion_event or {}).get("type"),
        "reason": completion_payload.get("reason"),
        "message": (completion_event or {}).get("message"),
        "known_states": completion_payload.get("known_states"),
        "frontier_states": completion_payload.get("frontier_states"),
        "frontier_candidates": completion_payload.get("frontier_candidates"),
        "blocked_candidates": completion_payload.get("blocked_candidates")
        or completion_payload.get("candidates_blocked"),
        "restorations_abandoned": completion_payload.get(
            "restorations_abandoned"
        ),
        "restrictions": completion_payload.get("restrictions") or [],
        "unreachable_restorations": completion_payload.get("unreachable_restorations") or [],
    }
    # Resolve the same explicit evidence ledger used by the standalone report.
    # Historical aggregate-only runs retain their original counts and status.
    ledger = coverage_restriction_ledger(completion_payload, events)
    completion["restrictions"] = ledger["restrictions"]
    if ledger["has_individual_records"]:
        completion["blocked_candidates"] = ledger["restriction_count"]
    if ledger["resolved_restrictions"]:
        completion["resolved_restrictions"] = ledger["resolved_restrictions"]
        completion["resolved_restriction_count"] = ledger["resolved_restriction_count"]
    if (
        status_value == "completed"
        and completion["type"] == "coverage_complete_with_restrictions"
    ):
        label = "Exploração concluída com restrições"
        description = (
            "A cobertura alcançável foi esgotada; controles que não puderam "
            "ser executados com segurança ficaram documentados."
        )

    overall_percentage: int | None = percentage
    if phase in {"exploring", "waiting_input"}:
        overall_percentage = (
            round(browser_percentage * 0.7)
            if browser_percentage is not None
            else None
        )
    elif phase == "documenting":
        overall_percentage = 70 + round(percentage * 0.25)
    elif phase == "reporting":
        overall_percentage = 95 + round(percentage * 0.05)
    elif phase == "complete":
        overall_percentage = 100

    return {
        "phase": phase,
        "phase_label": label,
        "phase_description": description,
        "phase_progress": {
            "current": current,
            "total": total,
            "percentage": percentage,
        },
        "browser_progress": browser_progress,
        "overall_progress": (
            min(overall_percentage, 100)
            if overall_percentage is not None
            else None
        ),
        "postprocessing": postprocessing,
        "completion": completion,
    }


def _exploration_payload(item: dict[str, Any]) -> dict[str, Any]:
    item = without_rerun_recipes(item)
    result = localize_report_presentation(prepare_scenario_timelines(item)) if "scenarios" in item else dict(item)
    exploration_id = result["id"]
    result["detail_url"] = f"/api/explorations/{exploration_id}"
    result["report_url"] = (
        f"/api/explorations/{exploration_id}/report"
        if result.get("report_available")
        else None
    )
    raw_max_steps = result.get("options", {}).get("max_steps")
    max_steps = max(int(raw_max_steps), 1) if raw_max_steps is not None else None
    current_step = int(result.get("steps_completed", 0))
    percentage = (
        min(round((current_step / max_steps) * 100), 100)
        if max_steps is not None
        else None
    )
    if result.get("status") == "completed":
        percentage = 100
    result["progress"] = percentage
    result["progress_detail"] = {
        "current": current_step,
        "total": max_steps,
        "percentage": percentage,
    }
    result["current_step"] = f"Etapa {current_step}" + (
        f" de {max_steps}" if max_steps is not None else " · cobrindo até esgotar"
    )
    result["current_step_number"] = current_step
    result["current_action"] = result.get("current_action")
    result["counters"] = result.get("counters") or {
        "actions": 0,
        "issues": 0,
        "scenarios": 0,
        "pages_visited": 1 if result.get("app_url") else 0,
    }
    result["latest_screenshot_url"] = None
    result["screenshot_url"] = None
    result["updated_at"] = result.get("updated_at") or (
        result.get("finished_at")
        or result.get("started_at")
        or result.get("created_at")
    )
    if "actions" in result:
        actions = result.get("actions", [])
        issues = result.get("issues", [])
        scenarios = result.get("scenarios", [])
        visited = {result.get("app_url"), result.get("current_url")}
        for action in actions:
            action_result = action.get("result", {})
            visited.add(action_result.get("before_url"))
            visited.add(action_result.get("after_url"))
        result["counters"] = {
            "actions": len(actions),
            "issues": result.get("technical_signal_summary", {}).get(
                "occurrences", len(issues)
            ),
            "scenarios": len(scenarios),
            "pages_visited": len({url for url in visited if url}),
        }
        events = result.get("events", [])
        if events:
            result["updated_at"] = events[-1].get("created_at") or result["updated_at"]
            last_action_event = next(
                (
                    event
                    for event in reversed(events)
                    if event.get("type") in {"action_started", "action_completed"}
                ),
                None,
            )
            if last_action_event and last_action_event.get("type") == "action_started":
                result["current_action"] = last_action_event.get("payload", {}).get(
                    "action"
                ) or last_action_event.get("message")
    events = result.get("events", [])
    result.update(_phase_payload(result, events))
    result["duration_summary"] = execution_duration_summary(result)
    if "scenarios" in result:
        result["verification_summary"] = verification_summary(result["scenarios"])
        visual_requested = (result.get("options") or {}).get("auxiliary_evidence_mode") == "visual"
        result["visual_review_summary"] = (
            {"requested": True, **visual_review_counts(result["scenarios"])}
            if visual_requested else
            {**visual_review_counts([]), "requested": False,
             "total": len(result["scenarios"]), "not_requested": len(result["scenarios"])}
        )
        result["visual_review_resume"] = visual_review_resume_summary(result)
    if "screenshots" in result:
        result["screenshots"] = [
            {
                **screenshot,
                "url": f"/api/explorations/{exploration_id}/screenshots/{screenshot['path']}",
            }
            for screenshot in result["screenshots"]
        ]
        if result["screenshots"]:
            result["latest_screenshot_url"] = result["screenshots"][-1]["url"]
            result["screenshot_url"] = result["latest_screenshot_url"]
    return result


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "autojev", "version": __version__}


@router.get("/providers")
async def list_providers(request: Request) -> list[dict[str, Any]]:
    return [_provider_payload(item) for item in _repository(request).list_providers()]


@router.post("/providers", status_code=status.HTTP_201_CREATED)
async def create_provider(body: ProviderCreate, request: Request) -> dict[str, Any]:
    api_key = body.api_key.get_secret_value() if body.api_key else None
    try:
        await OpenAICompatibleClient(
            body.base_url,
            api_key,
            request.app.state.settings.http_timeout_seconds,
        ).validate_credentials()
    except ProviderConnectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    provider = _repository(request).create_provider(
        name=body.name,
        base_url=body.base_url,
        api_key=api_key,
        enabled=body.enabled,
        models=[
            ProviderModel(
                id=model.id,
                label=model.label or model.id,
                enabled=model.enabled,
                input_modalities=model.input_modalities,
                output_modalities=model.output_modalities,
                supports_vision=model.supports_vision,
                capabilities_known=(
                    model.capabilities_known
                    or bool(
                        {"input_modalities", "output_modalities", "supports_vision"}
                        & model.model_fields_set
                    )
                ),
            )
            for model in body.models
        ],
    )
    return _provider_payload(provider)


@router.get("/providers/{provider_id}")
async def get_provider(provider_id: str, request: Request) -> dict[str, Any]:
    try:
        return _provider_payload(_repository(request).get_provider(provider_id))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _update_provider(
    provider_id: str, body: ProviderUpdate, request: Request
) -> dict[str, Any]:
    fields = body.model_fields_set
    models = None
    repository = _repository(request)
    try:
        current = repository.get_provider(provider_id)
        if "models" in fields:
            existing_by_id = {model.id: model for model in current.models}
            models = []
            for model in body.models or []:
                existing = existing_by_id.get(model.id)
                capability_fields = {
                    "input_modalities",
                    "output_modalities",
                    "supports_vision",
                    "capabilities_known",
                }
                capabilities_supplied = bool(
                    capability_fields & model.model_fields_set
                )
                models.append(
                    ProviderModel(
                        id=model.id,
                        label=model.label or model.id,
                        enabled=model.enabled,
                        input_modalities=(
                            model.input_modalities
                            if "input_modalities" in model.model_fields_set or existing is None
                            else existing.input_modalities
                        ),
                        output_modalities=(
                            model.output_modalities
                            if "output_modalities" in model.model_fields_set or existing is None
                            else existing.output_modalities
                        ),
                        supports_vision=(
                            model.supports_vision
                            if "supports_vision" in model.model_fields_set or "input_modalities" in model.model_fields_set or existing is None
                            else existing.supports_vision
                        ),
                        capabilities_known=(
                            model.capabilities_known
                            if "capabilities_known" in model.model_fields_set
                            else capabilities_supplied or bool(existing and existing.capabilities_known)
                        ),
                    )
                )
        if fields & {"base_url", "api_key"}:
            effective_base_url = body.base_url or current.base_url
            if "api_key" in fields:
                effective_api_key = (
                    body.api_key.get_secret_value() if body.api_key else None
                )
            else:
                effective_api_key = repository.get_provider_secret(provider_id)
            await OpenAICompatibleClient(
                effective_base_url,
                effective_api_key,
                request.app.state.settings.http_timeout_seconds,
            ).validate_credentials()
        provider = _repository(request).update_provider(
            provider_id,
            name=body.name,
            base_url=body.base_url,
            api_key=body.api_key.get_secret_value() if body.api_key else None,
            replace_api_key="api_key" in fields,
            enabled=body.enabled,
            models=models,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderConnectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _provider_payload(provider)


@router.patch("/providers/{provider_id}")
async def patch_provider(
    provider_id: str, body: ProviderUpdate, request: Request
) -> dict[str, Any]:
    return await _update_provider(provider_id, body, request)


@router.put("/providers/{provider_id}")
async def put_provider(
    provider_id: str, body: ProviderUpdate, request: Request
) -> dict[str, Any]:
    return await _update_provider(provider_id, body, request)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(provider_id: str, request: Request) -> Response:
    try:
        _repository(request).delete_provider(provider_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/providers/{provider_id}/discover-models")
async def discover_models(provider_id: str, request: Request) -> dict[str, Any]:
    repository = _repository(request)
    try:
        provider = repository.get_provider(provider_id)
        client = OpenAICompatibleClient(
            provider.base_url,
            repository.get_provider_secret(provider_id),
            request.app.state.settings.http_timeout_seconds,
        )
        discovered = await client.list_models()
        if "openrouter.ai" in provider.base_url.lower():
            discovered = [
                *discovered,
                {
                    "id": "typesafe/jev-1.13",
                    "label": "TypeSafe: Jev 1.13",
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                    "supports_vision": False,
                    "capabilities_known": True,
                },
            ]
        discovered_by_id = {model["id"]: model for model in discovered}
        merged: list[ProviderModel] = []
        seen: set[str] = set()
        for current in provider.models:
            catalog_model = discovered_by_id.get(current.id)
            if catalog_model and catalog_model.get("capabilities_known"):
                current = ProviderModel(
                    id=current.id,
                    label=current.label,
                    enabled=current.enabled,
                    input_modalities=catalog_model.get("input_modalities") or [],
                    output_modalities=catalog_model.get("output_modalities") or [],
                    supports_vision=bool(catalog_model.get("supports_vision")),
                    capabilities_known=True,
                )
            merged.append(current)
            seen.add(current.id)
        for model in discovered:
            if model["id"] not in seen:
                merged.append(
                    ProviderModel(
                        id=model["id"],
                        label=model["label"],
                        enabled=True,
                        input_modalities=model.get("input_modalities") or [],
                        output_modalities=model.get("output_modalities") or [],
                        supports_vision=bool(model.get("supports_vision")),
                        capabilities_known=bool(model.get("capabilities_known")),
                    )
                )
                seen.add(model["id"])
        provider = repository.replace_provider_models(provider_id, merged)
        return {
            "discovered": len(discovered),
            "models": [asdict(model) for model in provider.models],
            "provider": _provider_payload(provider),
        }
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/providers/{provider_id}/test")
async def test_provider(
    provider_id: str,
    request: Request,
    body: ConnectionTestInput | None = None,
) -> dict[str, Any]:
    repository = _repository(request)
    try:
        provider = repository.get_provider(provider_id)
        client = OpenAICompatibleClient(
            provider.base_url,
            repository.get_provider_secret(provider_id),
            request.app.state.settings.http_timeout_seconds,
        )
        return await client.test(body.model_id if body else None)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/models")
async def list_models(
    request: Request, include_disabled: bool = Query(default=False)
) -> list[dict[str, Any]]:
    result = []
    for provider in _repository(request).list_providers():
        if not include_disabled and not provider.enabled:
            continue
        for model in provider.models:
            if not include_disabled and not model.enabled:
                continue
            result.append(
                {
                    "provider_id": provider.id,
                    "provider_name": provider.name,
                    "id": model.id,
                    "label": model.label,
                    "enabled": model.enabled and provider.enabled,
                    "input_modalities": model.input_modalities,
                    "output_modalities": model.output_modalities,
                    "supports_vision": model.supports_vision,
                    "capabilities_known": model.capabilities_known,
                }
            )
    return result


def _validate_selection(
    repository: SQLiteRepository,
    selection: ModelSelection,
    role: str,
    *,
    require_vision: bool = False,
) -> None:
    try:
        provider = repository.get_provider(selection.provider_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=422, detail=f"{role} provider was not found") from exc
    if not provider.enabled:
        raise HTTPException(status_code=422, detail=f"{role} provider is disabled")
    selected = next(
        (
            model
            for model in provider.models
            if model.id == selection.model_id and model.enabled
        ),
        None,
    )
    if selected is None:
        raise HTTPException(
            status_code=422,
            detail=f"{role} model is not configured or is disabled for this provider",
        )
    if require_vision and not selected.supports_vision:
        raise HTTPException(
            status_code=422,
            detail=f"{role} model does not declare image input support",
        )


async def _validate_exploration_credentials(
    repository: SQLiteRepository,
    selections: tuple[ModelSelection, ...],
    timeout_seconds: float,
) -> None:
    """Falha antes de abrir o browser quando a credencial do provedor é inválida."""

    checked: set[str] = set()
    for selection in selections:
        if selection.provider_id in checked:
            continue
        checked.add(selection.provider_id)
        provider = repository.get_provider(selection.provider_id)
        client = OpenAICompatibleClient(
            provider.base_url,
            repository.get_provider_secret(provider.id),
            timeout_seconds,
        )
        await client.validate_credentials()


@router.post("/explorations", status_code=status.HTTP_202_ACCEPTED)
async def create_exploration(
    body: ExplorationCreate, request: Request
) -> dict[str, Any]:
    repository = _repository(request)
    agent = ModelSelection(
        provider_id=body.agent.provider_id, model_id=body.agent.model_id
    )
    auxiliary = ModelSelection(
        provider_id=body.auxiliary_llm.provider_id,
        model_id=body.auxiliary_llm.model_id,
    )
    _validate_selection(repository, agent, "JEV")
    _validate_selection(
        repository,
        auxiliary,
        "Auxiliary LLM",
        require_vision=body.options.auxiliary_evidence_mode == "visual",
    )
    try:
        await _validate_exploration_credentials(
            repository,
            (agent, auxiliary),
            request.app.state.settings.http_timeout_seconds,
        )
    except ProviderConnectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    options = ExplorationOptions(**body.options.model_dump())
    domain_request = ExplorationRequest(
        app_url=body.app_url,
        agent=agent,
        auxiliary_llm=auxiliary,
        options=options,
    )
    exploration_id = repository.create_exploration(domain_request)
    credentials = {
        key: value.get_secret_value() for key, value in body.credentials.items()
    }
    request.app.state.credential_vault.put(exploration_id, credentials)
    request.app.state.exploration_manager.submit(exploration_id)
    return _exploration_payload(
        repository.get_exploration(exploration_id, include_details=False)
    )


@router.get("/explorations")
async def list_explorations(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> list[dict[str, Any]]:
    repository = _repository(request)
    return [
        _exploration_payload(item)
        for item in repository.list_explorations(limit)
    ]


@router.get("/explorations/{exploration_id}")
async def get_exploration(
    exploration_id: str, request: Request
) -> dict[str, Any]:
    try:
        rerun = request.app.state.scenario_rerun_service.state_for(exploration_id)
        item = _repository(request).get_exploration(exploration_id)
        result = _exploration_payload(item)
        result["visual_review_resume"] = request.app.state.visual_review_resume_service.state_for(exploration_id, item)
        if rerun.get("source_exploration_id"):
            result["rerun"] = rerun
        return result
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/explorations/{exploration_id}/scenarios/{scenario_id}/rerun")
async def scenario_rerun_preflight(
    exploration_id: str, scenario_id: str, request: Request,
) -> dict[str, Any]:
    """Read-only availability: opening this screen never opens a browser."""
    try:
        result = request.app.state.scenario_rerun_service.preflight(exploration_id, scenario_id)
        return {
            **result, "checks": [check_label(kind) for kind in result.get("checks") or []],
            "source_exploration_id": exploration_id, "scenario_id": scenario_id,
            "manual_preparation_required": True,
            "effects": [
                "A ação original será repetida uma única vez e pode modificar dados na aplicação em teste.",
                "Prepare o login e o estado da página no navegador antes de continuar.",
                "O resultado ficará em uma nova execução; o relatório original será preservado.",
                "Cards extras de banco de dados são apenas visuais e não serão executados.",
                "Esta reexecução usa verificações locais, sem chamadas ao JEV ou à LLM auxiliar.",
            ],
        }
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/explorations/{exploration_id}/scenarios/{scenario_id}/rerun", status_code=status.HTTP_202_ACCEPTED)
async def scenario_rerun_start(
    exploration_id: str, scenario_id: str, body: ScenarioRerunInput, request: Request,
) -> dict[str, Any]:
    # CORS is not an authorization check. Reject browser requests from an
    # unrelated/null origin even if a future CORS configuration is permissive.
    origin = request.headers.get("origin")
    allowed = {*request.app.state.settings.cors_origins, str(request.base_url).rstrip("/")}
    if origin is not None and origin not in allowed:
        raise HTTPException(status_code=403, detail="Abra a reexecução pela aplicação AutoJev.")
    try:
        new_id = request.app.state.scenario_rerun_service.submit(exploration_id, scenario_id)
        result = _exploration_payload(_repository(request).get_exploration(new_id))
        result["rerun"] = request.app.state.scenario_rerun_service.state_for(new_id)
        return result
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/explorations/{exploration_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_exploration(
    exploration_id: str, request: Request
) -> dict[str, Any]:
    try:
        _repository(request).request_cancel(exploration_id)
        request.app.state.exploration_manager.cancel(exploration_id)
        if request.app.state.scenario_rerun_service.state_for(exploration_id).get("source_exploration_id"):
            request.app.state.scenario_rerun_service.cancel(exploration_id)
        return _exploration_payload(
            _repository(request).get_exploration(exploration_id, include_details=False)
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/explorations/{exploration_id}/resume", status_code=status.HTTP_202_ACCEPTED)
async def resume_exploration(
    exploration_id: str, request: Request
) -> dict[str, Any]:
    try:
        request.app.state.exploration_manager.resume_after_user_input(exploration_id)
        if request.app.state.scenario_rerun_service.state_for(exploration_id).get("source_exploration_id"):
            request.app.state.scenario_rerun_service.resume(exploration_id)
        return _exploration_payload(
            _repository(request).get_exploration(exploration_id, include_details=False)
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/explorations/{exploration_id}/visual-reviews/resume", status_code=status.HTTP_202_ACCEPTED)
async def resume_visual_reviews(
    exploration_id: str, body: VisualReviewResumeInput, request: Request,
) -> dict[str, Any]:
    try:
        return request.app.state.visual_review_resume_service.resume(
            exploration_id, body.max_requests, body.max_attempts)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/explorations/{exploration_id}/visual-reviews/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_visual_reviews(exploration_id: str, request: Request) -> dict[str, Any]:
    try:
        return await request.app.state.visual_review_resume_service.cancel(exploration_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/explorations/{exploration_id}/report", response_class=HTMLResponse)
async def get_report(exploration_id: str, request: Request) -> FileResponse:
    try:
        raw_path = _repository(request).get_report_path(exploration_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not raw_path:
        raise HTTPException(status_code=409, detail="Report is not ready")
    path = Path(raw_path).resolve()
    allowed = (request.app.state.settings.artifact_dir / exploration_id).resolve()
    if allowed not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Report artifact was not found")
    # Existing reports are immutable evidence. Render their updated presentation
    # to a separate artifact, retaining original verdicts and the original HTML.
    # No browser/model calls or historical data changes are needed.
    snapshot = _repository(request).get_exploration(exploration_id)
    snapshot["visual_review_resume"] = visual_review_resume_summary(snapshot)
    marker = b'data-report-summary="execution-v2"'
    coverage_marker = b'data-coverage-ledger="execution-evidence-v1"'
    cost_marker = b'data-cost-breakdown="roles-v1"'
    visual_policy_marker = b'data-visual-review-policy="bounded-v1"'
    locale_marker = f'data-report-locale="{REPORT_LOCALE_VERSION}"'.encode("ascii")
    layout_marker = f'data-report-layout="{REPORT_LAYOUT_VERSION}"'.encode("ascii")
    snapshot_marker = f'data-report-snapshot="{report_snapshot_key(snapshot)}"'.encode("ascii")

    def valid_presentation(candidate: Path) -> bool:
        if not candidate.is_file() or allowed not in candidate.resolve().parents:
            return False
        with candidate.open("rb") as artifact:
            header = artifact.read(8192)
            if any(required not in header for required in (
                marker, coverage_marker, cost_marker, visual_policy_marker, locale_marker, layout_marker, snapshot_marker,
            )):
                return False
            artifact.seek(max(candidate.stat().st_size - 1024, 0))
            return artifact.read().rstrip().endswith(b"</html>")

    def current_report() -> Path:
        from autojev.adapters.report import StandaloneHtmlReport

        updated = allowed / "report-ct-timeline-v1.html"
        presentation_modified = max(
            path.stat().st_mtime,
            Path(getfile(StandaloneHtmlReport)).stat().st_mtime,
            Path(getfile(prepare_scenario_timelines)).stat().st_mtime,
            Path(getfile(coverage_restriction_ledger)).stat().st_mtime,
            Path(getfile(execution_duration_summary)).stat().st_mtime,
            Path(getfile(visual_review_counts)).stat().st_mtime,
            Path(getfile(localize_report_presentation)).stat().st_mtime,
            Path(getfile(render_premium_report)).stat().st_mtime,
            (REPORT_ASSETS / "premium.css").stat().st_mtime,
            (REPORT_ASSETS / "journey.css").stat().st_mtime,
            (REPORT_ASSETS / "premium.js").stat().st_mtime,
        )
        if valid_presentation(path) and path.stat().st_mtime >= presentation_modified:
            return path
        if valid_presentation(updated) and updated.stat().st_mtime >= presentation_modified:
            return updated

        # A separate temporary artifact and atomic rename avoid serving a
        # partially written HTML when two report requests arrive together.
        with NamedTemporaryFile(dir=allowed, suffix=".html", delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            StandaloneHtmlReport(request.app.state.settings.artifact_dir).generate(
                snapshot, str(temporary_path)
            )
            temporary_path.replace(updated)
        finally:
            temporary_path.unlink(missing_ok=True)
        return updated

    # Encoding many local captures should not block the exploration's async
    # browser/CT pipeline while the user opens a historical report.
    path = await run_in_threadpool(current_report)
    return FileResponse(path, media_type="text/html; charset=utf-8", filename="report.html")


@router.get("/explorations/{exploration_id}/screenshots/{filename:path}")
async def get_screenshot(
    exploration_id: str, filename: str, request: Request
) -> FileResponse:
    try:
        item = _repository(request).get_exploration(exploration_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    basename = Path(filename).name
    if not any(Path(entry["path"]).name == basename for entry in item["screenshots"]):
        raise HTTPException(status_code=404, detail="Screenshot was not found")
    directory = (
        request.app.state.settings.artifact_dir / exploration_id / "screenshots"
    ).resolve()
    path = (directory / basename).resolve()
    if directory not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot artifact was not found")
    media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(path, media_type=media_type)
