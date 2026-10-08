import base64
from copy import deepcopy

import pytest

from autojev.adapters.report import StandaloneHtmlReport
from autojev.services.scenario_timeline import TIMELINE_VERSION


def test_report_separates_recorded_jev_auxiliary_and_total_cost_without_changing_usage(tmp_path):
    run = {
        "id": "cost-split", "status": "completed", "app_url": "https://shop.test",
        "usage": {
            "total_tokens": 397386, "cost": 0.038586, "cost_known": True, "cost_complete": True,
            "by_role": {
                "jev": {"cost": 0.001, "cost_known": True, "cost_complete": True},
                "auxiliary": {"cost": 0.037586, "cost_known": True, "cost_complete": True},
            },
        },
    }
    before = deepcopy(run)
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()
    assert 'data-cost-breakdown="roles-v1"' in document[:8192]
    assert 'data-cost-role="jev"><strong>$0.001000</strong>Custo JEV' in document
    assert 'data-cost-role="auxiliary"><strong>$0.037586</strong>Custo LLM' in document
    assert 'data-cost-role="total"><strong>$0.038586</strong>Custo total' in document
    assert ">397.386</strong>Tokens" in document
    assert "Divisão por modelo não registrada" not in document
    assert '<span class="cost-state">Parcial</span>' not in document
    assert run == before


def test_report_preserves_historical_total_without_inventing_a_model_split(tmp_path):
    run = {"id": "old-cost", "usage": {"cost": 0.0042, "cost_known": True}}
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()
    assert 'data-cost-role="jev"><strong>—</strong>Custo JEV' in document
    assert 'data-cost-role="auxiliary"><strong>—</strong>Custo LLM' in document
    assert 'data-cost-role="total"><strong>$0.004200</strong>Custo total' in document
    assert "Divisão por modelo não registrada nesta execução" in document
    assert "$0.000000" not in document


def test_report_shows_reported_zero_cost_but_not_unknown_cost_as_zero(tmp_path):
    run = {"id": "free-cost", "usage": {
        "cost": 0, "cost_known": True,
        "by_role": {
            "jev": {"cost": 0, "cost_known": True},
            "auxiliary": {"cost": 0, "cost_known": False},
        },
    }}
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()
    assert 'data-cost-role="jev"><strong>$0.000000</strong>Custo JEV' in document
    assert 'data-cost-role="auxiliary"><strong>—</strong>Custo LLM' in document
    assert 'data-cost-role="total"><strong>$0.000000</strong>Custo total' in document
    assert "Custo não informado pelo provedor" in document


def test_report_labels_partial_known_sums_and_keeps_fully_unknown_total_unreported(tmp_path):
    run = {"id": "partial-cost", "usage": {
        "cost": 0.0042, "cost_known": True, "cost_complete": False,
        "by_role": {
            "jev": {"cost": 0.0042, "cost_known": True, "cost_complete": False},
            "auxiliary": {"cost": 0, "cost_known": False, "cost_complete": False},
        },
    }}
    path = tmp_path / "report.html"
    report = StandaloneHtmlReport(tmp_path)
    report.generate(run, str(path))
    document = path.read_text()
    assert document.count('<span class="cost-state">Parcial</span>') == 2
    assert document.count("Valor parcial · soma dos custos informados.") == 2
    assert 'data-cost-role="total"><strong>$0.004200</strong>Custo total' in document
    run["usage"]["cost_known"] = False
    report.generate(run, str(path))
    document = path.read_text()
    assert 'data-cost-role="total"><strong>—</strong>Custo total' in document
    assert document.count('<span class="cost-state">Parcial</span>') == 1


@pytest.mark.parametrize("invalid", [None, True, float("nan"), float("inf"), -1, '<script>alert("price")</script>'])
def test_report_never_renders_invalid_or_unsafe_prices_as_reported_cost(tmp_path, invalid):
    run = {"id": "invalid-cost", "usage": {
        "cost": invalid, "cost_known": True,
        "by_role": {"jev": {"cost": invalid, "cost_known": True}},
    }}
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()
    assert 'data-cost-role="jev"><strong>—</strong>Custo JEV' in document
    assert 'data-cost-role="total"><strong>—</strong>Custo total' in document
    assert "alert(" not in document
    assert "$0.000000" not in document


def test_report_names_masked_field_and_escapes_blocking_field(tmp_path):
    run = {"id": "fixture", "status": "completed", "app_url": "https://shop.test",
           "completion": {"restrictions": [{"name": "Continue", "field_name": "Zip/Postal Code",
               "reason": "required_field_validation_masked", "action": "click", "source_url": "https://shop.test/form",
               "validation_probe": {"blocking_fields": [{"name": "<script>Last Name</script>"}]}}]}}
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()
    assert "Zip/Postal Code · Continue" in document
    assert "A tentativa foi executada" in document
    assert "Campos que impediram a preparação" in document
    assert "&lt;script&gt;Last Name&lt;/script&gt;" in document
    assert "<script>Last Name</script>" not in document


