from unittest.mock import patch
import importlib.util

import pytest

from local.classification_schema import Classification
from local.gemini_provider import GeminiProviderError, classify_gemini
from local.settings_manager import SettingsUpdate

try:
    _gemini_sdk = importlib.util.find_spec("google.genai")
except ModuleNotFoundError:
    _gemini_sdk = None
pytestmark = pytest.mark.skipif(_gemini_sdk is None, reason="Google Gen AI SDK is not installed")


def _classification() -> Classification:
    return Classification(
        category="news", relevance_score=4, importance_score=4,
        evidence_quality=4, actionability_score=4, topic_category="AI",
        reason="Concrete technical connection", clearly_unrelated=False,
    )


def test_missing_gemini_key_is_safe() -> None:
    with patch("local.gemini_provider.read_gemini_key", return_value=""):
        with pytest.raises(GeminiProviderError, match="not configured"):
            classify_gemini({"category": "news"}, "source", SettingsUpdate(provider="gemini", gemini_model="gemini-test"))


def test_worker_uses_gemini_only_when_selected() -> None:
    import local.worker as worker

    with patch("local.worker.classify_gemini", return_value=_classification()) as gemini:
        result = worker.classify_for_worker(
            {"category": "news", "summary": ""}, "source",
            SettingsUpdate(provider="gemini", gemini_model="gemini-test"),
        )
    gemini.assert_called_once()
    assert result["score"] == 4


def test_gemini_malformed_response_is_not_retried() -> None:
    class Response:
        text = "not json"

    class Models:
        def generate_content(self, **kwargs):
            return Response()

    class Client:
        models = Models()

    with patch("local.gemini_provider.read_gemini_key", return_value="secret"), patch("google.genai.Client", return_value=Client(), create=True):
        with pytest.raises(GeminiProviderError, match="invalid classification"):
            classify_gemini({"category": "news"}, "source", SettingsUpdate(provider="gemini", gemini_model="gemini-test"))
