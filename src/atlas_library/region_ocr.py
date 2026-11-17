"""Opt-in OCR of small raster image regions inside text-readable PDF pages.

The normal mixed-content pass checks large pictures (roughly >= 10% of a page).
This conservative extra pass crops raster objects between 0.3% and 10% of a
page for additional OCR without processing the entire text-rich page again.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Callable

import pypdfium2 as pdfium


def small_image_regions(
    page, *, min_fraction: float = 0.003,
    max_fraction: float = 0.10, max_regions: int = 6,
) -> list[tuple[float, float, float, float]]:
    """Return clipped (left,bottom,right,top) raster-object bounds in PDF points.

    This is a candidate heuristic, not a text detector. Limits avoid OCR of
    tiny decorative icons and excessive work on heavily illustrated pages.
    """
    page_w, page_h = page.get_size()
    page_area = float(page_w) * float(page_h)
    if page_area <= 0:
        return []
    regions = []
    for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
        try:
            left, bottom, right, top = obj.get_bounds()
            left, right = max(0.0, float(left)), min(float(page_w), float(right))
            bottom, top = max(0.0, float(bottom)), min(float(page_h), float(top))
            width, height = right - left, top - bottom
            if width < 18 or height < 12:
                continue
            portion = width * height / page_area
            if min_fraction <= portion < max_fraction:
                regions.append((left, bottom, right, top))
        except (ValueError, RuntimeError, TypeError):
            continue
    regions.sort(key=lambda b: (-(b[2]-b[0])*(b[3]-b[1]), b[1], b[0]))
    return regions[:max_regions]


def extract_small_region_text(
    page, executable: Path, language: str = "eng",
    *, recognizer: Callable[[Path, Path, str], str] | None = None,
) -> tuple[list[str], int]:
    """Render the PDF page once; OCR only bounded small-image crops.

    Coordinates in PDFium are bottom-left-origin PDF points. The rendered
    PIL page is top-left-origin, so image-region y coordinates are inverted.
    The PDF itself is never altered. One temporary crop exists at a time.
    """
    candidates = small_image_regions(page)
    if not candidates:
        return [], 0

    if recognizer is None:
        recognizer = _recognize_crop
    page_width, page_height = page.get_size()
    scale = min(3.0, 3400.0 / max(float(page_width), float(page_height), 1.0))
    bitmap = page.render(scale=scale)
    try:
        image = bitmap.to_pil().copy().convert("RGB")
    finally:
        bitmap.close()
    extracted = []
    try:
        real_scale_x = image.width / float(page_width)
        real_scale_y = image.height / float(page_height)
        for left, bottom, right, top in candidates:
            box = (
                max(0, int(left * real_scale_x)),
                max(0, int((page_height-top) * real_scale_y)),
                min(image.width, int(right * real_scale_x + 0.999)),
                min(image.height, int((page_height-bottom) * real_scale_y + 0.999)),
            )
            if box[2]-box[0] < 30 or box[3]-box[1] < 25:
                continue
            crop = image.crop(box)
            try:
                with tempfile.TemporaryDirectory(prefix="atlas-region-") as folder:
                    file = Path(folder) / "region.png"
                    crop.save(file, "PNG")
                    recognized = recognizer(file, executable, language).strip()
                if recognized and sum(c.isalnum() for c in recognized) >= 5:
                    extracted.append(recognized.replace("\r\n", "\n"))
            finally:
                crop.close()
        return extracted, len(candidates)
    finally:
        image.close()


def _recognize_crop(file: Path, executable: Path, language: str) -> str:
    completed = subprocess.run(
        [str(executable), str(file), "stdout", "-l", language, "--psm", "6"],
        capture_output=True, encoding="utf-8", errors="replace",
        timeout=45, check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            (completed.stderr or "Tesseract region OCR failed")[:350]
        )
    return completed.stdout
