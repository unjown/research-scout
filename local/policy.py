"""Load and validate trusted, editable Research Scout policy files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1] / "config"
Category = Literal["news", "patents", "opportunities"]
ALLOWED_DESTINATIONS: dict[str, tuple[str, ...]] = {
    "news": ("daily-news", "archive"),
    "patents": ("patents-main", "patents-explore", "archive"),
    "opportunities": ("opportunities", "archive"),
}


class ModelPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default: str
    context: int = Field(ge=1024, le=16384)
    temperature: float = Field(ge=0, le=1)
    max_output_tokens: int = Field(ge=128, le=4096)
    max_source_chars: int = Field(ge=500, le=20000)
    max_retries: int = Field(ge=0, le=4)


class ScoringPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strong_relevance_minimum: int = Field(ge=0, le=10)
    moderate_relevance_minimum: int = Field(ge=0, le=10)
    uncertain_relevance_minimum: int = Field(ge=0, le=10)
    unrelated_maximum: int = Field(ge=0, le=10)


class BasePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[2]
    system_rules: list[str] = Field(min_length=1, max_length=30)
    relevance_anchors: list[str] = Field(min_length=6, max_length=12)
    model: ModelPolicy
    scoring: ScoringPolicy


class CategoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[2]
    category: Category
    destinations: list[str]
    instructions: list[str] = Field(min_length=1, max_length=30)
    interest_profile: list[str] = Field(min_length=1, max_length=30)
    main_relevance_minimum: int | None = Field(default=None, ge=0, le=10)

    @model_validator(mode="after")
    def destinations_are_trusted(self) -> "CategoryPolicy":
        expected = list(ALLOWED_DESTINATIONS[self.category])
        if self.destinations != expected:
            raise ValueError(f"destinations must be {expected!r}")
        if self.category == "patents" and self.main_relevance_minimum is None:
            raise ValueError("patents require main_relevance_minimum")
        if self.category != "patents" and self.main_relevance_minimum is not None:
            raise ValueError("main_relevance_minimum is patent-only")
        return self


class PolicyBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base: BasePolicy
    category: CategoryPolicy


def _read(filename: str) -> object:
    path = ROOT / filename
    if path.stat().st_size > 32768:
        raise ValueError(f"Oversized policy: {filename}")
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_policy(category: str) -> PolicyBundle:
    """Return the single validated policy contract for a category."""
    if category not in ALLOWED_DESTINATIONS:
        raise ValueError(f"Invalid category: {category}")
    bundle = PolicyBundle.model_validate(
        {"base": _read("classifier.json"), "category": _read(f"{category}.json")}
    )
    if bundle.category.category != category:
        raise ValueError("Policy category mismatch")
    return bundle
