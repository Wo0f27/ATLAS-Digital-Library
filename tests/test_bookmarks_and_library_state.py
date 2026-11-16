"""Regression tests for local favorites, reading state and page bookmarks."""

from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QInputDialog

from atlas_library.app import LibraryWindow
from atlas_library.catalog import Catalog
from atlas_library.reader import PdfReader


def _book_file(path: Path) -> None:
    img = Image.new("RGB", (230, 300), "white")
    img.save(path, format="PDF", save_all=True, append_images=[img.copy()])


def test_profiles_and_bookmarks_survive_restart_and_rescan(tmp_path: Path):
    db = tmp_path / "catalog.sqlite3"
    path = tmp_path / "books" / "Adventure.pdf"
    first = Catalog(db)
    first.upsert(
        path=path, library_root=path.parent, title="Adventure",
        size_bytes=122, modified_ns=1, page_count=10, cover_path="",
    )
    assert first.get_profile(path) == {"favorite": False, "reading_status": "unread"}
    first.set_favorite(path, True)
    first.set_reading_status(path, "reading")
    first.set_reading_status(path, "finished")
    bookmark = first.add_bookmark(path, 5, "My important encounter")
    assert first.add_bookmark(path, 5, "My important encounter") == bookmark
    first.upsert(
        path=path, library_root=path.parent, title="Adventure",
        size_bytes=144, modified_ns=2, page_count=12, cover_path="",
    )
    first.close()

    again = Catalog(db)
    assert again.get_profile(path) == {"favorite": True, "reading_status": "finished"}
    assert len(again.list_bookmarks(path)) == 1
    assert again.list_bookmarks(path)[0]["page_index"] == 5
    assert not again.delete_bookmark(tmp_path / "other.pdf", bookmark)
    assert again.delete_bookmark(path, bookmark)
    assert again.list_bookmarks(path) == []
    again.close()


def test_invalid_user_state_is_rejected(tmp_path: Path):
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    book = tmp_path / "book.pdf"
    with pytest.raises(ValueError):
        catalog.set_reading_status(book, "abandoned")
    with pytest.raises(ValueError):
        catalog.add_bookmark(book, -1, "Bad page")
    with pytest.raises(ValueError):
        catalog.add_bookmark(book, 2, "")
    with pytest.raises(ValueError):
        catalog.add_bookmark(book, 2, "x" * 151)
    catalog.close()


def test_embedded_reader_bookmarks(tmp_path: Path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    pdf = tmp_path / "Reader.pdf"
    _book_file(pdf)
    original = pdf.read_bytes()
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    reader = PdfReader(pdf, catalog)
    reader.resize(1000, 700)
    reader.show()
    for _ in range(30):
        app.processEvents()
        if reader._loaded:
            break
    assert reader._loaded
    reader.goto_page(1)
    app.processEvents()
    monkeypatch.setattr(
        QInputDialog, "getText",
        lambda *args, **kwargs: ("Dragon lair", True),
    )
    reader._add_bookmark()
    assert reader.bookmark_list.count() == 1
    assert "Dragon lair" in reader.bookmark_list.item(0).text()
    assert reader.side_tabs.currentIndex() == 1
    reader.goto_page(0)
    reader._jump_to_bookmark(reader.bookmark_list.item(0))
    assert reader.navigator.currentPage() == 1
    reader._remove_bookmark()
    assert reader.bookmark_list.count() == 0
    reader.dispose()
    reader.close()
    assert pdf.read_bytes() == original
    catalog.close()


def test_bookshelf_favorites_and_reading_filters(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "unmounted-library"
    data = tmp_path / "state"
    favorite = root / "Red Dragon.pdf"
    other = root / "Blue Dragon.pdf"
    catalog = Catalog(data / "catalog.sqlite3")
    for book in [favorite, other]:
        catalog.upsert(
            path=book, library_root=root.resolve(), title=book.stem,
            size_bytes=42, modified_ns=1, page_count=10, cover_path="",
        )
    catalog.set_favorite(favorite, True)
    catalog.set_reading_status(favorite, "reading")
    catalog.close()

    window = LibraryWindow(data, root)
    window.show()
    app.processEvents()
    assert window.gallery.count() == 2
    window.view_filter.setCurrentIndex(window.view_filter.findData("favorites"))
    assert window.gallery.count() == 1
    assert "Red Dragon" in window.gallery.item(0).text()
    window.view_filter.setCurrentIndex(window.view_filter.findData("reading"))
    assert window.gallery.count() == 1
    window.view_filter.setCurrentIndex(window.view_filter.findData("finished"))
    assert window.gallery.count() == 0
    window.view_filter.setCurrentIndex(window.view_filter.findData("all"))
    window.gallery.setCurrentItem(window.gallery.item(0))
    assert window.favorite_check.isEnabled()
    window.close()
