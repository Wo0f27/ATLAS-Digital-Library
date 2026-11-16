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
        self.connection.execute("PRAGMA foreign_keys=ON")
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
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS book_metadata (
                path TEXT PRIMARY KEY,
                display_title TEXT NOT NULL DEFAULT '',
                authors TEXT NOT NULL DEFAULT '',
                publisher TEXT NOT NULL DEFAULT '',
                edition TEXT NOT NULL DEFAULT '',
                publication_year INTEGER,
                game_system TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS book_tags (
                path TEXT NOT NULL,
                tag TEXT NOT NULL COLLATE NOCASE,
                PRIMARY KEY(path, tag)
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_book_tags_tag ON book_tags(tag)"
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS collections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                created_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS collection_books (
                collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY(collection_id, path)
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_collection_books_path ON collection_books(path)"
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
            SELECT b.*, COALESCE(m.display_title, '') AS display_title
            FROM books b
            LEFT JOIN book_metadata m ON m.path = b.path
            WHERE b.library_root = ? AND b.status = 'available'
            ORDER BY COALESCE(NULLIF(m.display_title, ''), b.title) COLLATE NOCASE, b.path
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


    @staticmethod
    def _text(value: str, field: str, maximum: int) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be text")
        value = value.strip()
        if len(value) > maximum:
            raise ValueError(f"{field} must be at most {maximum} characters")
        return value

    @classmethod
    def _clean_tags(cls, tags) -> list[str]:
        if tags is None:
            return []
        if isinstance(tags, str):
            raise ValueError("Tags must be a sequence of names")
        result: dict[str, str] = {}
        for item in tags:
            label = cls._text(item, "Tag", 80)
            if label:
                result.setdefault(label.casefold(), label)
        if len(result) > 100:
            raise ValueError("A book may have at most 100 tags")
        return list(result.values())

    def get_metadata(self, path: Path | str) -> dict:
        """Personal book metadata. Nothing is written to or read from PDF metadata."""
        row = self.connection.execute(
            """
            SELECT display_title, authors, publisher, edition, publication_year,
                   game_system, description, notes
            FROM book_metadata WHERE path = ?
            """,
            (str(path),),
        ).fetchone()
        data = dict(row) if row else {
            "display_title": "", "authors": "", "publisher": "",
            "edition": "", "publication_year": None, "game_system": "",
            "description": "", "notes": "",
        }
        data["tags"] = self.get_book_tags(path)
        return data

    def save_metadata(
        self, path: Path | str, *, display_title: str = "",
        authors: str = "", publisher: str = "", edition: str = "",
        publication_year: int | None = None, game_system: str = "",
        description: str = "", notes: str = "", tags=None,
    ) -> None:
        fields = {
            "display_title": self._text(display_title, "Display title", 350),
            "authors": self._text(authors, "Authors", 500),
            "publisher": self._text(publisher, "Publisher", 250),
            "edition": self._text(edition, "Edition", 150),
            "game_system": self._text(game_system, "Game system", 150),
            "description": self._text(description, "Description", 12000),
            "notes": self._text(notes, "Notes", 20000),
        }
        if publication_year in ("", 0):
            publication_year = None
        if publication_year is not None:
            try:
                publication_year = int(publication_year)
            except (TypeError, ValueError) as exc:
                raise ValueError("Publication year must be a valid year") from exc
            if publication_year < 1000 or publication_year > 2100:
                raise ValueError("Publication year must be 1000-2100 or blank")
        clean_tags = self._clean_tags(tags) if tags is not None else None

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO book_metadata (
                    path, display_title, authors, publisher, edition,
                    publication_year, game_system, description, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    display_title=excluded.display_title,
                    authors=excluded.authors,
                    publisher=excluded.publisher,
                    edition=excluded.edition,
                    publication_year=excluded.publication_year,
                    game_system=excluded.game_system,
                    description=excluded.description,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    str(path), fields["display_title"], fields["authors"],
                    fields["publisher"], fields["edition"], publication_year,
                    fields["game_system"], fields["description"], fields["notes"],
                    _utc_now(),
                ),
            )
            if clean_tags is not None:
                self._replace_tags(path, clean_tags)

    def _replace_tags(self, path: Path | str, tags: list[str]) -> None:
        self.connection.execute(
            "DELETE FROM book_tags WHERE path = ?", (str(path),)
        )
        self.connection.executemany(
            "INSERT INTO book_tags(path, tag) VALUES (?, ?)",
            [(str(path), tag) for tag in tags],
        )

    def set_book_tags(self, path: Path | str, tags) -> None:
        clean_tags = self._clean_tags(tags)
        with self.connection:
            self._replace_tags(path, clean_tags)

    def get_book_tags(self, path: Path | str) -> list[str]:
        return [
            r["tag"] for r in self.connection.execute(
                "SELECT tag FROM book_tags WHERE path=? ORDER BY tag COLLATE NOCASE",
                (str(path),),
            )
        ]

    def list_tags(self, library_root: Path | str) -> list[str]:
        """Show only tags on currently available books within the selected root."""
        rows = self.connection.execute(
            """
            SELECT MIN(t.tag) AS tag FROM book_tags t
            JOIN books b ON b.path=t.path
            WHERE b.library_root=? AND b.status='available'
            GROUP BY t.tag COLLATE NOCASE ORDER BY tag COLLATE NOCASE
            """,
            (str(library_root),),
        )
        return [row["tag"] for row in rows]

    def paths_for_tag(self, tag: str) -> set[str]:
        rows = self.connection.execute(
            "SELECT path FROM book_tags WHERE tag=? COLLATE NOCASE",
            (tag,),
        )
        return {r["path"] for r in rows}

    def list_collections(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT id, name FROM collections ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
        return [dict(row) for row in rows]

    def create_collection(self, name: str) -> int:
        name = self._text(name, "Collection name", 120)
        if not name:
            raise ValueError("Collection name cannot be blank")
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO collections(name, created_at) VALUES (?, ?)",
                (name, _utc_now()),
            )
        return int(cursor.lastrowid)

    def rename_collection(self, collection_id: int, name: str) -> None:
        name = self._text(name, "Collection name", 120)
        if not name:
            raise ValueError("Collection name cannot be blank")
        with self.connection:
            row = self.connection.execute(
                "UPDATE collections SET name = ? WHERE id = ?",
                (name, int(collection_id)),
            )
        if row.rowcount != 1:
            raise ValueError("Collection does not exist")

    def delete_collection(self, collection_id: int) -> bool:
        """Delete a virtual collection only. Source files are never touched."""
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM collections WHERE id = ?", (int(collection_id),)
            )
        return cursor.rowcount == 1

    def get_collection_paths(self, collection_id: int) -> set[str]:
        rows = self.connection.execute(
            "SELECT path FROM collection_books WHERE collection_id=?",
            (int(collection_id),),
        )
        return {row["path"] for row in rows}

    def get_book_collections(self, path: Path | str) -> set[int]:
        return {
            int(row["collection_id"])
            for row in self.connection.execute(
                "SELECT collection_id FROM collection_books WHERE path=?",
                (str(path),),
            )
        }

    def set_book_collections(self, path: Path | str, collection_ids) -> None:
        """Replace the book's memberships without affecting PDF or other books."""
        ids = {int(value) for value in collection_ids}
        if ids and any(value <= 0 for value in ids):
            raise ValueError("Invalid collection ID")
        with self.connection:
            if ids:
                placeholders = ", ".join("?" for _ in ids)
                valid = {
                    row["id"] for row in self.connection.execute(
                        f"SELECT id FROM collections WHERE id IN ({placeholders})",
                        tuple(ids),
                    )
                }
                if valid != ids:
                    raise ValueError("One or more collections no longer exist")
            self.connection.execute(
                "DELETE FROM collection_books WHERE path=?", (str(path),)
            )
            self.connection.executemany(
                """
                INSERT INTO collection_books(collection_id, path, added_at)
                VALUES (?, ?, ?)
                """,
                [(value, str(path), _utc_now()) for value in sorted(ids)],
            )
