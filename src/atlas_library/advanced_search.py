"""Advanced, offline search helpers for A.T.L.A.S. Digital Library.

Fuzzy search finds close words in the local FTS5 vocabulary; this is not semantic
search. Regular expressions run with per-page timeouts and a whole-query budget,
so badly shaped patterns cannot freeze the application indefinitely.
"""

from __future__ import annotations

import difflib
import re
import time
from pathlib import Path
from typing import Callable

import regex

from .fulltext import IndexCancelled, PageIndex


def _excerpt(text: str, start: int, end: int, *, context: int = 95) -> str:
    left = max(0, start - context)
    right = min(len(text), end + context)
    before = ("…" if left else "") + text[left:start]
    matched = text[start:end]
    after = text[end:right] + ("…" if right < len(text) else "")
    return (before + "[" + matched + "]" + after).replace("\r", " ").replace("\n", " ")


class AdvancedSearcher:
    def __init__(self, index: PageIndex):
        self.index = index

    def fuzzy(
        self,
        query: str,
        root: Path | str,
        *,
        allowed_paths: set[str] | None = None,
        limit: int = 150,
    ) -> tuple[list[dict], str]:
        """Single approximate word, edit-distance-like candidate matching.

        Candidates are limited to a same-first-letter vocabulary slice to keep
        full-library queries responsive. This intentionally does not promise
        arbitrary spell correction or approximate multi-word phrases.
        """
        term = query.strip().casefold()
        if not re.fullmatch(r"[a-z][a-z0-9'-]{2,39}", term):
            raise ValueError("Fuzzy search accepts one Latin-alphabet word (3–40 characters).")
        # FTS5's unicode61 tokenizer splits punctuation; avoid mismatch.
        if not re.fullmatch(r"[a-z][a-z0-9]{2,39}", term):
            raise ValueError("Fuzzy search accepts one word without punctuation.")

        self.index.connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS atlas_terms USING fts5vocab(page_fts, 'row')"
        )
        # SQLite GLOB character classes and bounded vocabulary enumeration.
        # Wider searches are not attempted silently: the notice describes scope.
        terms = [
            row["term"]
            for row in self.index.connection.execute(
                """
                SELECT term FROM atlas_terms
                WHERE term GLOB ?
                  AND length(term) BETWEEN ? AND ?
                ORDER BY doc DESC, term
                LIMIT 10000
                """,
                (
                    term[0] + "*", max(2, len(term) - 2),
                    len(term) + 2,
                ),
            )
        ]
        matches = difflib.get_close_matches(term, terms, n=8, cutoff=0.72)
        if term in terms and term not in matches:
            matches.insert(0, term)
        if not matches:
            return [], "No similar words found in the indexed vocabulary."
        expression = " OR ".join(
            '"' + word.replace('"', '""') + '"' for word in matches
        )
        hits = self.index.search(
            expression, root, mode="advanced",
            limit=limit, allowed_paths=allowed_paths,
        )
        return hits, "Matched indexed terms: " + ", ".join(matches)

    def regex_search(
        self,
        query: str,
        root: Path | str,
        *,
        allowed_paths: set[str] | None = None,
        limit: int = 150,
        cancel: Callable[[], bool] | None = None,
        max_seconds: float = 12.0,
        max_pages: int = 50000,
    ) -> tuple[list[dict], str]:
        if not query or len(query) > 200:
            raise ValueError("Regular expression must contain 1–200 characters.")
        try:
            pattern = regex.compile(query, regex.IGNORECASE | regex.VERSION1)
        except regex.error as exc:
            raise ValueError(f"Invalid regular expression: {exc}") from exc
        # Zero-width matching is often confusing for snippets, and broad regex
        # patterns can match virtually every page. Permit but cap results.
        target = max(1, min(500, int(limit)))
        results: list[dict] = []
        scanned = 0
        timeouts = 0
        truncated_long_pages = 0
        deadline = time.monotonic() + max(0.2, float(max_seconds))
        notice = ""
        with self.index.scoped_paths(allowed_paths):
            cursor = self.index.connection.execute(
                """
                SELECT p.path, p.page_index, p.source, p.text
                FROM page_text p
                JOIN indexed_files f ON f.path=p.path
                WHERE f.library_root=?
                  AND (?=0 OR p.path IN (SELECT path FROM atlas_search_scope))
                ORDER BY p.path, p.page_index
                """,
                (str(root), int(allowed_paths is not None)),
            )
            for row in cursor:
                if cancel and cancel():
                    raise IndexCancelled()
                if scanned >= max_pages or time.monotonic() >= deadline:
                    notice = f"Search stopped after {scanned} pages (time/page limit)."
                    break
                scanned += 1
                content = row["text"]
                if len(content) > 150000:
                    truncated_long_pages += 1
                    content = content[:150000]
                try:
                    match = pattern.search(content, timeout=0.035)
                except TimeoutError:
                    timeouts += 1
                    continue
                if match:
                    results.append(
                        {
                            "path": row["path"],
                            "page_index": row["page_index"],
                            "source": row["source"],
                            "excerpt": _excerpt(content, match.start(), match.end()),
                            "relevance": 0,
                        }
                    )
                    if len(results) >= target:
                        notice = f"Showing first {target} matching pages."
                        break
        suffix = []
        if timeouts:
            suffix.append(f"{timeouts} page(s) exceeded regex timeout")
        if truncated_long_pages:
            suffix.append(f"{truncated_long_pages} very long page(s) checked only partly")
        if notice:
            suffix.insert(0, notice)
        elif not suffix:
            suffix.append(f"Scanned {scanned} indexed pages.")
        return results, " | ".join(suffix)
