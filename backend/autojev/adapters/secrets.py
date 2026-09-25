from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from threading import RLock

from cryptography.fernet import Fernet, InvalidToken


class SecretError(RuntimeError):
    pass


class SecretCipher:
    """Encrypt provider API keys at rest with a deployment-local Fernet key."""

    def __init__(self, data_dir: Path, configured_key: str | None = None) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self._key_path = data_dir / ".master_key"
        key = self._normalise_key(configured_key) if configured_key else self._local_key()
        self._fernet = Fernet(key)

    @staticmethod
    def _normalise_key(value: str) -> bytes:
        raw = value.strip().encode("utf-8")
        try:
            Fernet(raw)
            return raw
        except (ValueError, TypeError):
            return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())

    def _local_key(self) -> bytes:
        if self._key_path.exists():
            return self._key_path.read_bytes().strip()
        key = Fernet.generate_key()
        try:
            fd = os.open(self._key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(key + b"\n")
        except FileExistsError:
            return self._key_path.read_bytes().strip()
        return key

    def encrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str | None) -> str | None:
        if not token:
            return None
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretError("Stored secret cannot be decrypted with the configured key") from exc


class EphemeralCredentialVault:
    """Process-memory-only storage for credentials supplied for one exploration."""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, str]] = {}
        self._lock = RLock()

    def put(self, exploration_id: str, credentials: dict[str, str]) -> None:
        with self._lock:
            self._items[exploration_id] = dict(credentials)

    def get(self, exploration_id: str) -> dict[str, str]:
        with self._lock:
            return dict(self._items.get(exploration_id, {}))

    def discard(self, exploration_id: str) -> None:
        with self._lock:
            values = self._items.pop(exploration_id, None)
            if values:
                values.clear()


def redact(value: object, secrets: list[str] | None = None) -> object:
    """Recursively redact secret-looking fields and exact in-memory secret values."""

    blocked_keys = {"api_key", "apikey", "authorization", "password", "secret", "token"}
    secret_values = {item for item in (secrets or []) if item}
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if key.lower().replace("-", "_") in blocked_keys
            else redact(item, list(secret_values))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, list(secret_values)) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item, list(secret_values)) for item in value)
    if isinstance(value, str):
        result = value
        for secret in secret_values:
            result = result.replace(secret, "[REDACTED]")
        return result
    return value

