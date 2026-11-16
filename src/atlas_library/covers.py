"""Render and cache covers without modifying original PDF files."""

from __future__ import annotations

import hashlib
from pathlib import Path


def cover_for_pdf(path: Path, cache_dir: Path, size_bytes: int, modified_ns: int) -> tuple[int | None, str]:
    import pypdfium2 as pdfium

    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(
        f"{path}|{size_bytes}|{modified_ns}".encode("utf-8")
    ).hexdigest()
    target = cache_dir / f"{key}.png"

    document = pdfium.PdfDocument(str(path))
    try:
        pages = len(document)
        if not pages:
            return 0, ""
        if not target.is_file():
            page = document[0]
            try:
                bitmap = page.render(scale=0.65)
                try:
                    image = bitmap.to_pil().copy()
                    image.thumbnail((240, 340))
                    image.save(target, format="PNG")
                finally:
                    bitmap.close()
            finally:
                page.close()
        return pages, str(target)
    finally:
        document.close()
