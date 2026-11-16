"""Windows GUI regression tests for library-wide page search."""

from pathlib import Path

from PySide6.QtWidgets import QApplication

from atlas_library.app import LibraryWindow
from atlas_library.catalog import Catalog
from atlas_library.fulltext import PageIndex, index_pdf
from atlas_library.search_panel import IndexWorker

from test_fulltext import make_text_pdf


def test_library_search_opens_exact_page(tmp_path: Path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "books"
    root.mkdir()
    book = root / "Supplement.pdf"
    make_text_pdf(book, [
        "The magic item rules define how legendary treasures are identified.",
        "The mind flayer controls an underground colony of strange monsters.",
    ])
    data = tmp_path / "userdata"
    catalog = Catalog(data / "catalog.sqlite3")
    stat = book.stat()
    catalog.upsert(
        path=book.resolve(), library_root=root.resolve(),
        title="Supplement", size_bytes=stat.st_size,
        modified_ns=stat.st_mtime_ns, page_count=2, cover_path="",
    )
    catalog.close()

    index = PageIndex(data / "search.sqlite3")
    assert index_pdf(index, book, root)["indexed"] == 2
    index.close()

    # Do not trigger the unrelated automatic bookshelf cover scan.
    monkeypatch.setattr(LibraryWindow, "scan", lambda self: None)
    window = LibraryWindow(data, root)
    window.resize(1300, 820)
    window.show()
    for _ in range(10):
        app.processEvents()

    assert window.tabs.count() == 2
    assert window.tabs.tabText(1) == "Full-Text Search"
    window.tabs.setCurrentIndex(1)
    panel = window.search_panel
    panel.query.setText('"mind flayer"')
    panel.mode.setCurrentIndex(panel.mode.findData("advanced"))
    panel.search()
    assert panel.results.count() == 1
    assert "Page 2" in panel.results.item(0).text()
    panel._open_match(panel.results.item(0))
    for _ in range(35):
        app.processEvents()

    assert window.tabs.count() == 3
    reader = window.reader_tabs[str(book.resolve())]
    assert reader._loaded
    assert reader.navigator.currentPage() == 1

    # A second result on an existing tab should jump rather than duplicate it.
    panel.query.setText("legendary treasures")
    panel.mode.setCurrentIndex(panel.mode.findData("words"))
    panel.search()
    assert panel.results.count() == 1
    panel._open_match(panel.results.item(0))
    for _ in range(10):
        app.processEvents()
    assert window.tabs.count() == 3
    assert reader.navigator.currentPage() == 0
    window.close()


def test_background_indexer_and_resumed_scan(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "books"
    root.mkdir()
    book = root / "Manual.pdf"
    make_text_pdf(book, [
        "The ancient wizard recorded new spells in a dusty spellbook.",
        "A paladin takes an oath to protect innocent travelers and villagers.",
    ])
    db_path = tmp_path / "search.sqlite3"
    worker = IndexWorker(db_path, root, [book], "", False)
    worker.start()
    assert worker.wait(25000)
    index = PageIndex(db_path)
    assert index.file_state(book.resolve())["state"] == "complete"
    assert index.search("paladin", root)[0]["page_index"] == 1
    index.close()

    again = IndexWorker(db_path, root, [book], "", False)
    again.start()
    assert again.wait(25000)
    state = PageIndex(db_path)
    assert state.stats(root)["processed_pages"] == 2
    assert len(state.search("wizard", root)) == 1
    state.close()
