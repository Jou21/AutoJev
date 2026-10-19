"""Only the registered frame can share a checkbox; sharing never resumes."""
from types import SimpleNamespace

import pytest
from autojev.adapters.browser_rerun_panel import PANEL_URL, BrowserRerunPanel
from autojev.services.rerun_preparation_presentation import (
    CONSENT_LABEL,
    CONTINUE_LABEL,
    preparation_presentation,
)


@pytest.fixture
def consent_panel():
    calls, resumes = [], []
    page = object()
    executor = SimpleNamespace(_page=page, notify_user_input=lambda: resumes.append(True))

    async def capture(*args):
        pytest.fail("A preparation checkbox must never attach an image")

    panel = BrowserRerunPanel(executor, capture)
    panel.frame = SimpleNamespace(url=PANEL_URL, is_detached=lambda: False)
    panel._payload = {"running": True, "phase": "waiting_preparation", "preparation": {"version": 1}}
    selection = {"preparation_version": 1, "revision": 0, "checked": False}

    async def consent(version, revision, checked):
        if revision != selection["revision"]:
            raise ValueError("A preparação foi alterada em outra janela.")
        calls.append((version, revision, checked))
        selection.update(revision=revision + 1, checked=checked)
        return dict(selection)

    panel.consent_handler = consent
    source = {"page": page, "frame": panel.frame}
    command = {"op": "consent", "nonce": panel._nonce, "preparation_version": 1,
               "expected_revision": 0, "checked": True}
    return panel, source, command, calls, resumes


@pytest.mark.asyncio
async def test_sharing_and_unsharing_never_consumes_resume_confirmation(consent_panel):
    panel, source, command, calls, resumes = consent_panel
    assert await panel.command(source, command) == {"preparation_version": 1, "revision": 1, "checked": True}
    assert await panel.command(source, {**command, "expected_revision": 1, "checked": False}) == {
        "preparation_version": 1, "revision": 2, "checked": False,
    }
    assert len(calls) == 2 and not resumes and not panel._used
    assert panel._delivered is None
    await panel.command(source, {"op": "resume", "nonce": panel._nonce, "preparation_version": 1, "confirm": True})
    assert resumes == [True] and ("resume", 1) in panel._used


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["parent", "other_frame", "other_page", "nonce", "version", "phase", "closed"])
async def test_untrusted_or_stale_consent_commands_fail_closed(consent_panel, change):
    panel, source, command, calls, resumes = consent_panel
    if change in {"parent", "other_frame"}:
        source["frame"] = object()
    elif change == "other_page":
        source["page"] = object()
    elif change == "nonce":
        command["nonce"] = "not-the-private-capability"
    elif change == "version":
        command["preparation_version"] = 2
    elif change == "phase":
        panel._payload["phase"] = "validating_preparation"
    else:
        panel.close()
    with pytest.raises(ValueError):
        await panel.command(source, command)
    assert not calls and not resumes


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("checked", 1), ("checked", "true"), ("expected_revision", True),
                                          ("expected_revision", -1), ("preparation_version", True)])
async def test_consent_requires_strict_boolean_and_version_revision_types(consent_panel, field, value):
    panel, source, command, calls, resumes = consent_panel
    with pytest.raises(ValueError):
        await panel.command(source, {**command, field: value})
    assert not calls and not resumes


def test_same_presentation_contract_preserves_consent_scope_and_cta():
    for reason in ["authentication_required", "before_url_changed", None]:
        for phase in ["waiting_preparation", "validating_preparation"]:
            result = preparation_presentation({"reason_code": reason}, phase)
            assert result["consent_label"] == CONSENT_LABEL
            assert "somente se a conferência dos pré-requisitos passar" in result["consent_label"]
            assert result["details_label"] == "Ver detalhes da preparação"
            if phase == "waiting_preparation":
                assert result["button_label"] == CONTINUE_LABEL
                assert CONTINUE_LABEL in result["instruction"]
                if reason == "authentication_required":
                    assert "continua automaticamente após o login" in result["note"]
                else:
                    assert "não inicia o teste" in result["note"]
            else:
                assert "Aguarde" in result["instruction"]
                assert "Não é necessário confirmar novamente" in result["instruction"]


def test_successful_native_login_has_no_stale_authentication_warning():
    result = preparation_presentation({
        "reason_code": None, "manual_login": {"status": "ready", "reason_code": None},
        "login_attempt": {"reason_label": "Login ainda não confirmado"},
    }, "waiting_preparation")
    assert result["kicker"] == "Login reconhecido"
    assert result["title"] == "Login realizado. Autorize para continuar"
    assert "Marque a autorização" in result["instruction"]
    assert "automaticamente" in result["instruction"]
    assert result["feedback"] == "Login reconhecido; aguardando sua autorização para continuar."
