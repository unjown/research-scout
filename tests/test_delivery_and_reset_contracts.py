from __future__ import annotations

from unittest.mock import Mock, patch

from local.worker import deliver_finding_outbox


class Query:
    def __init__(self, db: "FakeDB", table: str):
        self.db = db
        self.table_name = table
        self.payload = None

    def select(self, *_args): return self
    def eq(self, *_args): return self
    def in_(self, *_args): return self

    def update(self, payload):
        self.payload = payload
        return self

    def execute(self):
        if self.payload is not None:
            self.db.updates.append((self.table_name, self.payload))
            return Mock(data=[])
        return Mock(data=[{
            "id": 9, "finding_id": 7, "destination": "daily-news",
            "content": "message", "status": "pending", "attempts": 0,
        }])


class FakeDB:
    def __init__(self):
        self.updates = []

    def table(self, name):
        return Query(self, name)


@patch("local.worker.send_webhook", side_effect=RuntimeError("network"))
def test_failed_discord_delivery_remains_retryable(_send: Mock) -> None:
    db = FakeDB()
    assert deliver_finding_outbox(db, 7) == "notification_failed"
    assert ("local_notification_outbox", {
        "status": "failed", "attempts": 1, "last_error": "RuntimeError",
    }) in db.updates
    assert ("findings", {"discord_status": "failed"}) in db.updates


def test_reset_protects_publication_history_and_active_leases() -> None:
    sql = open("database/04_reset_classification_reviewed.sql", encoding="utf-8").read()
    assert "not exists (\n      select 1 from public.local_notification_outbox" in sql
    assert "i.status = 'running'" in sql
    assert "i.lease_until > now()" in sql
    assert "delete from" not in sql.lower()


def test_migration_persistently_prevents_duplicate_outbox_rows() -> None:
    sql = open("database/02_inclusive_policy_migration.sql", encoding="utf-8").read()
    assert "create unique index if not exists local_notification_outbox_one_finding" in sql
    worker = open("local/worker.py", encoding="utf-8").read()
    assert '.eq("status", "sent")' in worker


def test_worker_preserves_transactional_rpc_sequence() -> None:
    worker = open("local/worker.py", encoding="utf-8").read()
    claim = worker.index('"scout_claim_item"')
    complete = worker.index('"scout_complete_item"')
    fail = worker.index('"scout_fail_item"')
    assert claim < complete < fail
