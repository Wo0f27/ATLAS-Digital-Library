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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pypdfium2 as pdfium

from .hybrid_ocr import large_embedded_image, merge_ocr_text
from .region_ocr import extract_small_region_text


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

            CREATE TABLE IF NOT EXISTS hybrid_ocr_pages (
                path TEXT NOT NULL,
                page_index INTEGER NOT NULL,
                status TEXT NOT NULL,
                last_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(path, page_index)
            );

            CREATE TABLE IF NOT EXISTS small_region_ocr_pages (
                path TEXT NOT NULL,
                page_index INTEGER NOT NULL,
                status TEXT NOT NULL,
                last_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(path, page_index)
            );

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
                "DELETE FROM hybrid_ocr_pages WHERE path=?", (str(path),)
            )
            self.connection.execute(
                "DELETE FROM small_region_ocr_pages WHERE path=?", (str(path),)
            )
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

    def hybrid_pending(self, path: Path, before_page: int) -> list[int]:
        """Already indexed native-text pages not yet checked in mixed OCR mode."""
        rows = self.connection.execute(
            """
            SELECT p.page_index
            FROM page_text p
            LEFT JOIN hybrid_ocr_pages h
              ON h.path=p.path AND h.page_index=p.page_index
            WHERE p.path=? AND p.page_index < ? AND p.source='text'
              AND (h.status IS NULL OR h.status='error')
            ORDER BY p.page_index
            """,
            (str(path), int(before_page)),
        )
        return [int(row["page_index"]) for row in rows]

    def small_region_pending(self, path: Path, before_page: int) -> list[int]:
        """Previously indexed text/mixed pages without a finished small-region pass."""
        rows = self.connection.execute(
            """
            SELECT p.page_index
            FROM page_text p
            LEFT JOIN small_region_ocr_pages r
              ON r.path=p.path AND r.page_index=p.page_index
            WHERE p.path=? AND p.page_index < ?
              AND p.source IN ('text', 'mixed')
              AND (r.status IS NULL OR r.status='error')
            ORDER BY p.page_index
            """,
            (str(path), int(before_page)),
        )
        return [int(row["page_index"]) for row in rows]

    def mark_small_region(
        self, path: Path, page_index: int, status: str, error: str = "",
    ) -> None:
        if status not in ("done", "no_image", "no_new_text", "error"):
            raise ValueError("Invalid small-region OCR status")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO small_region_ocr_pages(
                    path, page_index, status, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(path,page_index) DO UPDATE SET
                    status=excluded.status,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (str(path), int(page_index), status, error[:400], _now()),
            )

    def page_record(self, path: Path, page_index: int) -> dict | None:
        row = self.connection.execute(
            "SELECT text, source FROM page_text WHERE path=? AND page_index=?",
            (str(path), int(page_index)),
        ).fetchone()
        return dict(row) if row else None

    def mark_hybrid(
        self, path: Path, page_index: int, status: str, error: str = ""
    ) -> None:
        if status not in ("done", "no_image", "no_new_text", "error"):
            raise ValueError("Unknown hybrid OCR status")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO hybrid_ocr_pages (
                    path, page_index, status, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(path, page_index) DO UPDATE SET
                    status=excluded.status,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (str(path), int(page_index), status, error[:400], _now()),
            )

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

    @contextmanager
    def scoped_paths(self, allowed_paths: set[str] | None):
        """Optional per-query whitelist of cataloged paths, with no query-size limit."""
        self.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS atlas_search_scope(path TEXT PRIMARY KEY)"
        )
        self.connection.execute("DELETE FROM atlas_search_scope")
        try:
            if allowed_paths is not None:
                self.connection.executemany(
                    "INSERT INTO atlas_search_scope(path) VALUES (?)",
                    [(p,) for p in sorted(allowed_paths)],
                )
            yield
        finally:
            self.connection.execute("DELETE FROM atlas_search_scope")

    def search(
        self, query: str, root: Path | str, mode: str = "words", limit: int = 150,
        allowed_paths: set[str] | None = None,
    ) -> list[dict]:
        expression = self._match_expression(query, mode)
        if not expression:
            return []
        try:
            with self.scoped_paths(allowed_paths):
                rows = self.connection.execute(
                    """
                    SELECT p.path, p.page_index, p.source,
                           snippet(page_fts, 0, '[', ']', '…', 23) AS excerpt,
                           bm25(page_fts) AS relevance
                    FROM page_fts
                    JOIN page_text p ON p.id=page_fts.rowid
                    JOIN indexed_files f ON f.path=p.path
                    WHERE page_fts MATCH ? AND f.library_root=?
                      AND (?=0 OR p.path IN (SELECT path FROM atlas_search_scope))
                    ORDER BY bm25(page_fts), p.path, p.page_index
                    LIMIT ?
                    """,
                    (expression, str(root), int(allowed_paths is not None),
                     min(500, max(1, int(limit)))),
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
    mixed_ocr: bool = False,
    image_detector: Callable[[object], bool] = large_embedded_image,
    small_region_ocr: bool = False,
    region_reader: Callable[[object, Path], tuple[list[str], int]] = extract_small_region_text,
) -> dict:
    """Resumable indexing with independent large-image and small-region passes."""
    if mixed_ocr and small_region_ocr:
        raise ValueError("Choose either mixed-page or small-region OCR per pass")
    path = Path(path).resolve()
    root = Path(root).resolve()
    stat = path.stat()
    document = pdfium.PdfDocument(str(path))
    try:
        count = len(document)
        start = repository.prepare(path, root, stat.st_size, stat.st_mtime_ns, count)
        pending_before = repository.pending_pages(path)
        wants_mixed = bool(mixed_ocr and ocr_binary is not None)
        mixed_old = (
            repository.hybrid_pending(path, start) if wants_mixed else []
        )
        wants_small = bool(small_region_ocr and ocr_binary is not None)
        small_old = repository.small_region_pending(path, start) if wants_small else []

        if start >= count and not (
            (ocr_binary is not None and pending_before) or mixed_old or small_old
        ):
            final = repository.finish(path)
            return {
                "pages": count, "indexed": 0, "mixed_checked": 0,
                "mixed_added": 0, "region_checked": 0,
                "region_candidates": 0, "region_added": 0,
                "region_failed": 0, **final,
            }

        candidates = set(mixed_old) | set(small_old)
        if start >= count:
            candidates |= set(pending_before if ocr_binary else [])
        else:
            candidates |= set(range(start, count))
        indices = sorted(candidates)

        indexed = 0
        mixed_checked = 0
        mixed_added = 0
        region_checked = 0
        region_candidates = 0
        region_added = 0
        region_failed = 0
        for page_index in indices:
            if cancel and cancel():
                raise IndexCancelled()
            text = ""
            source = "text"
            error = ""
            page = None
            text_page = None
            prior = repository.page_record(path, page_index) if page_index < start else None
            is_old_mixed = bool(prior and page_index in mixed_old)
            is_old_small = bool(prior and page_index in small_old)
            should_update_text = not (is_old_mixed or is_old_small)
            hybrid_status = None
            hybrid_error = ""
            region_status = None
            region_error = ""
            try:
                page = document[page_index]
                if is_old_mixed or is_old_small:
                    text = prior["text"]
                    source = prior["source"]
                else:
                    text_page = page.get_textpage()
                    text = text_page.get_text_bounded().replace("\r\n", "\n").strip()
                if not has_meaningful_text(text) and not (is_old_mixed or is_old_small):
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
                elif wants_mixed and has_meaningful_text(text) and source == "text":
                    try:
                        is_candidate = image_detector(page)
                        mixed_checked += 1
                        if is_candidate:
                            recognized = ocr(page, ocr_binary)
                            combined, added_lines = merge_ocr_text(text, recognized)
                            if added_lines:
                                text = combined
                                source = "mixed"
                                mixed_added += 1
                                should_update_text = True
                            hybrid_status = "done" if added_lines else "no_new_text"
                        else:
                            hybrid_status = "no_image"
                    except Exception as exc:
                        # Preserve the pre-existing searchable native text.
                        hybrid_status = "error"
                        hybrid_error = f"{type(exc).__name__}: {exc}"
                        error = f"Mixed OCR page {page_index + 1}: {exc}"
                elif wants_small and has_meaningful_text(text) and source in ("text", "mixed"):
                    try:
                        region_checked += 1
                        recognized_chunks, region_count = region_reader(page, ocr_binary)
                        region_candidates += region_count
                        recognized = "\n".join(recognized_chunks)
                        merged, new_lines = merge_ocr_text(text, recognized)
                        if new_lines:
                            text = merged
                            source = "mixed"
                            region_added += 1
                            should_update_text = True
                        region_status = (
                            "done" if new_lines else
                            ("no_new_text" if region_count else "no_image")
                        )
                    except Exception as exc:
                        # Never overwrite native text when a crop or OCR step fails.
                        region_status = "error"
                        region_failed += 1
                        region_error = f"{type(exc).__name__}: {exc}"
                        error = f"Region OCR page {page_index + 1}: {exc}"
            except Exception as exc:
                if not (is_old_mixed or is_old_small):
                    source = "error"
                    error = f"Text page {page_index + 1}: {exc}"
                elif is_old_small:
                    error = f"Region OCR page {page_index + 1}: {exc}"
                    region_status, region_error = "error", error
                    region_failed += 1
                else:
                    error = f"Mixed OCR page {page_index + 1}: {exc}"
                    hybrid_status = "error"
                    hybrid_error = error
            finally:
                if text_page is not None:
                    text_page.close()
                if page is not None:
                    page.close()

            if should_update_text:
                repository.put_page(path, page_index, text, source, error)
            if hybrid_status is not None:
                # Searchable text commits before completion checkpoints.
                repository.mark_hybrid(
                    path, page_index, hybrid_status, hybrid_error,
                )
            if region_status is not None:
                repository.mark_small_region(
                    path, page_index, region_status, region_error,
                )
            indexed += 1
            if progress:
                progress(page_index + 1, count, source)
        final = repository.finish(path)
        return {
            "pages": count, "indexed": indexed,
            "mixed_checked": mixed_checked, "mixed_added": mixed_added,
            "region_checked": region_checked,
            "region_candidates": region_candidates,
            "region_added": region_added,
            "region_failed": region_failed, **final,
        }
    finally:
        document.close()
