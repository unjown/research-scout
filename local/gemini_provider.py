"""Gemini adapter using the Google Gen AI SDK and the shared classifier contract."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from pydantic import ValidationError

from .classification_schema import Classification
from .ollama_classifier import (
    _build_system_prompt,
    _current_manila_date,
    _source_record,
    normalize_model_output,
)
from .settings_manager import SettingsUpdate, read_gemini_key
from .policy import load_policy

log = logging.getLogger(__name__)


class GeminiProviderError(RuntimeError):
    """A safe, user-facing Gemini provider failure."""


def _client() -> Any:
    try:
        from google import genai
    except ImportError as exc:
        raise GeminiProviderError("Gemini SDK is not installed") from exc
    key = read_gemini_key()
    if not key:
        raise GeminiProviderError("Gemini API key is not configured")
    return genai.Client(api_key=key)


def _transient(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status in {408, 409, 429} or (isinstance(status, int) and status >= 500):
        return True
    text = str(exc).lower()
    return any(value in text for value in ("timeout", "temporarily", "rate limit", "unavailable", "503", "429"))


def classify_gemini(item: dict[str, Any], source_text: str, settings: SettingsUpdate) -> Classification:
    """Classify one item with Gemini, retrying only transient failures."""
    category = str(item.get("category") or "")
    bundle = load_policy(category)
    system = _build_system_prompt(category, bundle, _current_manila_date())
    record = _source_record(item, source_text, bundle.base.model.max_source_chars)
    client = _client()
    last: Exception | None = None
    for attempt in range(3):
        try:
            from google.genai import types
            response = client.models.generate_content(
                model=settings.gemini_model,
                contents=json.dumps(record, ensure_ascii=False, default=str),
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=Classification,
                    temperature=settings.temperature,
                    max_output_tokens=settings.max_output_tokens,
                ),
            )
            raw = getattr(response, "text", None)
            if not raw:
                raise ValueError("Gemini returned an empty response")
            return Classification.model_validate(normalize_model_output(raw, category))
        except (ValidationError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise GeminiProviderError("Gemini returned invalid classification data") from exc
        except Exception as exc:
            last = exc
            if not _transient(exc) or attempt == 2:
                raise GeminiProviderError(f"Gemini request failed: {type(exc).__name__}") from exc
            time.sleep(2 ** attempt)
    raise GeminiProviderError("Gemini request failed") from last


def check_gemini_model(model: str) -> dict[str, Any]:
    """Verify credentials and that the configured model is listed by Gemini."""
    if not model.strip():
        return {"available": False, "error": "Gemini model is not configured"}
    try:
        client = _client()
        names = {str(getattr(value, "name", "")).removeprefix("models/") for value in client.models.list()}
        available = model in names
        return {"available": available, "model": model, "error": None if available else "Model is not available"}
    except GeminiProviderError as exc:
        return {"available": False, "model": model, "error": str(exc)}
    except Exception:
        return {"available": False, "model": model, "error": "Gemini availability check failed"}
