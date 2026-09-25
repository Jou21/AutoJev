from pathlib import Path

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import ExplorationOptions


def test_public_browser_urls_redact_secrets_and_preserve_spa_routes(tmp_path: Path):
    executor = PlaywrightExecutor(
        tmp_path, ExplorationOptions(), secret_values=["known-secret"]
    )

    public = executor._public_url(
        "https://example.test/app?q=ok&token=unknown&name=known-secret#/dashboard"
    )
    assert "unknown" not in public
    assert "known-secret" not in public
    assert "q=ok" in public
    assert public.endswith("#/dashboard")
