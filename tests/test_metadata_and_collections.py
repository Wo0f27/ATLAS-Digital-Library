"""Milestone 2 metadata and virtual collections regression tests."""

from pathlib import Path
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox

from atlas_library.catalog import Catalog
from atlas_library.app import LibraryWindow
from atlas_library.organizer import MetadataDialog, CollectionMembershipDialog


def _index(catalog: Catalog, path: Path, root: Path, changed: int = 0):
    catalog.upsert(
        path=path, library_root=root, title=path.stem,
        size_bytes=100 + changed, modified_ns=123 + changed,
        page_count=55, cover_path="",
    )


def test_metadata_tags_shelves_persist_and_rescan_preserves_everything(tmp_path: Path):
    root = tmp_path / "books"
    book_a, book_b = root / "Creature Guide.pdf", root / "Creature Guide 2.pdf"
    db = tmp_path / "application.sqlite3"
    catalog = Catalog(db)
    for path in (book_a, book_b):
        _index(catalog, path, root)

    shelf = catalog.create_collection("Dragon Studies")
    other_shelf = catalog.create_collection("Rules Reference")
    catalog.set_book_collections(book_a, {shelf, other_shelf})
    catalog.set_book_collections(book_b, {shelf})
    catalog.save_metadata(
        book_a, display_title="Dragon Studies: Definitive Edition",
        authors="A. Writer and B. Writer", publisher="Fiction Press",
        edition="Revised 2014", publication_year=2020, game_system="D&D 5e",
        description="A personal abstract", notes="Reference on page 42",
        tags=["Dragons", "Lore", "dragons", "  Rules  "],
    )
    catalog.set_favorite(book_a, True)
    catalog.save_reader_state(
        book_a, page_index=18, zoom_mode="width",
        zoom_factor=1.0, page_mode="continuous",
    )
    catalog.add_bookmark(book_a, 42, "Important section")
    _index(catalog, book_a, root, changed=1)  # routine rescan
    assert catalog.get(book_a)["title"] == "Creature Guide"
    by_path = {row["path"]: row for row in catalog.available(root)}
    assert by_path[str(book_a)]["display_title"] == "Dragon Studies: Definitive Edition"
    assert catalog.get_book_tags(book_a) == ["Dragons", "Lore", "Rules"]
    assert catalog.paths_for_tag("DRAGONS") == {str(book_a)}
    assert set(catalog.list_tags(root)) == {"Dragons", "Lore", "Rules"}
    assert catalog.get_collection_paths(shelf) == {str(book_a), str(book_b)}
    catalog.close()

    again = Catalog(db)
    metadata = again.get_metadata(book_a)
    assert metadata["edition"] == "Revised 2014"
    assert metadata["publication_year"] == 2020
    assert metadata["notes"] == "Reference on page 42"
    assert again.get_book_collections(book_a) == {shelf, other_shelf}
    assert again.get_profile(book_a)["favorite"]
    assert again.get_reader_state(book_a)["page_index"] == 18
    assert again.list_bookmarks(book_a)[0]["page_index"] == 42

    again.rename_collection(shelf, "Bestiary Notes")
    assert any(row["name"] == "Bestiary Notes" for row in again.list_collections())
    again.set_book_collections(book_a, {shelf})
    assert again.get_collection_paths(other_shelf) == set()
    assert again.delete_collection(other_shelf)
    assert again.get_collection_paths(shelf) == {str(book_a), str(book_b)}
    assert again.get_metadata(book_a)["tags"] == ["Dragons", "Lore", "Rules"]
    again.close()


def test_atomic_validation_and_shelf_constraints(tmp_path: Path):
    c = Catalog(tmp_path / "catalog.sqlite3")
    path = tmp_path / "book.pdf"
    c.save_metadata(path, display_title="Correct", tags=["Alpha"])
    with pytest.raises(ValueError):
        c.save_metadata(path, display_title="Bad", publication_year=700, tags=["Beta"])
    assert c.get_metadata(path)["display_title"] == "Correct"
    assert c.get_book_tags(path) == ["Alpha"]
    with pytest.raises(ValueError):
        c.save_metadata(path, display_title="Bad", tags=["x" * 81])
    assert c.get_metadata(path)["display_title"] == "Correct"
    with pytest.raises(ValueError):
        c.create_collection(" ")
    shelf = c.create_collection("One")
    with pytest.raises(sqlite3.IntegrityError):
        c.create_collection("ONE")
    c.set_book_collections(path, {shelf})
    with pytest.raises(ValueError):
        c.set_book_collections(path, {shelf, 999999})
    assert c.get_book_collections(path) == {shelf}
    with pytest.raises(ValueError):
        c.rename_collection(99999, "Missing")
    assert c.delete_collection(shelf)
    assert c.get_book_collections(path) == set()
    c.close()


