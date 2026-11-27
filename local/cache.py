import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "research_cache.sqlite3"


def connect():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("""
        CREATE TABLE IF NOT EXISTS source_cache (
            url TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            fetched_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )
    """)
    return db


def get_cached(url):
    with connect() as db:
        row = db.execute(
            """
            SELECT content FROM source_cache
            WHERE url=? AND expires_at>?
            """,
            (url, int(time.time())),
        ).fetchone()

    return row[0] if row else None


def save_cached(url, content, ttl=86400):
    now = int(time.time())

    with connect() as db:
        db.execute(
            """
            INSERT INTO source_cache
            (url, content, fetched_at, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                content=excluded.content,
                fetched_at=excluded.fetched_at,
                expires_at=excluded.expires_at
            """,
            (url, content, now, now + ttl),
        )


def cleanup_cache():
    with connect() as db:
        db.execute(
            "DELETE FROM source_cache WHERE expires_at<=?",
            (int(time.time()),),
        )