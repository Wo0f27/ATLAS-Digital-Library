"""Small raster-region OCR regression tests on synthetic mixed PDF pages."""

from __future__ import annotations

import gc
import zlib
from pathlib import Path

import pytest

import pypdfium2 as pdfium
from PIL import Image
from PySide6.QtWidgets import QApplication

from atlas_library.app import LibraryWindow
from atlas_library.catalog import Catalog
from atlas_library.fulltext import PageIndex, index_pdf
from atlas_library.hybrid_ocr import large_embedded_image
from atlas_library.region_ocr import extract_small_region_text, small_image_regions


# Prevent Qt's QApplication from being released while other test widgets
# remain pending for deletion during a combined Windows pytest process.
_QT_APP = None


@pytest.fixture(autouse=True)
def cleanup_native_pdf_resources():
    """Release image/PDF wrapper finalizers before the next Windows GUI test."""
    yield
    gc.collect()


def make_mixed_region_pdf(path: Path, phrase: str = "The elder wizard protects the mountain library."):
    """Real PDFium-readable PDF with selectable text and a 1.5% raster object."""
    image = Image.new("L", (360, 180), 240)
    try:
        packed = zlib.compress(image.tobytes())
    finally:
        image.close()
    stream = (
        f"BT /F1 16 Tf 52 735 Td ({phrase}) Tj ET\n"
        "q 120 0 0 60 65 570 cm /Im1 Do Q\n"
        "q 14 0 0 14 220 560 cm /Im1 Do Q\n"
    ).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Resources << /Font << /F1 5 0 R >> "
         b"/XObject << /Im1 6 0 R >> >> /Contents 4 0 R >>"),
        f"<< /Length {len(stream)} >>\nstream\n".encode()
        + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (f"<< /Type /XObject /Subtype /Image /Width 360 /Height 180 "
         f"/ColorSpace /DeviceGray /BitsPerComponent 8 "
         f"/Filter /FlateDecode /Length {len(packed)} >>\nstream\n").encode()
        + packed + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(output)


def test_small_raster_bounds_and_crop_exclude_decorative_icon(tmp_path: Path):
    pdf = tmp_path / "Hybrid.pdf"
    make_mixed_region_pdf(pdf)
    original = pdf.read_bytes()
    doc = pdfium.PdfDocument(str(pdf))
    page = doc[0]
    try:
        assert not large_embedded_image(page)  # existing 10% detector misses it
        regions = small_image_regions(page)
        assert len(regions) == 1
        assert regions[0] == (65.0, 570.0, 185.0, 630.0)
        seen = []

        def recognize(file, executable, language):
            with Image.open(file) as crop:
                seen.append((crop.width, crop.height))
            assert executable == Path("mock")
            assert language == "eng"
            return "A previously unindexed starforge inscription appears."

        texts, count = extract_small_region_text(
            page, Path("mock"), recognizer=recognize,
        )
        assert count == 1
        assert texts == ["A previously unindexed starforge inscription appears."]
        assert seen[0][0] >= 300 and seen[0][1] >= 150
    finally:
        page.close()
        doc.close()
    assert pdf.read_bytes() == original


def test_small_region_ocr_adds_only_new_text_and_checkpoints(tmp_path: Path):
    """Exercise the page/FTS/checkpoint transaction without repeat PDFium loads."""
    from atlas_library.hybrid_ocr import merge_ocr_text
    root = tmp_path / "books"
    root.mkdir()
    pdf = root / "Hybrid.pdf"
    make_mixed_region_pdf(pdf)
    original = pdf.read_bytes()
    db_path = tmp_path / "private" / "search.sqlite3"
    index = PageIndex(db_path)
    resolved = pdf.resolve()
    stat = pdf.stat()
    assert index.prepare(
        resolved, root.resolve(), stat.st_size, stat.st_mtime_ns, 1,
    ) == 0
    native = "The elder wizard protects the mountain library."
    index.put_page(resolved, 0, native, "text")
    assert index.search("wizard", root)
    assert not index.search("starforge", root)
    assert index.small_region_pending(resolved, 1) == [0]

    combined, added = merge_ocr_text(
        native,
        chr(10).join([
            "The elder wizard protects the mountain library.",
            "A hidden starforge inscription appears in a small diagram.",
        ]),
    )
    assert added == 1
    index.put_page(resolved, 0, combined, "mixed")
    index.mark_small_region(resolved, 0, "done")
    assert index.small_region_pending(resolved, 1) == []
    assert len(index.search("starforge", root)) == 1
    assert len(index.search("wizard", root)) == 1
    assert index.search("starforge", root)[0]["source"] == "mixed"
    assert index.page_record(resolved, 0)["text"].count(native) == 1
    index.close()

    reopened = PageIndex(db_path)
    assert reopened.prepare(
        resolved, root.resolve(), stat.st_size, stat.st_mtime_ns, 1,
    ) == 1
    assert reopened.small_region_pending(resolved, 1) == []
    assert len(reopened.search("starforge", root)) == 1
    assert pdf.read_bytes() == original
    reopened.close()


