"""Regression tests for smooth reader zoom and fixed-width preview panel."""

from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from atlas_library.app import LibraryWindow
from atlas_library.catalog import Catalog
from atlas_library.reader import PdfReader


def _sample_pdf(path: Path):
    image = Image.new("RGB", (300, 440), "#faf8f4")
    image.save(path, "PDF")


def _wheel(modifiers):
    return QWheelEvent(
        QPointF(80, 90), QPointF(80, 90),
        QPoint(0, 0), QPoint(0, 120),
        Qt.MouseButton.NoButton, modifiers,
        Qt.ScrollPhase.ScrollUpdate, False,
    )


def test_ctrl_wheel_zoom_preserves_smooth_initial_fit(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    pdf = tmp_path / "Book.pdf"
    _sample_pdf(pdf)
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    reader = PdfReader(pdf, catalog)
    reader.resize(1000, 720)
    reader.show()
    for _ in range(40):
        app.processEvents()
        if reader._loaded:
            break
    assert reader._loaded

    fit_factor = reader._effective_zoom()
    assert fit_factor > 0

    event = _wheel(Qt.KeyboardModifier.ControlModifier)
    QApplication.sendEvent(reader.view.viewport(), event)
    app.processEvents()
    assert event.isAccepted()
    assert reader.zoom_mode.currentData() == "custom"
    assert reader.view.zoomFactor() == pytest.approx(
        min(5.0, max(.25, fit_factor * 1.12)), rel=0.04
    )
    # The temporary preview should cover Qt's uncached white page rendering.
    assert reader._zoom_overlay.isVisible()
    assert reader._zoom_frame is not None
    assert not reader._zoom_overlay.pixmap().isNull()

    from PySide6.QtTest import QTest
    QTest.qWait(280)
    assert not reader._zoom_overlay.isVisible()
    assert reader._zoom_frame is None

    after_zoom = reader.view.zoomFactor()
    regular = _wheel(Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(reader.view.viewport(), regular)
    app.processEvents()
    assert reader.view.zoomFactor() == pytest.approx(after_zoom)

    reader.dispose()
    reader.close()
    catalog.close()


def test_preview_does_not_resize_for_long_paths(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "fake-unmounted-books"
    data = tmp_path / "data"
    path = root / ("very_long_folder_name_" * 15) / (
        "Unusually Long Sourcebook Name " * 7 + ".pdf"
    )
    catalog = Catalog(data / "catalog.sqlite3")
    catalog.upsert(
        path=path,
        library_root=root.resolve(),
        title=path.stem,
        size_bytes=4000,
        modified_ns=1,
        page_count=200,
        cover_path="",
    )
    catalog.close()

    window = LibraryWindow(data, root)
    window.show()
    app.processEvents()
    assert window.gallery.count() == 1

    width_before = window.preview_panel.width()
    window.gallery.setCurrentItem(window.gallery.item(0))
    app.processEvents()
    assert window.preview_panel.width() == width_before == 285
    assert str(path) not in window.detail_info.text()
    assert window.detail_info.toolTip() == str(path)
    assert window.detail_title.toolTip() == path.stem

    window.copy_full_path()
    assert QApplication.clipboard().text() == str(path)
    window.close()
