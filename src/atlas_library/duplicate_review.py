"""Read-only duplicate/possible-edition review for local PDF catalogs.

Exact duplicate claims require equal full SHA-256 of the file bytes.
Similar titles are only review candidates, never deletion instructions.
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable

HEADERS = (
    "Match Type", "Review Group", "Source Path", "Relative Path", "Filename",
    "Catalog Title", "Catalog Edition", "Size Bytes", "SHA-256",
    "Review Notes", "Decision",
)


class ReviewCancelled(Exception):
    """The user interrupted the review before export."""


def _title_key(filename: str) -> str:
    stem = Path(filename).stem
    value = unicodedata.normalize("NFKC", stem).casefold()
    value = value.replace("’", "'").replace("&", " and ")
    # These are only *candidate* equivalences; a matching key proves nothing.
    value = re.sub(
        r"\b(?:5e|5th edition|pdf version\s*\d+(?:\.\d+)*|"
        r"v(?:ersion)?\s*\d+(?:\.\d+)*|rev(?:ision)?\s*\d+)\b",
        " ", value,
    )
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def _sha256(path: Path, interrupted: Callable[[], bool]) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            if interrupted():
                raise ReviewCancelled()
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _row(entry: dict, kind: str, group: str, note: str) -> dict:
    return {
        "Match Type": kind, "Review Group": group,
        "Source Path": entry["path"], "Relative Path": entry["relative"],
        "Filename": entry["filename"], "Catalog Title": entry["title"],
        "Catalog Edition": entry["edition"], "Size Bytes": entry["size"],
        "SHA-256": entry.get("sha256", ""),
        "Review Notes": note, "Decision": "PENDING",
    }


def build_duplicate_review(
    root: Path,
    books: Iterable[dict],
    interrupted: Callable[[], bool] | None = None,
) -> tuple[list[dict], dict]:
    """Only stat/read source files; no SQLite connection or source writes.

    Every catalog-snapshot mismatch appears as a separate warning row.
    All same-sized valid files are hashed to prove/deny exact equality,
    while normalized filename groups are *only* possible edition candidates.
    """
    root = Path(root).resolve()
    cancelled = interrupted or (lambda: False)
    valid: list[dict] = []
    warnings: list[dict] = []
    sizes: dict[int, list[dict]] = defaultdict(list)
    checked = 0

    for book in books:
        if cancelled():
            raise ReviewCancelled()
        source = Path(str(book["path"]))
        relative = ""
        problem = ""
        try:
            relative_path = source.relative_to(root)
            relative = relative_path.as_posix()
            if source.is_symlink():
                problem = "SYMLINK_NOT_REVIEWED"
            elif not source.is_file():
                problem = "MISSING_OR_UNREADABLE"
            else:
                state = source.stat()
                if (
                    state.st_size != int(book["size_bytes"])
                    or state.st_mtime_ns != int(book["modified_ns"])
                ):
                    problem = "CHANGED_SINCE_CATALOG_SCAN"
        except (OSError, ValueError):
            problem = "OUTSIDE_ROOT_OR_UNREADABLE"
        entry = {
            "path": str(source), "relative": relative,
            "filename": source.name,
            "title": str(book.get("display_title") or book.get("title") or source.stem),
            "edition": str(book.get("edition") or ""),
            "size": str(book.get("size_bytes") or ""),
        }
        checked += 1
        if problem:
            warnings.append(_row(entry, "SOURCE_WARNING", "", problem))
        else:
            entry["_size"] = state.st_size
            entry["_mtime_ns"] = state.st_mtime_ns
            entry["_key"] = _title_key(source.name)
            sizes[state.st_size].append(entry)
            valid.append(entry)

    hashed = 0
    for entries in sizes.values():
        if len(entries) < 2:
            continue
        for entry in entries:
            if cancelled():
                raise ReviewCancelled()
            source = Path(entry["path"])
            try:
                value = _sha256(source, cancelled)
                state = source.stat()
                if state.st_size != entry["_size"] or state.st_mtime_ns != entry["_mtime_ns"]:
                    raise OSError("source changed during hashing")
            except OSError:
                warnings.append(_row(entry, "SOURCE_WARNING", "", "CHANGED_OR_UNREADABLE_DURING_HASH"))
                valid.remove(entry)
                continue
            entry["sha256"] = value
            hashed += 1

    exact: dict[str, list[dict]] = defaultdict(list)
    for entry in valid:
        if entry.get("sha256"):
            exact[entry["sha256"]].append(entry)
    exact_groups = [group for group in exact.values() if len(group) > 1]
    exact_groups.sort(key=lambda group: min(x["relative"].casefold() for x in group))

    results: list[dict] = []
    for number, group in enumerate(exact_groups, 1):
        for entry in sorted(group, key=lambda x: x["relative"].casefold()):
            results.append(_row(
                entry, "EXACT_SHA256", f"EXACT-{number:03d}",
                "Full-file SHA-256 identical; review data/path state before removal",
            ))

    titles: dict[str, list[dict]] = defaultdict(list)
    for entry in valid:
        if entry["_key"]:
            titles[entry["_key"]].append(entry)
    possible: list[list[dict]] = []
    for group in titles.values():
        if len(group) < 2:
            continue
        hashes = {entry.get("sha256") for entry in group}
        if len(hashes) == 1 and None not in hashes:
            # Already fully explained by a confirmed exact-duplicate group.
            continue
        possible.append(group)
    possible.sort(key=lambda group: min(x["relative"].casefold() for x in group))
    for number, group in enumerate(possible, 1):
        for entry in sorted(group, key=lambda x: x["relative"].casefold()):
            results.append(_row(
                entry, "POSSIBLE_EDITION_OR_VARIANT", f"EDITION-{number:03d}",
                "Filename similarity only; inspect title pages, editions, and contents",
            ))

    results.extend(warnings)
    summary = {
        "catalog_books": checked,
        "exact_groups": len(exact_groups),
        "exact_files": sum(map(len, exact_groups)),
        "possible_edition_groups": len(possible),
        "source_warnings": len(warnings),
        "files_hashed": hashed,
    }
    return results, summary


def export_duplicate_review(destination: Path, rows: list[dict], root: Path) -> None:
    """Atomically export CSV outside the sourcebook root, formula-safe."""
    destination = Path(destination).resolve()
    root = Path(root).resolve()
    if destination == root or root in destination.parents:
        raise ValueError("Save reports outside the PDF library root.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", newline="", encoding="utf-8-sig",
            dir=destination.parent, prefix=".atlas-duplicate-", suffix=".tmp",
            delete=False,
        ) as file:
            temporary = file.name
            writer = csv.DictWriter(file, fieldnames=HEADERS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                safe = {}
                for name in HEADERS:
                    value = str(row.get(name, ""))
                    if value.lstrip().startswith(("=", "+", "-", "@")):
                        value = "'" + value
                    safe[name] = value
                writer.writerow(safe)
        os.replace(temporary, destination)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
