"""Validated boundary between untrusted model output and Python policy."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_FIELD_LABELS = {
    "category", "relevance_score", "importance_score", "evidence_quality",
    "actionability_score", "topic_category", "reason", "clearly_unrelated",
    "unrelated_reason", "needs_verification", "verification_reasons",
    "uncertainty_labels", "event_status", "eligibility_status", "destination",
}


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    category: Literal["news", "patents", "opportunities"]
    relevance_score: int = Field(strict=True, ge=0, le=10)
    importance_score: int = Field(strict=True, ge=0, le=10)
    evidence_quality: int = Field(strict=True, ge=0, le=10)
    actionability_score: int = Field(strict=True, ge=0, le=10)
    topic_category: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=500)
    clearly_unrelated: bool
    unrelated_reason: str = Field(default="", max_length=500)
    needs_verification: bool = False
    verification_reasons: list[str] = Field(default_factory=list, max_length=10)
    uncertainty_labels: list[str] = Field(default_factory=list, max_length=10)
    event_status: Literal["open", "upcoming", "past", "unknown", "not_applicable"] = "not_applicable"
    eligibility_status: Literal["confirmed", "unconfirmed", "ineligible", "not_applicable"] = "not_applicable"

    @field_validator("uncertainty_labels")
    @classmethod
    def remove_schema_field_labels(cls, labels: list[str]) -> list[str]:
        """Drop model artifacts that merely repeat output field names."""
        cleaned: list[str] = []
        for value in labels:
            label = " ".join(str(value).strip().split())[:100]
            normalized = label.lower().replace(" ", "_").replace("-", "_")
            if not label or normalized in SCHEMA_FIELD_LABELS:
                continue
            if label not in cleaned:
                cleaned.append(label)
        return cleaned

    @model_validator(mode="after")
    def validate_unrelatedness(self) -> "Classification":
        if self.clearly_unrelated:
            if self.relevance_score > 1:
                raise ValueError("clearly unrelated findings must score 0-1")
            if not self.unrelated_reason:
                raise ValueError("clearly unrelated findings require evidence-grounded reason")
        elif self.unrelated_reason:
            raise ValueError("unrelated_reason requires clearly_unrelated")
        if self.category == "opportunities" and self.event_status == "not_applicable":
            raise ValueError("opportunities require an event status")
        if self.category != "opportunities" and self.event_status != "not_applicable":
            raise ValueError("event status is opportunity-only")
        return self
