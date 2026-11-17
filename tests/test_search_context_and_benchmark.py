"""Milestone 3: OCR search-result context and local index benchmarks."""

from __future__ import annotations

import time
from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import QApplication

from atlas_library.app import LibraryWindow
from atlas_library.benchmark import benchmark_index, format_benchmark
from atlas_library.catalog import Catalog
from atlas_library.fulltext import PageIndex, index_pdf
from atlas_library.search_context import (
    context_for_book, format_excerpt_html, source_label,
)

from test_fulltext import make_text_pdf


def test_excerpt_is_html_safe_and_highlights_only_marked_terms():
    rendered = format_excerpt_html('<script>alert("x")</script> [dragon] & [gryphon]')
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<span" in rendered
    assert "dragon</span>" in rendered
    assert "gryphon</span>" in rendered
    assert source_label("ocr") == "Offline OCR"
    assert source_label("mixed") == "PDF text + offline OCR"
    assert source_label("text") == "Selectable PDF text"


def test_context_for_book_filters_and_does_not_modify_rows():
    rows = [
        {"path": "a.pdf", "page_index": 0, "source": "ocr", "excerpt": "[dragon]"},
        {"path": "b.pdf", "page_index": 3, "source": "text", "excerpt": "[wizard]"},
    ]
    chosen = context_for_book(rows, "a.pdf")
    assert len(chosen) == 1 and chosen[0]["source"] == "ocr"
    chosen[0]["excerpt"] = "edited"
    assert rows[0]["excerpt"] == "[dragon]"


def test_local_benchmark_never_changes_pdfs(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    book = root / "Creatures.pdf"
    make_text_pdf(book, [
        "Ancient dragons inhabit these mountains and temples.",
        "A druid studies the old forest and its creatures.",
    ])
    initial_bytes = book.read_bytes()
    index = PageIndex(tmp_path / "private" / "search.sqlite3")
    index_pdf(index, book, root)
    results = benchmark_index(index, root, terms=("dragon", "creatures"))
    assert results["books"] == 1
    assert results["pages"] == 2
    assert results["largest_book_pages"] == 2
    assert results["characters"] > 50
    assert results["index_megabytes"] > 0
    assert results["median_ms"] is not None
    assert any(item["result_pages"] == 1 for item in results["timings"])
    assert "LOCAL SEARCH INDEX BENCHMARK" in format_benchmark(results)
    assert book.read_bytes() == initial_bytes
    assert benchmark_index(index, tmp_path / "different")["pages"] == 0
    index.close()


def test_ocr_search_results_display_context_and_navigate_pages(tmp_path: Path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "book-library"
    root.mkdir()
    book = root / "Image Only.pdf"
    first = Image.new("RGB", (300, 400), (250, 250, 250))
    second = Image.new("RGB", (300, 400), (245, 245, 245))
    first.save(book, "PDF", save_all=True, append_images=[second])
    untouched = book.read_bytes()

    data = tmp_path / "private-data"
    catalog = Catalog(data / "catalog.sqlite3")
    st = book.stat()
    catalog.upsert(
        path=book.resolve(), library_root=root.resolve(),
        title="Image Only", size_bytes=st.st_size, modified_ns=st.st_mtime_ns,
        page_count=2, cover_path="",
    )
    catalog.close()

    index = PageIndex(data / "search.sqlite3")
    index_pdf(
        index, book, root,
        ocr_binary=Path("fake-tesseract"),
        ocr=lambda page, binary: "An elusive gryphon perches on the northern cliffs.",
    )
    index.close()

    monkeypatch.setattr(LibraryWindow, "scan", lambda self: None)
    window = LibraryWindow(data, root)
    window.resize(1300, 800)
    window.show()
    for _ in range(10):
        app.processEvents()

    panel = window.search_panel
    panel.query.setText("gryphon")
    panel.search()
    assert panel.results.count() == 2
    assert "Offline OCR" in panel.results.item(0).text()
    panel._open_match(panel.results.item(0))
    for _ in range(40):
        app.processEvents()

    reader = window.reader_tabs[str(book.resolve())]
    assert reader._loaded
    assert reader.match_context.isVisible()
    assert len(reader._indexed_matches) == 2
    assert "Offline OCR" in reader.match_heading.text()
    assert "gryphon" in reader.match_excerpt.toPlainText().lower()
    assert reader.match_next.isEnabled()
    first_page = reader.navigator.currentPage()
    reader.match_next.click()
    app.processEvents()
    assert reader.navigator.currentPage() != first_page
    assert reader._indexed_match_position == 1
    reader.match_previous.click()
    app.processEvents()
    assert reader.navigator.currentPage() == first_page
    reader.match_close.click()
    assert not reader.match_context.isVisible()

    panel.run_benchmark()
    deadline = time.monotonic() + 10.0
    while panel.benchmark_worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert panel.benchmark_worker is None
    assert "Indexed pages: 2" in panel.benchmark_report.toPlainText()
    assert "OCR/mixed pages: 2" in panel.benchmark_report.toPlainText()
    assert book.read_bytes() == untouched
    window.close()
