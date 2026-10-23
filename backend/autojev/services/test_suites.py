"""Test-suite projections and sequential rounds, without rewriting executions."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from threading import Lock

from autojev.adapters.execution_plans import ExecutionPlanStore, PlanConflict
from autojev.adapters.repository import NotFoundError
from autojev.domain.models import utc_now
from autojev.services.functional_flows import (
    classify_case_flows,
    group_functional_flows,
)
from autojev.services.rerun_projection import without_rerun_recipes
from autojev.services.scenario_revisions import (
    project_scenario_revisions,
    scenario_report_origin,
)
from autojev.services.scenario_step_order import presentation_scenario_steps

TERMINAL = {"completed", "partial", "failed", "cancelled"}
ACTIVE = {"running", "waiting_manual", "stopping"}
VERDICTS = {"PASS", "FAIL", "BLOCKED", "SKIPPED"}


def definition(case):
    return {key: deepcopy(case.get(key, [] if key in {"steps", "preconditions"} else ""))
            for key in ("title", "description", "preconditions", "steps", "expected_result", "url")}


def recorded_step_metadata(steps, owner):
    """Lightweight, owner-bound image references; never embed images or action data."""
    result = []
    for step in steps:
        item = {"title": step.get("title") or "Registro", "kind": step.get("kind") or "observation"}
        preview = step.get("action_preview")
        screenshot = step.get("screenshot") or (preview.get("screenshot") if isinstance(preview, dict) else None)
        if isinstance(screenshot, str):
            parts = screenshot.replace("\\", "/").split("/")
            filename = parts[-1]
            if (owner and ".." not in parts and not any(ord(char) < 32 or char in "?#" for char in screenshot)
                    and filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))):
                item.update(screenshot=filename, evidence_exploration_id=owner)
        result.append(item)
    return result


class TestSuiteService:
    def __init__(self, repository, reruns):
        self.repository, self.reruns = repository, reruns
        self.store = ExecutionPlanStore(repository)
        self.tasks: dict[str, asyncio.Task] = {}
        self.signals: dict[str, asyncio.Event] = {}
        self.report_gate = Lock()
        # Startup reconciliation never submits/repeats browser work.
        for plan in self.store.list():
            if plan["status"] in ACTIVE:
                plan.update(status="interrupted", finished_at=utc_now(), reason="O servidor reiniciou. Planeje os CTs pendentes em uma nova rodada.")
                for item in plan["items"]:
                    if item["state"] in {"queued", "running", "waiting_manual"}:
                        item["state"] = "interrupted"
                        if item.get("execution_id"):
                            try:
                                owner = repository.get_exploration(item["execution_id"])
                                outcomes = owner.get("scenarios") or []
                                if owner["status"] in TERMINAL and len(outcomes) == 1:
                                    item.update(state="completed", result={**without_rerun_recipes(outcomes[0]), "id": item["case_id"], "evidence_exploration_id": item["execution_id"]}, finished_at=owner.get("finished_at"))
                            except NotFoundError:
                                pass
                self.store.replace(plan, plan["revision"])

    def root(self, identity):
        original = scenario_report_origin(self.repository, identity)["report_exploration_id"]
        return (self.store.session(original) or {}).get("root_id", original)

    def render_report(self, snapshot, path, artifact_dir, image_url=None):
        from autojev.adapters.report import StandaloneHtmlReport
        # Real reports can embed hundreds of megabytes of evidence. Serialize
        # encoding, not browsing or streaming, so repeated downloads stay bounded.
        if image_url is not None:
            # Lightweight browsing never queues behind a large offline export,
            # nor reads/encodes its screenshots. Evidence is fetched on demand.
            StandaloneHtmlReport(artifact_dir, image_url=image_url).generate(snapshot, str(path))
        else:
            with self.report_gate:
                StandaloneHtmlReport(artifact_dir).generate(snapshot, str(path))

    def groups(self):
        groups = {}
        plans = self.store.list()
        planned_runs = {item.get("execution_id") for plan in plans for item in plan["items"]}
        for run in self.repository.list_explorations(10000):
            root = self.root(run["id"])
            group = groups.setdefault(root, {"id": root, "runs": [], "rounds": []})
            group["runs"].append(run)
        for group in groups.values():
            original = next((run for run in group["runs"] if run["id"] == group["id"]), None)
            if original is None:
                original = self.repository.get_exploration(group["id"], include_details=False)
            group.update(app_url=original["app_url"], created_at=original["created_at"], initial_status=original["status"],
                         initial_count=(original.get("counters") or {}).get("scenarios", len(original.get("scenarios") or [])))
            group["rounds"] = [self.round_summary(plan) for plan in plans if plan["root_id"] == group["id"]]
            group["legacy_runs"] = [dict(id=run["id"], created_at=run["created_at"], status=run["status"],
                                         count=(run.get("counters") or {}).get("scenarios", 0)) for run in group["runs"]
                                    if run["id"] != group["id"] and run["id"] not in planned_runs]
            group["active_count"] = sum(run["status"] not in TERMINAL for run in group["runs"]) + sum(plan["status"] == "waiting_manual" for plan in group["rounds"])
            group["latest_activity"] = max([run.get("finished_at") or run["created_at"] for run in group.pop("runs")]
                                           + [plan["updated_at"] for plan in group["rounds"]]
                                           + [case["updated_at"] for case in self.store.cases(group["id"])])
            states = self.store.catalog_states(group["id"])
            group["archived_count"] = sum(value["archived"] for value in states.values())
            group["case_count"] = group["initial_count"] + len(self.store.cases(group["id"])) - group["archived_count"]
            group["latest_activity"] = max([group["latest_activity"], *[value["catalog_updated_at"] for value in states.values()]])
        return sorted(groups.values(), key=lambda group: group["latest_activity"], reverse=True)

    @staticmethod
    def round_summary(plan):
        return {**{key: plan.get(key) for key in ("id", "root_id", "name", "status", "revision", "created_at", "updated_at", "started_at", "finished_at", "reason")},
                "count": len(plan["items"]), "finished_count": sum(item["state"] in {"completed", "failed", "cancelled", "interrupted"} for item in plan["items"]),
                "summary": {state.lower(): sum((item.get("result") or {}).get("status") == state for item in plan["items"]) for state in VERDICTS}}

    def catalog(self, identity):
        root = self.root(identity)
        initial = self.repository.get_exploration(root)
        current = project_scenario_revisions(self.repository, initial)
        catalog = []
        for case in current.get("scenarios") or []:
            revision = case.get("execution_revision") or {}
            catalog.append(dict(id=case["id"], mode="automatic", revision=revision.get("version", 0) + 1,
                                lineage_exploration_id=root, lineage_case_id=case["id"],
                                source_exploration_id=revision.get("exploration_id", root),
                                source_scenario_id=revision.get("scenario_id", case["id"]),
                                recorded_steps=recorded_step_metadata(presentation_scenario_steps(case.get("evidence") or {}), case.get("evidence_exploration_id") or revision.get("exploration_id", root)),
                                status=case.get("status"), recorded_at=revision.get("recorded_at"), flow_classification=classify_case_flows(case), **definition(case)))
        plans = self.store.list(root)
        for case in self.store.cases(root):
            if case["mode"] == "automatic":
                imported = project_scenario_revisions(self.repository, self.repository.get_exploration(case["source_exploration_id"]))
                actual = next(value for value in imported["scenarios"] if value["id"] == case["source_scenario_id"])
                revision = actual.get("execution_revision") or {}
                catalog.append({**case, "status": actual.get("status"), "recorded_at": revision.get("recorded_at"), "flow_classification": classify_case_flows(actual),
                                "recorded_steps": recorded_step_metadata(presentation_scenario_steps(actual.get("evidence") or {}), actual.get("evidence_exploration_id") or revision.get("exploration_id", case["source_exploration_id"])),
                                "lineage_exploration_id": case["source_exploration_id"], "lineage_case_id": case["source_scenario_id"],
                                "source_exploration_id": revision.get("exploration_id", case["source_exploration_id"]),
                                "source_scenario_id": revision.get("scenario_id", case["source_scenario_id"])})
                continue
            attempts = [item for plan in plans for item in plan["items"] if item["case_id"] == case["id"] and item.get("result") and item["definition_revision"] == case["revision"]]
            latest = attempts[-1] if attempts else {}
            catalog.append({**case, "status": (latest.get("result") or {}).get("status"), "recorded_at": latest.get("finished_at"),
                            "flow_classification": classify_case_flows({**case, "evidence": (latest.get("result") or {}).get("evidence") or {}})})
        states = self.store.catalog_states(root)
        catalog = [{**case, **states.get(case["id"], {"archived": False, "catalog_revision": 0})} for case in catalog]
        return dict(id=root, app_url=initial["app_url"], created_at=initial["created_at"], initial_count=len(initial.get("scenarios") or []),
                    cases=catalog, functional_flows=group_functional_flows(catalog), rounds=[self.round_summary(plan) for plan in reversed(plans)], discoveries=self.store.sessions(root),
                    assistant_model=initial["auxiliary_llm"]["model_id"], assistant_llm=initial["auxiliary_llm"])

    def archive_case(self, identity, case_id, archived, expected_revision):
        with self.repository._lock:
            suite = self.catalog(identity)
            if not any(case["id"] == case_id for case in suite["cases"]):
                raise NotFoundError("Teste não encontrado nesta base.")
            root = suite["id"]
            if archived:
                if self.repository.get_exploration(root, include_details=False)["status"] not in TERMINAL or any(plan["status"] in ACTIVE for plan in self.store.list(root)):
                    raise PlanConflict("Aguarde a execução desta base terminar antes de arquivar testes.")
                for state in self.reruns.states.values():
                    if state.get("running") and self.root(state["source_exploration_id"]) == root:
                        raise PlanConflict("Aguarde a reexecução desta base terminar antes de arquivar testes.")
            return {"id": case_id, **self.store.set_archived(root, case_id, archived, expected_revision)}

    def save_case(self, identity, value, case_id=None, expected_revision=None):
        root = self.root(identity)
        initial = self.repository.get_exploration(root)
        if initial["status"] not in TERMINAL:
            raise ValueError("Aguarde a exploração inicial terminar antes de criar ou importar novos CTs.")
        if case_id and any(case["id"] == case_id and case["mode"] != "manual" for case in self.store.cases(root)):
            raise ValueError("Duplique este CT como manual para alterar seu objetivo sem modificar a receita automática.")
        return self.store.save_case(root, value, [case["id"] for case in initial.get("scenarios") or []], case_id, expected_revision)

    def import_discovered_case(self, root_id, session_id, case_id):
        root = self.root(root_id)
        if (self.store.session(session_id) or {}).get("root_id") != root:
            raise ValueError("A descoberta não pertence a este conjunto.")
        for case in self.store.cases(root):
            if case.get("source_exploration_id") == session_id and case.get("source_scenario_id") == case_id:
                return case
        source = self.repository.get_exploration(session_id)
        if source["status"] not in TERMINAL:
            raise ValueError("Espere a descoberta terminar antes de importar o CT.")
        case = next((case for case in source.get("scenarios") or [] if case["id"] == case_id), None)
        if case is None:
            raise ValueError("CT não encontrado nesta descoberta.")
        return self.save_case(root, dict(**definition(case), mode="automatic", source_exploration_id=session_id, source_scenario_id=case_id))

    def _items(self, identity, selected):
        suite = self.catalog(identity)
        available = {case["id"]: case for case in suite["cases"]}
        result, seen = [], set()
        for request in selected:
            case_id = request["case_id"]
            if case_id not in available or case_id in seen:
                raise ValueError("Selecione CTs distintos que pertençam a este conjunto.")
            seen.add(case_id)
            case = available[case_id]
            if case.get("archived"):
                raise ValueError(f"{case_id} está arquivado. Restaure o teste antes de incluí-lo em uma execução.")
            source_id = request.get("source_exploration_id") or case.get("source_exploration_id")
            source_case = request.get("source_scenario_id") or case.get("source_scenario_id")
            frozen = definition(case)
            capture_plan = request.get("capture_plan")
            integrations = deepcopy(request.get("planned_integrations") or [])
            step_count = len(case.get("recorded_steps") or case["steps"])
            if case["mode"] == "automatic":
                origin = scenario_report_origin(self.repository, source_id)
                lineage_root, lineage_case = case["lineage_exploration_id"], case["lineage_case_id"]
                if origin["report_exploration_id"] != lineage_root or (source_id == lineage_root and source_case != lineage_case) or (source_id != lineage_root and origin["report_scenario_id"] != lineage_case):
                    raise ValueError("A execução de referência não pertence a este CT.")
                source = self.repository.get_exploration(source_id)
                stored = next((value for value in source.get("scenarios") or [] if value["id"] == source_case), None)
                if stored is None:
                    raise ValueError("O CT de referência não existe.")
                frozen = definition(stored)
                step_count = len(presentation_scenario_steps(stored.get("evidence") or {}) or frozen["steps"])
            elif capture_plan or integrations or request.get("source_exploration_id") or request.get("source_scenario_id"):
                raise ValueError("Um CT manual não aceita receita automática nem etapas extras de automação.")
            if any(point["gap"] > step_count for point in integrations):
                raise ValueError("A posição de uma integração não corresponde às etapas deste teste.")
            result.append(dict(case_id=case_id, mode=case["mode"], definition=frozen, definition_revision=case["revision"],
                               source_exploration_id=source_id, source_scenario_id=source_case,
                               capture_plan=capture_plan, planned_integrations=integrations, state="queued", execution_id=None, result=None, finished_at=None))
        return suite["id"], result

    def save_plan(self, identity, name, selected, plan_id=None, expected_revision=None):
        root, items = self._items(identity, selected)
        if plan_id is None:
            return self.store.create(root, name, items)
        plan = self.store.get(plan_id)
        if plan["root_id"] != root or plan["status"] != "draft":
            raise PlanConflict("Somente rascunhos deste conjunto podem ser editados.")
        plan.update(name=name, items=items)
        return self.store.replace(plan, expected_revision)

    def validate(self, plan_id):
        plan = self.store.get(plan_id)
        checks = []
        archived = {identity for identity, state in self.store.catalog_states(plan["root_id"]).items() if state["archived"]}
        for item in plan["items"]:
            if item["case_id"] in archived:
                check = {"available": False, "code": "archived", "reason": "Teste arquivado. Restaure-o na base ou remova-o desta execução."}
            elif item.get("planned_integrations"):
                check = {"available": False, "code": "integration_unavailable", "reason": "SWS/SQL estão apenas planejados: integração ainda indisponível. Remova essas etapas para executar este roteiro."}
            elif item["mode"] == "manual":
                check = {"available": True, "code": "manual", "reason": "Execução e avaliação feitas pelo usuário, sem automação."}
            else:
                try:
                    check = self.reruns.preflight(item["source_exploration_id"], item["source_scenario_id"])
                    if check["available"]:
                        self.reruns._capture_plan(item.get("capture_plan"), check["timeline_count"], check["action_index"])
                except (ValueError, NotFoundError) as exc:
                    check = {"available": False, "reason": str(exc)}
            checks.append({"case_id": item["case_id"], **check})
        return {"revision": plan["revision"], "available": bool(checks) and all(check["available"] for check in checks), "items": checks,
                "effects": ["Os CTs serão executados um de cada vez, na ordem planejada.",
                            "Cada CT automático usa um navegador novo e exige sua preparação/continuação.",
                            "CTs manuais aguardam o resultado registrado por você. Ações podem modificar dados na aplicação.",
                            "SWS, SQL e instruções livres não são executados."]}

    def start(self, plan_id, expected_revision):
        plan = self.store.get(plan_id)
        # An already accepted start is idempotent, even after it finishes.
        if plan["status"] != "draft":
            return plan
        if plan["revision"] != expected_revision:
            raise PlanConflict("Revise a versão atual do planejamento antes de executar.")
        # Serialize the last archive check with the durable start transition.
        with self.repository._lock:
            if not self.validate(plan_id)["available"]:
                raise ValueError("Há CTs indisponíveis. Revise o planejamento antes de executar.")
            plan.update(status="running", started_at=utc_now())
            plan = self.store.replace(plan, expected_revision)
        self.signals[plan_id] = asyncio.Event()
        task = asyncio.create_task(self._run(plan_id))
        self.tasks[plan_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(plan_id, None))
        return plan

    def stop(self, plan_id):
        plan = self.store.get(plan_id)
        if plan["status"] not in ACTIVE:
            return plan
        plan["status"] = "stopping"
        plan = self.store.replace(plan, plan["revision"])
        for item in plan["items"]:
            if item["state"] == "running" and item.get("execution_id"):
                self.reruns.cancel(item["execution_id"])
        if plan_id in self.signals:
            self.signals[plan_id].set()
        return plan

    def manual_result(self, plan_id, case_id, verdict, actual_result, expected_revision):
        plan = self.store.get(plan_id)
        item = next((value for value in plan["items"] if value["case_id"] == case_id), None)
        if plan["status"] != "waiting_manual" or item is None or item["mode"] != "manual" or item["state"] != "waiting_manual":
            raise PlanConflict("Este CT não está aguardando avaliação manual.")
        now = utc_now()
        item.update(state="completed", finished_at=now, result=dict(id=case_id, **item["definition"], status=verdict,
                    actual_result=actual_result, created_at=now, screenshots=[], evidence={"manual_execution": True,
                    "manual_result": {"recorded_at": now, "recorded_by": "Usuário", "note": actual_result},
                    "timeline": [{"kind": "observation", "title": "Avaliação manual registrada pelo usuário", "occurred_at": now,
                                  "message": actual_result + " · Resultado informado pelo usuário, sem conferência automática."}]}))
        plan["status"] = "running"
        plan = self.store.replace(plan, expected_revision)
        if plan_id in self.signals:
            self.signals[plan_id].set()
        return plan

    async def _run(self, identity):
        try:
            for index in range(len(self.store.get(identity)["items"])):
                plan = self.store.get(identity)
                if plan["status"] == "stopping":
                    break
                item = plan["items"][index]
                if item["mode"] == "manual":
                    self.signals[identity].clear()
                    item["state"], plan["status"] = "waiting_manual", "waiting_manual"
                    self.store.replace(plan, plan["revision"])
                    await self.signals[identity].wait()
                    continue
                # No await between dispatch and durable binding: shutdown or a
                # duplicate request cannot slip in and dispatch this item twice.
                child = self.reruns.submit(item["source_exploration_id"], item["source_scenario_id"], capture_plan=item.get("capture_plan"))
                item.update(state="running", execution_id=child)
                self.repository.add_event(child, "info", "execution_round_item", "CT vinculado à rodada", {"round_id": identity, "root_id": plan["root_id"], "case_id": item["case_id"]})
                self.store.replace(plan, plan["revision"])
                while True:
                    run = self.repository.get_exploration(child)
                    if run["status"] in TERMINAL:
                        break
                    await asyncio.sleep(.4)
                plan = self.store.get(identity)
                item = plan["items"][index]
                outcomes = run.get("scenarios") or []
                item.update(state="completed" if len(outcomes) == 1 else "failed", finished_at=utc_now(),
                            result={**without_rerun_recipes(deepcopy(outcomes[0])), "id": item["case_id"], "evidence_exploration_id": child} if len(outcomes) == 1 else None)
                if not outcomes:
                    item["reason"] = "Tentativa encerrada sem resultado registrado."
                if run["status"] in {"cancelled", "failed"} or not outcomes:
                    plan.update(status="stopping", reason="Uma tentativa foi interrompida. Os próximos CTs não foram iniciados; planeje uma nova rodada para continuar.")
                self.store.replace(plan, plan["revision"])
            plan = self.store.get(identity)
            stopped = plan["status"] == "stopping"
            for item in plan["items"]:
                if item["state"] in {"queued", "waiting_manual"}:
                    item["state"] = "cancelled" if stopped else "interrupted"
            plan.update(status="cancelled" if stopped else "completed" if all(item.get("result") for item in plan["items"]) else "partial", finished_at=utc_now())
            self.store.replace(plan, plan["revision"])
        except asyncio.CancelledError:
            raise
        except Exception:
            plan = self.store.get(identity)
            plan.update(status="interrupted", finished_at=utc_now(), reason="A rodada foi interrompida. Resultados existentes foram preservados; nenhuma ação será repetida automaticamente.")
            self.store.replace(plan, plan["revision"])
        finally:
            self.signals.pop(identity, None)

    def report_snapshot(self, identity, context="consolidated", round_id=None, case_id=None):
        root = self.root(identity)
        initial = self.repository.get_exploration(root)
        if case_id is not None:
            # Filter before revision projection and rendering, not after an
            # export containing every CT and all of its images was generated.
            initial["scenarios"] = [case for case in initial.get("scenarios") or [] if case["id"] == case_id]
        snapshot = deepcopy(initial) if context in {"initial", "round"} else project_scenario_revisions(self.repository, initial)
        label, round_name = "Consolidado do conjunto", None
        if context == "initial":
            label = "Execução inicial · resultado histórico preservado"
        elif context == "round":
            plan = self.store.get(round_id)
            if plan["root_id"] != root:
                raise ValueError("A rodada não pertence a este conjunto.")
            label, round_name = "Rodada selecionada · somente os CTs desta rodada", plan["name"]
            snapshot["scenarios"] = []
            for item in plan["items"]:
                if case_id is not None and item["case_id"] != case_id:
                    continue
                case = deepcopy(item.get("result")) or dict(id=item["case_id"], **item["definition"], status="SKIPPED", screenshots=[],
                     created_at=plan["created_at"], actual_result="Não executado nesta rodada ou sem resultado registrado.", evidence={})
                case.pop("execution_history", None)
                snapshot["scenarios"].append(case)
            snapshot["actions"] = []
            snapshot.update(created_at=plan["created_at"], started_at=plan.get("started_at"), finished_at=plan.get("finished_at"),
                            status=plan["status"] if plan["status"] in TERMINAL else "waiting_input" if plan["status"] == "waiting_manual" else "running" if plan["status"] == "running" else "partial",
                            events=[], issues=[], screenshots=[])
            snapshot["usage"] = {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                 "cost": 0, "cost_known": True, "cost_complete": True}
            for item in plan["items"]:
                if item.get("execution_id"):
                    usage = self.repository.get_exploration(item["execution_id"], include_details=False).get("usage") or {}
                    for key in ("total_tokens", "prompt_tokens", "completion_tokens", "cost"):
                        snapshot["usage"][key] += usage.get(key) or 0
                    snapshot["usage"]["cost_known"] &= bool(usage.get("cost_known"))
                    snapshot["usage"]["cost_complete"] &= bool(usage.get("cost_known")) and usage.get("cost_complete") is not False
        else:
            for case in self.store.cases(root):
                if case_id is not None and case["id"] != case_id:
                    continue
                if case["mode"] == "automatic":
                    imported = project_scenario_revisions(self.repository, self.repository.get_exploration(case["source_exploration_id"]))
                    latest = deepcopy(next(value for value in imported["scenarios"] if value["id"] == case["source_scenario_id"]))
                    latest["id"] = case["id"]
                    snapshot["scenarios"].append(latest)
                    continue
                attempts = [item for plan in self.store.list(root) for item in plan["items"] if item["case_id"] == case["id"] and item.get("result") and item["definition_revision"] == case["revision"]]
                latest = deepcopy(attempts[-1]["result"]) if attempts else dict(**definition(case), id=case["id"], status="SKIPPED", actual_result="CT manual ainda não executado.", screenshots=[], created_at=case["updated_at"], evidence={})
                snapshot["scenarios"].append(latest)
        if case_id is not None:
            if len(snapshot["scenarios"]) != 1 or snapshot["scenarios"][0]["id"] != case_id:
                raise NotFoundError("CT não encontrado neste conjunto ou nesta rodada.")
            selected = snapshot["scenarios"][0]
            steps = {entry.get("step_number", entry.get("action_step")) for entry in (selected.get("evidence") or {}).get("timeline") or [] if entry.get("kind") == "action"}
            snapshot["actions"] = [action for action in snapshot.get("actions") or [] if action.get("step_number") in steps]
            snapshot.update(events=[], issues=[], screenshots=[], scenario_contexts=[], completion={})
            # Do not attribute the whole suite's costs or duration to one CT.
            snapshot["usage"] = {}
            snapshot["started_at"] = snapshot["finished_at"] = None
        snapshot["summary"] = {"total_scenarios": len(snapshot["scenarios"]), **{state.lower(): sum(case.get("status") == state for case in snapshot["scenarios"]) for state in VERDICTS}}
        snapshot["suite_context"] = dict(root_id=root, scope=context, label=label, round_name=round_name, generated_at=utc_now(),
             note="O consolidado reúne resultados de datas diferentes; uma reexecução parcial não valida novamente os demais CTs." if context == "consolidated" else "Este relatório não inclui resultados emprestados de outras rodadas.")
        if case_id is not None:
            snapshot["suite_context"]["focused_case_id"] = case_id
        # Scope-specific exports must not be replaced by the legacy freshness
        # endpoint's differently scoped report when the app regains focus.
        snapshot.pop("scenario_revisions_key", None)
        return without_rerun_recipes(snapshot)

    async def shutdown(self):
        for identity, task in list(self.tasks.items()):
            task.cancel()
            plan = self.store.get(identity)
            if plan["status"] in ACTIVE:
                plan.update(status="interrupted", finished_at=utc_now(), reason="Servidor encerrado; nenhuma ação será retomada automaticamente.")
                self.store.replace(plan, plan["revision"])
        if self.tasks:
            await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
