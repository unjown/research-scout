"""Local Qwen classification. No model is loaded until classify() is called."""
import json
import os
import requests
from local.patent_filter import patent_guard
from local.news_filter import news_guard

SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 10},
        "destination": {"type": "string", "enum": ["opportunities", "patents-main", "patents-explore", "daily-news", "archive"]},
        "summary": {"type": "string"},
        "reason": {"type": "string"},
        "needs_verification": {"type": "boolean"}
    },
    "required": ["score", "destination", "summary", "reason", "needs_verification"],
    "additionalProperties": False
}

SYSTEM = """Classify ONE discovered item for a Philippine-based technology enthusiast.
Interests: accessible IoT, sensing, computer vision, robotics, software, cybersecurity, hardware research, hackathons, case competitions, startup pitches, and important Philippine/technology news.
Return valid JSON only. Never invent source facts or eligibility.
Opportunities: include global, PH, and unknown-eligibility events; never exclude because eligibility is unstated. Archive only if clearly expired or explicitly restricted to a named non-PH school.
Patents: use patents-main for strong relevance, patents-explore for uncertain/adjacent novelty. Never infer legal status or patent validity from a snippet.
News: publish only technology, AI, cybersecurity, engineering/innovation, major Philippine politics/headlines, and globally significant events. General biology, clinical research, entertainment and trivia are not news priorities. An irrelevant 3/10 item must be archived, not sent for verification. Do not mark an item needs_verification solely because its relevance is low. Summarize factual news in 1-2 sentences. Attribute allegations rather than asserting them as fact.
For patents, score the concrete invention, not generic keywords. Genetic engineering, pharmaceuticals and specialized medical/lab inventions are outside scope unless there is a clear accessible hardware/software technique. Generic humanoid robotics or AI patents should go to Explore unless the abstract establishes a reproducible technical mechanism. Never claim a patent is new or expired without publication/legal metadata. Archive obvious unrelated or spam items. Use needs_verification when facts cannot be established. Keep summary under 240 characters and reason under 160 characters.
"""


def classify(item, page_text=""):
    endpoint = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    if endpoint not in ("http://127.0.0.1:11434", "http://localhost:11434"):
        raise ValueError("Ollama must use local loopback endpoint")
    data = {k: item.get(k) for k in ("id", "title", "category", "url", "summary", "source_name", "published_at", "deadline_at")}
    if page_text:
        data["source_excerpt"] = page_text[:2600]
    payload = {
        "model": os.getenv("OLLAMA_QUICK_MODEL", "qwen3:1.7b"),
        "stream": False, "think": False, "format": SCHEMA, "keep_alive": "0",
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(data, ensure_ascii=False)}],
        "options": {"temperature": float(os.getenv("OLLAMA_QUICK_TEMPERATURE", "0.1")),
                    "num_ctx": int(os.getenv("OLLAMA_QUICK_CONTEXT", "4096")),
                    "num_predict": int(os.getenv("OLLAMA_QUICK_MAX_TOKENS", "768"))}
    }
    r = requests.post(endpoint + "/api/chat", json=payload, timeout=240)
    r.raise_for_status()
    result = json.loads(r.json()["message"]["content"])
    if not isinstance(result.get("score"), int) or not 0 <= result["score"] <= 10:
        raise ValueError("Invalid relevance score")
    valid = {"opportunities", "patents-main", "patents-explore", "daily-news", "archive"}
    if result.get("destination") not in valid:
        raise ValueError("Invalid destination")
    expected = {"opportunities": {"opportunities", "archive"},
                "patents": {"patents-main", "patents-explore", "archive"},
                "news": {"daily-news", "archive"}}
    if item.get("category") not in expected:
        raise ValueError("Unsupported finding category")
    if result["destination"] not in expected[item["category"]]:
        # Qwen 1.7B sometimes selects a channel belonging to another category.
        # Keep the original category authoritative; never silently archive it.
        category = item["category"]
        fallback = {"opportunities": "opportunities", "patents": "patents-explore", "news": "daily-news"}
        original = result["destination"]
        result["destination"] = fallback[category]
        result["needs_verification"] = True
        result["reason"] = (f"AI channel mismatch ({original}); manual review required. "
                            + str(result.get("reason", "")))[:250]
        # A cross-category classification cannot be trusted as a high relevance score.
        result["score"] = min(result["score"], 4)

    result["summary"] = str(result.get("summary", ""))[:400]
    result["reason"] = str(result.get("reason", ""))[:250]
    result["needs_verification"] = bool(result.get("needs_verification", True))
    result = patent_guard(item, result, page_text)
    return news_guard(item, result, minimum_score=int(os.getenv("NEWS_MIN_SCORE", "7")))
