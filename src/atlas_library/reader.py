"""Embedded PDF reader using Qt's native PDF widgets.

Reader state is stored in the local SQLite catalog; source PDF bytes are never changed.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtPdf import QPdfBookmarkModel, QPdfDocument, QPdfSearchModel
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter,
    QTreeView, QVBoxLayout, QWidget,
)

from .catalog import Catalog


def clamp_page(page: int, page_count: int) -> int:
    """Zero-based page within a document, even for empty or damaged PDFs."""
    return max(0, min(int(page), max(0, int(page_count) - 1)))


class PdfReader(QWidget):
    """A book tab with scrolling, TOC, search, and persisted reading position."""

    failed = Signal(str)

    def __init__(self, path: Path, catalog: Catalog, parent: QWidget | None = None):
        super().__init__(parent)
        self.path = Path(path)
        self.catalog = catalog
        self._loaded = False
        self._restoring = True
        self._match_index = -1
        self._saved = catalog.get_reader_state(self.path) or {}

        self.document = QPdfDocument(self)
        self.view = QPdfView(self)
        self.view.setDocument(self.document)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.navigator = self.view.pageNavigator()

        self.bookmarks = QPdfBookmarkModel(self)
        self.bookmarks.setDocument(self.document)
        self.toc = QTreeView(self)
        self.toc.setModel(self.bookmarks)
        self.toc.setHeaderHidden(True)
        self.toc.clicked.connect(self._on_bookmark)
        self.toc.setMinimumWidth(165)

        self.search_model = QPdfSearchModel(self)
        self.search_model.setDocument(self.document)
        self.view.setSearchModel(self.search_model)

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(700)
        self.save_timer.timeout.connect(self.save_state)

        self._build_ui()
        self.navigator.currentPageChanged.connect(self._page_changed)
        self.view.zoomFactorChanged.connect(lambda _factor: self._schedule_save())
        self.search_model.countChanged.connect(self._on_search_count)
        self.document.statusChanged.connect(self._on_document_status)

        # Qt can finish opening short documents synchronously. Handle both cases.
        self.document.load(str(self.path))
        if self.document.status() == QPdfDocument.Status.Ready:
            self._ready()
        elif self.document.status() == QPdfDocument.Status.Error:
            self._fail("This PDF could not be opened. It may be damaged or password protected.")

    def _build_ui(self):
        outer = QVBoxLayout(self)
        toolbar = QHBoxLayout()

        self.previous = QPushButton("Previous")
        self.previous.clicked.connect(lambda: self.goto_page(self.navigator.currentPage() - 1))
        toolbar.addWidget(self.previous)

        self.page_number = QLineEdit("1")
        self.page_number.setFixedWidth(62)
        self.page_number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_number.returnPressed.connect(self._page_requested)
        toolbar.addWidget(self.page_number)

        self.page_total = QLabel("/ ...")
        toolbar.addWidget(self.page_total)

        self.next = QPushButton("Next")
        self.next.clicked.connect(lambda: self.goto_page(self.navigator.currentPage() + 1))
        toolbar.addWidget(self.next)

        self.zoom_out = QPushButton("-")
        self.zoom_out.clicked.connect(lambda: self._zoom(0.8))
        toolbar.addWidget(self.zoom_out)

        self.zoom_in = QPushButton("+")
        self.zoom_in.clicked.connect(lambda: self._zoom(1.25))
        toolbar.addWidget(self.zoom_in)

        self.zoom_mode = QComboBox()
        self.zoom_mode.addItem("Fit width", "width")
        self.zoom_mode.addItem("Fit page", "page")
        self.zoom_mode.addItem("Custom zoom", "custom")
        self.zoom_mode.currentIndexChanged.connect(self._set_zoom_mode)
        toolbar.addWidget(self.zoom_mode)

        self.page_mode = QComboBox()
        self.page_mode.addItem("Continuous scroll", "continuous")
        self.page_mode.addItem("Single page", "single")
        self.page_mode.currentIndexChanged.connect(self._set_page_mode)
        toolbar.addWidget(self.page_mode)

        toolbar.addStretch(1)
        outer.addLayout(toolbar)

        findbar = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Find in this book (extractable text only)...")
        self.query.returnPressed.connect(lambda: self._step_match(1))
        self.query.textChanged.connect(self._search)
        findbar.addWidget(self.query, 1)

        self.prev_match = QPushButton("Find previous")
        self.prev_match.clicked.connect(lambda: self._step_match(-1))
        findbar.addWidget(self.prev_match)
        self.next_match = QPushButton("Find next")
        self.next_match.clicked.connect(lambda: self._step_match(1))
        findbar.addWidget(self.next_match)
        self.match_count = QLabel("0 results")
        findbar.addWidget(self.match_count)
        outer.addLayout(findbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.toc)
        splitter.addWidget(self.view)
        splitter.setSizes([220, 900])
        outer.addWidget(splitter, 1)

        self.message = QLabel("")
        outer.addWidget(self.message)

    def _on_document_status(self, status):
        if status == QPdfDocument.Status.Ready:
            self._ready()
        elif status == QPdfDocument.Status.Error:
            self._fail("Could not load this PDF (corrupt, encrypted, or unsupported).")

    def _ready(self):
        if self._loaded:
            return
        self._loaded = True
        count = self.document.pageCount()
        self.page_total.setText(f"/ {count}")
        self.message.setText(f"{self.path.name}  |  {count} pages")
        if count <= 0:
            self._restoring = False
            return
        saved_mode = self._saved.get("zoom_mode", "width")
        saved_pages = self._saved.get("page_mode", "continuous")
        self.zoom_mode.setCurrentIndex(max(0, self.zoom_mode.findData(saved_mode)))
        self.page_mode.setCurrentIndex(max(0, self.page_mode.findData(saved_pages)))
        if saved_mode == "custom":
            self.view.setZoomMode(QPdfView.ZoomMode.Custom)
            self.view.setZoomFactor(max(0.25, min(5.0, float(self._saved.get("zoom_factor", 1)))))
        # The widget is created before its tab becomes visible. Jumping too
        # early may be reset by Qt during the first layout/scroll calculation.
        QTimer.singleShot(0, self._restore_position)

    def _restore_position(self):
        if not self._loaded or self.document.pageCount() < 1:
            self._restoring = False
            return
        self.goto_page(clamp_page(
            self._saved.get("page_index", 0), self.document.pageCount()
        ))
        self._restoring = False

    def _fail(self, message: str):
        self.message.setText(message)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.failed.emit(message)

    def goto_page(self, page: int):
        if not self._loaded or self.document.pageCount() < 1:
            return
        number = clamp_page(page, self.document.pageCount())
        self.navigator.jump(number, QPointF(0, 0))
        self._page_changed(number)

    def _page_requested(self):
        try:
            number = int(self.page_number.text())
        except ValueError:
            number = self.navigator.currentPage() + 1
        self.goto_page(number - 1)

    def _page_changed(self, page: int):
        if not self._loaded:
            return
        self.page_number.setText(str(clamp_page(page, self.document.pageCount()) + 1))
        self.previous.setEnabled(page > 0)
        self.next.setEnabled(page < self.document.pageCount() - 1)
        self._schedule_save()

    def _on_bookmark(self, index):
        page = index.data(QPdfBookmarkModel.Role.Page)
        if page is not None:
            self.goto_page(int(page))

    def _zoom(self, multiplier: float):
        if not self._loaded:
            return
        factor = max(0.25, min(5.0, self.view.zoomFactor() * multiplier))
        self.zoom_mode.setCurrentIndex(self.zoom_mode.findData("custom"))
        self.view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.view.setZoomFactor(factor)
        self._schedule_save()

    def _set_zoom_mode(self, _index: int):
        mode = self.zoom_mode.currentData()
        if mode == "width":
            self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        elif mode == "page":
            self.view.setZoomMode(QPdfView.ZoomMode.FitInView)
        else:
            self.view.setZoomMode(QPdfView.ZoomMode.Custom)
        self._schedule_save()

    def _set_page_mode(self, _index: int):
        mode = self.page_mode.currentData()
        self.view.setPageMode(
            QPdfView.PageMode.SinglePage if mode == "single"
            else QPdfView.PageMode.MultiPage
        )
        self._schedule_save()

    def _search(self, text: str):
        self._match_index = -1
        self.view.setCurrentSearchResultIndex(-1)
        self.search_model.setSearchString(text)
        if not text:
            self.match_count.setText("0 results")

    def _on_search_count(self, count: int):
        self.match_count.setText(f"{count} result{'s' if count != 1 else ''}")

    def _step_match(self, direction: int):
        count = self.search_model.count()
        if not count:
            return
        self._match_index = (self._match_index + direction) % count
        self.view.setCurrentSearchResultIndex(self._match_index)
        self.navigator.jump(self.search_model.resultAtIndex(self._match_index))

    def _schedule_save(self):
        if self._loaded and not self._restoring:
            self.save_timer.start()

    def save_state(self):
        if not self._loaded or not self.path.is_file():
            return
        self.save_timer.stop()
        self.catalog.save_reader_state(
            self.path,
            page_index=clamp_page(self.navigator.currentPage(), self.document.pageCount()),
            zoom_mode=self.zoom_mode.currentData() or "width",
            zoom_factor=max(0.25, min(5.0, self.view.zoomFactor())),
            page_mode=self.page_mode.currentData() or "continuous",
        )

    def dispose(self):
        self.save_state()
        self.document.close()
