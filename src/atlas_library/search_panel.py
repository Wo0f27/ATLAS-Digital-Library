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
from .advanced_search import AdvancedSearcher
from .catalog import Catalog


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


class SearchWorker(QThread):
    """Potentially expensive fuzzy/regex matching must not block the reader."""
    completed = Signal(list, str)
    failed = Signal(str)

    def __init__(self, db_path, query, mode, root, allowed_paths):
        super().__init__()
        self.db_path = db_path
        self.query = query
        self.mode = mode
        self.root = root
        self.allowed_paths = allowed_paths

    def run(self):
        index = None
        try:
            index = PageIndex(self.db_path)
            searcher = AdvancedSearcher(index)
            if self.mode == "fuzzy":
                rows, notice = searcher.fuzzy(
                    self.query, self.root, allowed_paths=self.allowed_paths
                )
            else:
                rows, notice = searcher.regex_search(
                    self.query, self.root, allowed_paths=self.allowed_paths,
                    cancel=self.isInterruptionRequested,
                )
            if not self.isInterruptionRequested():
                self.completed.emit(rows, notice)
        except IndexCancelled:
            return
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            if index is not None:
                index.close()


class SearchPanel(QWidget):
    def __init__(
        self, db_path: Path,
        root_getter: Callable[[], Path],
        books_getter: Callable[[], dict],
        open_result: Callable[[str, int], None],
        parent: QWidget | None = None,
        catalog_getter: Callable[[], Catalog] | None = None,
    ):
        super().__init__(parent)
        self.index = PageIndex(db_path)
        self.root_getter = root_getter
        self.books_getter = books_getter
        self.open_result = open_result
        self.catalog_getter = catalog_getter
        self.worker: IndexWorker | None = None
        self.search_worker: SearchWorker | None = None
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
        self.mode.addItem("Fuzzy word", "fuzzy")
        self.mode.addItem("Regular expression", "regex")
        self.mode.setToolTip(
            'Advanced examples: dragon AND ancient; "legendary actions"; '
            '"green dragon" OR "blue dragon"; NEAR(dragon treasure, 6)'
        )
        search_row.addWidget(self.mode)
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.search)
        search_row.addWidget(self.search_btn)
        outer.addLayout(search_row)

        facets_row = QHBoxLayout()
        self.tag_filter = QComboBox()
        self.tag_filter.setToolTip("Limit results to books with a particular tag")
        self.tag_filter.addItem("Any tag", None)
        facets_row.addWidget(self.tag_filter)
        self.shelf_filter = QComboBox()
        self.shelf_filter.setToolTip("Limit results to a virtual shelf")
        self.shelf_filter.addItem("Any shelf", None)
        facets_row.addWidget(self.shelf_filter)
        self.meta_filter = QLineEdit()
        self.meta_filter.setPlaceholderText("Filter author, publisher, edition or system...")
        facets_row.addWidget(self.meta_filter, 1)
        self.refresh_filters_button = QPushButton("Refresh filters")
        self.refresh_filters_button.clicked.connect(self.refresh_filters)
        facets_row.addWidget(self.refresh_filters_button)
        outer.addLayout(facets_row)

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
        self.refresh_filters()

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

    def refresh_filters(self):
        if self.catalog_getter is None:
            return
        catalog = self.catalog_getter()
        previous_tag = self.tag_filter.currentData()
        previous_shelf = self.shelf_filter.currentData()
        self.tag_filter.blockSignals(True)
        self.shelf_filter.blockSignals(True)
        try:
            self.tag_filter.clear()
            self.tag_filter.addItem("Any tag", None)
            for tag in catalog.list_tags(self.root_getter()):
                self.tag_filter.addItem(tag, tag)
            self.shelf_filter.clear()
            self.shelf_filter.addItem("Any shelf", None)
            for shelf in catalog.list_collections():
                self.shelf_filter.addItem(shelf["name"], shelf["id"])
            self.tag_filter.setCurrentIndex(
                max(0, self.tag_filter.findData(previous_tag))
            )
            self.shelf_filter.setCurrentIndex(
                max(0, self.shelf_filter.findData(previous_shelf))
            )
        finally:
            self.tag_filter.blockSignals(False)
            self.shelf_filter.blockSignals(False)

    def _allowed_paths(self) -> set[str] | None:
        tag = self.tag_filter.currentData()
        shelf_id = self.shelf_filter.currentData()
        keyword = self.meta_filter.text().strip().casefold()
        if tag is None and shelf_id is None and not keyword:
            return None
        selected = set(self.books_getter())
        if self.catalog_getter is None:
            return selected
        catalog = self.catalog_getter()
        if tag is not None:
            selected &= catalog.paths_for_tag(str(tag))
        if shelf_id is not None:
            selected &= catalog.get_collection_paths(int(shelf_id))
        if keyword:
            selected = {
                path for path in selected
                if any(
                    keyword in str(meta.get(field) or "").casefold()
                    for meta in [catalog.get_metadata(path)]
                    for field in ("authors", "publisher", "edition", "game_system")
                )
            }
        return selected

    def search(self):
        if self.search_worker is not None:
            return
        query = self.query.text().strip()
        self.results.clear()
        if not query:
            self.result_status.setText("Enter a search query.")
            return
        mode = self.mode.currentData()
        allowed = self._allowed_paths()
        if mode in ("fuzzy", "regex"):
            worker = SearchWorker(
                self.index.path, query, mode, self.root_getter(), allowed
            )
            self.search_worker = worker
            self.search_btn.setEnabled(False)
            self.result_status.setText("Searching indexed pages...")
            worker.completed.connect(self._display_results)
            worker.failed.connect(
                lambda error: self.result_status.setText(error)
            )
            worker.finished.connect(self._search_worker_finished)
            worker.start()
            return
        try:
            rows = self.index.search(
                query, self.root_getter(), mode, allowed_paths=allowed
            )
        except ValueError as exc:
            self.result_status.setText(str(exc))
            return
        self._display_results(rows, "")

    def _search_worker_finished(self):
        self.search_worker = None
        self.search_btn.setEnabled(True)

    def _display_results(self, rows: list[dict], notice: str):
        self.results.clear()
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
            + (f" {notice}" if notice else "")
        )

    def _open_match(self, item: QListWidgetItem):
        result = item.data(Qt.ItemDataRole.UserRole)
        if result and result["path"] in self.books_getter():
            self.open_result(result["path"], result["page_index"])

    def shutdown(self):
        if self.search_worker:
            self.search_worker.requestInterruption()
            self.search_worker.wait()
        if self.worker:
            self.worker.requestInterruption()
            self.worker.wait()
        self.index.close()
