from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from local import audience_profiles
from local.dashboard import HTML


def test_all_json_profiles_validate_against_one_schema() -> None:
    profiles = audience_profiles.load_audience_profiles()
    assert set(profiles) == {"developer", "business", "educator"}
    assert {profile.id for profile in profiles.values()} == set(profiles)
    assert all(set(profile.category_instructions) == {"news", "patents", "opportunities"} for profile in profiles.values())


def test_developer_is_default_and_unknown_id_falls_back() -> None:
    assert audience_profiles.get_audience_profile().id == "developer"
    assert audience_profiles.get_audience_profile("unexpected").id == "developer"


def test_missing_profile_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audience_profiles, "PROFILE_DIR", Path("missing-audience-profile-dir"))
    with pytest.raises(ValueError, match="Invalid audience profile"):
        audience_profiles.load_audience_profiles()


def test_dashboard_has_local_persistence_and_read_only_profile_loading() -> None:
    soup = BeautifulSoup(HTML, "html.parser")
    selector = soup.find("select", id="audience")
    assert selector is not None
    assert [option.get("value") for option in selector.find_all("option")] == ["developer", "business", "educator"]
    script = soup.find("script").get_text()
    assert "localStorage.getItem(audienceStorageKey)" in script
    assert "localStorage.setItem(audienceStorageKey, safeId)" in script
    assert "fetch(`/api/audiences/${encodeURIComponent(profileId)}`" in script
    assert "localStorage" in script and "developer" in script
    assert "fetch('/api/approve'" not in script.split("async function configureAudience", 1)[1].split("function requiredElement", 1)[0]


def test_audience_endpoints_do_not_mutate_job_or_delivery_state() -> None:
    import inspect
    from local import dashboard

    assert "load_audience_profiles" in inspect.getsource(dashboard.audiences)
    source = inspect.getsource(dashboard.audience)
    assert "get_audience_profile" in source
    assert "db(" not in source
    assert "run_approved" not in source
    assert "retry_pending_summaries" not in source


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_embedded_javascript_parses_with_audience_control() -> None:
    soup = BeautifulSoup(HTML, "html.parser")
    script = soup.find("script").get_text()
    result = subprocess.run(["node", "-e", "new Function(process.argv[1])", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_branding_and_theme_tokens_are_present() -> None:
    assert '<title>RS.ai // Control</title>' in HTML
    for color in ("#0B1220", "#142337", "#22D3EE", "#F1F5F9", "#94A3B8", "#10B981", "#FBBF24", "#F87171", "#38BDF8"):
        assert color in HTML