def test_report_separates_active_restrictions_from_evidenced_recovery_and_keeps_ct_fail(tmp_path):
    shots = tmp_path / "fixture" / "screenshots"
    shots.mkdir(parents=True)
    (shots / "047.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    block = {"id": "route-1", "name": "<b>Continue Shopping</b>", "reason": "no_safe_restore_route"}
    resolved = {**block, "status": "resolved", "resolution": {
        "source": "actual_semantic_move_execution", "action_step": 47,
        "occurred_at": "2026-10-04T12:00:00+00:00", "source_url": "https://shop.test/cart?token=secret-recovery",
        "action": "click", "target": "<script>e4</script>", "screenshot": "screenshots/047.png",
        "success": True, "assertions_passed": False,
    }}
    masked = {"id": "postal-1", "name": "Continue", "field_name": "Zip/Postal Code",
              "reason": "required_field_validation_masked"}
    run = {"id": "fixture", "status": "completed", "app_url": "https://shop.test",
           "events": [
               {"type": "coverage_restriction_recorded", "payload": block},
               {"type": "coverage_restriction_resolved", "payload": resolved},
               {"type": "coverage_complete_with_restrictions", "payload": {"restrictions": [block, masked]}},
           ], "scenarios": [{"id": "CT047", "title": "Cart", "status": "FAIL", "evidence": {
               "timeline": [{"kind": "action", "step_number": 47, "title": "Click", "screenshot": "047.png"}],
               "assertions": [{"kind": "cart_count", "passed": False}],
           }}]}
    before = deepcopy(run)
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()
    assert "Cobertura e restrições · 1 registros" in document
    assert "Restrições resolvidas com evidência · 1 registros" in document
    assert "Uma verificação reprovou; consulte o resultado do CT" in document
    assert 'data-status="FAIL"' in document
    assert '<a href="#timeline-4354303437-0">Ver ação da etapa 47' in document
    assert 'id="timeline-4354303437-0"' in document
    assert 'download="047.png"' in document
    assert "data:image/png;base64," in document
    assert "secret-recovery" not in document
    assert "&lt;b&gt;Continue Shopping&lt;/b&gt;" in document
    assert "&lt;script&gt;e4&lt;/script&gt;" in document
    assert run == before


def test_report_cannot_infer_recovery_and_never_links_missing_timeline_or_screenshot(tmp_path):
    block = {"id": "r1", "name": "Continue Shopping", "reason": "no_safe_restore_route"}
    run = {"id": "fixture", "status": "cancelled", "app_url": "https://shop.test", "events": [
        {"type": "coverage_restriction_recorded", "payload": block},
        {"type": "action_completed", "payload": {"name": "Continue Shopping", "success": True}},
    ]}
    path = tmp_path / "report.html"
    report = StandaloneHtmlReport(tmp_path)
    report.generate(run, str(path))
    document = path.read_text()
    assert "Cobertura e restrições · 1 registros" in document
    assert "Restrições resolvidas com evidência" not in document
    run["events"].append({"type": "coverage_restriction_resolved", "payload": {
        **block, "status": "resolved", "resolution": {
            "source": "actual_semantic_move_execution", "action_step": 47,
            "occurred_at": "2026-10-04T12:00:00+00:00", "source_url": "https://shop.test/cart",
            "action": "click", "target": "e4", "screenshot": "../../missing.png",
            "success": True, "assertions_passed": True,
        },
    }})
    report.generate(run, str(path))
    document = path.read_text()
    assert "Cobertura e restrições · 0 registros" in document
    assert "Restrições resolvidas com evidência · 1 registros" in document
    assert "Etapa 47 · ação registrada" in document
    assert "Captura da recuperação indisponível" in document
    assert 'href="#timeline-' not in document
    assert 'download="missing.png"' not in document


def test_report_names_form_probe_target_that_could_not_be_emptied(tmp_path):
    run = {"id": "fixture", "status": "completed", "app_url": "https://shop.test", "completion": {
        "restrictions": [{"name": "Continue", "reason": "form_probe_target_not_empty"}],
    }}
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    assert "O campo continuou preenchido após as tentativas de limpeza" in path.read_text()


def test_report_keeps_add_restriction_open_and_links_real_failed_remove_dependency(tmp_path):
    shots = tmp_path / "fixture" / "screenshots"
    shots.mkdir(parents=True)
    (shots / "022-remove.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    block = {"id": "r-add", "name": "Add to cart", "reason": "no_safe_restore_route", "status": "active",
             "dependency": {"kind": "failed_inverse_cart_toggle", "source": "actual_business_assertion",
                 "item_id": "4", "surface": "inventory", "source_url": "https://shop.test/inventory?token=private-token",
                 "required_action": "add", "name": "<script>Remove</script>", "action": "click", "action_step": 22,
                 "screenshot": "screenshots/022-remove.png", "failed_assertion_kinds": ["cart_item_removed", "<b>cart_count</b>"]}}
    run = {"id": "fixture", "status": "completed", "app_url": "https://shop.test", "completion": {"restrictions": [block]},
           "scenarios": [{"id": "CT022", "title": "Remove item", "status": "FAIL", "evidence": {
               "assertions": [{"kind": "cart_item_removed", "passed": False}],
               "timeline": [{"kind": "action", "step_number": 22, "title": "Remove", "screenshot": "022-remove.png"}],
           }}]}
    before = deepcopy(run)
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()
    assert "Cobertura e restrições · 1 registros" in document
    assert "Restrições resolvidas com evidência" not in document
    assert "Dependência que impediu esta validação" in document
    assert "a restrição permanece aberta e não conta como aprovação" in document
    assert '<a href="#timeline-4354303232-0">Ver falha anterior da etapa 22' in document
    assert 'id="timeline-4354303232-0"' in document
    assert 'data-status="FAIL"' in document
    assert 'download="022-remove.png"' in document
    assert "&lt;script&gt;Remove&lt;/script&gt;" in document
    assert "&lt;b&gt;cart_count&lt;/b&gt;" in document
    assert "private-token" not in document
    assert run == before


def test_report_dependency_does_not_fabricate_links_or_images_and_requires_local_provenance(tmp_path):
    dependency = {"kind": "failed_inverse_cart_toggle", "source": "actual_business_assertion",
                  "action_step": 22, "screenshot": "../../missing.png", "failed_assertion_kinds": ["cart_count"]}
    run = {"id": "fixture", "status": "completed", "app_url": "https://shop.test", "completion": {
        "restrictions": [{"name": "Add to cart", "reason": "no_safe_restore_route", "dependency": dependency}],
    }}
    path = tmp_path / "report.html"
    report = StandaloneHtmlReport(tmp_path)
    report.generate(run, str(path))
    document = path.read_text()
    assert "Dependência que impediu esta validação" in document
    assert "Etapa 22 · ação anterior registrada" in document
    assert 'href="#timeline-' not in document
    assert 'download="missing.png"' not in document
    dependency["source"] = "model_guess"
    report.generate(run, str(path))
    assert "Dependência que impediu esta validação" not in path.read_text()


def test_report_distinguishes_strength_and_lists_individual_restrictions(tmp_path):
    run = {"id": "fixture", "status": "completed", "app_url": "https://shop.test",
           "scenarios": [
               {"id": "CT001", "title": "Click", "status": "PASS", "evidence": {"assertions": [{"kind": "observable_state_change", "passed": True}]}},
               {"id": "CT002", "title": "Cart", "status": "PASS", "evidence": {"assertions": [{"kind": "cart_count", "passed": True}]}},
           ], "events": [{"type": "coverage_complete_with_restrictions", "payload": {
               "restrictions": [{"name": "<script>bad</script>", "reason": "no_safe_restore_route", "action": "click", "source_url": "https://shop.test/cart?token=private"}], "candidates_blocked": 1,
           }}]}
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()
    assert "1 aprovado(s) com asserção de negócio" in document
    assert "1 aprovado(s) apenas pela interação técnica" in document
    assert "Cobertura e restrições · 1 registros" in document
    assert "&lt;script&gt;bad&lt;/script&gt;" in document
    assert "token=private" not in document


def test_visual_report_prominently_explains_unavailable_reviews_without_changing_pass(tmp_path):
    scenarios = [{
        "id": f"CT{i:03d}", "title": "Local assertion passed", "status": "PASS",
        "evidence": {"visual_assessment": {"requested": True, "status": "unavailable", "reason": reason}},
    } for i, reason in enumerate(["auxiliary_rate_limited", "visual_assessment_not_returned"], 1)]
    run = {"id": "fixture", "status": "completed", "app_url": "http://fixture.test/",
           "options": {"auxiliary_evidence_mode": "visual"}, "summary": {"total_scenarios": 2, "pass": 2}, "scenarios": scenarios}
    before = deepcopy(run)
    output = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(output))
    html = output.read_text()
    assert "Análise visual não realizada" in html
    assert "0 de 2 CTs receberam uma análise visual válida" in html
    assert "1 limitados pelo provedor" in html
    assert "1 com resposta visual ausente ou inválida" in html
    assert "Análise visual indisponível não significa aprovação visual" in html
    assert html.index('class="completion visual-review-summary"') < html.index('id="scenarios"')
    assert html.count('data-status="PASS"') == 2
    assert run == before


