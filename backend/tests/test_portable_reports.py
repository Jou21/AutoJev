"""Portable report round trips: isolated SQLite, no browser dispatch."""
import json
from copy import deepcopy

import pytest

from test_scenario_rerun_api import runtime
from autojev.domain.models import AgentDecision, Observation, TestScenario as StoredScenario
from autojev.services.portable_reports import ReportDecoder, decode, embedded, export_package
from autojev.services.rerun_recipe import build_rerun_recipe
from autojev.services.rerun_journey import build_rerun_journey


def embedded_unchecked(package):
    return '<script id="autojev-portable-report" type="application/json">' + json.dumps(package).replace("<", "\\u003c") + "</script>"


def runnable(app, source, journey=False):
    repository = app.state.repository
    before = Observation("https://shop.test", "Shop", "Public page", [])
    recipe = build_rerun_recipe(before, AgentDecision("reload", "Replay"), before.url, capture_journey=journey)
    assert recipe["enabled"]
    checks = [{"kind": "no_new_browser_errors", "expected": 0, "actual": 0, "passed": True}]
    result = {"before_url": before.url, "after_url": before.url, "success": True, "assertions": checks, "evidence": {"rerun_recipe": recipe}}
    repository.add_action(source, 1, {"action": "reload"}, result)
    scenario = StoredScenario(**repository.get_exploration(source)["scenarios"][0])
    scenario.evidence = {"action_step": 1, "assertions": checks, "timeline": [{"kind": "action", "title": "Reload"}]}
    if journey:
        scenario.evidence["rerun_journey"] = build_rerun_journey(repository.get_exploration(source)["actions"], 1, before.url, checks)
    repository.add_scenario(source, 1, scenario)


@pytest.mark.parametrize("journey", [False, True])
def test_roundtrip_preserves_real_recipes_and_is_rerunnable_after_reload(runtime, journey):
    app, client, source = runtime
    runnable(app, source, journey)
    before = app.state.repository.get_exploration(source)
    response = client.get(f"/api/test-suites/{source}/report")
    assert response.status_code == 200, response.text
    package = decode(response.content)
    assert package["cases"][0]["recipe"]["enabled"] is True
    imported = client.post("/api/test-suites/import", content=response.content)
    assert imported.status_code == 201, imported.text
    identity = imported.json()["id"]
    assert identity != source
    assert client.get("/api/test-suites").json()[0]["id"] == identity
    catalog = client.get(f"/api/test-suites/{identity}").json()
    assert catalog["cases"][0]["title"] == before["scenarios"][0]["title"]
    preflight = client.get(f"/api/explorations/{identity}/scenarios/CT001/rerun").json()
    assert preflight["available"] is True, preflight
    plan = client.post(f"/api/test-suites/{identity}/plans", json={"name": "Imported replay", "items": [{"case_id": "CT001"}]} )
    assert plan.status_code == 201, plan.text
    validation = client.post(f'/api/test-suites/rounds/{plan.json()["id"]}/validate').json()
    assert validation["available"], validation
    from autojev.adapters.repository import SQLiteRepository
    reloaded = SQLiteRepository(app.state.repository.database_path, app.state.repository.cipher)
    assert reloaded.get_exploration(identity)["scenarios"][0]["evidence"]["assertions"] == package["cases"][0]["assertions"]
    assert app.state.repository.get_exploration(source) == before
    assert app.state.scenario_rerun_service.tasks == {}
    assert app.state.test_suite_service.tasks == {}
    assert decode(client.get(f"/api/test-suites/{identity}/report").content) == package


