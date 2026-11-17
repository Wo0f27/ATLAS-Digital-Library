"""Regression coverage for opt-in mixed-content OCR and preserved PDF text."""

from pathlib import Path

import pypdfium2 as pdfium
from PySide6.QtWidgets import QApplication

from atlas_library.app import LibraryWindow
from atlas_library.catalog import Catalog
from atlas_library.fulltext import PageIndex, index_pdf
from atlas_library.hybrid_ocr import large_embedded_image, merge_ocr_text

from test_fulltext import make_text_pdf


def test_line_dedup_preserves_new_ocr_text():
    native = "The dragon guards its hoard. Ancient scholars record these facts."
    recognized = (
        "THE DRAGON GUARDS ITS HOARD\n"
        "Ancient scholars record these facts!\n"
        "The unnamed dragon has runes marked on its wing."
    )
    merged, added = merge_ocr_text(native, recognized)
    assert added == 1
    assert merged.count("The dragon guards") == 1
    assert "runes marked on its wing" in merged
    assert merge_ocr_text(native, "THE DRAGON GUARDS ITS HOARD.")[1] == 0
    assert merge_ocr_text("", "Image-only text")[1] == 1


class FakeImage:
    def __init__(self, bounds):
        self.bounds = bounds

    def get_bounds(self):
        return self.bounds


class FakePdfPage:
    def __init__(self, images):
        self.images = images

    def get_size(self):
        return (600, 800)

    def get_objects(self, filter):
        assert filter == [pdfium.raw.FPDF_PAGEOBJ_IMAGE]
        return iter(FakeImage(bounds) for bounds in self.images)


def test_detects_large_clipped_images_but_ignores_small_decoration():
    assert not large_embedded_image(FakePdfPage([(5, 5, 45, 45)]))
    assert large_embedded_image(FakePdfPage([(50, 50, 500, 650)]))
    assert large_embedded_image(FakePdfPage([(-300, -100, 620, 800)]))
    assert not large_embedded_image(FakePdfPage([(800, 100, 900, 200)]))


