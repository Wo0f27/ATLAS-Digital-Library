"""Regression tests for on-page OCR highlights embedded in the reader."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from atlas_library.catalog import Catalog
from atlas_library.inline_ocr import InlineOcrPage
from atlas_library.reader import PdfReader
from test_fulltext import make_text_pdf


def _fake_result():
    image = Image.new("RGB", (500, 720), "white")
    output = io.BytesIO()
    image.save(output, "PNG")
    return {
        "image": output.getvalue(), "width": 500, "height": 720,
        "words": [
            {"text": "dragon", "x": .15, "y": .42, "w": .2, "h": .035},
            {"text": "rune", "x": .50, "y": .42, "w": .12, "h": .035},
        ],
        "cached": True, "page_index": 1,
    }


def test_inline_page_draws_only_matching_boxes_and_zoom(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    overlay = InlineOcrPage()
    overlay.resize(850, 700)
    overlay.show()
    overlay.display_result(_fake_result(), "The [dragon] rune")
    app.processEvents()
    assert overlay.highlight_count == 1
    assert len(overlay.scene.items()) == 2
    assert "cached coordinates" in overlay.status.text()
    assert overlay.view.auto_fit

    overlay.zoom_in.click()
    assert not overlay.view.auto_fit
    overlay.fit_button.click()
    assert overlay.view.auto_fit
    overlay.display_result(_fake_result(), "The [beholder] rune")
    assert overlay.highlight_count == 0
    assert len(overlay.scene.items()) == 1
    assert "No speculative highlights" in overlay.status.text()
    overlay.close()


def test_reader_inline_mode_preserves_original_qpdfview_and_restores_page(
    tmp_path: Path, monkeypatch,
):
    app = QApplication.instance() or QApplication([])
    book = tmp_path / "Book.pdf"
    make_text_pdf(book, [
        "Ancient dragons guard kingdoms across the mountains and rivers.",
        "A wizard protects an enchanted citadel from invaders and thieves.",
    ])
    original = book.read_bytes()
    database = Catalog(tmp_path / "catalog.sqlite3")
    reader = PdfReader(
        book, database, search_db_path=tmp_path / "search.sqlite3",
    )
    reader.resize(1200, 800)
    reader.show()
    for _ in range(30):
        app.processEvents()
        if reader._loaded:
            break
    assert reader._loaded
    native = reader.view
    zoom_mode = native.zoomMode()
    original_page_mode = native.pageMode()
    original_zoom = native.zoomFactor()

    matches = [
        {"path": str(book), "page_index": 1, "source": "ocr",
         "excerpt": "The [citadel] stands"},
        {"path": str(book), "page_index": 0, "source": "mixed",
         "excerpt": "An ancient [dragon]"},
    ]
    reader.set_search_matches(matches)
    assert reader.locate_ocr_button.isVisible()
    assert reader.inline_ocr_button.isVisible()
    assert reader.navigator.currentPage() == 1

    activations = []
    def fake_activate(self, *args):
        activations.append(args)
        self.display_result(_fake_result(), "[dragon]")
    monkeypatch.setattr(InlineOcrPage, "activate", fake_activate)
    reader.inline_ocr_button.click()
    app.processEvents()
    assert reader.reader_stack.currentWidget() is reader.inline_page
    assert reader.inline_page.highlight_count == 1
    assert not reader.zoom_mode.isEnabled()
    assert len(activations) == 1
    assert reader.navigator.currentPage() == 1

    reader.inline_page.back_btn.click()
    app.processEvents()
    assert reader.reader_stack.currentWidget() is native
    assert reader.zoom_mode.isEnabled()
    assert reader.navigator.currentPage() == 1
    assert native.zoomMode() == zoom_mode
    assert native.pageMode() == original_page_mode
    assert native.zoomFactor() == original_zoom

    # Changing the search result automatically exits OCR mode and navigates.
    reader.inline_ocr_button.click()
    reader.match_next.click()
    app.processEvents()
    assert reader.reader_stack.currentWidget() is native
    assert reader.navigator.currentPage() == 0
    assert reader._indexed_match_position == 1

    reader.inline_ocr_button.click()
    reader.match_close.click()
    assert reader.reader_stack.currentWidget() is native
    assert not reader.match_context.isVisible()

    reader.dispose()
    reader.close()
    assert book.read_bytes() == original
    database.close()


def test_ctrl_wheel_zooms_inline_page_not_pdf(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    page = InlineOcrPage()
    page.resize(1000, 820)
    page.show()
    page.display_result(_fake_result(), "[dragon]")
    app.processEvents()
    transform_before = page.view.transform().m11()
    wheel = QWheelEvent(
        QPointF(100, 100), QPointF(100, 100),
        QPoint(0, 0), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.ScrollUpdate, False,
    )
    QApplication.sendEvent(page.view.viewport(), wheel)
    app.processEvents()
    assert wheel.isAccepted()
    assert page.view.transform().m11() > transform_before
    page.close()
