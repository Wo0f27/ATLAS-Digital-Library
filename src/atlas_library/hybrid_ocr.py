"""Optional OCR of large raster regions on pages that already have PDF text.

The image heuristic is intentionally conservative. It does not infer the
presence of words: it only identifies pages worth an opt-in OCR pass.
"""

from __future__ import annotations

import re

import pypdfium2 as pdfium


def large_embedded_image(page, threshold: float = 0.10) -> bool:
    """Detect substantial raster page objects, including within form XObjects.

    The page/object bounds are PDF-space points, not raster pixel dimensions.
    A page-wide illustration also qualifies; this function is a candidate
    detector, not a classifier that guarantees an image contains text.
    """
    width, height = page.get_size()
    area = float(width) * float(height)
    if area <= 0:
        return False
    for image in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
        try:
            left, bottom, right, top = image.get_bounds()
            clipped_width = max(0.0, min(float(right), width) - max(float(left), 0.0))
            clipped_height = max(0.0, min(float(top), height) - max(float(bottom), 0.0))
            if clipped_width * clipped_height >= area * threshold:
                return True
        except (ValueError, RuntimeError):
            continue
    return False


def _normalized(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold(), flags=re.UNICODE))


def merge_ocr_text(native_text: str, recognized_text: str) -> tuple[str, int]:
    """Keep PDF text and append OCR lines only when not already represented.

    This is deliberately a conservative textual deduplication. It avoids exact
    duplicate lines even across different wrapping/punctuation, but may retain
    near-duplicates containing OCR mistakes. No source PDF is ever modified.
    """
    native = native_text.strip()
    normalized_existing = _normalized(native)
    additions: list[str] = []
    for line in recognized_text.replace("\r\n", "\n").splitlines():
        line = line.strip()
        normalized_line = _normalized(line)
        if not normalized_line:
            continue
        if normalized_line in normalized_existing:
            continue
        additions.append(line)
        normalized_existing = (
            normalized_existing + " " + normalized_line
        ).strip()
    if not additions:
        return native, 0
    return (native + "\n" if native else "") + "\n".join(additions), len(additions)
