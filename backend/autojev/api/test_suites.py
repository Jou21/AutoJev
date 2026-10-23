"""Application-only planning endpoints. GET never dispatches browser actions."""
from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal
from urllib.parse import quote, urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import (
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)
from starlette.concurrency import run_in_threadpool

from autojev.adapters.execution_plans import PlanConflict
from autojev.adapters.repository import NotFoundError
from autojev.api.schemas import (
    ApiModel,
    CaptureCheckpointInput,
    ModelSelectionInput,
    ScenarioCapturePlanInput,
    ScenarioRerunInput,
)

router = APIRouter(prefix="/api/test-suites")


class DefinitionInput(ApiModel):
    title: StrictStr = Field(min_length=1, max_length=300)
    description: StrictStr = Field(default="", max_length=3000)
    preconditions: list[StrictStr] = Field(default_factory=list, max_length=30)
    steps: list[StrictStr] = Field(min_length=1, max_length=100)
    expected_result: StrictStr = Field(min_length=1, max_length=3000)
    url: StrictStr = Field(min_length=8, max_length=2000)

    @field_validator("title", "expected_result")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("Preencha um texto não vazio.")
        return value.strip()

    @field_validator("steps", "preconditions")
    @classmethod
    def bounded_steps(cls, values):
        if any(not value.strip() or len(value) > 2000 for value in values):
            raise ValueError("Cada passo deve conter de 1 a 2000 caracteres.")
        return [value.strip() for value in values]

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Informe uma URL HTTP(S) sem credenciais.")
        return value


class UpdateDefinitionInput(DefinitionInput):
    expected_revision: StrictInt = Field(ge=1)


class CatalogStateInput(ApiModel):
    archived: StrictBool
    expected_revision: StrictInt = Field(ge=0)


class PlannedIntegrationInput(CaptureCheckpointInput):
    kind: Literal["sws", "sql"]


class PlanItemInput(ApiModel):
    case_id: StrictStr = Field(min_length=1, max_length=100)
    source_exploration_id: StrictStr | None = Field(default=None, max_length=100)
    source_scenario_id: StrictStr | None = Field(default=None, max_length=100)
    capture_plan: ScenarioCapturePlanInput | None = None
    planned_integrations: list[PlannedIntegrationInput] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def distinct_additions(self):
        points = [*(self.capture_plan.checkpoints if self.capture_plan else []), *self.planned_integrations]
        if len(points) > 100 or len({point.id for point in points}) != len(points) or len({(point.gap, point.order) for point in points}) != len(points):
            raise ValueError("Use até 100 etapas extras, com identificadores e posições distintos por teste.")
        return self


class PlanInput(ApiModel):
    name: StrictStr = Field(min_length=1, max_length=160)
    items: list[PlanItemInput] = Field(min_length=1, max_length=500)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Dê um nome ao planejamento.")
        return value.strip()


class PlanUpdateInput(PlanInput):
    expected_revision: StrictInt = Field(ge=1)


class StartInput(ScenarioRerunInput):
    expected_revision: StrictInt = Field(ge=1)