def _two_page_pdf(tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    path = root / "Hybrid.pdf"
    make_text_pdf(
        path,
        [
            "A crystal dragon guards the citadel high above the mountains.",
            "The great paladin approaches the eastern gate of the fortress.",
        ],
    )
    return root, path


def test_mixed_ocr_revisits_existing_index_and_preserves_native_text(tmp_path):
    root, pdf = _two_page_pdf(tmp_path)
    original = pdf.read_bytes()
    index = PageIndex(tmp_path / "local" / "search.sqlite3")
    assert index_pdf(index, pdf, root)["indexed"] == 2
    assert index.search("paladin", root)
    assert not index.search("starglass", root)

    calls = []
    def fake_detector(page):
        calls.append("detector")
        return len(calls) == 1

    def fake_ocr(page, executable):
        calls.append("ocr")
        return (
            "A CRYSTAL DRAGON GUARDS THE CITADEL HIGH ABOVE THE MOUNTAINS.\n"
            "A starglass inscription describes an unknown world."
        )

    result = index_pdf(
        index, pdf, root,
        ocr_binary=Path("mock-tesseract"),
        mixed_ocr=True, ocr=fake_ocr, image_detector=fake_detector,
    )
    assert result["indexed"] == 2
    assert result["mixed_checked"] == 2
    assert result["mixed_added"] == 1
    assert len(index.search("starglass", root)) == 1
    assert len(index.search("crystal", root)) == 1
    assert len(index.search("paladin", root)) == 1
    assert index.search("starglass", root)[0]["source"] == "mixed"
    assert len(index.connection.execute(
        "SELECT * FROM page_text WHERE path=?", (str(pdf.resolve()),)
    ).fetchall()) == 2

    again = index_pdf(
        index, pdf, root, ocr_binary=Path("mock-tesseract"),
        mixed_ocr=True,
        ocr=lambda page, exe: (_ for _ in ()).throw(
            AssertionError("OCR should not re-run on unchanged page")
        ),
        image_detector=lambda page: True,
    )
    assert again["indexed"] == 0
    assert pdf.read_bytes() == original
    index.close()

    reopened = PageIndex(tmp_path / "local" / "search.sqlite3")
    assert reopened.search("starglass", root)
    assert reopened.search("paladin", root)
    reopened.close()


def test_mixed_ocr_failure_preserves_text_and_retries(tmp_path):
    root, pdf = _two_page_pdf(tmp_path)
    index = PageIndex(tmp_path / "search.sqlite3")
    index_pdf(index, pdf, root)

    result = index_pdf(
        index, pdf, root, ocr_binary=Path("mock-tesseract"),
        mixed_ocr=True, image_detector=lambda page: True,
        ocr=lambda page, exe: (_ for _ in ()).throw(RuntimeError("OCR unavailable")),
    )
    assert result["mixed_added"] == 0
    assert index.search("citadel", root)
    assert index.search("paladin", root)
    assert index.connection.execute(
        "SELECT COUNT(*) FROM hybrid_ocr_pages WHERE status='error'"
    ).fetchone()[0] == 2

    result = index_pdf(
        index, pdf, root, ocr_binary=Path("mock-tesseract"),
        mixed_ocr=True, image_detector=lambda page: True,
        ocr=lambda page, exe: "A previously unseen zephyrglyph inscription appears.",
    )
    assert result["mixed_added"] == 2
    assert len(index.search("zephyrglyph", root)) == 2
    assert not index.hybrid_pending(pdf.resolve(), 2)
    index.close()


def test_mixed_ocr_reset_only_when_source_pdf_changes(tmp_path):
    root, pdf = _two_page_pdf(tmp_path)
    index = PageIndex(tmp_path / "search.sqlite3")
    index_pdf(index, pdf, root, ocr_binary=Path("mock"), mixed_ocr=True,
              image_detector=lambda page: False)
    assert index.hybrid_pending(pdf.resolve(), 2) == []
    make_text_pdf(
        pdf, ["An entirely different desert dragon rules the coastal marshes."],
    )
    changed = index_pdf(index, pdf, root)
    assert changed["pages"] == 1
    assert index.connection.execute(
        "SELECT COUNT(*) FROM hybrid_ocr_pages"
    ).fetchone()[0] == 0
    assert not index.search("paladin", root)
    assert index.search("marshes", root)
    index.close()


def test_index_scope_and_mixed_ocr_toggle_in_ui(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "books"
    root.mkdir()
    first = root / "First.pdf"
    second = root / "Second.pdf"
    make_text_pdf(first, ["The ancient red dragon guards the mountain fortress."])
    make_text_pdf(second, ["The ancient green dragon sleeps beneath the mountain."])
    catalog_dir = tmp_path / "local"
    catalog = Catalog(catalog_dir / "catalog.sqlite3")
    for book in (first, second):
        st = book.stat()
        catalog.upsert(
            path=book.resolve(), library_root=root.resolve(),
            title=book.stem, size_bytes=st.st_size,
            modified_ns=st.st_mtime_ns, page_count=1, cover_path="",
        )
    catalog.close()
    monkeypatch.setattr(LibraryWindow, "scan", lambda self: None)
    window = LibraryWindow(catalog_dir, root)
    window.show()
    app.processEvents()
    panel = window.search_panel
    assert not panel.mixed_checkbox.isChecked()
    assert panel.index_scope.count() == 3
    panel.index_scope.setCurrentIndex(
        panel.index_scope.findData(str(first.resolve()))
    )
    assert panel.index_scope.currentData() == str(first.resolve())
    panel.refresh_filters()
    assert panel.index_scope.currentData() == str(first.resolve())
    panel.ocr_checkbox.setChecked(False)
    assert not panel.mixed_checkbox.isEnabled()
    panel.ocr_checkbox.setChecked(True)
    assert panel.mixed_checkbox.isEnabled()
    window.close()
