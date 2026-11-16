"""Search/index UI. Indexing and Tesseract run outside the Qt GUI thread."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QSettings, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from .fulltext import IndexCancelled, PageIndex, index_pdf, read_ocr, resolve_tesseract


class IndexWorker(QThread):
    progress = Signal(int, int, str)
    completed = Signal(dict)

    def __init__(
        self, db_path: Path, root: Path, books: list[Path],
        tesseract_path: str, enable_ocr: bool, language: str = "eng",
    ):
        super().__init__()
        self.db_path = Path(db_path)
        self.root = Path(root)
        self.books = list(books)
        self.tesseract_path = tesseract_path
        self.enable_ocr = enable_ocr
        self.language = language

    def run(self):
        index = None
        outcome = {
            "books": 0, "pages": 0, "waiting_ocr": 0,
            "errors": [], "cancelled": False, "ocr_available": False,
        }
        try:
            index = PageIndex(self.db_path)
            binary = resolve_tesseract(self.tesseract_path) if self.enable_ocr else None
            outcome["ocr_available"] = binary is not None
            total = len(self.books)
            for ordinal, path in enumerate(self.books, start=1):
                if self.isInterruptionRequested():
                    outcome["cancelled"] = True
                    break
                self.progress.emit(ordinal, total, f"{path.name} — starting")
                try:
                    report = index_pdf(
                        index, path, self.root,
                        ocr_binary=binary,
                        cancel=self.isInterruptionRequested,
                        ocr=lambda page, exe: read_ocr(page, exe, self.language),
                        progress=lambda page, pages, source, name=path.name, number=ordinal: (
                            self.progress.emit(
                                number, total,
                                f"{name} — page {page}/{pages} ({source})"
                            ) if page % 5 == 0 or page == pages else None
                        ),
                    )
                    outcome["books"] += 1
                    outcome["pages"] += report["indexed"]
                    outcome["waiting_ocr"] += report["pending_ocr"]
                except IndexCancelled:
                    outcome["cancelled"] = True
                    break
                except Exception as exc:
                    outcome["errors"].append(f"{path.name}: {exc}"[:250])
                if len(outcome["errors"]) > 100:
                    break
        except Exception as exc:
            outcome["errors"].append(f"Index initialization: {exc}"[:400])
        finally:
            if index is not None:
                index.close()
            self.completed.emit(outcome)


class SearchPanel(QWidget):
    def __init__(
        self, db_path: Path,
        root_getter: Callable[[], Path],
        books_getter: Callable[[], dict],
        open_result: Callable[[str, int], None],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.index = PageIndex(db_path)
        self.root_getter = root_getter
        self.books_getter = books_getter
        self.open_result = open_result
        self.worker: IndexWorker | None = None
        self.settings = QSettings("ATLAS", "DigitalLibrary")
        self._build_ui()
        self.refresh_status()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        heading = QLabel("FULL-TEXT RESEARCH")
        heading.setObjectName("appTitle")
        outer.addWidget(heading)
        hint = QLabel(
            "Search across indexed PDF pages, then double-click a result to open "
            "the sourcebook at that page. OCR results are searchable here, too."
        )
        hint.setWordWrap(True)
        outer.addWidget(hint)

        search_row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search for a creature, spell, phrase, or rule...")
        self.query.returnPressed.connect(self.search)
        search_row.addWidget(self.query, 1)
        self.mode = QComboBox()
        self.mode.addItem("All words", "words")
        self.mode.addItem("Exact phrase", "phrase")
        self.mode.addItem("Boolean / FTS5", "advanced")
        self.mode.setToolTip(
            'Advanced examples: dragon AND ancient; "legendary actions"; '
            '"green dragon" OR "blue dragon"; NEAR(dragon treasure, 6)'
        )
        search_row.addWidget(self.mode)
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.search)
        search_row.addWidget(self.search_btn)
        outer.addLayout(search_row)

        index_row = QHBoxLayout()
        self.index_btn = QPushButton("Index / Update Library")
        self.index_btn.clicked.connect(self.start_index)
        index_row.addWidget(self.index_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_index)
        self.stop_btn.setEnabled(False)
        index_row.addWidget(self.stop_btn)
        self.ocr_checkbox = QCheckBox("OCR scanned pages")
        self.ocr_checkbox.setChecked(True)
        index_row.addWidget(self.ocr_checkbox)
        self.ocr_select_btn = QPushButton("Locate Tesseract...")
        self.ocr_select_btn.clicked.connect(self.choose_tesseract)
        index_row.addWidget(self.ocr_select_btn)
        self.language = QLineEdit(str(self.settings.value("ocr_language", "eng")))
        self.language.setFixedWidth(64)
        self.language.setToolTip("Installed Tesseract language code (e.g. eng)")
        index_row.addWidget(QLabel("OCR language:"))
        index_row.addWidget(self.language)
        index_row.addStretch(1)
        outer.addLayout(index_row)

        self.ocr_info = QLabel()
        self.ocr_info.setWordWrap(True)
        outer.addWidget(self.ocr_info)
        self.index_status = QLabel()
        self.index_status.setWordWrap(True)
        outer.addWidget(self.index_status)

        self.results = QListWidget()
        self.results.setWordWrap(True)
        self.results.itemDoubleClicked.connect(self._open_match)
        outer.addWidget(self.results, 1)
        self.result_status = QLabel(
            "Search indexes are stored locally. No files are uploaded or modified."
        )
        outer.addWidget(self.result_status)
        self._update_ocr_info()

    def _update_ocr_info(self):
        selected = str(self.settings.value("tesseract_path", ""))
        binary = resolve_tesseract(selected)
        if binary:
            self.ocr_info.setText(f"Offline OCR engine: {binary}")
        elif selected:
            self.ocr_info.setText(
                f"OCR executable not found: {selected}. "
                "Existing PDF text can still be indexed."
            )
        else:
            self.ocr_info.setText(
                "Optional offline OCR: Tesseract is not detected. "
                "Install it locally and use Locate Tesseract. "
                "Text-based PDFs can still be indexed."
            )

    def choose_tesseract(self):
        selected = QFileDialog.getOpenFileName(
            self, "Choose local Tesseract executable", str(Path.home()),
            "Executables (*.exe);;All files (*)",
        )[0]
        if selected:
            self.settings.setValue("tesseract_path", selected)
            self._update_ocr_info()

    def refresh_status(self):
        stats = self.index.stats(self.root_getter())
        self.index_status.setText(
            f"Indexed documents: {stats['files']} | "
            f"Completed: {stats['complete']} | "
            f"Awaiting OCR: {stats['waiting_ocr']} | "
            f"Pages processed: {stats['processed_pages']}"
        )

    def start_index(self):
        if self.worker is not None:
            return
        root = self.root_getter()
        if not root.is_dir():
            self.result_status.setText("Select an existing library folder first.")
            return
        language = self.language.text().strip().lower()
        if not re.fullmatch(r"[a-z0-9_]+(?:\+[a-z0-9_]+)*", language):
            self.result_status.setText("Enter a valid Tesseract language code (e.g. eng).")
            return
        self.settings.setValue("ocr_language", language)
        books = [Path(path) for path in self.books_getter()]
        if not books:
            self.result_status.setText("No cataloged PDF files found. Scan the bookshelf first.")
            return

        self.index_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.ocr_select_btn.setEnabled(False)
        self.ocr_checkbox.setEnabled(False)
        self.language.setEnabled(False)
        self.result_status.setText(
            "Indexing on your computer. You may continue reading other books."
        )
        worker = IndexWorker(
            self.index.path, root, books,
            str(self.settings.value("tesseract_path", "")),
            self.ocr_checkbox.isChecked(), language,
        )
        self.worker = worker
        worker.progress.connect(self._on_progress)
        worker.completed.connect(self._on_done)
        worker.finished.connect(self._worker_finished)
        worker.start()

    def _on_progress(self, current: int, total: int, title: str):
        self.index_status.setText(f"Indexing book {current}/{total}: {title}")

    def stop_index(self):
        if self.worker:
            self.worker.requestInterruption()
            self.stop_btn.setEnabled(False)
            self.result_status.setText("Stopping after the current page completes...")

    def _on_done(self, result: dict):
        self.refresh_status()
        summary = (
            f"{'Stopped' if result['cancelled'] else 'Indexing finished'}: "
            f"{result['books']} books checked, "
            f"{result['pages']} new/reprocessed pages, "
            f"{result['waiting_ocr']} pages awaiting OCR."
        )
        if not result["ocr_available"] and self.ocr_checkbox.isChecked():
            summary += " Tesseract not found; scanned pages remain pending."
        if result["errors"]:
            summary += " Errors: " + " | ".join(result["errors"][:3])
        self.result_status.setText(summary)
        if self.query.text().strip():
            self.search()

    def _worker_finished(self):
        self.worker = None
        self.index_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.ocr_select_btn.setEnabled(True)
        self.ocr_checkbox.setEnabled(True)
        self.language.setEnabled(True)

    def search(self):
        query = self.query.text().strip()
        self.results.clear()
        if not query:
            self.result_status.setText("Enter a search query.")
            return
        try:
            rows = self.index.search(query, self.root_getter(), self.mode.currentData())
        except ValueError as exc:
            self.result_status.setText(str(exc))
            return
        books = self.books_getter()
        shown = 0
        for result in rows:
            book = books.get(result["path"])
            if book is None:
                continue
            display = book.get("display_title") or book["title"]
            snippet = result["excerpt"] or "(Matching indexed page)"
            excerpt = snippet.replace("\r", " ").replace("\n", " ")
            if len(excerpt) > 310:
                excerpt = excerpt[:307] + "…"
            label = f"{display}  •  Page {result['page_index'] + 1}\n{excerpt}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, result)
            item.setToolTip(result["path"])
            self.results.addItem(item)
            shown += 1
        note = "Showing at most 150 results" if shown == 150 else f"{shown} results"
        self.result_status.setText(
            f"{note} from indexed pages in the selected library. "
            "Double-click to open the matching page."
        )

    def _open_match(self, item: QListWidgetItem):
        result = item.data(Qt.ItemDataRole.UserRole)
        if result and result["path"] in self.books_getter():
            self.open_result(result["path"], result["page_index"])

    def shutdown(self):
        if self.worker:
            self.worker.requestInterruption()
            self.worker.wait()
        self.index.close()
