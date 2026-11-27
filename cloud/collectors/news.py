"""Broad configured news feeds; contextual relevance belongs to local AI."""

FEEDS = {
    "technology": "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "security": "https://feeds.arstechnica.com/arstechnica/security",
    "philippines": "https://www.rappler.com/feed/",
    "philippines_technology": "https://www.rappler.com/technology/feed/",
}


def allow_news(title: str, summary: str = "", source: str = "") -> bool:
    """Apply objective record validation only; do not maintain topic blacklists."""
    del summary, source
    return bool(str(title or "").strip())