def test_failed_region_ocr_retries_and_does_not_erase_previous_text(tmp_path: Path):
    root = tmp_path / "books"
    root.mkdir()
    pdf = root / "Hybrid.pdf"
    make_mixed_region_pdf(pdf)
    index = PageIndex(tmp_path / "search.sqlite3")
    index_pdf(index, pdf, root)
    result = index_pdf(
        index, pdf, root, ocr_binary=Path("mock"),
        small_region_ocr=True,
        region_reader=lambda page, exe: (_ for _ in ()).throw(
            RuntimeError("OCR failed")
        ),
    )
    assert result["region_added"] == 0
    assert result["region_failed"] == 1
    assert index.search("wizard", root)
    assert index.connection.execute(
        "SELECT status FROM small_region_ocr_pages"
    ).fetchone()["status"] == "error"
    recovered = index_pdf(
        index, pdf, root, ocr_binary=Path("mock"),
        small_region_ocr=True,
        region_reader=lambda page, exe: (["A secret skyforge workshop."], 1),
    )
    assert recovered["region_added"] == 1
    assert recovered["region_failed"] == 0
    assert index.search("skyforge", root)
    assert index.small_region_pending(pdf.resolve(), 1) == []
    # Standard scan after detailed OCR must not revert the merged search text.
    assert index_pdf(index, pdf, root)["indexed"] == 0
    assert index.search("skyforge", root)
    index.close()


def test_region_checkpoint_resets_for_changed_pdf(tmp_path: Path):
    root = tmp_path / "books"
    root.mkdir()
    pdf = root / "Hybrid.pdf"
    make_mixed_region_pdf(pdf)
    index = PageIndex(tmp_path / "search.sqlite3")
    index_pdf(index, pdf, root, ocr_binary=Path("mock"),
              small_region_ocr=True,
              region_reader=lambda page, exe: (["Mystic skyforge diagram"], 1))
    assert index.search("skyforge", root)
    make_mixed_region_pdf(pdf, phrase="A knight follows an entirely new secret oath.")
    index_pdf(index, pdf, root)
    assert not index.search("skyforge", root)
    assert index.connection.execute(
        "SELECT COUNT(*) FROM small_region_ocr_pages"
    ).fetchone()[0] == 0
    assert index.search("knight", root)
    index.close()


def test_ocr_modes_are_exclusive_and_ui_defaults_are_safe(tmp_path, monkeypatch):
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    app = _QT_APP
    root = tmp_path / "books"
    root.mkdir()
    pdf = root / "Hybrid.pdf"
    make_mixed_region_pdf(pdf)
    data = tmp_path / "private"
    index = PageIndex(data / "search.sqlite3")
    with pytest.raises(ValueError, match="either"):
        index_pdf(
            index, pdf, root, mixed_ocr=True, small_region_ocr=True,
            ocr_binary=Path("mock"),
        )
    index.close()
    catalog = Catalog(data / "catalog.sqlite3")
    st = pdf.stat()
    catalog.upsert(
        path=pdf.resolve(), library_root=root.resolve(), title="Hybrid",
        size_bytes=st.st_size, modified_ns=st.st_mtime_ns,
        page_count=1, cover_path="",
    )
    catalog.close()
    monkeypatch.setattr(LibraryWindow, "scan", lambda self: None)
    window = LibraryWindow(data, root)
    window.show()
    app.processEvents()
    panel = window.search_panel
    assert not panel.region_checkbox.isChecked()
    assert not panel.mixed_checkbox.isChecked()
    panel.mixed_checkbox.setChecked(True)
    panel.region_checkbox.setChecked(True)
    assert panel.region_checkbox.isChecked()
    assert not panel.mixed_checkbox.isChecked()
    panel.mixed_checkbox.setChecked(True)
    assert not panel.region_checkbox.isChecked()
    panel.ocr_checkbox.setChecked(False)
    assert not panel.region_checkbox.isEnabled()
    panel.ocr_checkbox.setChecked(True)
    assert panel.region_checkbox.isEnabled()
    window.close()
    window.deleteLater()
    app.processEvents()
