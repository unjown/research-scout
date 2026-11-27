"""Deterministic final publication policy; model output is advisory evidence."""
from __future__ import annotations
from dataclasses import dataclass
from .classification_schema import Classification
from .policy import PolicyBundle, load_policy


@dataclass(frozen=True)
class PolicyDecision:
    destination: str
    outcome: str
    uncertainty_labels: tuple[str, ...]
    reason: str


def apply_policy(result: Classification, policy: PolicyBundle | None = None) -> PolicyDecision:
    """Exclude scores 0-1 and publish valid, unique scores 2-10."""
    bundle = policy or load_policy(result.category)
    labels = list(dict.fromkeys(result.uncertainty_labels))
    if result.needs_verification:
        labels.append("verification needed")
    if 2 <= result.relevance_score <= 3:
        labels.append("weak or uncertain relevance")
    if result.category == "opportunities":
        if result.event_status == "past":
            labels.append("historical")
        if result.eligibility_status == "unconfirmed":
            labels.append("eligibility unconfirmed")
    if result.category == "patents" and result.needs_verification:
        labels.append("technical feasibility uncertain")
    labels = list(dict.fromkeys(labels))

    if result.relevance_score <= bundle.base.scoring.unrelated_maximum:
        reason = result.unrelated_reason if result.clearly_unrelated else result.reason
        return PolicyDecision("archive", "rejected", tuple(labels), reason)
    if result.category == "patents":
        minimum = bundle.category.main_relevance_minimum or 7
        destination = "patents-main" if result.relevance_score >= minimum else "patents-explore"
    elif result.category == "news":
        destination = "daily-news"
    else:
        destination = "opportunities"
    return PolicyDecision(destination, "publish_candidate", tuple(labels), result.reason)