def test_visual_report_distinguishes_local_request_budget_from_unexecuted_tests(tmp_path):
    run = {
        "id": "fixture", "status": "completed", "app_url": "https://shop.test",
        "options": {"auxiliary_evidence_mode": "visual", "auxiliary_max_requests": 120},
        "scenarios": [{"id": "CT001", "title": "Cart updated", "status": "PASS", "evidence": {
            "assertions": [{"kind": "cart_count", "passed": True}],
            "visual_assessment": {"requested": True, "status": "unavailable", "reason": "auxiliary_request_budget_exhausted"},
        }}],
    }
    before = deepcopy(run)
    output = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(output))
    html = output.read_text()
    assert 'data-visual-review-policy="bounded-v1"' in html
    assert "Limite local registrado: 120 requisições HTTP auxiliares" in html
    assert "não representa uma quantidade de CTs nem falta de crédito" in html
    assert "após confirmação de custo, sem repetir a exploração" in html
    assert "até 3 tentativas por revisão, contando a primeira" in html
    assert 'data-status="PASS"' in html
    assert run == before


def test_visual_report_marks_assessed_legacy_reviews_as_partial_context(tmp_path):
    run = {
        "id": "fixture", "status": "completed", "app_url": "https://shop.test",
        "options": {"auxiliary_evidence_mode": "visual"},
        "scenarios": [{"id": "CT001", "title": "Saved images", "status": "PASS", "evidence": {
            "visual_assessment": {"requested": True, "status": "assessed", "context_limited": True,
                                  "limitations": ["current_dom_and_visible_text_not_preserved"]},
        }}],
    }
    output = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(output))
    html = output.read_text()
    assert "Capturas analisadas · contexto parcial" in html
    assert "1 revisões com contexto textual limitado" in html
    assert "DOM e o texto visível originais não foram preservados" in html
    assert "não equivalem a uma análise com o contexto semântico completo" in html
    assert "Contexto textual limitado: esta revisão usa as capturas salvas" in html
    assert "current_dom_and_visible_text_not_preserved" not in html


