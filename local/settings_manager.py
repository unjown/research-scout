"""Validated local dashboard settings; secrets never enter the API payload."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = ROOT / "local" / "dashboard_settings.json"
SECRET_FILE = ROOT / "local" / ".gemini_api_key"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _endpoint(value: str) -> str:
    parsed = urlsplit(value.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in ALLOWED_HOSTS or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Endpoint must be an HTTP loopback URL without a path")
    return value.rstrip("/")


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = "local"
    gemini_model: str = ""
    local_model: str = "qwen3:1.7b"
    local_endpoint: str = "http://127.0.0.1:11434"
    context_size: int = Field(2048, ge=256, le=131072)
    max_output_tokens: int = Field(900, ge=1, le=32768)
    temperature: float = Field(0.1, ge=0, le=2)
    dashboard_port: int = Field(8765, ge=1, le=65535)
    connection_timeout: int = Field(30, ge=1, le=300)
    check_services: bool = True
    audience: str = "developer"
    batch_size: int = Field(50, ge=1, le=100)
    categories: list[str] = ["opportunities", "patents", "news"]

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, value: str) -> str:
        if value not in {"local", "gemini"}: raise ValueError("Provider must be Local AI or Gemini API")
        return value

    @field_validator("local_endpoint")
    @classmethod
    def valid_endpoint(cls, value: str) -> str: return _endpoint(value)

    @field_validator("audience")
    @classmethod
    def valid_audience(cls, value: str) -> str:
        if value not in {"developer", "business", "educator"}: raise ValueError("Invalid audience")
        return value

    @field_validator("categories")
    @classmethod
    def valid_categories(cls, value: list[str]) -> list[str]:
        if not value or not set(value) <= {"opportunities", "patents", "news"}: raise ValueError("Choose valid categories")
        return list(dict.fromkeys(value))


def load_settings() -> SettingsUpdate:
    if not SETTINGS_FILE.exists(): return SettingsUpdate(dashboard_port=int(os.getenv("DASHBOARD_PORT", "8765")))
    try: return SettingsUpdate.model_validate(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError): return SettingsUpdate()


def save_settings(update: SettingsUpdate) -> SettingsUpdate:
    SETTINGS_FILE.write_text(json.dumps(update.model_dump(), indent=2) + "\n", encoding="utf-8")
    return update


def sanitized() -> dict:
    data = load_settings().model_dump()
    data.update({"gemini_key_configured": SECRET_FILE.exists() and bool(SECRET_FILE.read_text(encoding="utf-8").strip()), "active_relevance_policy": "Publish relevance 2–10; exclude 0–1."})
    return data


def set_gemini_key(value: str) -> None:
    if not value.strip(): raise ValueError("API key cannot be empty")
    tmp = SECRET_FILE.with_suffix(".tmp")
    tmp.write_text(value.strip(), encoding="utf-8")
    os.replace(tmp, SECRET_FILE)


def clear_gemini_key() -> None:
    if SECRET_FILE.exists(): SECRET_FILE.unlink()


def gemini_key_configured() -> bool:
    return SECRET_FILE.exists() and bool(SECRET_FILE.read_text(encoding="utf-8").strip())


def read_gemini_key() -> str:
    """Read the server-side key for the provider; never serialize this value."""
    try:
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
