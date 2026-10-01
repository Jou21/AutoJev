import base64

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
                "message": "Failed to load resource",
                "url": "https://example.test/items?token=secret-one&item=1",
                "screenshot": "041.png",
            },
            {
                "type": "http",
                "status_code": 404,
                "message": "GET returned HTTP 404",
                "url": "https://example.test/items?token=secret-two&item=1",
                "screenshot": "041.png",
            },
        ],
    }

    output = root / "run-2" / "report.html"
    StandaloneHtmlReport(root).generate(exploration, str(output))
    document = output.read_text()

    assert "Concluída com restrições" in document
    assert "SEM VALIDAÇÃO" in document
    assert "Sinais técnicos observados (1 ocorrência)" in document
    assert "2 sinal(is) correlacionado(s)" in document
    assert "2 registro(s) persistido(s)" in document
    assert "secret-one" not in document
    assert "secret-two" not in document
    assert "item=1" in document
    assert "%5BREDACTED%5D" in document


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
