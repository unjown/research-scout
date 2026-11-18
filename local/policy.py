"""Read and validate local, trusted Research Scout policy JSON files."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "config"
CATEGORIES = ("news", "patents", "opportunities")
ALLOWED_DESTINATIONS = {
    "news": ("daily-news", "archive"),
    "patents": ("patents-main", "patents-explore", "archive"),
    "opportunities": ("opportunities", "archive"),
}


def _read(filename):
    path = ROOT / filename
    if path.stat().st_size > 32768:
        raise ValueError(f"Oversized policy: {filename}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_policy(category):
    if category not in CATEGORIES:
        raise ValueError("Invalid category")
    base = _read("classifier.json")
    policy = _read(category + ".json")
    if base.get("version") != 1 or policy.get("category") != category:
        raise ValueError("Invalid policy version/category")
    if policy.get("destinations") != list(ALLOWED_DESTINATIONS[category]):
        raise ValueError("Destination configuration must match trusted allowlist")
    for instructions in (base.get("system_rules"), policy.get("instructions")):
        if not isinstance(instructions, list) or not instructions or any(not isinstance(s, str) or len(s) > 600 for s in instructions):
            raise ValueError("Invalid policy instructions")
    for name in ("excluded_title_patterns", "protected_title_patterns"):
        patterns = policy.get(name, [])
        if not isinstance(patterns, list) or len(patterns) > 30:
            raise ValueError("Invalid pattern list")
        for pattern in patterns:
            if not isinstance(pattern, str) or len(pattern) > 180:
                raise ValueError("Invalid regex pattern")
            re.compile(pattern)
    for key in ("minimum_publish_relevance", "minimum_main_relevance", "minimum_explore_relevance"):
        if key in policy and (type(policy[key]) is not int or not 0 <= policy[key] <= 10):
            raise ValueError("Invalid threshold")
    model = base.get("model", {})
    if not isinstance(model, dict) or not 1024 <= int(model.get("context", 0)) <= 16384:
        raise ValueError("Invalid model context")
    return base, policy


def matches(patterns, text):
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)
