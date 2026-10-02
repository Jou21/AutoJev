import base64
from copy import deepcopy

from autojev.adapters.report import StandaloneHtmlReport


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
