"""Book gallery ordering and selected-root folder classification.

These operations affect presentation only. No original files, catalog rows,
OCR indexes, or reading-state records are moved or modified.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


ROOT_ONLY_FOLDER = "__ATLAS_DIRECT_ROOT_PDFS__"


def directly_in_root(path: str | Path, root: str | Path) -> bool:
    """True only for a file directly inside the chosen library directory."""
    try:
        relative = Path(path).relative_to(Path(root))
    except ValueError:
        return False
    return len(relative.parts) == 1


def sorted_bookshelf_entries(
    books: Iterable[dict],
    root: str | Path,
    order: str = "title",
) -> list[dict]:
    """Stable ordering with optional root-level PDFs prioritized.

    Both modes alphabetize using the user-defined display title if present.
    Ties use the actual PDF path to keep ordering deterministic.
    """
    if order not in {"title", "root_first"}:
        order = "title"

    def title_key(book: dict) -> tuple[str, str]:
        label = book.get("display_title") or book["title"]
        return str(label).casefold(), str(book["path"]).casefold()

    if order == "root_first":
        return sorted(
            books,
            key=lambda book: (
                0 if directly_in_root(book["path"], root) else 1,
                *title_key(book),
            ),
        )
    return sorted(books, key=title_key)