def test_legacy_and_missing_recipes_are_honest(runtime):
    app, client, source = runtime
    before = client.get("/api/test-suites").json()
    response = client.post("/api/test-suites/import", content='<html><script>alert(1)</script></html>')
    assert response.status_code == 422
    assert "apenas apresentação" in response.json()["detail"]
    assert client.get("/api/test-suites").json() == before
    package = decode(client.get(f"/api/test-suites/{source}/report").content)
    assert package["cases"][0]["recipe"] is None
    identity = client.post("/api/test-suites/import", content=embedded_unchecked(package)).json()["id"]
    readiness = client.get(f"/api/explorations/{identity}/scenarios/CT001/rerun").json()
    assert not readiness["available"] and readiness["code"] == "missing_recipe"


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(credentials={"password": "private"}),
    lambda p: p.update(app_url="file:///etc/passwd"),
    lambda p: p.update(app_url="https://user:private@shop.test"),
    lambda p: p.update(app_url="https://shop.test?token=private"),
    lambda p: p.update(version=True),
    lambda p: p.update(cases=p["cases"] * 501),
    lambda p: p["cases"].append(deepcopy(p["cases"][0])),
    lambda p: p["cases"][0].update(id="../other"),
    lambda p: p["cases"][0].update(timeline=[{"kind": "action", "title": "Danger", "screenshot": "../../secret.png"}]),
    lambda p: p["cases"][0]["recipe"].update(private="private"),
    lambda p: p["cases"][0]["recipe"]["decision"].update(action="execute_sql"),
    lambda p: p["cases"][0]["recipe"]["decision"].update(action="navigate", url="file:///etc/passwd"),
    lambda p: p["cases"][0].update(assertions=[{"kind": "x", "expected": {"credentials": "private"}}]),
    lambda p: p["cases"][0].update(title="a" * 8001),
])
def test_invalid_packages_are_atomic_and_never_dispatch(runtime, mutation):
    app, client, source = runtime
    runnable(app, source)
    package = export_package(app.state.test_suite_service, app.state.test_suite_service.report_snapshot(source))
    mutation(package)
    before = app.state.repository.list_explorations()
    response = client.post("/api/test-suites/import", content=embedded_unchecked(package))
    assert response.status_code == 422, response.text
    assert app.state.repository.list_explorations() == before
    assert app.state.scenario_rerun_service.tasks == {}


