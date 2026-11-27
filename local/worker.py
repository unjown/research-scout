
"""Research Scout local worker with ACID completion and durable notifications."""

import logging
import os
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

import requests
from supabase import create_client

from local.ollama_classifier import classify_finding
from local.gemini_provider import classify_gemini
from local.settings_manager import SettingsUpdate, load_settings
from local.policy_engine import apply_policy
from local.safe_fetch import fetch_source


log = logging.getLogger("research_scout.worker")

WEBHOOK_KEYS = {
    "opportunities": "DISCORD_WEBHOOK_OPPORTUNITIES",
    "patents-main": "DISCORD_WEBHOOK_PATENTS_MAIN",
    "patents-explore": "DISCORD_WEBHOOK_PATENTS_EXPLORE",
    "daily-news": "DISCORD_WEBHOOK_DAILY_NEWS",
}

CATEGORY_DESTINATIONS = {
    "opportunities": "opportunities",
    "patents": "patents-main",
    "news": "daily-news",
}

ALLOWED_DESTINATIONS = set(WEBHOOK_KEYS) | {
    "archive",
    "needs-review",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def database():
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SECRET_KEY"],
    )


def send_webhook(key, message):
    url = os.getenv(key, "").strip()

    if not url:
        raise RuntimeError(f"Missing {key} in local .env")

    for attempt in range(3):
        try:
            response = requests.post(
                url,
                json={
                    "content": str(message)[:1900],
                    "allowed_mentions": {"parse": []},
                },
                timeout=(5, 15),
            )

            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    try:
                        delay = float(
                            response.headers.get("Retry-After", "2")
                        )
                    except (ValueError, TypeError):
                        delay = 2.0

                    time.sleep(max(1.0, min(delay, 30.0)))
                    continue

            response.raise_for_status()
            return

        except requests.RequestException:
            if attempt == 2:
                raise

            time.sleep(2 * (attempt + 1))


def deliver_summary(db, run_id):
    """Deliver a persisted summary unless it is already marked sent."""

    row = (
        db.table("local_run_summaries")
        .select("*")
        .eq("run_id", run_id)
        .single()
        .execute()
        .data
    )

    if not row:
        raise RuntimeError(f"Summary for run #{run_id} not found")

    if row["delivery_status"] == "sent":
        return True

    attempts = int(row.get("attempts") or 0) + 1

    try:
        send_webhook(
            "DISCORD_WEBHOOK_STATUS",
            row["message"],
        )

    except Exception as exc:
        error = f"{type(exc).__name__}: status notification failed"

        log.warning(
            "Run #%s final Discord summary failed: %s",
            run_id,
            error,
        )

        db.table("local_run_summaries").update({
            "delivery_status": "failed",
            "attempts": attempts,
            "last_error": error,
            "updated_at": now(),
        }).eq("run_id", run_id).execute()

        return False

    db.table("local_run_summaries").update({
        "delivery_status": "sent",
        "attempts": attempts,
        "last_error": None,
        "sent_at": now(),
        "updated_at": now(),
    }).eq("run_id", run_id).execute()

    log.info("Run #%s final Discord summary delivered", run_id)

    return True


def retry_pending_summaries(limit=10):
    """Retry failed summaries without invoking Ollama."""

    db = database()

    rows = (
        db.table("local_run_summaries")
        .select("run_id")
        .neq("delivery_status", "sent")
        .order("run_id")
        .limit(limit)
        .execute()
        .data
    ) or []

    results = {
        "attempted": len(rows),
        "sent": 0,
        "failed": 0,
    }

    for row in rows:
        try:
            if deliver_summary(db, row["run_id"]):
                results["sent"] += 1
            else:
                results["failed"] += 1

        except Exception:
            log.exception(
                "Could not retry summary for run #%s",
                row["run_id"],
            )
            results["failed"] += 1

    return results


def log_progress(run_id, index, total, item, counts, outcome):
    log.info(
        "Run #%s | %s/%s | %-20s | %s | #%s %s "
        "| published=%d review=%d rejected=%d failed=%d",
        run_id,
        index,
        total,
        outcome,
        item.get("category", "?"),
        item.get("id", "?"),
        str(item.get("title", ""))[:65],
        counts["published"],
        counts["needs_review"],
        counts["rejected"],
        counts["failed"],
    )


