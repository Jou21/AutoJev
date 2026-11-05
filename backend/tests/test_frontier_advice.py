"""Frontier advice: when to ask, what is accepted, and the focus gate/hints."""

import asyncio
import json

import pytest

from autojev.domain.models import ActionResult, AgentDecision, Observation
from autojev.services.coverage import CoveragePlanner
from autojev.services.frontier_advice import (
    FrontierAdvisor,
    opener_reason,
    parse_advice,
)

FOCUS = "Painel de Operações > Conciliação de Títulos Pagos em Cartório"


def element(ref, name, **extra):
    return {
        "ref": ref, "tag": "div", "name": name, "pointer_widget": True,
        "enabled": True, "interactable": True, "in_navigation": False, **extra,
    }


def observation(url, elements, text="", title="App"):
    return Observation(url=url, title=title, text=text, interactive_elements=elements)


def result(success=True):
    return ActionResult(
        action="click", success=success, started_at="t", finished_at="t",
        before_url="u", after_url="u",
    )


# --- when to ask -----------------------------------------------------------

HOME = observation("https://app.test/home", [element("e0", "Painel de Operações", in_navigation=True)])


def test_new_route_is_an_opening():
    after = observation("https://app.test/conciliacao", [element("e0", "Pesquisar")])
    assert opener_reason(
        decision=AgentDecision("click", "x", target="e0"), result=result(),
        before=HOME, after=after, new_state=True, mechanical=False,
    ) == "route_changed"


def test_flyout_or_side_panel_is_an_opening_without_a_url_change():
    flyout = [element(f"e{i}", f"Item {i}", in_navigation=True) for i in range(1, 5)]
    after = observation("https://app.test/home", [*HOME.interactive_elements, *flyout])
    assert opener_reason(
        decision=AgentDecision("click", "x", target="e0"), result=result(),
        before=HOME, after=after, new_state=True, mechanical=False,
    ) == "menu_or_panel_opened"


def test_dialog_is_an_opening():
    after = observation("https://app.test/home", [element("e0", "Fechar", in_overlay=True)])
    assert opener_reason(
        decision=AgentDecision("click", "x", target="e0"), result=result(),
        before=HOME, after=after, new_state=True, mechanical=False,
    ) == "overlay_opened"


@pytest.mark.parametrize("override", [
    {"mechanical": True},              # replaying a known path
    {"new_state": False},              # screen already seen
    {"result": result(success=False)},
    {"decision": AgentDecision("fill", "x", target="e0", value="a")},  # typing opens nothing
])
def test_ordinary_steps_never_reach_the_model(override):
    after = observation("https://app.test/conciliacao", [element("e0", "Pesquisar")])
    arguments = dict(
        decision=AgentDecision("click", "x", target="e0"), result=result(),
        before=HOME, after=after, new_state=True, mechanical=False,
    )
    arguments.update(override)
    assert opener_reason(**arguments) is None


def test_a_click_that_changes_little_is_not_an_opening():
    after = observation("https://app.test/home", [*HOME.interactive_elements, element("e1", "Novo")])
    assert opener_reason(
        decision=AgentDecision("click", "x", target="e0"), result=result(),
        before=HOME, after=after, new_state=True, mechanical=False,
    ) is None


# --- what is accepted --------------------------------------------------------

def test_only_offered_refs_are_kept_and_everything_is_clamped():
    advice = parse_advice(
        {
            "focus_status": "somewhere",
            "suggestions": [
                {"ref": "e2", "priority": 9, "why": "abre o submenu"},
                {"ref": "e99", "priority": 1, "why": "inventado"},
                {"ref": "e2", "priority": 1, "why": "duplicado"},
                "lixo",
            ],
            "unexplored_notes": ["campos abaixo da dobra", "", 3],
        },
        {"e1", "e2"}, "route_changed",
    )
    assert advice.focus_status == "unknown"
    assert advice.priorities == {"e2": 3}
    assert advice.dropped == 3
    assert advice.notes == ["campos abaixo da dobra"]


def test_garbage_is_harmless():
    assert parse_advice(["nope"], {"e1"}, "x").suggestions == []


# --- the advisor ---------------------------------------------------------------

class FakeClient:
    def __init__(self, answers=None, error=None):
        self.answers = answers or {"focus_status": "on_path", "suggestions": [], "unexplored_notes": []}
        self.error, self.calls = error, []

    async def chat_json(self, model, system, content, **kwargs):
        self.calls.append((model, system, content, kwargs))
        if self.error:
            raise self.error
        return self.answers


def context(key="s1", refs=("e1", "e2")):
    return {
        "state_key": key,
        "current_untried": [
            {"ref": ref, "name": f"Controle {ref}", "suggested_action": "click",
             "in_navigation": True, "context": None}
            for ref in refs
        ],
    }


def advise(advisor, key="s1", **kwargs):
    return asyncio.run(advisor.advise(
        observation("https://app.test/x?token=segredo", [], text="texto", title="Tela"),
        context(key), focus_area=FOCUS, focus_reached=False, reason="first_screen",
        trail=["click 'Início' -> /home"], **kwargs,
    ))


def test_one_consultation_per_state_within_the_cap():
    client = FakeClient({"focus_status": "on_path",
                         "suggestions": [{"ref": "e2", "priority": 1, "why": "abre o menu"}],
                         "unexplored_notes": []})
    advisor = FrontierAdvisor(client, "aux", max_requests=2)
    first = advise(advisor)
    assert first.priorities == {"e2": 1} and first.focus_status == "on_path"
    assert advise(advisor) is None                      # same state: never asked twice
    assert advise(advisor, key="s2") is not None        # second and last request
    assert advise(advisor, key="s3") is None            # cap reached
    assert len(client.calls) == 2


