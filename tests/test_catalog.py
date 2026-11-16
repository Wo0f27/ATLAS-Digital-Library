from pathlib import Path

from atlas_library.catalog import Catalog


def test_catalog_persists_and_marks_missing(tmp_path: Path):
    db = tmp_path / "data" / "catalog.sqlite3"
    book = tmp_path / "books" / "Test.pdf"
    root = book.parent

    first = Catalog(db)
    first.upsert(
        path=book, library_root=root, title="Test", size_bytes=12,
        modified_ns=123, page_count=2, cover_path="",
    )
    first.close()

    second = Catalog(db)
    assert second.get(book)["page_count"] == 2
    second.mark_missing_except(root, set())
    assert second.get(book)["status"] == "missing"
    second.upsert(
        path=book, library_root=root, title="Changed filename metadata",
        size_bytes=13, modified_ns=124, page_count=3, cover_path="",
    )
    assert second.get(book)["status"] == "available"
    assert second.get(book)["title"] == "Test"
    second.close()
