from autojev.adapters.secrets import EphemeralCredentialVault, SecretCipher, redact


def test_secret_cipher_encrypts_at_rest(tmp_path):
    cipher = SecretCipher(tmp_path, "test-only-passphrase")
    encrypted = cipher.encrypt("sk-private-value")

    assert encrypted is not None
    assert "sk-private-value" not in encrypted
    assert cipher.decrypt(encrypted) == "sk-private-value"
    assert (tmp_path / ".master_key").exists() is False


def test_local_key_and_ephemeral_vault(tmp_path):
    cipher = SecretCipher(tmp_path)
    assert (tmp_path / ".master_key").stat().st_mode & 0o777 == 0o600
    assert cipher.decrypt(cipher.encrypt("secret")) == "secret"

    vault = EphemeralCredentialVault()
    vault.put("run-1", {"password": "top-secret"})
    copy = vault.get("run-1")
    copy["password"] = "changed"
    assert vault.get("run-1")["password"] == "top-secret"
    vault.discard("run-1")
    assert vault.get("run-1") == {}


def test_redact_recursively_removes_sensitive_fields_and_values():
    payload = {
        "authorization": "Bearer abc",
        "nested": {"api_key": "abc", "message": "failed for top-secret"},
        "items": ["top-secret", "safe"],
    }
    result = redact(payload, ["top-secret"])

    assert result["authorization"] == "[REDACTED]"
    assert result["nested"]["api_key"] == "[REDACTED]"
    assert result["nested"]["message"] == "failed for [REDACTED]"
    assert result["items"][0] == "[REDACTED]"

