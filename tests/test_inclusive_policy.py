from __future__ import annotations

import pytest

from local.classification_schema import Classification
from local.policy_engine import apply_policy
from local.worker import determine_outcome, format_finding_message, normalize_destination


def classified(category: str = "news", **overrides) -> Classification:
    data = {
        "category": category,
        "relevance_score": 7,
        "importance_score": 5,
        "evidence_quality": 6,
        "actionability_score": 5,
        "topic_category": "technology",
        "reason": "The source describes a concrete research connection.",
        "clearly_unrelated": False,
        "unrelated_reason": "",
        "needs_verification": False,
        "verification_reasons": [],
        "uncertainty_labels": [],
        "event_status": "unknown" if category == "opportunities" else "not_applicable",
        "eligibility_status": "unconfirmed" if category == "opportunities" else "not_applicable",
    }
    data.update(overrides)
    return Classification.model_validate(data)


@pytest.mark.parametrize(
    ("score", "expected_outcome"),
    [(0, "rejected"), (1, "rejected"), (2, "publish_candidate"),
     (4, "publish_candidate"), (7, "publish_candidate"), (10, "publish_candidate")],
)
def test_relevance_boundary_controls_publication(score: int, expected_outcome: str) -> None:
    decision = apply_policy(classified(relevance_score=score))
    assert decision.outcome == expected_outcome
    assert decision.destination == ("archive" if score <= 1 else "daily-news")


def test_weak_uncertain_news_publishes_with_label() -> None:
    decision = apply_policy(classified(relevance_score=2, needs_verification=True))
    assert decision.outcome == "publish_candidate"
    assert "weak or uncertain relevance" in decision.uncertainty_labels


def test_clearly_unrelated_news_is_excluded() -> None:
    decision = apply_policy(classified(
        relevance_score=1, clearly_unrelated=True,
        unrelated_reason="The article is only a film casting announcement with no configured research connection.",
    ))
    assert decision.outcome == "rejected"
    assert decision.destination == "archive"


def test_high_importance_does_not_override_unrelatedness() -> None:
    decision = apply_policy(classified(
        relevance_score=0, importance_score=10, clearly_unrelated=True,
        unrelated_reason="Important globally, but no configured research connection is present.",
    ))
    assert decision.outcome == "rejected"


def test_ai_biology_is_not_rejected_by_topic() -> None:
    decision = apply_policy(classified(
        relevance_score=5, topic_category="AI-assisted biology",
        reason="A new machine-learning method supports biological research.",
    ))
    assert decision.destination == "daily-news"


def test_patent_strength_controls_main_vs_explore() -> None:
    assert apply_policy(classified("patents", relevance_score=8)).destination == "patents-main"
    weak = apply_policy(classified("patents", relevance_score=2, needs_verification=True))
    assert weak.destination == "patents-explore"
    assert "technical feasibility uncertain" in weak.uncertainty_labels


def test_past_opportunity_and_unknown_eligibility_publish() -> None:
    decision = apply_policy(classified(
        "opportunities", relevance_score=3, event_status="past",
        eligibility_status="unconfirmed", needs_verification=True,
    ))
    assert decision.destination == "opportunities"
    assert "historical" in decision.uncertainty_labels
    assert "eligibility unconfirmed" in decision.uncertainty_labels


def test_low_score_does_not_trigger_legacy_threshold() -> None:
    result = {"outcome": "publish_candidate", "score": 2}
    assert determine_outcome(result, threshold=7) == "publish_candidate"


@pytest.mark.parametrize("score", [0, 1])
def test_worker_defensively_excludes_zero_and_one(score: int) -> None:
    result = {"outcome": "publish_candidate", "score": score}
    assert determine_outcome(result, threshold=0) == "rejected"


@pytest.mark.parametrize("score", [None, "0", 1.5])
def test_worker_rejects_missing_or_invalid_relevance(score: object) -> None:
    result = {"outcome": "publish_candidate"}
    if score is not None:
        result["score"] = score
    with pytest.raises(ValueError, match="Missing or invalid relevance score"):
        determine_outcome(result, threshold=0)


def test_message_keeps_scores_labels_and_url_separate() -> None:
    message = format_finding_message(
        {"category": "news", "title": "Finding", "url": "https://example.test/source"},
        {"score": 3, "importance_score": 9, "evidence_quality": 2,
         "uncertainty_labels": ["date unconfirmed"], "verification_reasons": ["No publication date"]},
    )
    assert "Relevance: 3/10" in message
    assert "Importance: 9/10" in message
    assert "date unconfirmed" in message
    assert "https://example.test/source" in message


def test_invalid_cross_category_routing_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_destination("news", "patents-main")
