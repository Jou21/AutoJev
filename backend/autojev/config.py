from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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

    @classmethod
    def from_env(cls) -> "Settings":
        raw_dir = Path(os.getenv("AUTOJEV_DATA_DIR", "./data")).expanduser()
        data_dir = raw_dir if raw_dir.is_absolute() else (Path.cwd() / raw_dir)
        origins = tuple(
            origin.strip()
            for origin in os.getenv(
                "AUTOJEV_CORS_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000",
            ).split(",")
            if origin.strip()
        )
        return cls(
            data_dir=data_dir.resolve(),
            database_path=(data_dir / "autojev.sqlite3").resolve(),
            artifact_dir=(data_dir / "artifacts").resolve(),
            secret_key=os.getenv("AUTOJEV_SECRET_KEY") or None,
            cors_origins=origins,
            headless=_bool_env("AUTOJEV_HEADLESS", True),
            browser=os.getenv("AUTOJEV_BROWSER", "chromium").lower(),
            http_timeout_seconds=float(os.getenv("AUTOJEV_HTTP_TIMEOUT_SECONDS", "45")),
        )


settings = Settings.from_env()
