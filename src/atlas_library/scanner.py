"""Incremental, read-only PDF discovery. UI runs it in a worker thread."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .catalog import Catalog
from .covers import cover_for_pdf


def scan_library(
    root: Path,
    database_path: Path,
    cache_dir: Path,
) -> Iterator[dict]:
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Library folder not found: {root}")

    catalog = Catalog(database_path)
    seen: set[str] = set()
    try:
        for path in sorted(root.rglob("*"), key=lambda p: str(p).casefold()):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".pdf":
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            absolute = str(path.resolve())
            seen.add(absolute)
            previous = catalog.get(absolute)
            existing_cover = bool(
                previous and previous["cover_path"] and Path(previous["cover_path"]).is_file()
            )
            unchanged = bool(
                previous
                and previous["size_bytes"] == stat.st_size
                and previous["modified_ns"] == stat.st_mtime_ns
                and (existing_cover or bool(previous["scan_error"]))
            )
            if unchanged:
                pages = previous["page_count"]
                cover = previous["cover_path"]
                error = previous["scan_error"]
            else:
                try:
                    pages, cover = cover_for_pdf(
                        path, cache_dir, stat.st_size, stat.st_mtime_ns
                    )
                    error = ""
                except Exception as exc:
                    pages, cover = None, ""
                    error = f"{type(exc).__name__}: {exc}"[:400]

            record = catalog.upsert(
                path=absolute,
                library_root=root,
                title=path.stem.replace("_", " ").strip(),
                size_bytes=stat.st_size,
                modified_ns=stat.st_mtime_ns,
                page_count=pages,
                cover_path=cover,
                scan_error=error,
            )
            yield record

        # This executes only if the scan completes, not when cancelled.
        catalog.mark_missing_except(root, seen)
    finally:
        catalog.close()
