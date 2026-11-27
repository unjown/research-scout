"""Read-only loading and validation for dashboard audience profiles."""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PROFILE_DIR = Path(__file__).resolve().parent.parent / "config" / "audiences"
PROFILE_IDS = ("developer", "business", "educator")


class ResponsePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detail_level: str
    focus: list[str]
    preferred_sections: list[str]


class FilterPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_relevance: int = Field(ge=0, le=10)
    exclude_clearly_unrelated: bool


class AudienceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Literal["developer", "business", "educator"]
    display_name: str = Field(min_length=1)
    description: str
    interests: list[str]
    category_instructions: dict[Literal["news", "patents", "opportunities"], list[str]]
    response_preferences: ResponsePreferences
    filter_preferences: FilterPreferences

    @model_validator(mode="after")
    def require_all_categories(self):
        if set(self.category_instructions) != {"news", "patents", "opportunities"}:
            raise ValueError("category_instructions must define news, patents, and opportunities")
        return self


def load_audience_profiles() -> dict[str, AudienceProfile]:
    """Load all authoritative JSON definitions; malformed or missing files fail closed."""
    profiles: dict[str, AudienceProfile] = {}
    for profile_id in PROFILE_IDS:
        path = PROFILE_DIR / f"{profile_id}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            profile = AudienceProfile.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"Invalid audience profile configuration: {path.name}") from exc
        if profile.id != profile_id:
            raise ValueError(f"Audience profile id does not match {path.name}")
        profiles[profile_id] = profile
    return profiles


def get_audience_profile(profile_id: str = "developer") -> AudienceProfile:
    """Return the requested profile, falling back to Developer for unknown IDs."""
    profiles = load_audience_profiles()
    return profiles.get(profile_id, profiles["developer"])