def test_origin_invalid_json_and_nested_limits(runtime):
    app, client, _ = runtime
    response = client.post("/api/test-suites/import", content=b" " * (9 * 1024 * 1024))
    assert response.status_code == 422
    assert "apenas apresentação" in response.json()["detail"]
    assert client.post("/api/test-suites/import", content="{}").status_code == 422
    assert client.post("/api/test-suites/import", content='{"version":1,"version":1}').status_code == 422
    assert client.post("/api/test-suites/import", content="null").status_code == 422
    assert client.post("/api/test-suites/import", content="{}", headers={"Origin": "https://other.test"}).status_code == 403
    for data in (b"\xff", b"[" * 1100, json.dumps([[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]).encode()):
        with pytest.raises(ValueError):
            decode(data)


def test_hostile_text_stays_text_not_imported_html(runtime):
    app, client, source = runtime
    package = export_package(app.state.test_suite_service, app.state.test_suite_service.report_snapshot(source))
    package["cases"][0]["title"] = '<script>alert("not executed")</script>'
    identity = client.post("/api/test-suites/import", content=embedded_unchecked(package)).json()["id"]
    assert client.get(f"/api/test-suites/{identity}").json()["cases"][0]["title"] == package["cases"][0]["title"]
    snapshot = app.state.test_suite_service.report_snapshot(identity)
    assert snapshot["scenarios"][0]["title"] == package["cases"][0]["title"]


def test_authenticated_journey_exports_aliases_not_credentials(runtime):
    app, client, source = runtime
    runnable(app, source, journey=True)
    from autojev.services.rerun_setup import build_authentication_setup
    controls = [dict(ref="e1", tag="input", type="text", name="username"),
                dict(ref="e2", tag="input", type="password", name="password"),
                dict(ref="e3", tag="button", name="Entrar")]
    setup = build_authentication_setup(Observation("https://shop.test", "Login", "", controls), None, "https://shop.test")
    assert setup["enabled"]
    repository = app.state.repository
    scenario = StoredScenario(**repository.get_exploration(source)["scenarios"][0])
    scenario.evidence["rerun_journey"]["setup"] = setup
    scenario.evidence["rerun_journey"]["required_inputs"] = ["password", "username"]
    from dataclasses import asdict
    with repository._connect() as connection:
        connection.execute("UPDATE scenarios SET data_json = ? WHERE exploration_id = ? AND id = 'CT001'", (json.dumps(asdict(scenario)), source))
    response = client.get(f"/api/test-suites/{source}/report")
    assert response.status_code == 200, response.text
    result = client.post("/api/test-suites/import", content=response.content)
    assert result.status_code == 201, result.text
    ready = client.get(f'/api/explorations/{result.json()["id"]}/scenarios/CT001/rerun').json()
    assert ready["available"] and ready["required_inputs"] == ["password", "username"]


def test_incomplete_journey_never_becomes_single_action_replay(runtime):
    app, client, source = runtime
    runnable(app, source, journey=True)
    repository = app.state.repository
    with repository._connect() as connection:
        row = connection.execute("SELECT data_json FROM scenarios WHERE exploration_id = ?", (source,)).fetchone()
        data = json.loads(row[0])
        data["evidence"]["rerun_journey"]["complete"] = False
        connection.execute("UPDATE scenarios SET data_json = ? WHERE exploration_id = ?", (json.dumps(data), source))
    package = decode(client.get(f"/api/test-suites/{source}/report").content)
    assert package["cases"][0]["recipe"] is None
    assert package["cases"][0]["journey"] is None
    identity = client.post("/api/test-suites/import", content=embedded_unchecked(package)).json()["id"]
    assert not client.get(f"/api/explorations/{identity}/scenarios/CT001/rerun").json()["available"]


@pytest.mark.parametrize("wrap", [
    lambda block: block + block,
    lambda block: block.replace('type="application/json"', 'type="text/javascript"'),
    lambda block: block.replace('type="application/json"', 'type="application/json" id="other"'),
    lambda block: block.removesuffix('</script>'),
    lambda block: block.replace('"version":1', '"version":1,"version":1'),
    lambda block: block.replace('"version":1', '"version":2'),
])
def test_hostile_embedded_blocks_are_rejected_atomically(runtime, wrap):
    app, client, source = runtime
    package = export_package(app.state.test_suite_service, app.state.test_suite_service.report_snapshot(source))
    before = app.state.repository.list_explorations()
    result = client.post('/api/test-suites/import', content=wrap(embedded(package)))
    assert result.status_code == 422
    assert app.state.repository.list_explorations() == before


def test_real_download_routes_escape_script_and_ignore_hostile_presentation(runtime):
    app, client, source = runtime
    runnable(app, source)
    repository = app.state.repository
    title = '</script><script>alert("not executed")</script>'
    with repository._connect() as connection:
        row = connection.execute('SELECT data_json FROM scenarios WHERE exploration_id = ?', (source,)).fetchone()
        case = json.loads(row[0])
        case['title'] = title
        connection.execute('UPDATE scenarios SET data_json = ? WHERE exploration_id = ?', (json.dumps(case), source))
    output = app.state.settings.artifact_dir / source / "report.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("<html>original</html>")
    repository.update_exploration(source, report_path=str(output))
    for route in (f'/api/test-suites/{source}/report', f'/api/explorations/{source}/report'):
        report = client.get(route)
        assert report.status_code == 200
        assert b'\\u003c/script\\u003e' in report.content
        package = decode(report.content)
        assert package['cases'][0]['title'] == title
        assert b'credentials' not in embedded(package).encode()
        result = client.post('/api/test-suites/import', content=report.content + b'<script>throw Error()</script><iframe src="file:///private"></iframe>')
        assert result.status_code == 201, result.text
        assert client.get(f'/api/test-suites/{result.json()["id"]}').json()['cases'][0]['title'] == title
    preview = client.get(f'/api/test-suites/{source}/report?preview=true')
    assert b'id="autojev-portable-report"' not in preview.content
    assert client.get(f'/api/test-suites/{source}/export').status_code == 404
    package = export_package(app.state.test_suite_service, app.state.test_suite_service.report_snapshot(source))
    assert client.post('/api/test-suites/import', json=package).status_code == 422


def test_download_metadata_matches_scope_and_stable_ids_not_latest_catalog(runtime):
    app, client, root = runtime
    runnable(app, root)
    repo = app.state.repository
    from autojev.domain.models import ExplorationRequest, ExplorationOptions, ModelSelection
    child = repo.create_exploration(ExplorationRequest('https://shop.test', ModelSelection('local', 'agent'), ModelSelection('local', 'aux'), ExplorationOptions()))
    scenario = deepcopy(repo.get_exploration(root)['scenarios'][0])
    scenario.update(title='Latest child title', status='FAIL')
    repo.add_scenario(child, 1, StoredScenario(**scenario))
    runnable(app, child)
    repo.add_event(child, 'info', 'scenario_rerun_source', 'Source', {'source_exploration_id': root, 'source_scenario_id': 'CT001'})
    repo.update_exploration(child, status='completed')
    initial = decode(client.get(f'/api/test-suites/{root}/report?scope=initial').content)
    current = decode(client.get(f'/api/test-suites/{root}/report').content)
    assert initial['cases'][0]['title'] != current['cases'][0]['title']
    assert current['cases'][0]['title'] == 'Latest child title'
    assert current['cases'][0]['status'] == 'FAIL'
    from test_test_suites import new_manual
    manual = new_manual(client, root, 'Manual selected only')
    single = decode(client.get(f'/api/test-suites/{root}/report?case_id={manual["id"]}').content)
    assert [case['id'] for case in single['cases']] == [manual['id']]
    assert single['cases'][0]['recipe'] is None
    plan = client.post(f'/api/test-suites/{root}/plans', json={'name': 'Manual round', 'items': [{'case_id': manual['id']}]}).json()
    selected = decode(client.get(f'/api/test-suites/{root}/report?scope=round&round_id={plan["id"]}').content)
    assert [case['id'] for case in selected['cases']] == [manual['id']]
    assert selected['cases'][0]['title'] == 'Manual selected only'
    assert selected['cases'][0]['recipe'] is None


def test_large_html_streams_images_padding_and_roundtrips(runtime):
    app, client, source = runtime
    runnable(app, source)
    package = decode(client.get(f"/api/test-suites/{source}/report").content)
    padding = b"A" * (9 * 1024 * 1024)
    html = (b'\xef\xbb\xbf<html><!--' + padding + b'--><script>' + padding
            + b'</script><img src="data:image/png;base64,' + padding
            + b'">' + embedded(package).encode() + b'</html>')
    decoder = ReportDecoder()
    for offset in range(0, len(html), 4093):
        decoder.feed(html[offset:offset + 4093])
        assert len(decoder.tag) <= 16384
        assert len(decoder.pending) <= 256
        assert decoder.parser.rawdata == ""
    assert decoder.finish() == package
    response = client.post('/api/test-suites/import', content=(html[i:i + 4093] for i in range(0, len(html), 4093)))
    assert response.status_code == 201, response.text
    assert decode(client.get(f'/api/test-suites/{response.json()["id"]}/report').content) == package
    before = app.state.repository.list_explorations()
    invalid = html + embedded(package).encode()
    response = client.post('/api/test-suites/import', content=invalid)
    assert response.status_code == 422 and 'repetidos' in response.json()['detail']
    assert app.state.repository.list_explorations() == before


def test_metadata_over_eight_mib_exports_and_imports(runtime):
    app, client, source = runtime
    snapshot = app.state.test_suite_service.report_snapshot(source)
    original = snapshot['scenarios'][0]
    snapshot['scenarios'] = [dict(deepcopy(original), id=f'CT{i:03}', steps=['x' * 2000] * 100) for i in range(50)]
    snapshot['_portable_sources'] = {item['id']: (source, original['id']) for item in snapshot['scenarios']}
    package = export_package(app.state.test_suite_service, snapshot)
    html = embedded(package).encode()
    assert len(html) > 8 * 1024 * 1024
    assert decode(html) == package
    response = client.post('/api/test-suites/import', content=html)
    assert response.status_code == 201, response.text
    assert decode(client.get(f'/api/test-suites/{response.json()["id"]}/report').content) == package


@pytest.mark.parametrize('chunk_size', [1, 2, 7, 16384])
def test_stream_delimiters_utf8_and_strict_metadata(runtime, chunk_size):
    app, client, source = runtime
    package = decode(client.get(f'/api/test-suites/{source}/report').content)
    package['cases'][0]['title'] = 'Ação café ☕'
    block = embedded(package).replace('A\\u00e7\\u00e3o caf\\u00e9 \\u2615', 'Ação café ☕').encode()
    html = b'\xef\xbb\xbf<!-- pretend ' + block + b' --><style>' + block + b'</style>' + block
    decoder = ReportDecoder()
    for offset in range(0, len(html), chunk_size):
        decoder.feed(html[offset:offset + chunk_size])
    assert decoder.finish() == package


def test_selected_exports_cache_without_scope_or_metadata_downgrade(runtime, monkeypatch):
    app, client, root = runtime
    runnable(app, root)
    from autojev.adapters.report import StandaloneHtmlReport
    original = StandaloneHtmlReport.generate
    generated = []

    def tracked(self, snapshot, output_path):
        generated.append(snapshot['suite_context']['scope'])
        return original(self, snapshot, output_path)

    monkeypatch.setattr(StandaloneHtmlReport, 'generate', tracked)
    route = f'/api/test-suites/{root}/report?scope=initial'
    first = client.get(route)
    second = client.get(route)
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert generated == ['initial']
    assert decode(first.content)['cases'][0]['recipe'] is not None
    # Even identical cases in a different scope must not reuse that HTML.
    assert client.get(f'/api/test-suites/{root}/report').status_code == 200
    assert generated == ['initial', 'consolidated']
    assert client.get(route).status_code == 200
    assert generated == ['initial', 'consolidated', 'initial']
    with app.state.repository._connect() as connection:
        row = connection.execute('SELECT data_json FROM scenarios WHERE exploration_id = ?', (root,)).fetchone()
        case = json.loads(row[0])
        case['title'] = 'Fresh historical title'
        connection.execute('UPDATE scenarios SET data_json = ? WHERE exploration_id = ?', (json.dumps(case), root))
    fresh = client.get(route)
    assert decode(fresh.content)['cases'][0]['title'] == 'Fresh historical title'
    assert generated == ['initial', 'consolidated', 'initial', 'initial']
