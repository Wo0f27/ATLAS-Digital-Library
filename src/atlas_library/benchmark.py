"""Read-only, local performance measurements for the user's search index.

This does not open source PDFs, contact any service, or mutate index contents.
Measured query times are local observations, not promised performance figures.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

from .fulltext import PageIndex


TEST_TERMS = ("dragon", "spell", "attack", "creature", "legendary")


def benchmark_index(
    index: PageIndex,
    root: Path | str,
    *,
    terms: tuple[str, ...] = TEST_TERMS,
) -> dict:
    root = str(root)
    row = index.connection.execute(
        """
        SELECT COUNT(DISTINCT f.path) AS books,
               COUNT(p.id) AS pages,
               COALESCE(SUM(length(p.text)), 0) AS characters,
               COALESCE(SUM(CASE WHEN p.source IN ('ocr', 'mixed') THEN 1
                            ELSE 0 END), 0) AS ocr_pages,
               COALESCE(MAX(f.page_count), 0) AS largest_book_pages
        FROM indexed_files f
        LEFT JOIN page_text p ON p.path = f.path
        WHERE f.library_root = ?
        """,
        (root,),
    ).fetchone()
    storage_bytes = 0
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(index.path) + suffix)
        if path.is_file():
            storage_bytes += path.stat().st_size

    timings: list[dict] = []
    for term in terms[:12]:
        if not term or len(term) > 80:
            continue
        started = time.perf_counter()
        results = index.search(term, root, mode="words", limit=150)
        elapsed_ms = (time.perf_counter() - started) * 1000
        timings.append({
            "query": term,
            "milliseconds": round(elapsed_ms, 2),
            "result_pages": len(results),
        })
    numbers = [item["milliseconds"] for item in timings]
    return {
        "books": int(row["books"] or 0),
        "pages": int(row["pages"] or 0),
        "characters": int(row["characters"] or 0),
        "ocr_pages": int(row["ocr_pages"] or 0),
        "largest_book_pages": int(row["largest_book_pages"] or 0),
        "index_megabytes": round(storage_bytes / (1024 * 1024), 2),
        "median_ms": round(statistics.median(numbers), 2) if numbers else None,
        "slowest_ms": round(max(numbers), 2) if numbers else None,
        "timings": timings,
    }


def format_benchmark(result: dict) -> str:
    lines = [
        "LOCAL SEARCH INDEX BENCHMARK",
        f"Indexed books: {result['books']:,}",
        f"Indexed pages: {result['pages']:,}",
        f"OCR/mixed pages: {result['ocr_pages']:,}",
        f"Largest indexed book: {result['largest_book_pages']:,} pages",
        f"Text characters: {result['characters']:,}",
        f"Local search database size: {result['index_megabytes']:.2f} MiB "
        "(including SQLite WAL files)",
        f"Median sample query: {result['median_ms']} ms",
        f"Slowest sample query: {result['slowest_ms']} ms",
        "",
        "Query             Time (ms)    Matching pages shown",
    ]
    lines.extend(
        f"{item['query'][:15]:<16} {item['milliseconds']:>8.2f}    {item['result_pages']:>5}"
        for item in result["timings"]
    )
    lines.append(
        "\nSample queries are fixed, bounded to 150 results. Timings vary by "
        "computer, cache state and index size; they exclude OCR processing."
    )
    return "\n".join(lines)
