"""The report's "Reexecutar testes" resolves every CT in one request."""
import json
from copy import deepcopy

from test_scenario_rerun_api import runtime  # noqa: F401 - pytest fixture
from test_test_suites import new_manual
from autojev.domain.models import TestScenario as StoredScenario

KEYS = ("root_id", "case_id", "mode", "source_exploration_id", "source_scenario_id")


def batch(client, root, items):
    return client.get(f"/api/test-suites/{root}/case-contexts", params={"itens": json.dumps(items)})


def test_batch_matches_single_case_context_for_every_ct(runtime):
    app, client, root = runtime
    repo = app.state.repository
    second = deepcopy(repo.get_exploration(root)["scenarios"][0])
    second.update(id="CT002", title="Segundo CT")
    repo.add_scenario(root, 2, StoredScenario(**second))
    manual = new_manual(client, root, "Conferência manual")
    items = [{"source": root, "ct": "CT001"}, {"source": root, "ct": "CT002"}, {"source": root, "ct": manual["id"]}]
    response = batch(client, root, items)
    assert response.status_code == 200, response.text
    expected = []
    for item in items:
        single = client.get(f"/api/test-suites/{item['source']}/case-context/{item['ct']}")
        assert single.status_code == 200, single.text
        expected.append({key: single.json()[key] for key in KEYS})
    assert response.json() == expected
    assert [value["mode"] for value in response.json()] == ["automatic", "automatic", "manual"]


def test_batch_rejects_unknown_cts_and_malformed_lists(runtime):
    app, client, root = runtime
    assert batch(client, root, [{"source": root, "ct": "CT999"}]).status_code == 404
    for bad in ([], [{"source": root}], [{"source": root, "ct": "CT001", "extra": 1}], [{"source": root, "ct": ""}]):
        assert batch(client, root, bad).status_code == 422, bad
    assert client.get(f"/api/test-suites/{root}/case-contexts", params={"itens": "{not json"}).status_code == 422
