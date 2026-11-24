"""Duplicate review: all decisions remain read-only and human-controlled."""
from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

from atlas_library.duplicate_review import (
    ReviewCancelled, _title_key, build_duplicate_review, export_duplicate_review,
)


def record(path: Path) -> dict:
    stat = path.stat()
    return {
        "path": str(path), "title": path.stem, "display_title": "",
        "edition": "", "size_bytes": stat.st_size, "modified_ns": stat.st_mtime_ns,
    }


def test_exact_bytes_and_possible_edition_are_distinct(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    first = tmp_path / "one" / "Dungeon Master's Guide (5e).pdf"
    second = tmp_path / "two" / "Dungeon Masters Guide.pdf"
    third = tmp_path / "two" / "Dungeon Masters Guide v2.pdf"
    first.write_bytes(b"same sourcebook content")
    second.write_bytes(first.read_bytes())
    third.write_bytes(b"a different edition of the sourcebook")
    rows, summary = build_duplicate_review(tmp_path, map(record, (first, second, third)))
    assert summary["exact_groups"] == 1
    assert summary["exact_files"] == 2
    assert summary["possible_edition_groups"] == 1
    assert summary["files_hashed"] == 2
    exact = [row for row in rows if row["Match Type"] == "EXACT_SHA256"]
    assert {row["Source Path"] for row in exact} == {str(first), str(second)}
    assert len({row["SHA-256"] for row in exact}) == 1
    assert all(row["Decision"] == "PENDING" for row in rows)


def test_same_size_different_bytes_never_exact(tmp_path):
    first = tmp_path / "Handbook.pdf"
    second = tmp_path / "Handbook (5e).pdf"
    first.write_bytes(b"ABCDEFG")
    second.write_bytes(b"HIJKLMN")
    rows, summary = build_duplicate_review(tmp_path, [record(first), record(second)])
    assert summary["exact_groups"] == 0
    assert summary["possible_edition_groups"] == 1
    assert all(row["Match Type"] != "EXACT_SHA256" for row in rows)


def test_changed_and_missing_sources_are_not_claimed_identical(tmp_path):
    first = tmp_path / "Same.pdf"
    second = tmp_path / "Same (5e).pdf"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    prior = record(first)
    first.write_bytes(b"changed")
    second.unlink()
    rows, summary = build_duplicate_review(tmp_path, [prior, {
        "path": str(second), "title": "Same", "size_bytes": 4,
        "modified_ns": 0,
    }])
    assert summary["source_warnings"] == 2
    assert summary["exact_groups"] == 0
    assert set(row["Match Type"] for row in rows) == {"SOURCE_WARNING"}


def test_interruption_is_supported(tmp_path):
    book = tmp_path / "Book.pdf"
    book.write_bytes(b"data")
    with pytest.raises(ReviewCancelled):
        build_duplicate_review(tmp_path, [record(book)], interrupted=lambda: True)


def test_safe_csv_export_outside_library(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    report = tmp_path / "report.csv"
    export_duplicate_review(report, [{
        "Match Type": "SOURCE_WARNING",
        "Filename": "=danger.pdf",
        "Review Notes": "@injection",
    }], root)
    with report.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows[0]["Filename"] == "'=danger.pdf"
    assert rows[0]["Review Notes"] == "'@injection"
    with pytest.raises(ValueError, match="outside"):
        export_duplicate_review(root / "report.csv", [], root)


def test_titles_are_only_candidates_not_edition_facts():
    assert _title_key("Tasha’s Cauldron of Everything (5e).pdf") == _title_key(
        "Tashas Cauldron of Everything.pdf"
    )
    assert _title_key("Legendary Ledger - Book 2.pdf") != _title_key(
        "Legendary Ledger - Book 3.pdf"
    )
