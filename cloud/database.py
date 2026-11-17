"""Persist discovery candidates using Supabase Python SDK and a backend secret key."""
import os
from supabase import create_client


def save_findings(findings):
    if not findings:
        return 0
    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_SECRET_KEY"].strip()
    if not key:
        raise ValueError("SUPABASE_SECRET_KEY is empty")
    client = create_client(url, key)
    # Duplicate URLs are ignored by the unique constraint on findings.url.
    # Requires the Phase 1 findings table and unique URL constraint.
    client.table("findings").upsert(
        findings, on_conflict="url", ignore_duplicates=True
    ).execute()
    # Candidate count; not guaranteed number of newly inserted rows.
    return len(findings)
