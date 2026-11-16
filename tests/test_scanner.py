from pathlib import Path

from atlas_library.catalog import Catalog
from atlas_library.scanner import scan_library


def test_scan_is_read_only_and_tracks_missing(tmp_path: Path):
    root = tmp_path / "My Library"
    nested = root / "02_Collections" / "Bestiaries"
    nested.mkdir(parents=True)
    pdf = nested / "Creature.PDF"
    original = b"not a real PDF; scanner should still inventory it"
    pdf.write_bytes(original)

    database = tmp_path / "appdata" / "catalog.sqlite3"
    cache = tmp_path / "appdata" / "covers"

    found = list(scan_library(root, database, cache))
    assert len(found) == 1
    assert found[0]["title"] == "Creature"
    assert found[0]["scan_error"]
    assert pdf.read_bytes() == original

    pdf.unlink()
    assert list(scan_library(root, database, cache)) == []
    catalog = Catalog(database)
    assert catalog.get(pdf.resolve())["status"] == "missing"
    catalog.close()


def test_unrelated_files_are_ignored(tmp_path: Path):
    root = tmp_path / "books"
    root.mkdir()
    (root / "readme.txt").write_text("hello")
    assert list(scan_library(root, tmp_path / "db.sqlite3", tmp_path / "cache")) == []
