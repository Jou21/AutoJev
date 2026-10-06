import base64
from copy import deepcopy

from autojev.adapters.report import StandaloneHtmlReport


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
    assert "1 PASS com asserção de negócio" in document
    assert "1 PASS apenas de interação técnica" in document
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
    assert "Inclui revisões auxiliares e geração do relatório" in document
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
    assert ">$0.004200</strong>Custo" in document
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
    assert "Evidência de contexto · não é um CT" in document
    assert 'data-status="INFO"' in document
    assert "Não são testes aprovados e não entram na contagem de CTs." in document
    assert "Falha HTTP da aplicação" in document
    assert "secret-one" not in document
    assert "secret-two" not in document
    assert "item=1" in document
    assert "%5BREDACTED%5D" in document


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