def test_report_total_duration_and_unavailable_reasons_include_postprocessing_tail(tmp_path):
    run = {
        "id": "fixture", "status": "completed", "app_url": "https://shop.test",
        "started_at": "2026-10-04T03:22:54+00:00", "finished_at": "2026-10-04T03:28:48+00:00",
        "events": [{"type": "report_generation_started", "created_at": "2026-10-04T03:38:49+00:00"}],
        "options": {"auxiliary_evidence_mode": "visual"},
        "scenarios": [
            {"id": f"CT{i:03d}", "title": "Local", "status": "PASS", "evidence": {
                "visual_assessment": {"requested": True, "status": "unavailable", "reason": reason},
            }} for i, reason in enumerate(["documentation_time_limit", "TimeoutError", "unknown_reason"], 1)
        ],
    }
    before = deepcopy(run)
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()
    assert "Duração total" in document and "00:15:55" in document
    assert "00:05:54" not in document
    assert "inclui revisões auxiliares e geração do relatório" in document
    assert "1 sem revisão antes do limite de espera das revisões auxiliares" in document
    assert "1 com tempo de resposta da LLM auxiliar excedido" in document
    assert "Código: unknown_reason" in document
    assert "0 limitados pelo provedor" not in document
    assert 'data-report-summary="execution-v2"' in document
    assert document.count('data-status="PASS"') == 3
    assert run == before


