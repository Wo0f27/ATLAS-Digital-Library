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
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reader_state (
                path TEXT PRIMARY KEY,
                page_index INTEGER NOT NULL DEFAULT 0,
                zoom_mode TEXT NOT NULL DEFAULT 'width',
                zoom_factor REAL NOT NULL DEFAULT 1.0,
                page_mode TEXT NOT NULL DEFAULT 'continuous',
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS book_profile (
                path TEXT PRIMARY KEY,
                favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN (0, 1)),
                reading_status TEXT NOT NULL DEFAULT 'unread'
                    CHECK(reading_status IN ('unread', 'reading', 'finished')),
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS page_bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                page_index INTEGER NOT NULL CHECK(page_index >= 0),
                label TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(path, page_index, label)
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_page_bookmarks_path "
            "ON page_bookmarks(path, page_index, id)"
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

    def get_reader_state(self, path: Path | str) -> dict | None:
        """Get the last reading position for a specific physical book."""
        row = self.connection.execute(
            "SELECT * FROM reader_state WHERE path = ?", (str(path),)
        ).fetchone()
        return dict(row) if row else None

    def save_reader_state(
        self,
        path: Path | str,
        *,
        page_index: int,
        zoom_mode: str,
        zoom_factor: float,
        page_mode: str,
    ) -> None:
        """Persist user reading state, separate from source PDFs and scan metadata."""
        if zoom_mode not in ("width", "page", "custom"):
            raise ValueError("Unsupported zoom mode")
        if page_mode not in ("single", "continuous"):
            raise ValueError("Unsupported page mode")
        if not 0.25 <= float(zoom_factor) <= 5.0:
            raise ValueError("Zoom factor out of range")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO reader_state (
                    path, page_index, zoom_mode, zoom_factor, page_mode, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    page_index = excluded.page_index,
                    zoom_mode = excluded.zoom_mode,
                    zoom_factor = excluded.zoom_factor,
                    page_mode = excluded.page_mode,
                    updated_at = excluded.updated_at
                """,
                (
                    str(path), max(0, int(page_index)), zoom_mode,
                    float(zoom_factor), page_mode, _utc_now(),
                ),
            )


    def get_profile(self, path: Path | str) -> dict:
        """Book favorites and reading status, independent of PDF metadata."""
        row = self.connection.execute(
            "SELECT favorite, reading_status FROM book_profile WHERE path = ?",
            (str(path),),
        ).fetchone()
        return {
            "favorite": bool(row["favorite"]) if row else False,
            "reading_status": row["reading_status"] if row else "unread",
        }

    def set_favorite(self, path: Path | str, favorite: bool) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO book_profile(path, favorite, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                  favorite = excluded.favorite,
                  updated_at = excluded.updated_at
                """,
                (str(path), int(bool(favorite)), _utc_now()),
            )

    def set_reading_status(self, path: Path | str, status: str) -> None:
        if status not in {"unread", "reading", "finished"}:
            raise ValueError(f"Unsupported reading status: {status}")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO book_profile(path, reading_status, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                  reading_status = excluded.reading_status,
                  updated_at = excluded.updated_at
                """,
                (str(path), status, _utc_now()),
            )

    def list_bookmarks(self, path: Path | str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT id, path, page_index, label, created_at
            FROM page_bookmarks
            WHERE path = ?
            ORDER BY page_index, id
            """,
            (str(path),),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_bookmark(self, path: Path | str, page_index: int, label: str) -> int:
        if int(page_index) < 0:
            raise ValueError("Bookmark page cannot be negative")
        label = label.strip()
        if not label or len(label) > 150:
            raise ValueError("Bookmark label must contain 1-150 characters")
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO page_bookmarks (
                    path, page_index, label, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (str(path), int(page_index), label, _utc_now()),
            )
            row = self.connection.execute(
                """
                SELECT id FROM page_bookmarks
                WHERE path = ? AND page_index = ? AND label = ?
                """,
                (str(path), int(page_index), label),
            ).fetchone()
        assert row is not None
        return int(row["id"])

    def delete_bookmark(self, path: Path | str, bookmark_id: int) -> bool:
        """Restrict deletion to a bookmark belonging to the given book."""
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM page_bookmarks WHERE path = ? AND id = ?",
                (str(path), int(bookmark_id)),
            )
        return cursor.rowcount > 0
