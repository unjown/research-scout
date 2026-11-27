"""Compatibility facade for the consolidated classifier contract."""
from .ollama_classifier import ClassificationError, classify_finding


def classify(item: dict, page_text: str = "") -> dict:
    """Return validated model evidence; final routing remains in the worker."""
    return classify_finding(item, page_text).model_dump()