def deliver_finding_outbox(db, finding_id):
    """Send persisted notification messages after database commit."""

    rows = (
        db.table("local_notification_outbox")
        .select("*")
        .eq("finding_id", finding_id)
        .in_("status", ["pending", "failed"])
        .execute()
        .data
    ) or []

    for row in rows:
        try:
            destination = row["destination"]

            if destination not in WEBHOOK_KEYS:
                raise ValueError(
                    f"Invalid notification destination: {destination}"
                )

            send_webhook(
                WEBHOOK_KEYS[destination],
                row["content"],
            )

            db.table("local_notification_outbox").update({
                "status": "sent",
                "attempts": int(row.get("attempts") or 0) + 1,
                "last_error": None,
                "sent_at": now(),
            }).eq("id", row["id"]).execute()

            db.table("findings").update({
                "review_status": "published",
                "discord_status": "published",
            }).eq("id", finding_id).execute()

            return "published"

        except Exception as exc:
            log.warning(
                "Notification for finding #%s failed: %s",
                finding_id,
                type(exc).__name__,
            )

            db.table("local_notification_outbox").update({
                "status": "failed",
                "attempts": int(row.get("attempts") or 0) + 1,
                "last_error": type(exc).__name__,
            }).eq("id", row["id"]).execute()

            db.table("findings").update({
                "discord_status": "failed",
            }).eq("id", finding_id).execute()

            return "notification_failed"

    return None


def retry_finding_notifications(limit=20):
    """Retry notification delivery without fetching or classification."""

    db = database()

    rows = (
        db.table("local_notification_outbox")
        .select("finding_id")
        .in_("status", ["pending", "failed"])
        .limit(limit)
        .execute()
        .data
    ) or []

    counts = Counter()

    for finding_id in dict.fromkeys(
        row["finding_id"] for row in rows
    ):
        try:
            outcome = deliver_finding_outbox(db, finding_id)
            counts[outcome or "skipped"] += 1

        except Exception:
            log.exception(
                "Could not retry finding #%s notification",
                finding_id,
            )
            counts["failed"] += 1

    return dict(counts)


def classify_for_worker(item, source_text, provider_settings=None):
    """Classify once and let deterministic Python policy make the decision."""
    if provider_settings is not None and provider_settings.provider == "gemini":
        result = classify_gemini(item, source_text, provider_settings)
    else:
        result = classify_finding(item, source_text)
    if result.category != item["category"]:
        raise ValueError("Classifier category mismatch")
    decision = apply_policy(result)
    return {
        "score": result.relevance_score,
        "importance_score": result.importance_score,
        "evidence_quality": result.evidence_quality,
        "actionability_score": result.actionability_score,
        "category": result.category,
        "destination": decision.destination,
        "outcome": decision.outcome,
        "needs_verification": result.needs_verification,
        "reason": decision.reason,
        "summary": item.get("summary") or "",
        "event_status": result.event_status,
        "eligibility_status": result.eligibility_status,
        "verification_reasons": result.verification_reasons,
        "uncertainty_labels": list(decision.uncertainty_labels),
    }


def normalize_destination(category, destination):
    """Return a destination compatible with the existing Discord routing."""

    if category not in CATEGORY_DESTINATIONS:
        raise ValueError(f"Unsupported category: {category}")

    if destination not in ALLOWED_DESTINATIONS:
        raise ValueError(f"Unsupported destination: {destination}")

    if destination in ("archive", "needs-review"):
        return destination

    if category == "patents":
        if destination not in ("patents-main", "patents-explore"):
            raise ValueError("Invalid patent destination")
        return destination

    expected = CATEGORY_DESTINATIONS[category]

    if destination != expected:
        raise ValueError(
            f"Destination {destination} does not match {category}"
        )

    return destination


def determine_outcome(result, threshold=None):
    """Enforce the 0-1 exclusion boundary; threshold is legacy-compatible only."""
    del threshold
    score = result.get("score")
    if type(score) is not int or not 0 <= score <= 10:
        raise ValueError("Missing or invalid relevance score")
    if score <= 1:
        return "rejected"
    return result["outcome"]


