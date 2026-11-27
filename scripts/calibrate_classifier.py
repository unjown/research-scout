"""Run a no-database, no-Discord calibration set against local Ollama."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from local.ollama_classifier import classify_finding_with_trace  # noqa: E402
from local.policy_engine import apply_policy  # noqa: E402


CASES: tuple[dict[str, Any], ...] = (
    {"name": "vitamin_c", "band": (0, 1), "title": "Vitamin C could help block cancer-linked chemicals in your stomach, study finds", "summary": "A biomedical chemistry study reports that vitamin C may reduce formation of carcinogenic compounds in the stomach."},
    {"name": "alzheimers", "band": (0, 1), "title": "A hidden biological divide may help explain Alzheimer's and chronic pain", "summary": "Researchers report biological differences in immune cells associated with Alzheimer's disease and chronic pain, without a computing or engineering method."},
    {"name": "celebrity", "band": (0, 1), "title": "Actor joins cast of upcoming celebrity reality series", "summary": "An entertainment casting announcement with no technology, policy, or engineering connection."},
    {"name": "edge_ai", "band": (7, 10), "title": "New open-source edge AI model runs efficiently on low-power devices", "summary": "Developers released model weights and an inference toolkit optimized for embedded processors."},
    {"name": "cybersecurity", "band": (7, 10), "title": "Critical authentication vulnerability exposes enterprise servers", "summary": "Researchers published technical details, affected versions, mitigations, and a security patch."},
    {"name": "robotics", "band": (7, 10), "title": "Low-cost robotic platform improves autonomous navigation", "summary": "The system combines sensor fusion, embedded control, and open hardware for autonomous robots."},
    {"name": "developer_tools", "band": (7, 10), "title": "Open-source compiler tool catches concurrency defects before deployment", "summary": "The developer tool adds static analysis for race conditions and integrates with common CI systems."},
    {"name": "medical_imaging_ai", "band": (2, 8), "title": "AI method improves medical image segmentation with limited training data", "summary": "The paper introduces a reusable computer-vision architecture and publishes evaluation code."},
    {"name": "computational_biology", "band": (2, 8), "title": "Reusable graph neural network predicts protein interactions across datasets", "summary": "Researchers release a general machine-learning algorithm and code that transfers between biological datasets."},
    {"name": "startup_business", "band": (1, 5), "title": "Regional startup reports new funding round and expansion plans", "summary": "The company announced financing and business expansion but no new technical product or major policy impact."},
)


def main() -> int:
    failures = 0
    for index, case in enumerate(CASES, 1):
        item = {
            "id": -index, "category": "news", "title": case["title"],
            "summary": case["summary"], "source_name": "calibration",
            "url": f"https://calibration.invalid/{case['name']}",
            "published_at": "2026-11-20T08:00:00+08:00",
        }
        trace = classify_finding_with_trace(item, case["summary"])
        decision = apply_policy(trace.classification)
        low, high = case["band"]
        passed = low <= trace.classification.relevance_score <= high
        failures += int(not passed)
        print(json.dumps({
            "name": case["name"], "expected_band": [low, high], "passed": passed,
            "model": trace.model, "attempts": trace.attempts,
            "raw": list(trace.raw_outputs),
            "validated": trace.classification.model_dump(),
            "final": {"destination": decision.destination, "outcome": decision.outcome,
                      "labels": list(decision.uncertainty_labels), "reason": decision.reason},
        }, ensure_ascii=False))
    print(json.dumps({"cases": len(CASES), "failures": failures, "passed": failures == 0}))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
