"""Compatibility guard: patent routing is handled by the policy engine."""


def patent_guard(item: dict, result: dict, page_text: str = "") -> dict:
    return dict(result)