def test_report_is_standalone_and_escapes_model_content(tmp_path):
    root = tmp_path / "artifacts"
    shots = root / "run-1" / "screenshots"
    shots.mkdir(parents=True)
    png = b"\x89PNG\r\n\x1a\nnot-a-full-png-but-enough-for-encoding"
    (shots / "001.png").write_bytes(png)
    exploration = {
        "id": "run-1",
        "app_url": "https://example.test/?x=<script>alert(1)</script>",
        "status": "partial",
        "events": [
            {
                "type": "coverage_partial",
                "message": "Limite de tempo; a fronteira ainda tinha itens.",
            }
        ],
        "options": {
            "focus_area": "Administração > Usuários",
            "auxiliary_evidence_mode": "visual",
        },
        "summary": {
            "total_scenarios": 1,
            "pass": 1,
            "fail": 0,
            "blocked": 0,
            "skipped": 0,
        },
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "cost": 0.0042,
            "cost_known": True,
        },
        "scenarios": [
            {
                "id": "CT001",
                "title": "<img src=x onerror=alert(1)>",
                "description": "safe-ish",
                "preconditions": [],
                "steps": ["<script>steal()</script>"],
                "expected_result": "ok",
                "actual_result": "ok",
                "status": "PASS",
                "url": "https://example.test",
                "screenshots": ["001.png"],
                "evidence": {"assertions": []},
            }
        ],
        "issues": [],
    }
    output = root / "run-1" / "report.html"
    StandaloneHtmlReport(root).generate(exploration, str(output))
    document = output.read_text()

    assert "<img src=x onerror=alert(1)>" not in document
    assert "&lt;img src=x onerror=alert(1)&gt;" in document
    assert "<script>steal()</script>" not in document
    assert base64.b64encode(png).decode() in document
    assert "<style>" in document and "<script>" in document
    assert 'id="search"' in document
    assert '<details class="scenario"' in document
    assert 'data-report-schema="ct-timeline-v1"' in document
    assert "Linha do tempo · ações e evidências" in document
    assert "Sinais técnicos observados" not in document
    assert ">150</strong>Tokens" in document
    assert ">$0.004200</strong>Custo total" in document
    assert "Administração &gt; Usuários" in document
    assert "Visual · texto + imagem" in document
    assert "Cobertura parcial" in document
    assert "Motivo do encerramento:" in document
    assert "Limite de tempo; a fronteira ainda tinha itens." in document


def test_report_explains_restricted_completion_and_correlates_browser_signals(
    tmp_path,
):
    root = tmp_path / "artifacts"
    shots = root / "run-2" / "screenshots"
    shots.mkdir(parents=True)
    (shots / "041.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    exploration = {
        "id": "run-2",
        "app_url": "https://example.test",
        "status": "completed",
        "events": [
            {
                "type": "coverage_complete_with_restrictions",
                "message": "A cobertura alcançável terminou com controles bloqueados.",
            }
        ],
        "summary": {
            "total_scenarios": 1,
            "pass": 0,
            "fail": 0,
            "blocked": 0,
            "skipped": 1,
        },
        "scenarios": [
            {
                "id": "CT001",
                "title": "Ação sem alteração observável",
                "description": "A ação foi executada.",
                "preconditions": [],
                "steps": ["Clique"],
                "expected_result": "Estado verificável",
                "actual_result": "Sem alteração",
                "status": "SKIPPED",
                "url": "https://example.test/items",
                "screenshots": [],
                "evidence": {"assertions": []},
            }
        ],
        "issues": [
            {
                "type": "console",
                "status_code": 404,
                "message": "Failed to load resource: the server responded with a status of 404",
                "url": "https://example.test/items?token=secret-one&item=1",
                "occurred_at": "2026-01-01T12:00:00.100000+00:00",
                "screenshot": "041.png",
            },
            {
                "type": "http",
                "status_code": 404,
                "message": "GET returned HTTP 404",
                "url": "https://example.test/items?token=secret-two&item=1",
                "occurred_at": "2026-01-01T12:00:00.120000+00:00",
                "screenshot": "041.png",
            },
        ],
    }

    output = root / "run-2" / "report.html"
    StandaloneHtmlReport(root).generate(exploration, str(output))
    document = output.read_text()

    assert "Concluída com restrições" in document
    assert "SEM VALIDAÇÃO" in document
    assert "Sinais técnicos observados" not in document
    assert "2 registros correlacionados · mesma ocorrência" in document
    assert "Evidência de contexto" not in document
    assert 'data-status="INFO"' not in document
    assert 'id="execution-observations"' in document
    assert "Observações da execução" in document
    assert "Não alteram os resultados nem a contagem de testes." in document
    assert "Falha HTTP da aplicação" in document
    assert "secret-one" not in document
    assert "secret-two" not in document
    assert "item=1" in document
    assert "%5BREDACTED%5D" in document


