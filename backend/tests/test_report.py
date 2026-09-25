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
        "options": {"focus_area": "Administração > Usuários"},
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
    assert "Cobertura parcial" in document
    assert "Motivo do encerramento:" in document
    assert "Limite de tempo; a fronteira ainda tinha itens." in document
