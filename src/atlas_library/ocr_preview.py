"""Separate, zoomable page preview for OCR word-coordinate highlights.

The reader's Qt PDF view has no persisted OCR word bounds. This preview uses
the same page raster image that Tesseract inspected, so markers align with the
OCR coordinates. No annotation is written into the PDF.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout,
)

from .fulltext import resolve_tesseract
from .ocr_positions import PagePositionIndex, PositionCancelled, match_word_indices


class PositionWorker(QThread):
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(
        self, database: Path, path: Path, page_index: int,
        language: str, configured_tesseract: str,
    ):
        super().__init__()
        self.database = Path(database)
        self.path = Path(path)
        self.page_index = int(page_index)
        self.language = language
        self.configured_tesseract = configured_tesseract

    def run(self):
        positions = None
        try:
            positions = PagePositionIndex(self.database)
            resolved = resolve_tesseract(self.configured_tesseract)
            result = positions.locate(
                self.path, self.page_index, self.language, resolved,
                cancel=self.isInterruptionRequested,
            )
            if not self.isInterruptionRequested():
                self.completed.emit(result)
        except PositionCancelled:
            return
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if positions is not None:
                positions.close()


class FittingPageView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.auto_fit = True
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )

    def wheelEvent(self, event):
        """Ctrl+wheel zooms, while ordinary wheel events scroll the page."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            angle = event.angleDelta().y()
            pixels = event.pixelDelta().y()
            delta = (angle / 120.0) if angle else (pixels / 120.0)
            if delta:
                self.zoom(1.12 ** delta)
                event.accept()
                return
        super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.auto_fit and self.scene() is not None:
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def zoom(self, factor: float):
        self.auto_fit = False
        self.scale(factor, factor)

    def fit_page(self):
        self.auto_fit = True
        if self.scene() is not None:
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


class OcrPositionDialog(QDialog):
    def __init__(
        self, database: Path, path: Path, page_index: int, excerpt: str,
        language: str, tesseract_path: str, parent=None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(f"OCR word location — {Path(path).name}, page {page_index + 1}")
        self.resize(900, 790)
        self.database = Path(database)
        self.path = Path(path)
        self.page_index = int(page_index)
        self.excerpt = excerpt
        self.language = language
        self.tesseract_path = tesseract_path
        self.worker: PositionWorker | None = None
        self.highlight_count = 0

        layout = QVBoxLayout(self)
        self.description = QLabel(
            "OCR word positions are shown on a separate rendering of this PDF "
            "page. Original sourcebook and search index remain unchanged."
        )
        self.description.setWordWrap(True)
        layout.addWidget(self.description)

        self.view = FittingPageView(self)
        self.scene = QGraphicsScene(self.view)
        self.view.setScene(self.scene)
        layout.addWidget(self.view, 1)

        controls = QHBoxLayout()
        self.zoom_in = QPushButton("Zoom +")
        self.zoom_in.clicked.connect(lambda: self.view.zoom(1.2))
        controls.addWidget(self.zoom_in)
        self.zoom_out = QPushButton("Zoom −")
        self.zoom_out.clicked.connect(lambda: self.view.zoom(1 / 1.2))
        controls.addWidget(self.zoom_out)
        self.fit_button = QPushButton("Fit page")
        self.fit_button.clicked.connect(self.view.fit_page)
        controls.addWidget(self.fit_button)
        controls.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        controls.addWidget(self.close_button)
        layout.addLayout(controls)
        self.status = QLabel("Preparing OCR position preview...")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        QTimer.singleShot(0, self._begin)

    def _begin(self):
        if self.worker is not None:
            return
        worker = PositionWorker(
            self.database, self.path, self.page_index,
            self.language, self.tesseract_path,
        )
        self.worker = worker
        worker.completed.connect(self._display)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        worker.start()

    def _display(self, result: dict):
        page = QPixmap()
        if not page.loadFromData(result["image"], "PNG"):
            self._failed("PDF page preview could not be rendered.")
            return
        self.scene.clear()
        self.scene.addPixmap(page)
        width, height = result["width"], result["height"]
        self.scene.setSceneRect(0, 0, width, height)

        matching = match_word_indices(result["words"], self.excerpt)
        pen = QPen(QColor(244, 167, 35))
        pen.setWidthF(2)
        brush = QBrush(QColor(247, 193, 61, 95))
        for index in matching:
            word = result["words"][index]
            self.scene.addRect(
                word["x"] * width, word["y"] * height,
                word["w"] * width, word["h"] * height,
                pen, brush,
            )
        self.highlight_count = len(matching)
        self.view.fit_page()

        cache_note = "cached positions" if result["cached"] else "new OCR positions"
        if matching:
            self.status.setText(
                f"Highlighted {len(matching)} matching OCR word(s) using "
                f"{cache_note}. Highlight accuracy depends on OCR quality."
            )
        else:
            self.status.setText(
                "Page displayed, but the search excerpt's exact words could "
                "not be matched to OCR boxes. The OCR text and the search index "
                "may differ; no uncertain highlights were drawn. "
                f"({cache_note})"
            )

    def _failed(self, message: str):
        self.status.setText(message)

    def _finished(self):
        self.worker = None

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.requestInterruption()
            # Tesseract is polled in the worker; closing should cancel promptly.
            self.worker.wait()
        super().closeEvent(event)
