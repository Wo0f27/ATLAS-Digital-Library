"""Safe, review-only subject-library migration preview tests."""

from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtWidgets import QApplication

from atlas_library.app import LibraryWindow
from atlas_library.catalog import Catalog
from atlas_library.reorganization import (
    AWAITING, SUBJECT_FOLDERS, build_preview_rows,
    export_preview_csv, suggest_folder,
)

_QT_APP = None


def catalog_book(catalog: Catalog, book: Path, root: Path):
    book.parent.mkdir(parents=True, exist_ok=True)
    if not book.exists():
        book.write_bytes(b"untouched mock PDF bytes for catalog inventory")
    stat = book.stat()
    catalog.upsert(
        path=book.resolve(), library_root=root.resolve(),
        title=book.stem, size_bytes=stat.st_size,
        modified_ns=stat.st_mtime_ns, page_count=1, cover_path="",
    )


def classification_for(filename: str, root: Path) -> tuple[str, str]:
    row = {"title": Path(filename).stem, "path": str(root / filename)}
    folder, confidence, _reason, _alternatives = suggest_folder(row, root)
    return folder, confidence


def test_book_of_blades_split_by_subject_and_kobold_series(tmp_path: Path):
    root = tmp_path / "books"
    assert classification_for(
        "Book of Blades - Naval Battles.pdf", root
    )[0] == "06 Campaign Systems/Warfare, Naval & Vehicles"
    assert classification_for(
        "Book of Blades - Expanding the Paladin.pdf", root
    )[0] == "01 Rulebook Stacks/Classes, Species & Feats"
    assert classification_for(
        "Book of Blades - Fabled Armory.pdf", root
    )[0] == "02 Arcane Collection/Magic Items & Equipment"
    assert classification_for(
        "Bloodied & Bruised - Mythic Odysseys of Theros.pdf", root
    )[0] == "03 Bestiary/Encounters & Bosses"
    assert classification_for(
        "Kobold Guide to Worldbuilding.pdf", root
    )[0] == "04 World Atlas/Cultures, Factions & Lore"
    assert classification_for(
        "Kobold Guide to Monsters.pdf", root
    )[0] == "03 Bestiary/Monsters & Dragons"
    assert classification_for(
        "Dungeon Masters Guide.pdf", root
    )[0] == "01 Rulebook Stacks/Core & Variant Rules"
    assert classification_for(
        "An Unidentified Compendium.pdf", root
    )[0] == AWAITING
    assert len(SUBJECT_FOLDERS) == len(set(SUBJECT_FOLDERS))
    assert all(len(Path(entry).parts) == 2 for entry in SUBJECT_FOLDERS)


