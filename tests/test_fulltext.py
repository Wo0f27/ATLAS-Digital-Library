"""Full-text index tests with synthetic, locally generated PDF fixtures."""

from pathlib import Path

import pytest
from PIL import Image

from atlas_library.fulltext import (
    IndexCancelled, PageIndex, has_meaningful_text, index_pdf,
)


def make_text_pdf(path: Path, text_pages: list[str]):
    """Minimal valid multipage PDF containing extractable Helvetica text."""
    count = len(text_pages)
    page_ids = [3 + i for i in range(count)]
    stream_ids = [3 + count + i for i in range(count)]
    font_id = 3 + 2 * count
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] "
            f"/Count {count} >>"
        ).encode(),
    ]
    for stream_id in stream_ids:
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {stream_id} 0 R >>"
            ).encode()
        )
    for text in text_pages:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 15 Tf 60 730 Td ({safe}) Tj ET".encode("latin-1")
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode()
            + stream + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects)+1}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(
        (f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\n"
         f"startxref\n{xref}\n%%EOF\n").encode()
    )
    path.write_bytes(pdf)


def test_text_search_results_link_to_correct_pages(tmp_path: Path):
    root = tmp_path / "books"
    root.mkdir()
    book = root / "Dragons.pdf"
    make_text_pdf(
        book,
        [
            "The ancient green dragon guards the forest and its treasure.",
            "A purple worm burrows underneath the sunlit stone temple.",
            "The ancient blue dragon rules the harsh desert kingdom.",
        ],
    )
    original = book.read_bytes()
    db = PageIndex(tmp_path / "state" / "search.sqlite3")
    indexed = index_pdf(db, book, root)
    assert indexed["pages"] == 3
    assert indexed["indexed"] == 3
    assert indexed["pending_ocr"] == 0

    hits = db.search("ancient dragon", root, mode="words")
    assert {h["page_index"] for h in hits} == {0, 2}
    assert all("[ancient]" in h["excerpt"].lower() for h in hits)
    assert {h["source"] for h in hits} == {"text"}
    assert [h["page_index"] for h in db.search("purple worm", root, mode="phrase")] == [1]
    assert [h["page_index"] for h in db.search('"green dragon" OR "blue dragon"', root, mode="advanced")] == [0, 2]
    assert db.search("dragon", tmp_path / "other-root") == []
    assert db.search("kraken", root) == []
    assert index_pdf(db, book, root)["indexed"] == 0
    assert book.read_bytes() == original
    db.close()


def test_changed_pdf_rebuilds_index_without_old_hits(tmp_path: Path):
    root = tmp_path / "books"
    root.mkdir()
    book = root / "Revision.pdf"
    make_text_pdf(book, ["Ancient red dragon treasure is preserved forever."])
    db = PageIndex(tmp_path / "search.sqlite3")
    index_pdf(db, book, root)
    assert db.search("dragon", root)

    make_text_pdf(book, ["An old noble library contains rare books and historical scrolls."])
    index_pdf(db, book, root)
    assert not db.search("dragon", root)
    assert db.search("library", root)
    db.close()


def test_interrupted_index_resume_is_page_granular(tmp_path: Path):
    root = tmp_path / "books"
    root.mkdir()
    book = root / "Big.pdf"
    make_text_pdf(
        book,
        ["Long adventure material about dwarven citadels and mountain stone."]
        * 3,
    )
    db = PageIndex(tmp_path / "search.sqlite3")
    reached = [0]
    with pytest.raises(IndexCancelled):
        index_pdf(
            db, book, root,
            cancel=lambda: reached[0] >= 1,
            progress=lambda page, total, source: reached.__setitem__(0, page),
        )
    assert db.file_state(book)["next_page"] == 1
    assert len(db.search("citadels", root)) == 1
    db.close()

    resumed = PageIndex(tmp_path / "search.sqlite3")
    result = index_pdf(resumed, book, root)
    assert result["indexed"] == 2
    assert len(resumed.search("citadels", root)) == 3
    assert resumed.file_state(book)["state"] == "complete"
    resumed.close()


def test_image_pdf_waits_for_ocr_then_searches_recognized_text(tmp_path: Path):
    root = tmp_path / "scanned"
    root.mkdir()
    book = root / "Scanned.pdf"
    image = Image.new("RGB", (320, 440), "white")
    image.save(book, "PDF")
    original = book.read_bytes()

    db = PageIndex(tmp_path / "search.sqlite3")
    before = index_pdf(db, book, root)
    assert before["pending_ocr"] == 1
    assert db.file_state(book)["state"] == "needs_ocr"
    assert not db.search("gryphon", root)

    calls = []
    result = index_pdf(
        db, book, root,
        ocr_binary=Path("fake-tesseract"),
        ocr=lambda page, executable: (
            calls.append(executable) or
            "The gryphon protects a remote northern mountain fortress."
        ),
    )
    assert result["indexed"] == 1
    assert result["pending_ocr"] == 0
    assert calls == [Path("fake-tesseract")]
    assert db.search("gryphon", root)[0]["source"] == "ocr"
    assert index_pdf(
        db, book, root, ocr_binary=Path("fake-tesseract"),
        ocr=lambda page, executable: "should not run",
    )["indexed"] == 0
    assert book.read_bytes() == original
    db.close()


def test_search_validation_and_fts_injection_safety(tmp_path: Path):
    index = PageIndex(tmp_path / "search.sqlite3")
    assert not has_meaningful_text("Chapter 1")
    assert has_meaningful_text("Ancient dragonborn scholars maintained their libraries")
    assert index.search("", tmp_path) == []
    with pytest.raises(ValueError, match="Unknown"):
        index.search("dragon", tmp_path, mode="unspecified")
    with pytest.raises(ValueError, match="Invalid FTS5"):
        index.search('"unclosed phrase', tmp_path, mode="advanced")
    assert index.search("dragon'); DROP TABLE page_text; --", tmp_path, mode="words") == []
    index.close()
