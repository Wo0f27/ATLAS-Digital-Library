"""Exercise the native reader with a real generated PDF on Windows CI."""

from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import QApplication

from atlas_library.catalog import Catalog
from atlas_library.reader import PdfReader


def test_embedded_pdf_opens_and_remembers_page(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    pdf = tmp_path / "tiny sample.pdf"

    one = Image.new("RGB", (220, 300), (245, 245, 245))
    two = Image.new("RGB", (220, 300), (230, 230, 230))
    one.save(pdf, "PDF", save_all=True, append_images=[two])
    original = pdf.read_bytes()

    catalog = Catalog(tmp_path / "local.sqlite3")
    reader = PdfReader(pdf, catalog)
    for _ in range(25):
        app.processEvents()
        if reader._loaded:
            break
    assert reader._loaded
    assert reader.document.pageCount() == 2

    reader.goto_page(1)
    app.processEvents()
    assert reader.navigator.currentPage() == 1
    reader.page_mode.setCurrentIndex(reader.page_mode.findData("single"))
    reader.save_state()
    reader.dispose()
    reader.close()
    assert pdf.read_bytes() == original

    state = catalog.get_reader_state(pdf)
    assert state["page_index"] == 1
    assert state["page_mode"] == "single"

    reopened = PdfReader(pdf, catalog)
    for _ in range(25):
        app.processEvents()
        if reopened._loaded:
            break
    assert reopened._loaded
    assert reopened.navigator.currentPage() == 1
    reopened.dispose()
    reopened.close()
    catalog.close()