def format_finding_message(item, result):
    title = str(item.get("title") or "Untitled")[:150]
    url = str(item.get("url") or "")
    summary = str(result.get("summary") or "")[:280]

    score = result["score"]
    importance = result.get("importance_score")
    evidence = result.get("evidence_quality")

    lines = [
        f"**{title}**",
        url,
    ]

    if item["category"] == "opportunities":
        status = result.get("event_status", "unknown")
        lines.append(f"Event status: {str(status).upper()}")
        eligibility = result.get("eligibility_status", "unconfirmed")
        lines.append(f"Eligibility: {str(eligibility).upper()}")

    if summary:
        lines.append(summary)

    lines.append(f"Relevance: {score}/10")

    if importance is not None:
        lines.append(f"Importance: {importance}/10")

    if evidence is not None:
        lines.append(f"Evidence quality: {evidence}/10")

    labels = result.get("uncertainty_labels") or []
    if labels:
        lines.append("Labels: " + ", ".join(str(label) for label in labels))

    verification = result.get("verification_reasons") or []
    if verification:
        lines.append("Verify: " + "; ".join(str(reason) for reason in verification[:3]))

    return "\n".join(lines)


def process_item(db, run_id, item, threshold, provider_settings=None):
    """Claim, classify, and commit using the existing PostgreSQL RPCs."""

    finding_id = item["id"]
    token = str(uuid.uuid4())

    claimed = db.rpc(
        "scout_claim_item",
        {
            "p_run_id": run_id,
            "p_finding_id": finding_id,
            "p_token": token,
        },
    ).execute().data

    if not claimed:
        return "skipped"

    try:
        excerpt = ""

        try:
            excerpt = fetch_source(item["url"])

        except (requests.RequestException, ValueError, OSError) as exc:
            log.warning(
                "Source fetch failed for finding #%s: %s",
                finding_id,
                type(exc).__name__,
            )

        result = classify_for_worker(item, excerpt, provider_settings)

        result["destination"] = normalize_destination(
            item["category"],
            result["destination"],
        )

        outcome = determine_outcome(result, threshold)

        destination = result["destination"]

        # Review is an outcome, not a Discord webhook destination.
        if destination == "needs-review":
            destination = CATEGORY_DESTINATIONS[item["category"]]

        message = None
        previously_published = False

        if outcome == "publish_candidate":
            sent = (
                db.table("local_notification_outbox")
                .select("id")
                .eq("finding_id", finding_id)
                .eq("status", "sent")
                .limit(1)
                .execute()
                .data
            ) or []
            if sent or item.get("discord_status") == "published":
                previously_published = True
            else:
                message = format_finding_message(item, result)

        committed = db.rpc(
            "scout_complete_item",
            {
                "p_run_id": run_id,
                "p_finding_id": finding_id,
                "p_token": token,
                "p_score": int(result["score"]),
                "p_reason": str(result.get("reason") or "")[:250],
                "p_destination": destination,
                "p_outcome": outcome,
                "p_notification": message,
            },
        ).execute().data

        if not committed:
            raise RuntimeError(
                "Item lease lost before commit; result not saved"
            )

        # New columns are installed by database/02_inclusive_policy_migration.sql.
        # Keep this outside the RPC so the worker remains compatible before migration.
        try:
            db.table("findings").update({
                "importance_score": int(result["importance_score"]),
                "evidence_quality": int(result["evidence_quality"]),
                "verification_status": (
                    "needs_verification" if result.get("needs_verification") else "verified_from_source"
                ),
                "uncertainty_labels": result.get("uncertainty_labels") or [],
            }).eq("id", finding_id).execute()
        except Exception:
            log.info("Extended classification columns are not installed yet")

        if previously_published:
            return "previously_published"

        if outcome == "publish_candidate":
            return (
                deliver_finding_outbox(db, finding_id)
                or "notification_pending"
            )

        return outcome

    except Exception as exc:
        try:
            db.rpc(
                "scout_fail_item",
                {
                    "p_run_id": run_id,
                    "p_finding_id": finding_id,
                    "p_token": token,
                    "p_error": (
                        f"{type(exc).__name__}: {str(exc)[:250]}"
                    ),
                },
            ).execute()

        except Exception:
            log.exception(
                "Unable to persist failure for finding #%s",
                finding_id,
            )

        raise


def persist_run_summary(db, run_id, summary):
    """Create a summary once and retry delivery only if unsent."""

    existing = (
        db.table("local_run_summaries")
        .select("run_id,delivery_status")
        .eq("run_id", run_id)
        .execute()
        .data
    ) or []

    if not existing:
        try:
            db.table("local_run_summaries").insert({
                "run_id": run_id,
                "message": summary,
                "delivery_status": "pending",
                "attempts": 0,
                "updated_at": now(),
            }).execute()

        except Exception:
            # A concurrent worker may have inserted the same run summary.
            existing = (
                db.table("local_run_summaries")
                .select("run_id,delivery_status")
                .eq("run_id", run_id)
                .execute()
                .data
            ) or []

            if not existing:
                raise

    return deliver_summary(db, run_id)


