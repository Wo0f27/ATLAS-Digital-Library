"""Offline, resumable, page-level PDF index (SQLite FTS5 + optional Tesseract).

Uses a separate SQLite database outside the repository. Source PDFs are opened
read-only. Every completed page is committed separately so interrupted work can
resume safely without discarding earlier pages.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pypdfium2 as pdfium


class IndexCancelled(Exception):
    """An index job stopped at a safe page boundary."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def has_meaningful_text(text: str) -> bool:
    """Conservative heuristic for missing/near-empty PDF text layers."""
    return sum(char.isalnum() for char in text[:5000]) >= 20


def resolve_tesseract(configured: str = "") -> Path | None:
    """Find locally installed OCR; never download an engine automatically."""
    if configured:
        candidate = Path(configured).expanduser()
        return candidate if candidate.is_file() else None
    detected = shutil.which("tesseract")
    if detected:
        return Path(detected)
    for candidate in (
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    ):
        if candidate.is_file():
            return candidate
    return None


def read_ocr(page, executable: Path, language: str = "eng") -> str:
    """Render only one page at a time and OCR it through offline Tesseract."""
    bitmap = page.render(scale=2.0)
    try:
        image = bitmap.to_pil().copy().convert("RGB")
    finally:
        bitmap.close()
    try:
        with tempfile.TemporaryDirectory(prefix="atlas-ocr-") as directory:
            image_path = Path(directory) / "page.png"
            image.save(image_path, format="PNG")
            cmd = [
                str(executable), str(image_path), "stdout",
                "-l", language, "--psm", "3",
            ]
            completed = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60, check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    (completed.stderr or "Tesseract failed")[:300]
                )
            return completed.stdout.replace("\r\n", "\n").strip()
    finally:
        image.close()


