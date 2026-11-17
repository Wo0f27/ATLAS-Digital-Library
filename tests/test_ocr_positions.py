"""Milestone 3 tests for cached OCR word coordinates and page-preview highlights."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from atlas_library.fulltext import PageIndex, index_pdf
from atlas_library.ocr_positions import (
    PagePositionIndex, PositionCancelled, match_word_indices,
    parse_tsv_words,
)
from atlas_library.ocr_preview import OcrPositionDialog
from atlas_library.reader import PdfReader
from atlas_library.catalog import Catalog


HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext\n"
)


def tsv(*words):
    lines = [HEADER]
    for index, (word, x, y, width, height) in enumerate(words, start=1):
        lines.append(
            f"5\t1\t1\t1\t1\t{index}\t{x}\t{y}\t"
            f"{width}\t{height}\t94\t{word}\n"
        )
    return "".join(lines)


def scanned_pdf(path: Path, *, color="white"):
    image = Image.new("RGB", (300, 420), color)
    image.save(path, format="PDF")


def test_tsv_parsing_normalizes_word_boxes_and_clips_outside():
    source = (
        HEADER
        + "4\t1\t1\t1\t1\t0\t0\t0\t100\t20\t-1\t\n"
        + "5\t1\t1\t1\t1\t1\t10\t20\t40\t15\t85\tdragon\n"
        + "5\t1\t1\t1\t1\t2\t-10\t20\t35\t16\t95\tgryphon\n"
        + "5\t1\t1\t1\t1\t3\t200\t20\t10\t10\t95\toutside\n"
        + "5\t1\t1\t1\t1\t4\t0\t0\t20\t20\t5\tlow\n"
    )
    words = parse_tsv_words(source, 100, 100)
    assert len(words) == 2
    assert words[0]["text"] == "dragon"
    assert words[0]["x"] == pytest.approx(0.1)
    assert words[0]["w"] == pytest.approx(0.4)
    assert words[1]["x"] == 0
    assert words[1]["w"] == pytest.approx(0.25)
    assert match_word_indices(words, "The [dragon] and the [gryphon]") == [0, 1]
    assert match_word_indices(words, "Absent [beholder]") == []
    assert match_word_indices(words, "A [dragon gryphon] pair") == [0, 1]
    with pytest.raises(ValueError):
        parse_tsv_words(source, 0, 100)


def test_positions_ocr_once_then_reuses_cache_without_tesseract(tmp_path: Path):
    pdf = tmp_path / "Scanned.pdf"
    scanned_pdf(pdf)
    before = pdf.read_bytes()
    dbpath = tmp_path / "local" / "search.sqlite3"
    index = PageIndex(dbpath)
    index_pdf(
        index, pdf, tmp_path,
        ocr_binary=Path("mock-OCR"),
        ocr=lambda page, exe: "The dragon guards an enchanted grove.",
    )
    index.close()
    binary = tmp_path / "fake-tesseract.exe"
    binary.write_bytes(b"mock")
    store = PagePositionIndex(dbpath)
    calls = []
    def recognize(img_path, exe, lang, cancel):
        calls.append(img_path.read_bytes()[:4])
        assert exe == binary
        assert lang == "eng"
        return tsv(("dragon", 60, 95, 100, 40), ("guards", 180, 95, 80, 40))

    first = store.locate(pdf, 0, "eng", binary, ocr_tsv=recognize)
    assert first["cached"] is False
    assert first["image"].startswith(b"\x89PNG")
    assert len(first["words"]) == 2
    assert match_word_indices(first["words"], "The [dragon] guards") == [0]
    assert len(calls) == 1

    second = store.locate(
        pdf, 0, "eng", None,
        ocr_tsv=lambda *args: pytest.fail("should skip cached OCR"),
    )
    assert second["cached"] is True
    assert second["words"] == first["words"]
    assert pdf.read_bytes() == before
    store.close()
    reopened = PagePositionIndex(dbpath)
    assert reopened.locate(pdf, 0, "eng", None)["cached"] is True
    reopened.close()

    # The cache is invalidated by the PDF fingerprint, not by library rescans.
    scanned_pdf(pdf, color="grey")
    updated = PagePositionIndex(dbpath)
    third = updated.locate(pdf, 0, "eng", binary, ocr_tsv=recognize)
    assert third["cached"] is False
    assert len(calls) == 2
    updated.close()


def test_position_failure_never_commits_incomplete_boxes(tmp_path: Path):
    pdf = tmp_path / "Scan.pdf"
    scanned_pdf(pdf)
    binpath = tmp_path / "engine.exe"
    binpath.write_bytes(b"mock")
    store = PagePositionIndex(tmp_path / "search.sqlite3")

    with pytest.raises(PositionCancelled):
        store.locate(
            pdf, 0, "eng", binpath,
            ocr_tsv=lambda *args: (_ for _ in ()).throw(PositionCancelled()),
        )
    assert store.connection.execute(
        "SELECT COUNT(*) FROM ocr_position_pages"
    ).fetchone()[0] == 0

    with pytest.raises(FileNotFoundError):
        store.locate(pdf, 0, "eng", None)
    assert store.connection.execute(
        "SELECT COUNT(*) FROM ocr_position_pages"
    ).fetchone()[0] == 0
    with pytest.raises(ValueError):
        store.locate(pdf, 4, "eng", binpath)
    store.close()


def test_preview_highlights_only_confident_excerpt_words(tmp_path: Path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(OcrPositionDialog, "_begin", lambda self: None)
    dialog = OcrPositionDialog(
        tmp_path / "search.sqlite3", tmp_path / "Scanned.pdf", 0,
        "a rare [dragon] in the highlands", "eng", "",
    )
    dialog.show()
    image = Image.new("RGB", (400, 600), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    result = {
        "image": buffer.getvalue(), "width": 400, "height": 600,
        "words": [
            {"text": "rare", "x": .1, "y": .1, "w": .1, "h": .03},
            {"text": "dragon", "x": .2, "y": .3, "w": .2, "h": .04},
        ],
        "cached": False, "page_index": 0,
    }
    dialog._display(result)
    app.processEvents()
    assert dialog.highlight_count == 1
    assert len(dialog.scene.items()) == 2  # page pixmap and one rectangle
    assert "Highlighted 1" in dialog.status.text()
    dialog.view.zoom(1.2)
    assert not dialog.view.auto_fit
    dialog.view.fit_page()
    assert dialog.view.auto_fit
    dialog.excerpt = "a [beholder]"
    dialog._display(result)
    assert dialog.highlight_count == 0
    assert "no uncertain highlights" in dialog.status.text()
    dialog.close()


def test_reader_shows_ocr_locator_only_for_ocr_matches(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    pdf = tmp_path / "Text.pdf"
    from test_fulltext import make_text_pdf
    make_text_pdf(pdf, ["The ancient dragon rules its forgotten fortress."])
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    reader = PdfReader(
        pdf, catalog,
        search_db_path=tmp_path / "search.sqlite3",
    )
    reader.resize(1000, 700)
    reader.show()
    for _ in range(25):
        app.processEvents()
        if reader._loaded:
            break
    assert reader._loaded
    path = str(pdf)
    reader.set_search_matches([
        {"path": path, "page_index": 0, "source": "ocr",
         "excerpt": "An [ancient] dragon"},
    ])
    assert reader.locate_ocr_button.isVisible()
    reader.set_search_matches([
        {"path": path, "page_index": 0, "source": "text",
         "excerpt": "The [dragon]"},
    ])
    assert not reader.locate_ocr_button.isVisible()
    reader.dispose()
    reader.close()
    catalog.close()