def run_approved(run_id):
    """Process an approved run and persist its final summary."""

    db = None
    counts = Counter()
    total = 0
    fatal = None
    status = "failed"

    try:
        db = database()

        run = (
            db.table("local_analysis_runs")
            .select("*")
            .eq("id", run_id)
            .single()
            .execute()
            .data
        )

        if not run:
            raise ValueError(f"Run #{run_id} does not exist")

        # Snapshot provider configuration once; active runs never change provider mid-run.
        provider_settings = load_settings()
        if provider_settings.provider == "gemini" and not provider_settings.gemini_model:
            raise ValueError("Gemini provider requires a configured model")

        rows = (
            db.table("local_analysis_items")
            .select("finding_id,status")
            .eq("run_id", run_id)
            .execute()
            .data
        ) or []

        total = len(rows)

        db.table("local_analysis_runs").update({
            "status": "running",
            "started_at": now(),
        }).eq("id", run_id).execute()

        log.info(
            "Run #%s START | total=%d threshold=%d",
            run_id,
            total,
            run["minimum_score"],
        )

        for index, row in enumerate(rows, 1):
            if row["status"] == "completed":
                counts["previously_completed"] += 1
                continue

            finding_id = row["finding_id"]

            item = {
                "id": finding_id,
                "category": "?",
                "title": "(loading)",
            }

            try:
                item = (
                    db.table("findings")
                    .select("*")
                    .eq("id", finding_id)
                    .single()
                    .execute()
                    .data
                )

                if not item:
                    raise ValueError(
                        f"Finding #{finding_id} not found"
                    )

                log_progress(
                    run_id, index, total, item, counts, "START"
                )

                outcome = process_item(
                    db,
                    run_id,
                    item,
                    run["minimum_score"],
                    provider_settings,
                )

                counts[outcome] += 1

            except Exception:
                outcome = "failed"
                counts["failed"] += 1

                log.exception(
                    "Run #%s item #%s failed",
                    run_id,
                    finding_id,
                )

            log_progress(
                run_id,
                index,
                total,
                item,
                counts,
                outcome.upper(),
            )

        if counts["failed"] or counts["notification_failed"]:
            status = "completed_with_errors"
        else:
            status = "completed"

    except Exception as exc:
        fatal = f"{type(exc).__name__}: {str(exc)[:250]}"
        log.exception("Run #%s fatal error", run_id)

    finally:
        analyzed = sum(
            counts[key]
            for key in (
                "published",
                "needs_review",
                "rejected",
                "notification_failed",
                "notification_pending",
                "failed",
                "previously_published",
            )
        )

        summary = (
            f"**RS.ai — Quick Mode #{run_id}**\n"
            f"Status: {status}\n"
            f"Total: {total} | Analyzed this attempt: {analyzed}\n"
            f"Published: {counts['published']} | "
            f"Needs review: {counts['needs_review']}\n"
            f"Rejected: {counts['rejected']} | "
            f"Failed: {counts['failed']}\n"
            f"Notification failures: {counts['notification_failed']} | "
            f"Pending: {counts['notification_pending']}\n"
            f"Previously completed: {counts['previously_completed']}"
            f" | Previously published protected: {counts['previously_published']}"
        )

        log.info(
            "Run #%s FINAL: %s",
            run_id,
            summary.replace("\n", " | "),
        )

        if db is not None:
            try:
                db.table("local_analysis_runs").update({
                    "status": (
                        "failed"
                        if status == "completed_with_errors"
                        else status
                    ),
                    "finished_at": now(),
                    "error_message": fatal or (
                        "Some findings or notifications failed"
                        if status == "completed_with_errors"
                        else None
                    ),
                }).eq("id", run_id).execute()

            except Exception:
                log.exception(
                    "Run #%s could not persist final status",
                    run_id,
                )

            try:
                persist_run_summary(db, run_id, summary)

            except Exception:
                log.exception(
                    "Run #%s could not persist/deliver summary",
                    run_id,
                )

    return {
        "run_id": run_id,
        "status": status,
        "counts": dict(counts),
    }
