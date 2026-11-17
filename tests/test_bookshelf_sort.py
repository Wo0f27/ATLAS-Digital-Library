"""Root-level PDF filtering and bookshelf ordering, without filesystem changes."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from atlas_library.app import LibraryWindow
from atlas_library.bookshelf_sort import (
    ROOT_ONLY_FOLDER, directly_in_root, sorted_bookshelf_entries,
)
from atlas_library.catalog import Catalog


_QT_APP = None


def _row(path: Path, title: str, display_title: str = "") -> dict:
    return {
        "path": str(path),
        "title": title,
        "display_title": display_title,
    }


def test_root_first_places_direct_children_before_nested_books(tmp_path: Path):
    root = tmp_path / "library"
    books = [
        _row(root / "nested" / "A.pdf", "A Nested"),
        _row(root / "Z.pdf", "Z Root"),
        _row(root / "Alpha.pdf", "Alpha Root"),
        _row(root / "nested" / "B.pdf", "B Nested"),
    ]
    assert [book["title"] for book in sorted_bookshelf_entries(books, root)] == [
        "A Nested", "Alpha Root", "B Nested", "Z Root"
    ]
    assert [book["title"] for book in sorted_bookshelf_entries(
        books, root, "root_first"
    )] == [
        "Alpha Root", "Z Root", "A Nested", "B Nested"
    ]
    assert directly_in_root(root / "Alpha.pdf", root)
    assert not directly_in_root(root / "nested" / "A.pdf", root)
    assert not directly_in_root(tmp_path / "elsewhere.pdf", root)


def test_sorting_respects_custom_titles_and_tie_breaks(tmp_path: Path):
    root = tmp_path / "library"
    rows = [
        _row(root / "nested" / "A.pdf", "A Nested"),
        _row(root / "Z.pdf", "Z Root", "Beautiful Creatures"),
        _row(root / "A.pdf", "A Root", "beautiful creatures"),
        _row(root / "nested" / "C.pdf", "C Nested", "Abominations"),
    ]
    sorted_rows = sorted_bookshelf_entries(rows, root, "root_first")
    assert [Path(b["path"]).name for b in sorted_rows] == [
        "A.pdf", "Z.pdf", "A.pdf", "C.pdf"
    ]
    assert [Path(b["path"]).parent == root for b in sorted_rows] == [
        True, True, False, False
    ]
    assert sorted_bookshelf_entries(rows, root, "not-supported") == \
        sorted_bookshelf_entries(rows, root, "title")


def _gallery_titles(window: LibraryWindow) -> list[str]:
    return [window.gallery.item(i).text() for i in range(window.gallery.count())]


def test_root_folder_view_and_saved_sort_survive_catalog_rescan(
    tmp_path: Path, monkeypatch,
):
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    app = _QT_APP

    root = tmp_path / "library"
    (root / "Nested").mkdir(parents=True)
    root_alpha = root / "Alpha.pdf"
    root_omega = root / "Omega.pdf"
    nested = root / "Nested" / "AAA.pdf"
    for book in (root_alpha, root_omega, nested):
        book.write_bytes(b"small test fixture; must never be altered")
    before = {str(p): p.read_bytes() for p in (root_alpha, root_omega, nested)}

    data = tmp_path / "private-data"
    catalog = Catalog(data / "catalog.sqlite3")
    for path in (root_alpha, root_omega, nested):
        stat = path.stat()
        catalog.upsert(
            path=path.resolve(), library_root=root.resolve(),
            title=path.stem, size_bytes=stat.st_size,
            modified_ns=stat.st_mtime_ns, page_count=1, cover_path="",
        )
    catalog.close()

    monkeypatch.setattr(LibraryWindow, "scan", lambda self: None)
    window = LibraryWindow(data, root)
    settings = window.settings
    previous_sort = settings.value("bookshelf_sort", None)
    try:
        title_index = window.sort_order.findData("title")
        root_index = window.sort_order.findData("root_first")
        assert title_index >= 0 and root_index >= 0
        window.sort_order.setCurrentIndex(title_index)
        assert _gallery_titles(window) == ["AAA", "Alpha", "Omega"]

        window.sort_order.setCurrentIndex(root_index)
        assert _gallery_titles(window) == ["Alpha", "Omega", "AAA"]
        assert window.settings.value("bookshelf_sort") == "root_first"

        folders = window.tree.topLevelItem(0)
        assert folders.text(0) == "All Books"
        root_item = folders.child(0)
        assert root_item.text(0) == "Library root (PDFs only)"
        assert root_item.data(0, Qt.ItemDataRole.UserRole) == ROOT_ONLY_FOLDER
        window.tree.setCurrentItem(root_item)
        app.processEvents()
        assert _gallery_titles(window) == ["Alpha", "Omega"]

        window.tree.setCurrentItem(folders)
        assert _gallery_titles(window) == ["Alpha", "Omega", "AAA"]
        window._load_existing()
        assert _gallery_titles(window) == ["Alpha", "Omega", "AAA"]
        assert window.sort_order.currentData() == "root_first"
        for path in (root_alpha, root_omega, nested):
            assert path.read_bytes() == before[str(path)]
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
        if previous_sort is None:
            settings.remove("bookshelf_sort")
        else:
            settings.setValue("bookshelf_sort", previous_sort)
