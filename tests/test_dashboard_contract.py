from __future__ import annotations

import re
import shutil
import subprocess

import pytest
from bs4 import BeautifulSoup

from local.dashboard import HTML


def script_text() -> str:
    soup = BeautifulSoup(HTML, "html.parser")
    script = soup.find("script")
    assert script is not None
    return script.get_text()


def test_every_javascript_element_reference_exists() -> None:
    soup = BeautifulSoup(HTML, "html.parser")
    ids = {node["id"] for node in soup.select("[id]")}
    references = set(re.findall(r"(?:requiredElement|optionalElement)\('([^']+)'\)", script_text()))
    assert references <= ids


def test_every_inclusive_statistic_has_a_real_element() -> None:
    soup = BeautifulSoup(HTML, "html.parser")
    ids = {node["id"] for node in soup.select("[id]")}
    assert {
        "pending", "published", "opportunities", "patents", "news",
        "review", "rejected", "failed",
    } <= ids


def test_status_refresh_distinguishes_api_and_render_failures() -> None:
    script = script_text()
    assert "Status API failed:" in script
    assert "Dashboard render failed:" in script
    assert script.count("fetch('/api/status'") == 1


def test_status_api_contract_matches_renderer() -> None:
    import inspect
    import local.dashboard as dashboard

    source = inspect.getsource(dashboard.status)
    for field in (
        "pending", "classified", "runs", "worker_busy", "progress",
        "review_counts", "status_counts", "dashboard_counts",
        "retryable_count", "summaries",
    ):
        assert f'"{field}"' in source


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_embedded_javascript_parses() -> None:
    result = subprocess.run(
        ["node", "-e", "new Function(process.argv[1])", script_text()],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