def test_report_keeps_observations_outside_ct_cards_counts_and_filters(tmp_path):
    run = {
        "id": "observed", "timeline_version": TIMELINE_VERSION, "app_url": "https://shop.test",
        "summary": {"total_scenarios": 1, "pass": 1, "fail": 0},
        "scenarios": [{"id": "CT001", "title": "Business check", "status": "PASS"}],
        "scenario_contexts": [{
            "id": "CTX-private", "title": "Legacy context card", "status": "INFO",
            "url": "https://shop.test/fallback-that-must-not-be-inferred",
            "evidence": {"timeline": [{
                "id": "unowned-401", "kind": "signal",
                "occurred_at": "2026-10-06T21:59:22+00:00",
                "url": "https://telemetry.test/event?token=secret-endpoint",
                "signal": {"type": "http", "method": "POST", "status_code": 401,
                           "message": "POST returned HTTP 401", "url": "https://telemetry.test/event?token=secret-endpoint"},
                "observation": {
                    "summary": "Na página https://shop.test/inventory?token=secret-page, ocorreu HTTP 401.",
                    "page_url": "https://shop.test/inventory?token=secret-page",
                    "phase": "Carregamento da página",
                    "request_url": "https://telemetry.test/event?token=secret-endpoint",
                    "method": "POST", "status_code": 401,
                },
            }]},
        }],
    }
    before = deepcopy(run)
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()
    ct_section, observations = document.split('<section id="execution-observations"', 1)
    assert ct_section.count('<details class="scenario"') == 1
    assert ct_section.count('data-status="PASS"') == 1
    assert '<strong>1</strong>Total' in ct_section
    assert '<strong>0</strong>Reprovados' in ct_section
    assert '<details class="execution-observations">' in observations
    assert '<details class="execution-observations" open' not in document
    assert 'data-status=' not in observations
    assert '<span class="badge' not in observations
    assert "CTX-private" not in document and "Legacy context card" not in document
    assert "Evidência de contexto" not in document and "CONTEXTO" not in document
    assert 'const statusLabels = {' in document
    assert 'BLOCKED: "Bloqueados", SKIPPED: "Sem validação"' in document
    assert 'all("#case-rows tr[data-case-index]")' in document
    assert "Na página https://shop.test/inventory?token=%5BREDACTED%5D, ocorreu HTTP 401." in observations
    assert "Página: https://shop.test/inventory?token=%5BREDACTED%5D" in observations
    assert "Fase registrada: Carregamento da página" in observations
    assert "Requisição: POST · HTTP 401" in observations
    assert "Destino da requisição: https://telemetry.test/event?token=%5BREDACTED%5D" in observations
    assert "06/10/2026 21:59:22" in observations
    assert "secret-endpoint" not in document and "secret-page" not in document
    assert "fallback-that-must-not-be-inferred" not in document
    assert run == before


def test_report_observation_does_not_invent_page_phase_request_or_failure(tmp_path):
    payload = '<svg/onload=alert(1)>'
    run = {
        "id": "observed", "timeline_version": TIMELINE_VERSION, "app_url": "https://shop.test",
        "scenarios": [], "scenario_contexts": [{
            "id": "CTX-unlinked", "url": "https://shop.test/unsupported-fallback",
            "evidence": {"timeline": [{
                "kind": "signal", "occurred_at": None,
                "url": "https://shop.test/console-origin",
                "signal": {"type": "console", "status_code": 401, "message": payload},
                "observation": {"summary": payload, "page_url": None, "phase": None,
                                "request_url": None},
            }]},
        }],
    }
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()
    observations = document.split('<section id="execution-observations"', 1)[1]
    assert "Página:" not in observations and "Fase registrada:" not in observations
    assert "Requisição:" not in observations and "Destino da requisição:" not in observations
    assert "unsupported-fallback" not in document
    assert payload not in document and "&lt;svg/onload=alert(1)&gt;" in observations
    assert '<details class="scenario"' not in document
    assert 'data-status="FAIL"' not in document


def test_report_omits_empty_observations_section(tmp_path):
    run = {"id": "empty", "scenarios": [], "issues": []}
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    assert 'id="execution-observations"' not in path.read_text()


def test_report_partitions_legacy_observations_and_corrects_only_visible_ct_counts(tmp_path):
    scenarios = [{
        "id": f"CT{index:03d}", "title": f"Real {status} case", "status": status,
        "evidence": {"visual_assessment": {"requested": True, "status": "assessed"}},
    } for index, status in enumerate(("PASS", "FAIL", "BLOCKED", "SKIPPED"), 1)]
    scenarios[0]["evidence"]["assertions"] = [{"kind": "cart_count", "passed": True}]
    scenarios[1]["evidence"]["verdict"] = {
        "kind": "technical_failure", "label": "Falha histórica preservada",
        "explanation": "Mensagem histórica preservada", "historical": True,
    }
    legacy_observations = [{
        "id": f"legacy-{status.lower()}", "title": "Old pseudo test", "status": status,
        "evidence": {
            "visual_assessment": {"requested": True, "status": "pending"},
            "timeline": [{"kind": "signal", "message": f"Legacy {status} HTTP 401"}],
        },
    } for status in ("INFO", "CONTEXT")]
    canonical_observation = deepcopy(legacy_observations[0])
    canonical_observation["evidence"]["timeline"][0]["message"] = "Canonical telemetry HTTP 401"
    run = {
        "id": "legacy", "timeline_version": TIMELINE_VERSION,
        "options": {"auxiliary_evidence_mode": "visual"},
        "summary": {"total_scenarios": 6, "pass": 1, "fail": 1, "blocked": 1, "skipped": 3},
        "scenarios": [*scenarios, *legacy_observations],
        "scenario_contexts": [canonical_observation],
    }
    before = deepcopy(run)
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()
    ct_section, observations = document.split('<section id="execution-observations"', 1)

    assert ct_section.count('<details class="scenario"') == 4
    for status in ("PASS", "FAIL", "BLOCKED", "SKIPPED"):
        assert ct_section.count(f'data-status="{status}"') == 1
    for label in ("Aprovados", "Reprovados", "Bloqueados", "Sem validação"):
        assert f'<strong>1</strong>{label}' in ct_section
    assert '<strong>4</strong>Total' in ct_section
    assert "4 de 4 CTs receberam uma análise visual válida" in ct_section
    assert "0 em análise" in ct_section
    assert "1 aprovado(s) com asserção de negócio" in ct_section
    assert "1 falha(s) técnica(s)" in ct_section
    assert "Mensagem histórica preservada" in ct_section
    assert "Old pseudo test" not in document
    assert "Canonical telemetry HTTP 401" in observations
    assert "Legacy INFO HTTP 401" not in document
    assert observations.count("Legacy CONTEXT HTTP 401") == 1
    assert " · 2 registro(s)" in observations
    assert 'data-status=' not in observations
    assert run == before


