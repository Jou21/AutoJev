from __future__ import annotations

from typing import Any, Literal
from urllib.parse import parse_qsl, urlparse

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ProviderModelInput(ApiModel):
    # Provider editors often send both `id` and the equivalent `model_id`.
    # Ignore the duplicate while still accepting model_id-only clients.
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(
        min_length=1,
        max_length=300,
        validation_alias=AliasChoices("id", "model_id", "modelId"),
    )
    label: str | None = Field(default=None, max_length=300)
    enabled: bool = True
    input_modalities: list[str] = Field(
        default_factory=list,
        max_length=20,
        validation_alias=AliasChoices("input_modalities", "inputModalities"),
    )
    output_modalities: list[str] = Field(
        default_factory=list,
        max_length=20,
        validation_alias=AliasChoices("output_modalities", "outputModalities"),
    )
    supports_vision: bool = Field(
        default=False,
        validation_alias=AliasChoices("supports_vision", "supportsVision"),
    )
    capabilities_known: bool = Field(
        default=False,
        validation_alias=AliasChoices("capabilities_known", "capabilitiesKnown"),
    )

    @field_validator("id")
    @classmethod
    def clean_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Model id cannot be blank")
        return value

    @field_validator("input_modalities", "output_modalities")
    @classmethod
    def clean_input_modalities(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for modality in values:
            value = modality.strip().casefold()
            if not value:
                continue
            if len(value) > 50:
                raise ValueError("Input modality is too long")
            if value not in normalized:
                normalized.append(value)
        return normalized


class ProviderCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(
        min_length=8,
        max_length=2000,
        validation_alias=AliasChoices("base_url", "baseUrl"),
    )
    api_key: SecretStr | None = Field(
        default=None,
        max_length=8000,
        validation_alias=AliasChoices("api_key", "apiKey"),
    )
    enabled: bool = True
    models: list[ProviderModelInput] = Field(default_factory=list, max_length=500)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Provider name cannot be blank")
        return value

    @field_validator("base_url")
    @classmethod
    def safe_base_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Only absolute http(s) provider URLs are allowed")
        if parsed.username or parsed.password:
            raise ValueError("Credentials in provider URLs are not allowed")
        if parsed.query or parsed.fragment:
            raise ValueError("Provider base URL cannot contain a query or fragment")
        return value.strip().rstrip("/")


class ProviderUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(
        default=None,
        min_length=8,
        max_length=2000,
        validation_alias=AliasChoices("base_url", "baseUrl"),
    )
    api_key: SecretStr | None = Field(
        default=None,
        max_length=8000,
        validation_alias=AliasChoices("api_key", "apiKey"),
    )
    enabled: bool | None = None
    models: list[ProviderModelInput] | None = Field(default=None, max_length=500)

    @field_validator("base_url")
    @classmethod
    def safe_base_url(cls, value: str | None) -> str | None:
        return ProviderCreate.safe_base_url(value) if value is not None else None


class ConnectionTestInput(ApiModel):
    model_id: str | None = Field(
        default=None,
        max_length=300,
        validation_alias=AliasChoices("model_id", "modelId"),
    )


class ModelSelectionInput(ApiModel):
    provider_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("provider_id", "providerId", "provider"),
    )
    model_id: str = Field(
        min_length=1,
        max_length=300,
        validation_alias=AliasChoices("model_id", "modelId", "model"),
    )


class ExplorationOptionsInput(ApiModel):
    mode: str = Field(default="full", max_length=30)
    focus_area: str | None = Field(
        default=None,
        max_length=500,
        validation_alias=AliasChoices("focus_area", "focusArea"),
    )
    max_steps: int | None = Field(
        default=None,
        ge=1,
        le=100_000,
        validation_alias=AliasChoices("max_steps", "maxSteps"),
    )
    max_duration_seconds: int | None = Field(
        default=None,
        ge=10,
        le=604_800,
        validation_alias=AliasChoices("max_duration_seconds", "maxDurationSeconds"),
    )
    capture_screenshots: bool = Field(
        default=True,
        validation_alias=AliasChoices("capture_screenshots", "captureScreenshots", "screenshots"),
    )
    auxiliary_evidence_mode: Literal["text", "visual"] = Field(
        default="text",
        validation_alias=AliasChoices(
            "auxiliary_evidence_mode",
            "auxiliaryEvidenceMode",
            "evidence_mode",
            "evidenceMode",
        ),
    )
    capture_console_errors: bool = Field(
        default=True,
        validation_alias=AliasChoices("capture_console_errors", "captureConsoleErrors", "console_errors"),
    )
    capture_network_errors: bool = Field(
        default=True,
        validation_alias=AliasChoices("capture_network_errors", "captureNetworkErrors", "network_errors"),
    )
    generate_test_cases: bool = Field(
        default=True,
        validation_alias=AliasChoices("generate_test_cases", "generateTestCases"),
    )
    generate_html_report: bool = Field(
        default=True,
        validation_alias=AliasChoices("generate_html_report", "generateHtmlReport"),
    )
    viewport_width: int = Field(
        default=1440,
        ge=320,
        le=3840,
        validation_alias=AliasChoices("viewport_width", "viewportWidth"),
    )
    viewport_height: int = Field(
        default=900,
        ge=600,
        le=2160,
        validation_alias=AliasChoices("viewport_height", "viewportHeight"),
    )

    @field_validator("focus_area")
    @classmethod
    def clean_focus_area(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None


class ExplorationCreate(ApiModel):
    app_url: str = Field(
        min_length=8,
        max_length=2000,
        validation_alias=AliasChoices("app_url", "appUrl", "application_url", "applicationUrl"),
    )
    agent: ModelSelectionInput
    auxiliary_llm: ModelSelectionInput = Field(
        validation_alias=AliasChoices("auxiliary_llm", "auxiliaryLLM")
    )
    options: ExplorationOptionsInput = Field(default_factory=ExplorationOptionsInput)
    credentials: dict[str, SecretStr] = Field(default_factory=dict, max_length=50)

    @field_validator("app_url")
    @classmethod
    def safe_app_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Only absolute http(s) application URLs are allowed")
        if parsed.username or parsed.password:
            raise ValueError("Credentials in application URLs are not allowed")
        sensitive_query_keys = {
            "access_token",
            "authorization",
            "code",
            "credential",
            "jwt",
            "key",
            "magic",
            "password",
            "secret",
            "session",
            "sig",
            "signature",
            "token",
        }
        if any(
            key.lower().replace("-", "_") in sensitive_query_keys
            for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
        ):
            raise ValueError(
                "Sensitive URL query values are not accepted; use credential aliases instead"
            )
        fragment_pairs = parse_qsl(parsed.fragment.lstrip("?/"), keep_blank_values=True)
        if len(parsed.fragment) > 200 or any(
            key.lower().replace("-", "_") in sensitive_query_keys
            for key, _ in fragment_pairs
        ):
            raise ValueError("Sensitive URL fragments are not accepted")
        return value.strip()

    @field_validator("credentials")
    @classmethod
    def validate_credentials(
        cls, value: dict[str, SecretStr]
    ) -> dict[str, SecretStr]:
        for key, secret in value.items():
            if not key.strip() or len(key) > 100:
                raise ValueError("Credential aliases must contain 1 to 100 characters")
            if len(secret.get_secret_value()) > 8000:
                raise ValueError("Credential values may not exceed 8000 characters")
        return value


def model_dict(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(by_alias=False)