def test_request_carries_focus_controls_trail_but_no_query_string_or_secret():
    client = FakeClient()
    advisor = FrontierAdvisor(client, "aux", secret_values=["segredo"])
    advise(advisor)
    _, system, content, kwargs = client.calls[0]
    payload = json.loads(content)
    assert payload["focus_area"] == FOCUS and payload["focus_reached"] is False
    assert [c["ref"] for c in payload["controls"]] == ["e1", "e2"]
    assert payload["explored_trail"] == ["click 'Início' -> /home"]
    assert "segredo" not in content and "token" not in content   # path only, redacted
    assert "untrusted" in system and "Never invent refs" in system
    assert kwargs["response_schema"]["additionalProperties"] is False


def test_image_is_attached_only_when_a_multimodal_builder_is_given():
    seen = []

    def multimodal(observation_, text):
        seen.append(observation_.title)
        return [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA=="}}]

    client = FakeClient()
    advise(FrontierAdvisor(client, "aux", multimodal=multimodal))
    assert seen == ["Tela"] and isinstance(client.calls[0][2], list)
    plain = FakeClient()
    advise(FrontierAdvisor(plain, "aux"))
    assert isinstance(plain.calls[0][2], str)


def test_two_failures_in_a_row_turn_the_advisor_off_without_raising():
    client = FakeClient(error=RuntimeError("boom"))
    advisor = FrontierAdvisor(client, "aux")
    assert advise(advisor, key="a") is None and advisor.available
    assert advise(advisor, key="b") is None and not advisor.available
    assert advisor.disabled_reason == "RuntimeError"
    assert advise(advisor, key="c") is None and len(client.calls) == 2


# --- planner: gate and hints -----------------------------------------------------

def sidebar_home():
    return observation("https://app.test/gcar/home", [
        element("e0", "star"),                                   # header toy, not navigation
        element("e1", "Liberações", in_navigation=True),
        element("e2", "Painel de Operações", in_navigation=True),
        element("e3", "Salvar rascunho"),                        # unrelated button
    ])


def untried(planner, obs):
    return [item["name"] for item in planner.context(obs)["current_untried"]]


def test_without_the_gate_the_whole_entry_screen_is_in_scope():
    planner = CoveragePlanner("https://app.test/gcar/home", FOCUS)
    home = sidebar_home()
    planner.observe(home)
    assert set(untried(planner, home)) == {"star", "Liberações", "Painel de Operações", "Salvar rascunho"}


def test_gate_keeps_only_moves_that_can_lead_to_the_unreached_focus():
    planner = CoveragePlanner("https://app.test/gcar/home", FOCUS)
    planner.gate_until_focus = True
    home = sidebar_home()
    planner.observe(home)
    names = untried(planner, home)
    assert names[0] == "Painel de Operações"               # focus label first
    assert set(names) == {"Liberações", "Painel de Operações"}   # no header/unrelated moves


def test_advisor_hint_admits_and_promotes_a_move_but_never_adds_one():
    planner = CoveragePlanner("https://app.test/gcar/home", FOCUS)
    planner.gate_until_focus = True
    home = sidebar_home()
    planner.observe(home)
    accepted = planner.apply_advice(home, {"e3": 1, "e1": 2, "e404": 1})
    assert set(accepted) == {"e3", "e1"}                   # e404 is not a candidate
    names = untried(planner, home)
    assert names[:2] == ["Salvar rascunho", "Liberações"]  # hint beats the label match
    assert "Painel de Operações" in names


def test_a_hint_cannot_resurrect_a_move_already_attempted():
    planner = CoveragePlanner("https://app.test/gcar/home", FOCUS)
    home = sidebar_home()
    planner.observe(home)
    decision = AgentDecision("click", "x", target="e2")
    planner.record_action(home, decision, result(), observation("https://app.test/gcar/home", [element("e9", "Resumo")]))
    assert "e2" not in planner.apply_advice(home, {"e2": 1})


# --- what reaches the report ---------------------------------------------------

def test_ai_notes_become_deduplicated_leads_for_the_report():
    from autojev.services.coverage_ledger import ai_flagged_gaps

    event = lambda url, notes, trigger="route_changed": {
        "type": "frontier_advice",
        "payload": {"url": url, "screenshot": "003-click.jpg", "trigger": trigger,
                    "focus_status": "on_path", "unexplored_notes": notes},
    }
    gaps = ai_flagged_gaps([
        {"type": "action_completed", "payload": {}},
        event("https://app.test/a", ["Campos abaixo da dobra do painel", "  Aba  Cheque  não visitada "]),
        event("https://app.test/a", ["campos abaixo da dobra do painel."]),     # same note, same page
        event("https://app.test/b", ["Campos abaixo da dobra do painel"]),       # same note, other page
        {"type": "frontier_advice", "payload": "garbage"},
    ])
    assert [(gap["url"], gap["note"]) for gap in gaps] == [
        ("https://app.test/a", "Campos abaixo da dobra do painel"),
        ("https://app.test/a", "Aba Cheque não visitada"),
        ("https://app.test/b", "Campos abaixo da dobra do painel"),
    ]
    assert gaps[0]["screenshot"] == "003-click.jpg" and gaps[0]["trigger"] == "route_changed"
    assert ai_flagged_gaps(None) == []
