"""Milestone 3 advanced search tests against generated, private test PDFs."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from atlas_library.advanced_search import AdvancedSearcher
from atlas_library.app import LibraryWindow
from atlas_library.catalog import Catalog
from atlas_library.fulltext import PageIndex, index_pdf

from test_fulltext import make_text_pdf


def _pdfs(tmp_path: Path):
    root = tmp_path / "books"
    root.mkdir()
    draconic = root / "Monsters.pdf"
    other = root / "Knights.pdf"
    make_text_pdf(
        draconic,
        [
            "A mighty dragon dwells in the ancient mountain fortress.",
            "The emerald dragon guards its hatchlings and treasury.",
        ],
    )
    make_text_pdf(
        other,
        ["The brave paladin recites a sacred oath and wears shining armor."],
    )
    db = PageIndex(tmp_path / "search.sqlite3")
    for path in (draconic, other):
        index_pdf(db, path, root)
    return root, draconic, other, db


def test_fuzzy_word_handles_misspelling_and_whitelist(tmp_path: Path):
    root, dragons, knights, db = _pdfs(tmp_path)
    searcher = AdvancedSearcher(db)
    hits, notice = searcher.fuzzy("dragn", root)
    assert {hit["page_index"] for hit in hits} == {0, 1}
    assert {hit["path"] for hit in hits} == {str(dragons.resolve())}
    assert "dragon" in notice
    hits, notice = searcher.fuzzy("paladn", root, allowed_paths={str(knights.resolve())})
    assert len(hits) == 1
    assert hits[0]["path"] == str(knights.resolve())
    excluded, _ = searcher.fuzzy("dragn", root, allowed_paths={str(knights.resolve())})
    assert excluded == []
    assert db.search("dragon", root, allowed_paths=set()) == []
    assert len(db.search("dragon", root, allowed_paths={str(dragons.resolve())})) == 2
    assert len(db.search("dragon", root)) == 2
    with pytest.raises(ValueError, match="one"):
        searcher.fuzzy("two words", root)
    db.close()


def test_regex_search_is_page_specific_and_respects_scope(tmp_path: Path):
    root, dragons, knights, db = _pdfs(tmp_path)
    searcher = AdvancedSearcher(db)
    hits, notice = searcher.regex_search(r"\bdr.gon\b", root)
    assert {(hit["path"], hit["page_index"]) for hit in hits} == {
        (str(dragons.resolve()), 0), (str(dragons.resolve()), 1)
    }
    assert "[dragon]" in hits[0]["excerpt"].lower()
    scoped, _ = searcher.regex_search(
        r"\bpaladin\b", root, allowed_paths={str(dragons.resolve())}
    )
    assert scoped == []
    scoped, _ = searcher.regex_search(
        r"\bpaladin\b", root, allowed_paths={str(knights.resolve())}
    )
    assert len(scoped) == 1 and scoped[0]["page_index"] == 0
    assert searcher.regex_search(r"(?i)emerald", root, limit=1)[0]
    with pytest.raises(ValueError, match="Invalid regular expression"):
        searcher.regex_search("(", root)
    with pytest.raises(ValueError, match="1–200"):
        searcher.regex_search("", root)
    db.close()


def test_regex_respects_interrupt_and_page_budget(tmp_path: Path):
    root, _dragons, _knights, db = _pdfs(tmp_path)
    searcher = AdvancedSearcher(db)
    with pytest.raises(Exception) as raised:
        searcher.regex_search(r"dragon", root, cancel=lambda: True)
    assert type(raised.value).__name__ == "IndexCancelled"
    hits, notice = searcher.regex_search(r"zzzzzzzzz", root, max_pages=1)
    assert hits == []
    assert "stopped after 1 pages" in notice
    db.close()


def test_metadata_tags_and_shelves_restrict_search_in_window(tmp_path: Path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    root, dragons, knights, db = _pdfs(tmp_path)
    db.close()
    appstate = tmp_path / "appstate"
    catalog = Catalog(appstate / "catalog.sqlite3")
    for book in (dragons, knights):
        info = book.stat()
        catalog.upsert(
            path=book.resolve(), library_root=root.resolve(),
            title=book.stem, size_bytes=info.st_size,
            modified_ns=info.st_mtime_ns, page_count=2, cover_path="",
        )
    catalog.save_metadata(
        dragons.resolve(), authors="Mythic Writer",
        publisher="Dragon Forge Press", edition="2024",
        game_system="D&D 5e", tags=["Bestiary", "Dragons"],
    )
    catalog.save_metadata(
        knights.resolve(), authors="Knight Writer",
        publisher="Knight Chronicle", edition="2014",
        game_system="D&D 5e", tags=["Classes"],
    )
    shelf_id = catalog.create_collection("Monster Shelf")
    catalog.set_book_collections(dragons.resolve(), {shelf_id})
    catalog.close()
    # Copy only synthetic page index into the temp app-data location.
    import shutil
    shutil.copy2(tmp_path / "search.sqlite3", appstate / "search.sqlite3")
    monkeypatch.setattr(LibraryWindow, "scan", lambda self: None)
    window = LibraryWindow(appstate, root)
    window.show()
    app.processEvents()
    panel = window.search_panel
    panel.refresh_filters()

    panel.query.setText("dragon")
    panel.tag_filter.setCurrentIndex(panel.tag_filter.findData("Bestiary"))
    panel.search()
    assert panel.results.count() == 2

    panel.shelf_filter.setCurrentIndex(panel.shelf_filter.findData(shelf_id))
    panel.search()
    assert panel.results.count() == 2
    panel.meta_filter.setText("Knight Writer")
    panel.search()
    assert panel.results.count() == 0
    panel.meta_filter.setText("Dragon Forge")
    panel.search()
    assert panel.results.count() == 2

    panel.meta_filter.clear()
    panel.query.setText("dragn")
    panel.mode.setCurrentIndex(panel.mode.findData("fuzzy"))
    panel.search()
    deadline = time.monotonic() + 10
    while panel.search_worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert panel.search_worker is None
    assert panel.results.count() == 2

    panel.query.setText(r"dr.gon")
    panel.mode.setCurrentIndex(panel.mode.findData("regex"))
    panel.search()
    deadline = time.monotonic() + 10
    while panel.search_worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert panel.results.count() == 2

    # Original PDFs are never edited by searching.
    assert dragons.is_file() and knights.is_file()
    window.close()
