"""Safe presentation of indexed PDF search excerpts.

The bracketed terms come from SQLite FTS5 snippet() or our regex snippet
builder. This is *not* an overlay positioned inside a scanned PDF image.
"""

from __future__ import annotations

import html
import re


SOURCE_LABELS = {
    "text": "Selectable PDF text",
    "ocr": "Offline OCR",
    "mixed": "PDF text + offline OCR",
    "empty": "No indexed text",
}


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, "Indexed PDF text")


def format_excerpt_html(excerpt: str) -> str:
    """Escape all source text and emphasize [FTS/regex marked] spans safely."""
    excerpt = excerpt[:3000]
    tokens = re.split(r"(\[[^\]\r\n]{1,350}\])", excerpt)
    parts = []
    for token in tokens:
        if token.startswith("[") and token.endswith("]") and len(token) > 2:
            parts.append(
                '<span style="background-color:#766038; color:#fff3d7;'
                ' font-weight:600;">'
                + html.escape(token[1:-1])
                + "</span>"
            )
        else:
            parts.append(html.escape(token))
    return "".join(parts).replace("\n", "<p></p>")


def context_for_book(results: list[dict], path: str) -> list[dict]:
    """Normalize a page-result set for one book; leave original rows untouched."""
    return [
        {
            "path": row["path"],
            "page_index": max(0, int(row["page_index"])),
            "source": str(row.get("source") or "text"),
            "excerpt": str(row.get("excerpt") or "(No excerpt available)"),
        }
        for row in results
        if row.get("path") == path
    ]
