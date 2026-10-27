from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_AUXILIARY_MAX_REQUESTS = 300


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_path: Path
    artifact_dir: Path
    secret_key: str | None
    cors_origins: tuple[str, ...]
    headless: bool
    browser: str
    http_timeout_seconds: float
    auxiliary_max_requests: int = DEFAULT_AUXILIARY_MAX_REQUESTS
    auxiliary_max_rate_limits: int = 6
    auxiliary_tail_timeout_seconds: float = 600.0
    auxiliary_request_interval_seconds: float = 3.1
    auxiliary_review_max_attempts: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        raw_dir = Path(os.getenv("AUTOJEV_DATA_DIR", "./data")).expanduser()
        data_dir = raw_dir if raw_dir.is_absolute() else (Path.cwd() / raw_dir)
        origins = tuple(
            origin.strip()
            for origin in os.getenv(
                "AUTOJEV_CORS_ORIGINS",
                (
                    "http://localhost:3000,http://127.0.0.1:3000,"
                    "http://localhost:3001,http://127.0.0.1:3001,"
                    "https://autojev-qa-explorer.resumobiblico0714.chatgpt.site"
                ),
            ).split(",")
            if origin.strip()
        )
        return cls(
            data_dir=data_dir.resolve(),
            database_path=(data_dir / "autojev.sqlite3").resolve(),
            artifact_dir=(data_dir / "artifacts").resolve(),
            secret_key=os.getenv("AUTOJEV_SECRET_KEY") or None,
            cors_origins=origins,
            headless=_bool_env("AUTOJEV_HEADLESS", False),
            browser=os.getenv("AUTOJEV_BROWSER", "chromium").lower(),
            http_timeout_seconds=float(os.getenv("AUTOJEV_HTTP_TIMEOUT_SECONDS", "45")),
            auxiliary_max_requests=max(0, int(os.getenv("AUTOJEV_AUXILIARY_MAX_REQUESTS", str(DEFAULT_AUXILIARY_MAX_REQUESTS)))),
            auxiliary_max_rate_limits=max(1, int(os.getenv("AUTOJEV_AUXILIARY_MAX_RATE_LIMITS", "6"))),
            auxiliary_tail_timeout_seconds=max(1.0, float(os.getenv("AUTOJEV_AUXILIARY_TAIL_TIMEOUT_SECONDS", "600"))),
            auxiliary_request_interval_seconds=max(0.0, float(os.getenv("AUTOJEV_AUXILIARY_REQUEST_INTERVAL_SECONDS", "3.1"))),
            auxiliary_review_max_attempts=min(5, max(1, int(os.getenv("AUTOJEV_AUXILIARY_REVIEW_MAX_ATTEMPTS", "3")))),
        )


settings = Settings.from_env()
