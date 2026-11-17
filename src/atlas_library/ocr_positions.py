"""On-demand, offline OCR word positions for scanned PDF page previews.

Keeps word boxes in the existing local search SQLite database without changing
the FTS5 text layer, PDF source files, or previously indexed OCR text.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pypdfium2 as pdfium


class PositionCancelled(Exception):
    """The user stopped a word-position lookup."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_word(text: str) -> str:
    return "".join(re.findall(r"\w+", text.casefold(), flags=re.UNICODE))


def parse_tsv_words(tsv: str, image_width: int, image_height: int) -> list[dict]:
    """Convert Tesseract TSV word boxes to normalized [0, 1] page positions."""
    if image_width < 1 or image_height < 1:
        raise ValueError("Invalid OCR image dimensions")
    results = []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    for row in reader:
        try:
            if row.get("level") != "5":
                continue
            label = (row.get("text") or "").strip()
            confidence = float(row.get("conf") or "-1")
            left, top = int(row["left"]), int(row["top"])
            width, height = int(row["width"]), int(row["height"])
            if not label or confidence < 20 or width <= 0 or height <= 0:
                continue
            x1 = min(image_width, max(0, left))
            y1 = min(image_height, max(0, top))
            x2 = min(image_width, max(0, left + width))
            y2 = min(image_height, max(0, top + height))
            if x2 <= x1 or y2 <= y1:
                continue
            results.append({
                "text": label[:150],
                "x": round(x1 / image_width, 7),
                "y": round(y1 / image_height, 7),
                "w": round((x2 - x1) / image_width, 7),
                "h": round((y2 - y1) / image_height, 7),
            })
            if len(results) >= 15000:
                break
        except (KeyError, ValueError, OverflowError):
            continue
    return results


def match_word_indices(words: list[dict], excerpt: str) -> list[int]:
    """Match only bracket-marked excerpt terms, never arbitrary page words."""
    phrases = re.findall(r"\[([^\]\r\n]{1,350})\]", excerpt)
    sequences = [
        [_clean_word(token) for token in re.findall(r"\S+", phrase)]
        for phrase in phrases
    ]
    sequences = [[token for token in seq if token] for seq in sequences]
    normalized_words = [_clean_word(row["text"]) for row in words]
    result = set()
    for sequence in sequences:
        if not sequence:
            continue
        size = len(sequence)
        for pos in range(0, len(normalized_words) - size + 1):
            if normalized_words[pos: pos + size] == sequence:
                result.update(range(pos, pos + size))
    return sorted(result)


def _run_tesseract(
    image_path: Path, executable: Path, language: str,
    cancel: Callable[[], bool] | None,
) -> str:
    """Read TSV with cancellation support, and always terminate subprocesses."""
    command = [
        str(executable), str(image_path), "stdout",
        "-l", language, "--psm", "3", "tsv",
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    deadline = time.monotonic() + 90
    try:
        while True:
            if cancel and cancel():
                raise PositionCancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError("Word-position OCR exceeded 90 seconds")
            try:
                output, errors = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode != 0:
            raise RuntimeError(
                errors.decode("utf-8", errors="replace")[:350]
                or "Tesseract returned an error"
            )
        return output.decode("utf-8-sig", errors="replace")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


class PagePositionIndex:
    """Local OCR word boxes, invalidated when the source file changes."""

    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), timeout=25)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=25000")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ocr_position_pages (
                path TEXT NOT NULL,
                page_index INTEGER NOT NULL,
                language TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                word_boxes TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(path, page_index, language)
            )
            """
        )
        self.connection.commit()

    def close(self):
        self.connection.close()

    def saved_words(
        self, path: Path, page_index: int, language: str,
        size_bytes: int, modified_ns: int,
    ) -> list[dict] | None:
        row = self.connection.execute(
            """
            SELECT word_boxes, size_bytes, modified_ns FROM ocr_position_pages
            WHERE path=? AND page_index=? AND language=?
            """,
            (str(path), page_index, language),
        ).fetchone()
        if not row:
            return None
        if row["size_bytes"] != size_bytes or row["modified_ns"] != modified_ns:
            return None
        return json.loads(row["word_boxes"])

    def _save(
        self, path: Path, page_index: int, language: str,
        size_bytes: int, modified_ns: int, words: list[dict],
    ):
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO ocr_position_pages (
                    path, page_index, language, size_bytes, modified_ns,
                    word_boxes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path, page_index, language) DO UPDATE SET
                    size_bytes=excluded.size_bytes,
                    modified_ns=excluded.modified_ns,
                    word_boxes=excluded.word_boxes,
                    updated_at=excluded.updated_at
                """,
                (
                    str(path), page_index, language, size_bytes,
                    modified_ns, json.dumps(words, ensure_ascii=False), _now(),
                ),
            )

    def locate(
        self, path: Path, page_index: int, language: str,
        executable: Path | None, *,
        cancel: Callable[[], bool] | None = None,
        ocr_tsv: Callable = _run_tesseract,
    ) -> dict:
        """Render a page and retrieve or recognize its word boxes.

        Existing cached positions require no Tesseract installation. The PDF
        is opened for reading only; its bytes and the FTS5 text are untouched.
        """
        path = Path(path).resolve()
        language = language.strip().lower()
        if not re.fullmatch(r"[a-z0-9_]+(?:\+[a-z0-9_]+)*", language):
            raise ValueError("Invalid Tesseract language code")
        if cancel and cancel():
            raise PositionCancelled()
        initial = path.stat()
        document = pdfium.PdfDocument(str(path))
        try:
            if not 0 <= page_index < len(document):
                raise ValueError("Requested PDF page is out of range")
            page = document[page_index]
            try:
                page_width, page_height = page.get_size()
                largest = max(page_width, page_height)
                scale = min(2.0, 2600.0 / max(1.0, largest))
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil().copy().convert("RGB")
                finally:
                    bitmap.close()
                try:
                    stream = io.BytesIO()
                    image.save(stream, format="PNG")
                    preview = stream.getvalue()
                    width, height = image.size
                finally:
                    image.close()
            finally:
                page.close()
        finally:
            document.close()

        if cancel and cancel():
            raise PositionCancelled()
        words = self.saved_words(
            path, page_index, language,
            initial.st_size, initial.st_mtime_ns,
        )
        cached = words is not None
        if words is None:
            if executable is None or not Path(executable).is_file():
                raise FileNotFoundError(
                    "Tesseract is needed to locate OCR words for this page. "
                    "Select a local tesseract.exe in Full-Text Search."
                )
            with tempfile.TemporaryDirectory(prefix="atlas-wordpos-") as folder:
                png = Path(folder) / "page.png"
                png.write_bytes(preview)
                tsv = ocr_tsv(png, Path(executable), language, cancel)
            words = parse_tsv_words(tsv, width, height)
            if cancel and cancel():
                raise PositionCancelled()
            after = path.stat()
            if (after.st_size, after.st_mtime_ns) != (
                initial.st_size, initial.st_mtime_ns
            ):
                raise RuntimeError(
                    "The sourcebook changed during OCR. No word positions were saved."
                )
            self._save(
                path, page_index, language,
                initial.st_size, initial.st_mtime_ns, words,
            )
        return {
            "image": preview, "width": width, "height": height,
            "words": words, "cached": cached, "page_index": page_index,
        }