class PageIndex:
    """Local page-text store. Each instance owns its SQLite connection."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), timeout=25)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=25000")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS indexed_files (
                path TEXT PRIMARY KEY,
                library_root TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                page_count INTEGER NOT NULL,
                next_page INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'partial',
                last_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS page_text (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL,
                page_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                source TEXT NOT NULL,
                UNIQUE(path, page_index)
            );

            CREATE INDEX IF NOT EXISTS idx_page_text_book
                ON page_text(path, page_index);

            CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(
                text, content='page_text', content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );

            CREATE TRIGGER IF NOT EXISTS page_text_ai AFTER INSERT ON page_text BEGIN
                INSERT INTO page_fts(rowid, text) VALUES (new.id, new.text);
            END;

            CREATE TRIGGER IF NOT EXISTS page_text_ad AFTER DELETE ON page_text BEGIN
                INSERT INTO page_fts(page_fts, rowid, text)
                VALUES ('delete', old.id, old.text);
            END;

            CREATE TRIGGER IF NOT EXISTS page_text_au AFTER UPDATE ON page_text BEGIN
                INSERT INTO page_fts(page_fts, rowid, text)
                VALUES ('delete', old.id, old.text);
                INSERT INTO page_fts(rowid, text) VALUES (new.id, new.text);
            END;
            """
        )

    def close(self) -> None:
        self.connection.close()

    def file_state(self, path: Path | str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM indexed_files WHERE path = ?", (str(path),)
        ).fetchone()
        return dict(row) if row else None

    def prepare(self, path: Path, root: Path, size: int, mtime_ns: int, pages: int) -> int:
        """Continue a matching fingerprint; otherwise replace only this book's index."""
        previous = self.file_state(path)
        unchanged = (
            previous is not None
            and previous["size_bytes"] == size
            and previous["modified_ns"] == mtime_ns
            and previous["page_count"] == pages
        )
        if unchanged:
            if previous["library_root"] != str(root):
                with self.connection:
                    self.connection.execute(
                        """
                        UPDATE indexed_files SET library_root=?, updated_at=?
                        WHERE path=?
                        """,
                        (str(root), _now(), str(path)),
                    )
            return int(previous["next_page"])
        with self.connection:
            self.connection.execute("DELETE FROM page_text WHERE path=?", (str(path),))
            self.connection.execute(
                """
                INSERT INTO indexed_files(
                    path, library_root, size_bytes, modified_ns,
                    page_count, next_page, state, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, 'partial', '', ?)
                ON CONFLICT(path) DO UPDATE SET
                    library_root=excluded.library_root,
                    size_bytes=excluded.size_bytes,
                    modified_ns=excluded.modified_ns,
                    page_count=excluded.page_count,
                    next_page=0, state='partial', last_error='',
                    updated_at=excluded.updated_at
                """,
                (str(path), str(root), size, mtime_ns, pages, _now()),
            )
        return 0

    def put_page(
        self, path: Path, page_index: int, text: str, source: str, error: str = ""
    ) -> None:
        """Atomically commit searchable text together with resumable progress."""
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO page_text(path, page_index, text, source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path, page_index) DO UPDATE SET
                    text=excluded.text, source=excluded.source
                """,
                (str(path), page_index, text, source),
            )
            self.connection.execute(
                """
                UPDATE indexed_files
                SET next_page=MAX(next_page, ?), state='partial',
                    last_error=CASE WHEN ? != '' THEN ? ELSE last_error END,
                    updated_at=?
                WHERE path=?
                """,
                (page_index + 1, error, error[:400], _now(), str(path)),
            )

    def pending_pages(self, path: Path) -> list[int]:
        return [
            int(row["page_index"])
            for row in self.connection.execute(
                """
                SELECT page_index FROM page_text
                WHERE path=? AND source='ocr_pending' ORDER BY page_index
                """,
                (str(path),),
            )
        ]

    def finish(self, path: Path) -> dict:
        pending = len(self.pending_pages(path))
        with self.connection:
            self.connection.execute(
                """
                UPDATE indexed_files
                SET state=?, updated_at=?
                WHERE path=?
                """,
                ("needs_ocr" if pending else "complete", _now(), str(path)),
            )
        return {"pending_ocr": pending, "state": "needs_ocr" if pending else "complete"}

    def stats(self, library_root: Path | str) -> dict:
        row = self.connection.execute(
            """
            SELECT
              COUNT(*) AS files,
              COALESCE(SUM(CASE WHEN state='complete' THEN 1 ELSE 0 END),0)
                AS complete,
              COALESCE(SUM(CASE WHEN state='needs_ocr' THEN 1 ELSE 0 END),0)
                AS waiting_ocr,
              COALESCE(SUM(next_page),0) AS processed_pages
            FROM indexed_files WHERE library_root=?
            """,
            (str(library_root),),
        ).fetchone()
        return dict(row)

    @staticmethod
    def _match_expression(query: str, mode: str) -> str:
        query = query.strip()
        if not query:
            return ""
        if len(query) > 500:
            raise ValueError("Search query is too long")
        if mode == "advanced":
            return query
        if mode == "phrase":
            return '"' + query.replace('"', '""') + '"'
        if mode == "words":
            tokens = re.findall(r"\w+", query, flags=re.UNICODE)
            if not tokens:
                return ""
            return " AND ".join('"' + token + '"' for token in tokens[:40])
        raise ValueError("Unknown search mode")

    def search(
        self, query: str, root: Path | str, mode: str = "words", limit: int = 150
    ) -> list[dict]:
        expression = self._match_expression(query, mode)
        if not expression:
            return []
        try:
            rows = self.connection.execute(
                """
                SELECT p.path, p.page_index, p.source,
                       snippet(page_fts, 0, '[', ']', '…', 23) AS excerpt,
                       bm25(page_fts) AS relevance
                FROM page_fts
                JOIN page_text p ON p.id=page_fts.rowid
                JOIN indexed_files f ON f.path=p.path
                WHERE page_fts MATCH ? AND f.library_root=?
                ORDER BY bm25(page_fts), p.path, p.page_index
                LIMIT ?
                """,
                (expression, str(root), min(500, max(1, int(limit)))),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise ValueError(f"Invalid FTS5 query: {exc}") from exc
        return [dict(row) for row in rows]


def index_pdf(
    repository: PageIndex,
    path: Path,
    root: Path,
    *,
    ocr_binary: Path | None = None,
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    ocr: Callable[[object, Path], str] = read_ocr,
) -> dict:
    """Resumable indexing. OCR missing pages when a local engine is available."""
    path = Path(path).resolve()
    root = Path(root).resolve()
    stat = path.stat()
    document = pdfium.PdfDocument(str(path))
    try:
        count = len(document)
        start = repository.prepare(path, root, stat.st_size, stat.st_mtime_ns, count)
        pending_before = repository.pending_pages(path)

        if start >= count and not (ocr_binary is not None and pending_before):
            final = repository.finish(path)
            return {"pages": count, "indexed": 0, **final}

        if start >= count:
            indices = pending_before
        else:
            indices = list(range(start, count))

        indexed = 0
        for page_index in indices:
            if cancel and cancel():
                raise IndexCancelled()
            text = ""
            source = "text"
            error = ""
            page = None
            text_page = None
            try:
                page = document[page_index]
                text_page = page.get_textpage()
                text = text_page.get_text_bounded().replace("\r\n", "\n").strip()
                if not has_meaningful_text(text):
                    if ocr_binary is None:
                        source = "ocr_pending"
                    else:
                        try:
                            recognized = ocr(page, ocr_binary)
                            text = (text + "\n" + recognized).strip() if text else recognized
                            source = "ocr" if text.strip() else "empty"
                        except Exception as exc:
                            source = "ocr_pending"
                            error = f"OCR page {page_index + 1}: {exc}"
            except Exception as exc:
                source = "error"
                error = f"Text page {page_index + 1}: {exc}"
            finally:
                if text_page is not None:
                    text_page.close()
                if page is not None:
                    page.close()

            repository.put_page(path, page_index, text, source, error)
            indexed += 1
            if progress:
                progress(page_index + 1, count, source)
        final = repository.finish(path)
        return {"pages": count, "indexed": indexed, **final}
    finally:
        document.close()
