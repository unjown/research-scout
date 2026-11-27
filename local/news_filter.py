"""Compatibility guard: news has no manual topic blacklist."""


def news_guard(item: dict, result: dict) -> dict:
    return dict(result)
