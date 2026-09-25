from autojev.domain.models import Observation
from autojev.services.exploration import ExplorationManager


def test_login_form_requires_user_intervention_even_without_login_label():
    observation = Observation(
        url="https://www.saucedemo.com/",
        title="Swag Labs",
        text="Swag Labs Username Password for all users",
        interactive_elements=[
            {"ref": "e0", "tag": "input", "type": "text", "name": "user-name"},
            {"ref": "e1", "tag": "input", "type": "password", "name": ""},
            {"ref": "e2", "tag": "input", "type": "submit", "name": "Login"},
        ],
    )

    assert ExplorationManager._needs_login(observation) is True