def test_report_preserves_stored_summary_without_legacy_observations(tmp_path):
    run = {
        "id": "historical", "summary": {"total_scenarios": 18, "pass": 17, "fail": 1},
        "scenarios": [{"id": "CT001", "title": "Preserved failure", "status": "FAIL"}],
    }
    before = deepcopy(run)
    path = tmp_path / "report.html"
    StandaloneHtmlReport(tmp_path).generate(run, str(path))
    document = path.read_text()

    assert '<strong>18</strong>Total' in document
    assert '<strong>17</strong>Aprovados' in document
    assert '<strong>1</strong>Reprovados' in document
    assert run == before


def test_report_observation_deduplicates_recorded_message_and_endpoint_only(tmp_path):
    message = "POST returned HTTP 401"
    endpoint = "https://telemetry.test/event"
    scenario = {"id": "context", "evidence": {"timeline": [{
        "kind": "signal", "message": message, "url": endpoint,
        "observation": {
            "summary": "Na página https://shop.test, foi observado HTTP 401.",
            "page_url": "https://shop.test", "request_url": endpoint,
            "method": "POST", "status_code": 401,
        },
        "signal": {
            "type": "http", "message": message, "url": endpoint, "status_code": 401,
            "label": "Telemetria externa", "impact_message": "Sem impacto funcional confirmado",
            "evidence_channels": ["network", "console"],
            "related_console_errors": [{"message": "Correlated console detail"}],
        },
    }]}}
    before = deepcopy(scenario)
    report = StandaloneHtmlReport(tmp_path)
    observation = report._timeline("fixture", scenario, observation=True)
    ct_timeline = report._timeline("fixture", scenario)

    assert observation.count(message) == 1
    assert observation.count(endpoint) == 1
    assert observation.count("https://shop.test") == 2  # Factual summary and page metadata.
    assert "Telemetria externa" in observation
    assert "Sem impacto funcional confirmado" in observation
    assert "rede + console" in observation
    assert "Correlated console detail" in observation
    assert ct_timeline.count(message) == 2
    assert ct_timeline.count(endpoint) == 2
    assert scenario == before