class ManualResultInput(ApiModel):
    expected_revision: StrictInt = Field(ge=1)
    status: Literal["PASS", "FAIL", "BLOCKED", "SKIPPED"]
    actual_result: StrictStr = Field(min_length=3, max_length=4000)

    @field_validator("actual_result")
    @classmethod
    def observed_result(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("Descreva o resultado realmente observado.")
        return value.strip()


class ChatMessage(ApiModel):
    role: Literal["user", "assistant"]
    content: StrictStr = Field(min_length=1, max_length=4000)


class AssistantInput(ApiModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=12)
    selected_case_ids: list[StrictStr] = Field(default_factory=list, max_length=500)
    llm: ModelSelectionInput | None = None
    draft: DefinitionInput | None = None


class CoverageOperation(ApiModel):
    operation: Literal["create", "read", "update", "delete"]
    status: Literal["existing", "gap", "uncertain", "not_applicable"]
    case_ids: list[StrictStr] = Field(default_factory=list, max_length=50)
    new_case_indexes: list[StrictInt] = Field(default_factory=list, max_length=8)
    reason: StrictStr = Field(min_length=1, max_length=1200)


class CoverageAnalysis(ApiModel):
    screen: StrictStr = Field(min_length=1, max_length=200)
    operations: list[CoverageOperation] = Field(min_length=4, max_length=4)

    @field_validator("operations")
    @classmethod
    def distinct_operations(cls, values):
        if len({value.operation for value in values}) != 4:
            raise ValueError("Analise as quatro operações do CRUD uma vez cada.")
        return values


class AssistantOutput(ApiModel):
    reply: StrictStr = Field(min_length=1, max_length=8000)
    selected_case_ids: list[StrictStr] = Field(default_factory=list, max_length=500)
    new_cases: list[dict] = Field(default_factory=list, max_length=8)
    coverage: CoverageAnalysis | None = None


class DiscoveryInput(ScenarioRerunInput):
    focus: StrictStr = Field(min_length=3, max_length=1000)
    max_steps: StrictInt = Field(default=10, ge=1, le=30)


class ImportInput(ApiModel):
    session_id: StrictStr = Field(min_length=1, max_length=100)
    case_id: StrictStr = Field(min_length=1, max_length=100)


def service(request):
    return request.app.state.test_suite_service


def trusted(request):
    from autojev.api.routes import _require_rerun_origin
    _require_rerun_origin(request)


def call(operation):
    try:
        return operation()
    except NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (PlanConflict, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("")
def groups(request: Request):
    return service(request).groups()


@router.get("/rounds/{plan_id}")
def get_plan(plan_id: str, request: Request):
    return call(lambda: service(request).store.get(plan_id))


@router.post("/rounds/{plan_id}/validate")
def validate_plan(plan_id: str, request: Request):
    trusted(request)
    return call(lambda: service(request).validate(plan_id))


@router.post("/rounds/{plan_id}/start", status_code=202)
async def start_plan(plan_id: str, body: StartInput, request: Request):
    trusted(request)
    # Capture plans are accepted per CT only, never as a global ambient plan.
    if body.capture_plan is not None:
        raise HTTPException(422, "Configure os prints em cada CT.")
    return call(lambda: service(request).start(plan_id, body.expected_revision))


@router.post("/rounds/{plan_id}/stop")
async def stop_plan(plan_id: str, request: Request):
    trusted(request)
    return call(lambda: service(request).stop(plan_id))


@router.post("/rounds/{plan_id}/cases/{case_id}/result")
async def manual_result(plan_id: str, case_id: str, body: ManualResultInput, request: Request):
    trusted(request)
    return call(lambda: service(request).manual_result(plan_id, case_id, body.status, body.actual_result, body.expected_revision))


@router.get("/{root_id}")
def suite(root_id: str, request: Request):
    return call(lambda: service(request).catalog(root_id))


@router.get("/{source_id}/case-context/{case_id}")
def case_context(source_id: str, case_id: str, request: Request):
    from autojev.services.scenario_revisions import scenario_report_origin
    from autojev.services.test_suites import definition, recorded_step_metadata
    suites = service(request)
    origin = call(lambda: scenario_report_origin(suites.repository, source_id))
    catalog = call(lambda: suites.catalog(source_id))
    stable = origin["report_scenario_id"] if source_id != origin["report_exploration_id"] else case_id
    imported = next((case for case in catalog["cases"] if case.get("lineage_exploration_id") == origin["report_exploration_id"] and case.get("lineage_case_id") == stable), None)
    if imported is None and source_id == catalog["id"]:
        imported = next((case for case in catalog["cases"] if case["id"] == case_id and case["mode"] == "manual"), None)
    if imported is None:
        raise HTTPException(404, "CT não encontrado no conjunto. Importe o CT descoberto antes de planejá-lo.")
    reference = None
    if imported["mode"] == "automatic":
        source = suites.repository.get_exploration(source_id)
        source_case = next((case for case in source.get("scenarios") or [] if case["id"] == case_id), None)
        if source_case is None:
            raise HTTPException(404, "A referência selecionada não contém este CT.")
        reference = {**definition(source_case), "recorded_steps": recorded_step_metadata(suites.reruns._source_timeline(source, case_id), source_id)}
    return {"root_id": catalog["id"], "case_id": imported["id"], "mode": imported["mode"], "reference": reference,
            "source_exploration_id": source_id, "source_scenario_id": case_id}


@router.post("/{root_id}/assistant")
async def assistant(root_id: str, body: AssistantInput, request: Request):
    from autojev.services.planning_assistant import suggest_plan
    trusted(request)
    suites = service(request)
    catalog = call(lambda: suites.catalog(root_id))
    known = {case["id"] for case in catalog["cases"] if not case.get("archived")}
    if set(body.selected_case_ids) - known:
        raise HTTPException(422, "A seleção contém CTs de outro conjunto.")
    try:
        raw, usage, llm = await suggest_plan(request.app.state.exploration_manager, suites, root_id,
                                           [message.model_dump() for message in body.messages], body.selected_case_ids,
                                           body.llm.model_dump() if body.llm else None,
                                           draft=body.draft.model_dump() if body.draft else None)
        suggestion = AssistantOutput.model_validate(raw)
        if set(suggestion.selected_case_ids) - known:
            raise ValueError("Unknown IDs")
        new_cases = [DefinitionInput.model_validate({**value, "url": catalog["app_url"]}).model_dump() for value in suggestion.new_cases]
        if suggestion.coverage:
            for operation in suggestion.coverage.operations:
                if set(operation.case_ids) - known or any(index < 0 or index >= len(new_cases) for index in operation.new_case_indexes):
                    raise ValueError("Invalid coverage references")
                if operation.status == "existing" and not operation.case_ids:
                    raise ValueError("An existing test must reference the catalog")
                if operation.status in {"gap", "not_applicable"} and operation.case_ids:
                    raise ValueError("A gap cannot claim existing tests")
                if operation.status == "not_applicable" and operation.new_case_indexes:
                    raise ValueError("An inapplicable operation cannot propose tests")
        return {**suggestion.model_dump(), "new_cases": new_cases, "usage": usage, "llm": llm, "advisory_only": True}
    except Exception as exc:
        # Provider errors can include request content/headers. No raw exception
        # or unvalidated model output is exposed to the browser.
        raise HTTPException(409, "Não foi possível obter uma sugestão válida. Confira o modelo selecionado no chat e seu provedor; nenhum CT foi alterado.") from exc


@router.post("/{root_id}/discover", status_code=202)
async def discover(root_id: str, body: DiscoveryInput, request: Request):
    from autojev.domain.models import (
        ExplorationOptions,
        ExplorationRequest,
        ModelSelection,
    )
    trusted(request)
    suites = service(request)
    root = call(lambda: suites.root(root_id))
    source = suites.repository.get_exploration(root)
    if body.capture_plan is not None:
        raise HTTPException(422, "Descoberta não aceita um plano de capturas de reexecução.")
    manager = request.app.state.exploration_manager
    try:
        manager._client_for(source["agent"]["provider_id"])
        manager._client_for(source["auxiliary_llm"]["provider_id"])
    except Exception as exc:
        raise HTTPException(409, "Configure os provedores deste conjunto antes de iniciar a descoberta.") from exc
    identity = suites.repository.create_exploration(ExplorationRequest(source["app_url"], ModelSelection(**source["agent"]),
        ModelSelection(**source["auxiliary_llm"]), ExplorationOptions(focus_area=body.focus, max_steps=body.max_steps,
        max_duration_seconds=300, auxiliary_max_requests=body.max_steps, auxiliary_evidence_mode="text")))
    suites.store.register_session(identity, root, "discovery")
    suites.repository.add_event(identity, "info", "suite_discovery_source", "Descoberta de CTs vinculada ao conjunto", {"root_id": root, "focus": body.focus})
    manager.submit(identity)
    return {"id": identity, "root_id": root}


@router.post("/{root_id}/import", status_code=201)
def import_case(root_id: str, body: ImportInput, request: Request):
    trusted(request)
    return call(lambda: service(request).import_discovered_case(root_id, body.session_id, body.case_id))


@router.post("/{root_id}/recordings", status_code=201)
async def start_recording(root_id: str, body: ScenarioRerunInput, request: Request):
    trusted(request)
    if body.capture_plan is not None:
        raise HTTPException(422, "A gravação não aceita pausas de uma reexecução.")
    try:
        return await request.app.state.planning_recorder.start(root_id)
    except NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(409, "Não foi possível abrir a gravação. É necessário um navegador visível instalado e nenhuma outra gravação aberta.") from exc


@router.get("/recordings/{identity}/state")
def recording_state(identity: str, request: Request):
    return call(lambda: request.app.state.planning_recorder.get(identity))


@router.post("/recordings/{identity}/finish")
async def finish_recording(identity: str, request: Request):
    trusted(request)
    try:
        return await request.app.state.planning_recorder.finish(identity)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{root_id}/cases", status_code=201)
def create_case(root_id: str, body: DefinitionInput, request: Request):
    trusted(request)
    return call(lambda: service(request).save_case(root_id, body.model_dump()))


@router.put("/{root_id}/cases/{case_id}")
def update_case(root_id: str, case_id: str, body: UpdateDefinitionInput, request: Request):
    trusted(request)
    return call(lambda: service(request).save_case(root_id, body.model_dump(exclude={"expected_revision"}), case_id, body.expected_revision))


@router.patch("/{root_id}/cases/{case_id}/catalog-state")
def update_catalog_state(root_id: str, case_id: str, body: CatalogStateInput, request: Request):
    trusted(request)
    return call(lambda: service(request).archive_case(root_id, case_id, body.archived, body.expected_revision))


@router.post("/{root_id}/plans", status_code=201)
def create_plan(root_id: str, body: PlanInput, request: Request):
    trusted(request)
    return call(lambda: service(request).save_plan(root_id, body.name, [item.model_dump(exclude_none=True) for item in body.items]))


@router.put("/{root_id}/plans/{plan_id}")
def update_plan(root_id: str, plan_id: str, body: PlanUpdateInput, request: Request):
    trusted(request)
    return call(lambda: service(request).save_plan(root_id, body.name, [item.model_dump(exclude_none=True) for item in body.items], plan_id, body.expected_revision))


@router.get("/{root_id}/report")
async def report(root_id: str, request: Request, scope: Literal["consolidated", "initial", "round"] = "consolidated", round_id: str | None = None,
                 case_id: str | None = Query(default=None, min_length=1, max_length=100), preview: bool = False):
    if preview and case_id is None:
        raise HTTPException(422, "A visualização rápida exige um CT específico.")
    snapshot = await run_in_threadpool(lambda: call(lambda: service(request).report_snapshot(root_id, scope, round_id, case_id)))
    image_url = None
    if preview:
        export_query = urlencode({"scope": scope, "case_id": case_id, **({"round_id": round_id} if round_id else {})})
        snapshot["online_preview"] = {"export_path": f'/test-suites/{quote(snapshot["id"], safe="")}/report?{export_query}'}
        base = str(request.base_url).rstrip("/")
        image_url = lambda owner, filename: base + "/api/explorations/" + quote(owner, safe="") + "/screenshots/" + quote(filename, safe="")
    directory = request.app.state.settings.artifact_dir / snapshot["id"]
    directory.mkdir(parents=True, exist_ok=True)
    # Do not overwrite a historical report or share mutable artifacts across
    # concurrent export requests. File removal occurs after streaming finishes.
    with NamedTemporaryFile(dir=directory, suffix=".html", delete=False) as temporary:
        path = Path(temporary.name)
    try:
        await run_in_threadpool(service(request).render_report, snapshot, path, request.app.state.settings.artifact_dir, image_url)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    from starlette.background import BackgroundTask
    return FileResponse(path, media_type="text/html; charset=utf-8", filename=f"autojev-{scope}.html",
                        headers={"Cache-Control": "private, no-store"}, background=BackgroundTask(path.unlink, missing_ok=True))