def test_full_catalog_export_preserves_metadata_tags_and_shelves(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    a = root / "02_Collections" / "Book_of_Blades" / "Book of Blades - Naval Battles.pdf"
    b = root / "03_By_Topic" / "Bestiaries_And_Monsters" / "Unknown Codex.pdf"
    db = Catalog(tmp_path / "state" / "catalog.sqlite3")
    catalog_book(db, a, root)
    catalog_book(db, b, root)
    before_a = a.read_bytes()
    before_b = b.read_bytes()
    db.save_metadata(
        a.resolve(), display_title="Naval Battles (Annotated)",
        authors="Test Author", publisher="Test Press", edition="2014",
        game_system="5e", tags=["Warfare", "Naval"],
    )
    shelf_id = db.create_collection("Personal Warfare Research")
    db.set_book_collections(a.resolve(), {shelf_id})
    snapshot = db.available(root.resolve())

    rows, summary = build_preview_rows(db, root, snapshot)
    assert len(rows) == summary["total"] == 2
    assert all(row["Review Status"] == "PENDING" for row in rows)
    naval = next(row for row in rows if row["Filename"].endswith("Naval Battles.pdf"))
    assert naval["Display Title"] == "Naval Battles (Annotated)"
    assert naval["Authors"] == "Test Author"
    assert naval["Publisher"] == "Test Press"
    assert naval["Edition"] == "2014"
    assert naval["Tags"] == "Warfare; Naval"
    assert naval["Virtual Shelves"] == "Personal Warfare Research"
    assert naval["Approved Subject Folder"] == ""
    assert naval["Proposed Subject Folder"] == \
        "06 Campaign Systems/Warfare, Naval & Vehicles"
    assert naval["Proposed Relative Path"].endswith("/Book of Blades - Naval Battles.pdf")
    assert summary["missing"] == 0

    output = tmp_path / "Review" / "atlas_manifest.csv"
    export_preview_csv(output, rows)
    with output.open(encoding="utf-8-sig", newline="") as stream:
        parsed = list(csv.DictReader(stream))
    assert len(parsed) == 2
    assert parsed[0]["Review Status"] == "PENDING"
    assert parsed[0]["Source Path"] in {str(a.resolve()), str(b.resolve())}

    # Export must remain fully read-only, including the personal catalog.
    assert a.read_bytes() == before_a
    assert b.read_bytes() == before_b
    assert db.available(root.resolve()) == snapshot
    assert db.get_book_collections(a.resolve()) == {shelf_id}
    assert db.get_metadata(a.resolve())["tags"] == ["Warfare", "Naval"]
    db.close()


def test_manifest_flags_destination_collisions_and_unreadable_sources(tmp_path: Path):
    root = tmp_path / "books"
    root.mkdir()
    one = root / "SeriesA" / "Book of Blades - Naval Battles.pdf"
    two = root / "SeriesB" / "Book of Blades - Naval Battles.pdf"
    missing = root / "Not Yet" / "Unknown Codex.pdf"
    db = Catalog(tmp_path / "catalog.sqlite3")
    for book in (one, two, missing):
        catalog_book(db, book, root)
    missing.unlink()
    one.write_bytes(b"file changed since scan")

    rows, summary = build_preview_rows(db, root, db.available(root.resolve()))
    assert summary["total"] == 3
    assert summary["collisions"] == 2
    assert summary["missing"] == 1
    assert summary["unclassified"] == 1
    assert all(
        "TARGET_NAME_COLLISION" in r["Review Flags"]
        for r in rows if r["Filename"].endswith("Naval Battles.pdf")
    )
    assert any("CHANGED_SINCE_CATALOG_SCAN" in r["Review Flags"] for r in rows)
    assert any("SOURCE_MISSING_OR_UNREADABLE" in r["Review Flags"] for r in rows)
    assert any("NEEDS_SUBJECT_REVIEW" in r["Review Flags"] for r in rows)
    db.close()


def test_export_quotes_excel_formula_looking_metadata(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    book = root / "Feats of Valor.pdf"
    db = Catalog(tmp_path / "state.sqlite3")
    catalog_book(db, book, root)
    db.save_metadata(book.resolve(), display_title="=HYPERLINK(foo)")
    rows, _ = build_preview_rows(db, root, db.available(root.resolve()))
    target = tmp_path / "preview.csv"
    export_preview_csv(target, rows)
    with target.open(encoding="utf-8-sig", newline="") as stream:
        record = next(csv.DictReader(stream))
    assert record["Display Title"].startswith("'=HYPERLINK")
    assert db.get_metadata(book.resolve())["display_title"] == "=HYPERLINK(foo)"
    db.close()


def test_bookshelf_button_exports_only_csv_without_relocating(tmp_path: Path, monkeypatch):
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    app = _QT_APP
    root = tmp_path / "books"
    root.mkdir()
    book = root / "Book of Blades - Expanding the Paladin.pdf"
    data = tmp_path / "local"
    catalog = Catalog(data / "catalog.sqlite3")
    catalog_book(catalog, book, root)
    catalog.close()
    original = book.read_bytes()
    output = tmp_path / "folder-review.csv"

    # Avoid opening modal dialogs or running the separate cover-scanning thread.
    monkeypatch.setattr(LibraryWindow, "scan", lambda self: None)
    import atlas_library.app as window_module
    monkeypatch.setattr(
        window_module.QFileDialog, "getSaveFileName",
        lambda *args, **kwargs: (str(output), "CSV files (*.csv)"),
    )
    notices = []
    monkeypatch.setattr(
        window_module.QMessageBox, "information",
        lambda *args, **kwargs: notices.append(args[2]) if len(args) >= 3 else None,
    )

    window = LibraryWindow(data, root)
    try:
        assert window.folder_plan_btn.text() == "Plan Folders"
        window.folder_plan_btn.click()
        app.processEvents()
        assert output.is_file()
        assert "Prepared 1 catalog records" in notices[-1]
        assert book.read_bytes() == original
        assert not any(root.glob("01 Rulebook Stacks/*"))
        assert window.catalog.get_metadata(book.resolve())["display_title"] == ""
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
