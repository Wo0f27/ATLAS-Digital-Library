from pathlib import Path

import pytest

from atlas_library.catalog import Catalog


def test_reader_state_persists_across_reopen_and_rescan(tmp_path: Path):
    db = tmp_path / "catalog.sqlite3"
    book = tmp_path / "books" / "Dragon.pdf"
    root = book.parent
    catalog = Catalog(db)
    catalog.upsert(
        path=book, library_root=root, title="Dragon", size_bytes=15,
        modified_ns=1234, page_count=20, cover_path="",
    )
    assert catalog.get_reader_state(book) is None

    catalog.save_reader_state(
        book, page_index=7, zoom_mode="custom",
        zoom_factor=1.5, page_mode="single",
    )
    catalog.upsert(
        path=book, library_root=root, title="Dragon", size_bytes=15,
        modified_ns=1234, page_count=20, cover_path="",
    )
    catalog.close()

    again = Catalog(db)
    state = again.get_reader_state(book)
    assert state["page_index"] == 7
    assert state["zoom_mode"] == "custom"
    assert state["zoom_factor"] == 1.5
    assert state["page_mode"] == "single"
    again.close()


@pytest.mark.parametrize("invalid_mode", ["unknown", "", "fit-to-window"])
def test_reader_state_rejects_invalid_zoom_modes(tmp_path: Path, invalid_mode):
    catalog = Catalog(tmp_path / "db.sqlite3")
    with pytest.raises(ValueError):
        catalog.save_reader_state(
            tmp_path / "book.pdf", page_index=0,
            zoom_mode=invalid_mode, zoom_factor=1.0, page_mode="single",
        )
    catalog.close()


def test_reader_page_clamping():
    from atlas_library.reader import clamp_page
    assert clamp_page(-10, 20) == 0
    assert clamp_page(999, 20) == 19
    assert clamp_page(3, 20) == 3
    assert clamp_page(4, 0) == 0


def test_qt_pdf_widget_available():
    # Native Qt PDF support is required by the embedded reader, not an optional plugin.
    from PySide6.QtPdf import QPdfDocument, QPdfBookmarkModel, QPdfSearchModel
    from PySide6.QtPdfWidgets import QPdfView
    assert QPdfView and QPdfDocument and QPdfBookmarkModel and QPdfSearchModel