def test_metadata_and_membership_dialog_fields(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    original = tmp_path / "PHB.pdf"
    editor = MetadataDialog(original, "PHB", {
        "display_title": "Player's Handbook", "authors": "Author",
        "publisher": "Publisher", "edition": "2014",
        "publication_year": 2014, "game_system": "D&D 5e",
        "description": "Overview", "notes": "My notes",
        "tags": ["Core Rules", "Player"],
    })
    assert editor.values()["display_title"] == "Player's Handbook"
    assert editor.values()["publication_year"] == 2014
    editor.tags.setText("Core Rules, Character Creation")
    assert editor.values()["tags"] == ["Core Rules", " Character Creation"]
    editor.close()

    membership = CollectionMembershipDialog(
        "Player's Handbook",
        [{"id": 3, "name": "Core Books"}, {"id": 9, "name": "Writing"}],
        {9},
    )
    assert membership.selected_ids() == {9}
    membership.list.item(0).setCheckState(Qt.CheckState.Checked)
    assert membership.selected_ids() == {3, 9}
    membership.close()


def test_bookshelf_metadata_and_virtual_filters(tmp_path: Path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "book-root-that-does-not-exist"
    data = tmp_path / "appstate"
    book = root / "Unedited 5e.pdf"
    other = root / "Other.pdf"
    catalog = Catalog(data / "catalog.sqlite3")
    for path in (book, other):
        _index(catalog, path, root.resolve())
    shelf_id = catalog.create_collection("Favorite Sources")
    catalog.set_book_collections(book, {shelf_id})
    catalog.save_metadata(
        book, display_title="The Correct Display Title",
        authors="Lore Author", tags=["Dragons", "Official"],
    )
    catalog.close()

    window = LibraryWindow(data, root)
    window.show()
    app.processEvents()
    assert window.gallery.count() == 2
    labels = {window.gallery.item(i).text() for i in range(window.gallery.count())}
    assert "The Correct Display Title" in labels
    assert "Unedited 5e" not in labels

    window.search.setText("Correct Display")
    assert window.gallery.count() == 1
    window.search.setText("Unedited 5e")
    assert window.gallery.count() == 1
    window.search.clear()
    assert window.preview_panel.width() == 285

    window.navigation_tabs.setCurrentIndex(1)
    assert window.collection_list.count() == 1
    window.collection_list.setCurrentRow(0)
    app.processEvents()
    assert window.gallery.count() == 1

    window.navigation_tabs.setCurrentIndex(2)
    assert window.tag_list.count() == 2
    tags = [window.tag_list.item(i).text() for i in range(window.tag_list.count())]
    assert tags == ["Dragons", "Official"]
    window.tag_list.setCurrentRow(0)
    app.processEvents()
    assert window.gallery.count() == 1

    window.navigation_tabs.setCurrentIndex(0)
    window.gallery.setCurrentItem(window.gallery.item(0))
    assert window.edit_metadata_btn.isEnabled()
    assert window.assign_shelves_btn.isEnabled()
    window.close()



def test_bookshelf_editing_dialog_workflow_is_non_destructive(tmp_path: Path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "not-present"
    data = tmp_path / "appstate"
    book = root / "Book.pdf"
    catalog = Catalog(data / "catalog.sqlite3")
    _index(catalog, book, root.resolve())
    catalog.close()

    window = LibraryWindow(data, root)
    window.show()
    app.processEvents()
    window.gallery.setCurrentItem(window.gallery.item(0))
    assert window.selected_path == str(book)

    monkeypatch.setattr(
        MetadataDialog, "exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        MetadataDialog, "values",
        lambda self: {
            "display_title": "My Revised Display Title",
            "authors": "Example Author",
            "publisher": "Local Publisher",
            "edition": "2nd printing",
            "publication_year": 2019,
            "game_system": "D&D 5e",
            "description": "Indexable synopsis",
            "notes": "For private research",
            "tags": ["Lore", "Bestiary"],
        },
    )
    window._edit_metadata()
    assert "My Revised Display Title" in window.gallery.item(0).text()
    assert window.catalog.get(book)["title"] == "Book"  # original still indexed

    monkeypatch.setattr(
        QInputDialog, "getText",
        lambda *args, **kwargs: ("Campaign Research", True),
    )
    window._create_collection()
    assert window.collection_list.count() == 1
    collection_id = window.collection_list.item(0).data(Qt.ItemDataRole.UserRole)

    window.navigation_tabs.setCurrentIndex(0)
    window.gallery.setCurrentItem(window.gallery.item(0))
    monkeypatch.setattr(
        CollectionMembershipDialog, "exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        CollectionMembershipDialog, "selected_ids",
        lambda self: {collection_id},
    )
    window._edit_book_collections()
    assert window.catalog.get_book_collections(book) == {collection_id}

    window.navigation_tabs.setCurrentIndex(1)
    assert window.collection_list.currentItem().text() == "Campaign Research"
    assert window.gallery.count() == 1

    monkeypatch.setattr(
        QInputDialog, "getText",
        lambda *args, **kwargs: ("Campaign Research Renamed", True),
    )
    window._rename_collection()
    assert window.collection_list.currentItem().text() == "Campaign Research Renamed"

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    window._delete_collection()
    assert window.collection_list.count() == 0
    assert window.catalog.get_book_collections(book) == set()
    assert window.catalog.get_metadata(book)["notes"] == "For private research"
    assert window.catalog.get_profile(book)["reading_status"] == "unread"
    window.close()
