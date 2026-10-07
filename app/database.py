import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(path: Path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                course TEXT NOT NULL,
                issuer TEXT NOT NULL,
                issued_on TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS certificates (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id),
                position INTEGER NOT NULL,
                recipient TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS certificates_job ON certificates(job_id);
        """)
        # Background tasks cannot survive a restart; report interrupted work honestly.
        db.execute("""
            UPDATE certificates SET status = 'failed', error = 'Server stopped before completion'
            WHERE status = 'pending'
        """)
        db.execute("""
            UPDATE jobs SET status = 'interrupted' WHERE status IN ('queued', 'processing')
        """)