def test_report_places_legacy_signals_inside_ct_with_before_after_captures(tmp_path):
    root = tmp_path / "artifacts"
    shots = root / "legacy" / "screenshots"
    shots.mkdir(parents=True)
    before_png = b"\x89PNG\r\n\x1a\nbefore"
    after_png = b"\x89PNG\r\n\x1a\nafter"
    (shots / "before.png").write_bytes(before_png)
    (shots / "after.png").write_bytes(after_png)
    assertion = {
        "kind": "no_new_browser_errors",
        "passed": False,
        "message": "A request failed while the action succeeded",
    }
    exploration = {
        "id": "legacy",
        "app_url": "https://example.test",
        "status": "completed",
        "summary": {"total_scenarios": 1, "pass": 0, "fail": 1},
        "screenshots": [
            {"path": "before.png", "created_at": "2026-01-01T12:00:00+00:00"},
            {"path": "after.png", "created_at": "2026-01-01T12:00:02+00:00"},
        ],
        "actions": [{
            "step_number": 1,
            "decision": {"action": "click", "target": "cart", "rationale": "Open cart"},
            "result": {
                "action": "click",
                "success": True,
                "started_at": "2026-01-01T12:00:01+00:00",
                "finished_at": "2026-01-01T12:00:02+00:00",
                "before_url": "https://example.test/products",
                "after_url": "https://example.test/cart",
                "screenshot": "after.png",
                "message": "Cart opened",
                "assertions": [assertion],
            },
        }],
        "scenarios": [{
            "id": "CT001",
            "title": "Open cart",
            "status": "FAIL",
            "url": "https://example.test/cart",
            "screenshots": ["after.png"],
            "steps": ["Click cart"],
            "evidence": {"assertions": [assertion], "action_succeeded": True},
        }],
        "issues": [{
            "type": "http",
            "method": "POST",
            "status_code": 401,
            "url": "https://events.backtrace.io/unique-events?token=secret",
            "message": "POST returned HTTP 401",
            "occurred_at": "2026-01-01T12:00:01.500000+00:00",
            "screenshot": "after.png",
        }],
    }
    original = deepcopy(exploration)
    output = root / "legacy" / "report.html"
    StandaloneHtmlReport(root).generate(exploration, str(output))
    document = output.read_text()

    assert exploration == original
    assert 'data-status="FAIL"' in document
    assert "Verificação técnica falhou" in document
    assert "Resultado histórico preservado" in document
    assert 'data-status="INFO"' not in document
    assert "Telemetria externa" in document
    assert "O resultado desta verificação foi preservado." in document
    assert "Mesma captura" not in document or "causalidade" in document
    assert document.index("CT001") < document.index("POST returned HTTP 401")
    assert "01/01/2026 12:00:01" in document
    assert "01/01/2026 12:00:02" in document
    assert "Estado anterior · última captura disponível" in document
    assert "Estado após a ação" in document
    assert base64.b64encode(before_png).decode() in document
    assert base64.b64encode(after_png).decode() in document
    assert "token=secret" not in document
    assert "Sinais técnicos observados" not in document


def test_report_escapes_signal_and_assertion_content_in_timeline(tmp_path):
    payload = '<svg/onload=alert(1)>'
    exploration = {
        "id": "safe",
        "app_url": "https://example.test",
        "timeline_version": 1,
        "scenarios": [{
            "id": "CT001",
            "status": "FAIL",
            "title": "Safe case",
            "evidence": {
                "technical_signals": [{}],
                "timeline": [{
                    "kind": "signal",
                    "title": payload,
                    "message": payload,
                    "url": f"https://example.test/{payload}",
                    "occurred_at": payload,
                    "signal": {
                        "label": payload,
                        "message": payload,
                        "classification_reason": payload,
                        "impact": payload,
                        "related_console_errors": [{"message": payload}],
                    },
                }, {
                    "kind": "assertion",
                    "assertion": {"kind": payload, "message": payload, "passed": False},
                }],
            },
        }],
    }
    output = tmp_path / "safe.html"
    StandaloneHtmlReport(tmp_path).generate(exploration, str(output))
    document = output.read_text()

    assert payload not in document
    assert "&lt;svg/onload=alert(1)&gt;" in document


def test_report_pairs_one_generic_console_signal_without_merging_two_requests():
    issues = [
        {
            "type": "console",
            "message": (
                "Failed to load resource: the server responded with a status "
                "of 401 (Unauthorized)"
            ),
            # Chromium reports the document URL for this generic console line.
            "url": "https://app.test/inventory",
            "occurred_at": "2026-01-01T12:00:00.100000+00:00",
            "screenshot": "002.png",
        },
        {
            "type": "http",
            "method": "POST",
            "status_code": 401,
            "message": "POST returned HTTP 401",
            "url": "https://telemetry.test/unique-events",
            "occurred_at": "2026-01-01T12:00:00.120000+00:00",
            "screenshot": "002.png",
        },
        {
            "type": "http",
            "method": "POST",
            "status_code": 401,
            "message": "POST returned HTTP 401",
            "url": "https://telemetry.test/summed-events",
            "occurred_at": "2026-01-01T12:00:00.800000+00:00",
            "screenshot": "002.png",
        },
    ]

    grouped = StandaloneHtmlReport._group_issues(issues)

    assert len(grouped) == 2
    assert grouped[0]["count"] == 2
    assert grouped[0]["types"] == ["http", "console"]
    assert grouped[0]["urls"] == ["https://telemetry.test/unique-events"]
    assert grouped[1]["count"] == 1
    assert grouped[1]["urls"] == ["https://telemetry.test/summed-events"]


def test_cancelled_report_ignores_stale_coverage_completion():
    status, reason = StandaloneHtmlReport._completion(
        {
            "status": "cancelled",
            "events": [
                {
                    "type": "coverage_complete_with_restrictions",
                    "message": "Coverage ended before post-processing",
                },
                {
                    "type": "browser_closed",
                    "message": "The browser was closed",
                },
            ],
        }
    )

    assert status == "Cancelada"
    assert reason == "The browser was closed"
