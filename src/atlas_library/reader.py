"""Embedded PDF reader using Qt's native PDF widgets.

Reader state is stored in the local SQLite catalog; source PDF bytes are never changed.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QSize, Qt, QTimer, Signal, QSettings
from PySide6.QtPdf import QPdfBookmarkModel, QPdfDocument, QPdfSearchModel
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter,
    QTreeView, QVBoxLayout, QWidget, QListWidget, QListWidgetItem,
    QTabWidget, QInputDialog, QMessageBox, QTextBrowser,
)

from .catalog import Catalog
from .search_context import format_excerpt_html, source_label
from .ocr_preview import OcrPositionDialog


def clamp_page(page: int, page_count: int) -> int:
    """Zero-based page within a document, even for empty or damaged PDFs."""
    return max(0, min(int(page), max(0, int(page_count) - 1)))


class PdfReader(QWidget):
    """A book tab with scrolling, TOC, search, and persisted reading position."""

    failed = Signal(str)

    def __init__(
        self, path: Path, catalog: Catalog, parent: QWidget | None = None,
        start_page: int | None = None,
        search_db_path: Path | None = None,
    ):
        super().__init__(parent)
        self.path = Path(path)
        self.catalog = catalog
        self.search_db_path = Path(search_db_path) if search_db_path else None
        self._ocr_preview = None
        self._loaded = False
        self._restoring = True
        self._match_index = -1
        self._indexed_matches: list[dict] = []
        self._indexed_match_position = -1
        self._saved = catalog.get_reader_state(self.path) or {}
        if start_page is not None:
            self._saved["page_index"] = max(0, int(start_page))

        self.document = QPdfDocument(self)
        self.view = QPdfView(self)
        self.view.setDocument(self.document)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        # Trackpad pinch may arrive as a native gesture, a Qt pinch gesture,
        # or Ctrl+wheel depending on Windows driver/Qt platform integration.
        self.view.installEventFilter(self)
        self.view.viewport().installEventFilter(self)
        self.view.viewport().grabGesture(Qt.GestureType.PinchGesture)
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

        # QPdfView paints an empty white page while an uncached zoom size is
        # being rasterized. Temporarily show the previous rendered frame,
        # scaled to the new magnification, until the redraw settles.
        self._zoom_overlay = QLabel(self.view.viewport())
        self._zoom_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._zoom_overlay.hide()
        self._zoom_frame: QPixmap | None = None
        self._zoom_frame_factor = 1.0
        self._zoom_overlay_timer = QTimer(self)
        self._zoom_overlay_timer.setSingleShot(True)
        self._zoom_overlay_timer.setInterval(220)
        self._zoom_overlay_timer.timeout.connect(self._clear_zoom_overlay)

        self._build_ui()
        self._reload_bookmarks()
        self.navigator.currentPageChanged.connect(self._page_changed)
        self.view.zoomFactorChanged.connect(self._zoom_changed)
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
        self.zoom_out.clicked.connect(lambda: self._zoom(1 / 1.12))
        toolbar.addWidget(self.zoom_out)

        self.zoom_in = QPushButton("+")
        self.zoom_in.clicked.connect(lambda: self._zoom(1.12))
        toolbar.addWidget(self.zoom_in)

        self.zoom_mode = QComboBox()
        self.zoom_mode.addItem("Fit width", "width")
        self.zoom_mode.addItem("Fit page", "page")
        self.zoom_mode.addItem("Custom zoom", "custom")
        self.zoom_mode.currentIndexChanged.connect(self._set_zoom_mode)
        toolbar.addWidget(self.zoom_mode)

        self.zoom_readout = QLabel("100%")
        self.zoom_readout.setFixedWidth(49)
        self.zoom_readout.setToolTip("Zoom relative to the PDF page's point size")
        self.zoom_readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        toolbar.addWidget(self.zoom_readout)

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

        # OCR text is held in the local search index. Without OCR word
        # coordinates we can highlight its excerpt here, but not place a
        # misleading rectangle on the scanned page image itself.
        self.match_context = QWidget(self)
        match_layout = QVBoxLayout(self.match_context)
        match_layout.setContentsMargins(5, 4, 5, 4)
        controls = QHBoxLayout()
        self.match_heading = QLabel("")
        controls.addWidget(self.match_heading, 1)
        self.match_previous = QPushButton("Previous result")
        self.match_previous.clicked.connect(lambda: self._navigate_indexed_match(-1))
        controls.addWidget(self.match_previous)
        self.match_next = QPushButton("Next result")
        self.match_next.clicked.connect(lambda: self._navigate_indexed_match(1))
        controls.addWidget(self.match_next)
        self.locate_ocr_button = QPushButton("Locate OCR words")
        self.locate_ocr_button.setToolTip(
            "Recognize word positions on this scanned PDF page, "
            "then show a zoomable highlighted page preview"
        )
        self.locate_ocr_button.clicked.connect(self._locate_ocr_match)
        self.locate_ocr_button.hide()
        controls.addWidget(self.locate_ocr_button)
        self.match_close = QPushButton("Dismiss")
        self.match_close.clicked.connect(self.clear_search_matches)
        controls.addWidget(self.match_close)
        match_layout.addLayout(controls)

        self.match_excerpt = QTextBrowser(self)
        self.match_excerpt.setReadOnly(True)
        self.match_excerpt.setMaximumHeight(85)
        self.match_excerpt.setOpenExternalLinks(False)
        match_layout.addWidget(self.match_excerpt)
        self.match_note = QLabel(
            "The excerpt is highlighted in the local index. OCR word positions "
            "can be inspected separately using Locate OCR words."
        )
        self.match_note.setWordWrap(True)
        match_layout.addWidget(self.match_note)
        self.match_context.hide()
        outer.addWidget(self.match_context)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.side_tabs = QTabWidget()
        self.side_tabs.addTab(self.toc, "Contents")
        bookmarks_widget = QWidget()
        bookmarks_layout = QVBoxLayout(bookmarks_widget)
        self.bookmark_list = QListWidget()
        self.bookmark_list.itemDoubleClicked.connect(self._jump_to_bookmark)
        bookmarks_layout.addWidget(self.bookmark_list, 1)
        bookmark_buttons = QHBoxLayout()
        self.add_bookmark_button = QPushButton("+ Save page")
        self.add_bookmark_button.clicked.connect(self._add_bookmark)
        bookmark_buttons.addWidget(self.add_bookmark_button)
        self.remove_bookmark_button = QPushButton("Remove")
        self.remove_bookmark_button.clicked.connect(self._remove_bookmark)
        bookmark_buttons.addWidget(self.remove_bookmark_button)
        bookmarks_layout.addLayout(bookmark_buttons)
        self.side_tabs.addTab(bookmarks_widget, "My Bookmarks")
        splitter.addWidget(self.side_tabs)
        splitter.addWidget(self.view)
        splitter.setSizes([220, 900])
        outer.addWidget(splitter, 1)

        self.message = QLabel("")
        outer.addWidget(self.message)

    def set_search_matches(self, matches: list[dict], selected_index: int = 0):
        """Display indexed search context, including OCR-only text, in this tab."""
        self._indexed_matches = [
            row for row in matches
            if row.get("path") == str(self.path)
        ][:150]
        if not self._indexed_matches:
            self.clear_search_matches()
            return
        self.match_context.show()
        self._show_indexed_match(max(0, min(selected_index, len(self._indexed_matches) - 1)))

    def _show_indexed_match(self, position: int):
        if not self._indexed_matches:
            return
        self._indexed_match_position = position
        row = self._indexed_matches[position]
        page_index = max(0, int(row["page_index"]))
        source = source_label(str(row.get("source") or "text"))
        self.match_heading.setText(
            f"Indexed result {position + 1} of {len(self._indexed_matches)}"
            f"  •  Page {page_index + 1}  •  {source}"
        )
        self.match_excerpt.setHtml(
            '<div style="color:#eee6d5;">'
            + format_excerpt_html(str(row.get("excerpt") or ""))
            + '</div>'
        )
        self.match_previous.setEnabled(position > 0)
        self.match_next.setEnabled(position < len(self._indexed_matches) - 1)
        self.locate_ocr_button.setVisible(
            row.get("source") in ("ocr", "mixed")
            and self.search_db_path is not None
        )
        if self._loaded:
            self.goto_page(page_index)
        else:
            self._saved["page_index"] = page_index

    def _locate_ocr_match(self):
        if self.search_db_path is None or not self._indexed_matches:
            return
        row = self._indexed_matches[self._indexed_match_position]
        if row.get("source") not in ("ocr", "mixed"):
            return
        if self._ocr_preview is not None:
            self._ocr_preview.close()
        settings = QSettings("ATLAS", "DigitalLibrary")
        dialog = OcrPositionDialog(
            self.search_db_path, self.path, int(row["page_index"]),
            str(row.get("excerpt") or ""),
            str(settings.value("ocr_language", "eng")),
            str(settings.value("tesseract_path", "")),
            parent=self,
        )
        self._ocr_preview = dialog
        dialog.destroyed.connect(
            lambda _obj=None, target=dialog: self._clear_preview_reference(target)
        )
        dialog.show()

    def _clear_preview_reference(self, target):
        if self._ocr_preview is target:
            self._ocr_preview = None

    def _navigate_indexed_match(self, direction: int):
        candidate = self._indexed_match_position + direction
        if 0 <= candidate < len(self._indexed_matches):
            self._show_indexed_match(candidate)

    def clear_search_matches(self):
        self._indexed_matches = []
        self._indexed_match_position = -1
        self.match_context.hide()
        self.locate_ocr_button.hide()
        self.match_excerpt.clear()

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
        self._zoom_changed(self.view.zoomFactor())

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

    def eventFilter(self, watched, event):
        """Leave ordinary scrolling alone; zoom only on explicit gestures."""
        if watched in (self.view, self.view.viewport()) and self._loaded:
            kind = event.type()
            if kind == QEvent.Type.Resize:
                QTimer.singleShot(0, lambda: self._zoom_changed(self.view.zoomFactor()))
            if kind == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                angle = event.angleDelta().y()
                pixels = event.pixelDelta().y()
                delta = (angle / 120.0) if angle else (pixels / 120.0)
                if delta:
                    self._zoom(1.12 ** delta, event.position())
                    event.accept()
                    return True
            if kind == QEvent.Type.NativeGesture and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                # The Qt-native zoom value is an incremental scale delta.
                magnitude = max(0.5, min(1.5, 1.0 + float(event.value())))
                self._zoom(magnitude, event.position())
                event.accept()
                return True
            if kind == QEvent.Type.Gesture:
                pinch = event.gesture(Qt.GestureType.PinchGesture)
                if pinch is not None:
                    previous = float(pinch.lastScaleFactor())
                    current = float(pinch.scaleFactor())
                    if previous > 0 and current > 0:
                        self._zoom(current / previous, pinch.centerPoint())
                    event.accept()
                    return True
        return super().eventFilter(watched, event)

    def _effective_zoom(self) -> float:
        """Actual visible scale, even when Qt is fitting a page automatically."""
        mode = self.view.zoomMode()
        if mode == QPdfView.ZoomMode.Custom or self.document.pageCount() < 1:
            return self.view.zoomFactor()
        page = clamp_page(self.navigator.currentPage(), self.document.pageCount())
        size = self.document.pagePointSize(page)
        if size.width() <= 0 or size.height() <= 0:
            return self.view.zoomFactor()
        screen = QGuiApplication.primaryScreen()
        dpi_factor = (screen.logicalDotsPerInch() / 72.0) if screen else 1.0
        width = size.width() * dpi_factor
        height = size.height() * dpi_factor
        margins = self.view.documentMargins()
        viewport = self.view.viewport()
        available_width = max(1, viewport.width() - margins.left() - margins.right())
        available_height = max(1, viewport.height() - margins.top()
                               - margins.bottom() - self.view.pageSpacing())
        fit_width = available_width / width
        if mode == QPdfView.ZoomMode.FitToWidth:
            return fit_width
        return min(fit_width, available_height / height)

    def _show_zoom_preview(
        self, current_factor: float, next_factor: float, anchor: QPointF
    ):
        """Mask transient blank Qt PDF paints with a scaled prior frame."""
        viewport = self.view.viewport()
        if not viewport.isVisible() or viewport.width() < 1 or viewport.height() < 1:
            return
        if self._zoom_frame is None:
            frame = viewport.grab()
            if frame.isNull():
                return
            self._zoom_frame = frame
            self._zoom_frame_factor = max(0.001, current_factor)

        dpr = max(1.0, viewport.devicePixelRatioF())
        canvas = QPixmap(
            QSize(max(1, round(viewport.width() * dpr)),
                  max(1, round(viewport.height() * dpr)))
        )
        canvas.setDevicePixelRatio(dpr)
        canvas.fill(QColor("#202834"))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        scale = next_factor / self._zoom_frame_factor
        painter.translate(anchor.x(), anchor.y())
        painter.scale(scale, scale)
        painter.translate(-anchor.x(), -anchor.y())
        painter.drawPixmap(0, 0, self._zoom_frame)
        painter.end()

        self._zoom_overlay.setGeometry(viewport.rect())
        self._zoom_overlay.setPixmap(canvas)
        self._zoom_overlay.show()
        self._zoom_overlay.raise_()
        self._zoom_overlay_timer.start()

    def _clear_zoom_overlay(self):
        self._zoom_overlay.hide()
        self._zoom_overlay.clear()
        self._zoom_frame = None

    def _reload_bookmarks(self):
        self.bookmark_list.clear()
        for row in self.catalog.list_bookmarks(self.path):
            item = QListWidgetItem(
                f"Page {row['page_index'] + 1} — {row['label']}"
            )
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, row["page_index"])
            self.bookmark_list.addItem(item)

    def _add_bookmark(self):
        if not self._loaded or self.document.pageCount() == 0:
            return
        page_index = clamp_page(self.navigator.currentPage(), self.document.pageCount())
        label, accepted = QInputDialog.getText(
            self, "Save page bookmark", "Bookmark label:",
            text=f"Page {page_index + 1}",
        )
        if not accepted:
            return
        label = label.strip()
        if not label:
            return
        try:
            bookmark_id = self.catalog.add_bookmark(self.path, page_index, label)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Unable to save bookmark", str(exc))
            return
        self._reload_bookmarks()
        for index in range(self.bookmark_list.count()):
            item = self.bookmark_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == bookmark_id:
                self.bookmark_list.setCurrentItem(item)
                break
        self.side_tabs.setCurrentIndex(1)

    def _jump_to_bookmark(self, item: QListWidgetItem):
        page = item.data(Qt.ItemDataRole.UserRole + 1)
        if page is not None:
            self.goto_page(int(page))

    def _remove_bookmark(self):
        item = self.bookmark_list.currentItem()
        if item is None:
            return
        bookmark_id = item.data(Qt.ItemDataRole.UserRole)
        if self.catalog.delete_bookmark(self.path, int(bookmark_id)):
            self._reload_bookmarks()

    def _zoom(self, multiplier: float, anchor: QPointF | None = None):
        if not self._loaded or multiplier <= 0:
            return
        old_factor = max(0.001, self._effective_zoom())
        new_factor = max(0.25, min(5.0, old_factor * multiplier))
        if abs(new_factor - old_factor) < 0.0001:
            return
        viewport = self.view.viewport()
        if anchor is None:
            anchor = QPointF(viewport.width() / 2.0, viewport.height() / 2.0)
        self._show_zoom_preview(old_factor, new_factor, anchor)
        # Preserve approximately the content under the mouse/trackpad cursor.
        horizontal = self.view.horizontalScrollBar()
        vertical = self.view.verticalScrollBar()
        left, top = horizontal.value(), vertical.value()
        self.zoom_mode.blockSignals(True)
        self.zoom_mode.setCurrentIndex(self.zoom_mode.findData("custom"))
        self.zoom_mode.blockSignals(False)
        self.view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.view.setZoomFactor(new_factor)
        ratio = new_factor / old_factor

        def restore_anchor():
            if not self._loaded:
                return
            horizontal.setValue(round((left + anchor.x()) * ratio - anchor.x()))
            vertical.setValue(round((top + anchor.y()) * ratio - anchor.y()))

        QTimer.singleShot(0, restore_anchor)
        self._schedule_save()

    def _zoom_changed(self, _factor: float):
        self.zoom_readout.setText(f"{round(self._effective_zoom() * 100)}%")
        self._schedule_save()

    def _set_zoom_mode(self, _index: int):
        mode = self.zoom_mode.currentData()
        if mode == "width":
            self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        elif mode == "page":
            self.view.setZoomMode(QPdfView.ZoomMode.FitInView)
        else:
            self.view.setZoomMode(QPdfView.ZoomMode.Custom)
        self._zoom_changed(self.view.zoomFactor())
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
        if self._ocr_preview is not None:
            self._ocr_preview.close()
            self._ocr_preview = None
        self._zoom_overlay_timer.stop()
        self._clear_zoom_overlay()
        self.save_state()
        self.document.close()
