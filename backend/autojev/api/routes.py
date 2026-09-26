from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse

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

from .schemas import (
    ConnectionTestInput,
    ExplorationCreate,
    ProviderCreate,
    ProviderUpdate,
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
    max_steps = max(int(result.get("options", {}).get("max_steps", 1)), 1)
    actions = int(result.get("steps_completed", 0))
    browser_percentage = min(round((actions / max_steps) * 100), 100)
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
            else "O JEV decide e o Playwright executa e valida cada interação."
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
            label = "Criando os casos de teste"
            description = (
                "O navegador já terminou. A LLM auxiliar está organizando as "
                "evidências sem controlar o browser."
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
            }
        ),
        None,
    )
    enrichment = (enrichment_event or {}).get("payload") or {}
    legacy_enriched = sum(1 for event in events if event.get("type") == "scenario_created")
    legacy_local = sum(
        1 for event in events if event.get("type") == "scenario_documentation_fallback"
    )
    enriched_count = int(enrichment.get("enriched") or legacy_enriched)
    local_count = int(enrichment.get("local_evidence") or legacy_local)
    postprocessing = {
        "current": current if phase == "documenting" else int(enrichment.get("current") or 0),
        "total": int(enrichment.get("total") or len(result.get("scenarios") or [])),
        "enriched": enriched_count,
        "local_evidence": local_count,
        "stage": enrichment.get("stage"),
        "reason": enrichment.get("reason"),
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
                "failed",
                "cancelled",
            }
        ),
        None,
    )
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
    }

    overall_percentage = percentage
    if phase in {"exploring", "waiting_input"}:
        overall_percentage = round(browser_percentage * 0.7)
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
        "overall_progress": min(overall_percentage, 100),
        "postprocessing": postprocessing,
        "completion": completion,
    }


def _exploration_payload(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    exploration_id = result["id"]
    result["detail_url"] = f"/api/explorations/{exploration_id}"
    result["report_url"] = (
        f"/api/explorations/{exploration_id}/report"
        if result.get("report_available")
        else None
    )
    max_steps = max(int(result.get("options", {}).get("max_steps", 1)), 1)
    current_step = int(result.get("steps_completed", 0))
    percentage = min(round((current_step / max_steps) * 100), 100)
    if result.get("status") == "completed":
        percentage = 100
    result["progress"] = percentage
    result["progress_detail"] = {
        "current": current_step,
        "total": max_steps,
        "percentage": percentage,
    }
    result["current_step"] = f"Etapa {current_step} de {max_steps}"
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
            "issues": len(issues),
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
    provider = _repository(request).create_provider(
        name=body.name,
        base_url=body.base_url,
        api_key=body.api_key.get_secret_value() if body.api_key else None,
        enabled=body.enabled,
        models=[
            ProviderModel(id=model.id, label=model.label or model.id, enabled=model.enabled)
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
    if "models" in fields:
        models = [
            ProviderModel(id=model.id, label=model.label or model.id, enabled=model.enabled)
            for model in (body.models or [])
        ]
    try:
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
                {"id": "typesafe/jev-1.13", "label": "TypeSafe: Jev 1.13"},
            ]
        old_by_id = {model.id: model for model in provider.models}
        merged = list(provider.models)
        for model in discovered:
            if model["id"] not in old_by_id:
                merged.append(
                    ProviderModel(id=model["id"], label=model["label"], enabled=True)
                )
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
                }
            )
    return result


def _validate_selection(
    repository: SQLiteRepository, selection: ModelSelection, role: str
) -> None:
    try:
        provider = repository.get_provider(selection.provider_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=422, detail=f"{role} provider was not found") from exc
    if not provider.enabled:
        raise HTTPException(status_code=422, detail=f"{role} provider is disabled")
    if not any(model.id == selection.model_id and model.enabled for model in provider.models):
        raise HTTPException(
            status_code=422,
            detail=f"{role} model is not configured or is disabled for this provider",
        )


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
    _validate_selection(repository, auxiliary, "Auxiliary LLM")
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
        return _exploration_payload(
            _repository(request).get_exploration(exploration_id)
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/explorations/{exploration_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_exploration(
    exploration_id: str, request: Request
) -> dict[str, Any]:
    try:
        _repository(request).request_cancel(exploration_id)
        request.app.state.exploration_manager.cancel(exploration_id)
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
        return _exploration_payload(
            _repository(request).get_exploration(exploration_id, include_details=False)
        )
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
