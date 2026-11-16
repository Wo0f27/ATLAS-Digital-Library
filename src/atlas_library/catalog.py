"""Local SQLite catalog. No source PDF is changed by this module."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Catalog:
    def __init__(self, database_path: Path):
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), timeout=20)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS books (
                path TEXT PRIMARY KEY,
                library_root TEXT NOT NULL,
                title TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                page_count INTEGER,
                cover_path TEXT,
                status TEXT NOT NULL DEFAULT 'available',
                scan_error TEXT NOT NULL DEFAULT '',
                discovered_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_books_root ON books(library_root, status)"
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def get(self, path: Path | str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM books WHERE path = ?", (str(path),)
        ).fetchone()
        return dict(row) if row else None

    def upsert(
        self,
        *,
        path: Path | str,
        library_root: Path | str,
        title: str,
        size_bytes: int,
        modified_ns: int,
        page_count: int | None,
        cover_path: str,
        scan_error: str = "",
    ) -> dict:
        path = str(path)
        root = str(library_root)
        now = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO books (
                    path, library_root, title, size_bytes, modified_ns,
                    page_count, cover_path, status, scan_error,
                    discovered_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'available', ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    library_root = excluded.library_root,
                    size_bytes = excluded.size_bytes,
                    modified_ns = excluded.modified_ns,
                    page_count = excluded.page_count,
                    cover_path = excluded.cover_path,
                    status = 'available',
                    scan_error = excluded.scan_error,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    path, root, title, size_bytes, modified_ns, page_count,
                    cover_path, scan_error, now, now,
                ),
            )
        result = self.get(path)
        assert result is not None
        return result

    def available(self, library_root: Path | str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM books
            WHERE library_root = ? AND status = 'available'
            ORDER BY title COLLATE NOCASE, path
            """,
            (str(library_root),),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_missing_except(self, library_root: Path | str, seen_paths: set[str]) -> None:
        """Mark absent files; never delete their records or the files."""
        with self.connection:
            rows = self.connection.execute(
                "SELECT path FROM books WHERE library_root = ?",
                (str(library_root),),
            ).fetchall()
            for row in rows:
                if row["path"] not in seen_paths:
                    self.connection.execute(
                        "UPDATE books SET status = 'missing' WHERE path = ?",
                        (row["path"],),
                    )
