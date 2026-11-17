"""In-reader OCR highlight mode using the same raster and boxes as OCR.

QPdfView does not expose its page layout rectangles publicly. For now, a
single-page QGraphicsView occupies the reader's *existing viewing area* when
the user enables OCR highlights. Returning to PDF restores the original
QPdfView instance and its scrolling/zoom state without modifying any PDF.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget, QGraphicsScene,
)

from .ocr_positions import match_word_indices
from .ocr_preview import FittingPageView, PositionWorker


class InlineOcrPage(QWidget):
    backRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: PositionWorker | None = None
        self._pending: tuple | None = None
        self._active: tuple | None = None
        self.highlight_count = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        controls = QHBoxLayout()
        self.back_btn = QPushButton("Return to PDF")
        self.back_btn.clicked.connect(lambda _checked=False: self.backRequested.emit())
        controls.addWidget(self.back_btn)
        controls.addWidget(QLabel("OCR-highlighted page"))
        controls.addStretch(1)

        self.zoom_in = QPushButton("Zoom +")
        self.zoom_in.clicked.connect(lambda: self.view.zoom(1.2))
        controls.addWidget(self.zoom_in)
        self.zoom_out = QPushButton("Zoom −")
        self.zoom_out.clicked.connect(lambda: self.view.zoom(1 / 1.2))
        controls.addWidget(self.zoom_out)
        self.fit_button = QPushButton("Fit page")
        self.fit_button.clicked.connect(self.view_fit)
        controls.addWidget(self.fit_button)
        outer.addLayout(controls)

        self.view = FittingPageView(self)
        self.scene = QGraphicsScene(self.view)
        self.view.setScene(self.scene)
        outer.addWidget(self.view, 1)

        self.status = QLabel(
            "OCR highlights use an in-reader page image, not the native "
            "continuous-scroll PDF view."
        )
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

    def view_fit(self):
        self.view.fit_page()

    def activate(
        self, database: Path, path: Path, page_index: int,
        excerpt: str, language: str, tesseract_path: str,
    ):
        """Schedule one page; supersede stale results if requests overlap."""
        self._pending = (
            Path(database), Path(path), int(page_index),
            str(excerpt), str(language), str(tesseract_path),
        )
        self.scene.clear()
        self.highlight_count = 0
        if self.worker is not None:
            self._active = None
            self.worker.requestInterruption()
            self.status.setText("Preparing the selected OCR page...")
            return
        self._start_pending()

    def _start_pending(self):
        if self._pending is None:
            return
        self._active = self._pending
        self._pending = None
        database, path, page, _excerpt, language, executable = self._active
        self.status.setText(f"Locating OCR words on page {page + 1}...")
        worker = PositionWorker(database, path, page, language, executable)
        self.worker = worker
        worker.completed.connect(self._received)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        worker.start()

    def _received(self, result: dict):
        if self._active is None:
            return
        self.display_result(result, self._active[3])

    def display_result(self, result: dict, excerpt: str):
        """Public for deterministic GUI testing without invoking Tesseract."""
        bitmap = QPixmap()
        if not bitmap.loadFromData(result["image"], "PNG"):
            self._failed("Could not render the OCR page image.")
            return

        self.scene.clear()
        self.scene.addPixmap(bitmap)
        width, height = result["width"], result["height"]
        self.scene.setSceneRect(0, 0, width, height)
        matching = match_word_indices(result["words"], excerpt)

        pen = QPen(QColor(244, 167, 35))
        pen.setWidthF(2)
        brush = QBrush(QColor(247, 193, 61, 95))
        for position in matching:
            word = result["words"][position]
            self.scene.addRect(
                word["x"] * width, word["y"] * height,
                word["w"] * width, word["h"] * height,
                pen, brush,
            )
        self.highlight_count = len(matching)
        self.view.fit_page()
        origin = "cached" if result["cached"] else "newly recognized"
        if matching:
            self.status.setText(
                f"{len(matching)} highlighted OCR word(s), using {origin} "
                "coordinates. Return to PDF to resume normal scrolling."
            )
        else:
            self.status.setText(
                "OCR page loaded, but the indexed excerpt did not match its "
                "word coordinates. No speculative highlights were drawn. "
                "Return to PDF to resume normal scrolling."
            )

    def _failed(self, message: str):
        if self._active is not None:
            self.status.setText(message)

    def _finished(self):
        self.worker = None
        self._active = None
        if self._pending is not None:
            self._start_pending()

    def deactivate(self):
        """Stop the optional OCR task, but keep source PDF state untouched."""
        self._pending = None
        self._active = None
        if self.worker is not None:
            self.worker.requestInterruption()

    def shutdown(self):
        self.deactivate()
        if self.worker is not None:
            self.worker.wait()
