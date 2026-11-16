"""Native bookshelf UI (Milestone 1)."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, QStandardPaths, Qt, QThread, QUrl, Signal, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSplitter,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QAbstractItemView,
    QTabWidget, QTabBar,
)

from .catalog import Catalog
from .scanner import scan_library
from .reader import PdfReader


class ScanThread(QThread):
    found = Signal(dict)
    completed = Signal(int, str)

    def __init__(self, root: Path, database_path: Path, cache_dir: Path):
        super().__init__()
        self.root = root
        self.database_path = database_path
        self.cache_dir = cache_dir

    def run(self):
        count = 0
        try:
            for record in scan_library(self.root, self.database_path, self.cache_dir):
                if self.isInterruptionRequested():
                    self.completed.emit(count, "Scan stopped.")
                    return
                count += 1
                self.found.emit(record)
            self.completed.emit(count, "")
        except Exception as exc:
            self.completed.emit(count, str(exc))


def placeholder_icon() -> QIcon:
    pix = QPixmap(130, 184)
    pix.fill(QColor("#3d4651"))
    painter = QPainter(pix)
    painter.setPen(QColor("#dbcbac"))
    font = QFont()
    font.setPointSize(30)
    painter.setFont(font)
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "📖")
    painter.end()
    return QIcon(pix)


class LibraryWindow(QMainWindow):
    def __init__(self, data_dir: Path, initial_root: Path):
        super().__init__()
        self.setWindowTitle("A.T.L.A.S. Digital Library")
        self.resize(1220, 780)
        self.data_dir = data_dir
        self.database_path = data_dir / "catalog.sqlite3"
        self.cache_dir = data_dir / "covers"
        self.catalog = Catalog(self.database_path)
        self.root = initial_root.expanduser().resolve()
        self.settings = QSettings("ATLAS", "DigitalLibrary")
        self.books: dict[str, dict] = {}
        self.reader_tabs: dict[str, PdfReader] = {}
        self.worker: ScanThread | None = None
        self.active_folder = ""
        self.selected_path = ""
        self._empty_cover = placeholder_icon()
        self._build_ui()
        self._set_theme()
        self._load_existing()
        if self.root.is_dir():
            QTimer.singleShot(0, self.scan)

    def _build_ui(self):
        container = QWidget()
        self.tabs = QTabWidget(self)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_reader_tab)
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(container, "Bookshelf")
        self.tabs.tabBar().setTabButton(
            0, QTabBar.ButtonPosition.RightSide, None
        )
        outer = QVBoxLayout(container)

        header = QHBoxLayout()
        title = QLabel("A.T.L.A.S.  /  DIGITAL LIBRARY")
        title.setObjectName("appTitle")
        header.addWidget(title, 1)
        self.choose_btn = QPushButton("Choose Folder")
        self.choose_btn.clicked.connect(self.choose_folder)
        header.addWidget(self.choose_btn)
        self.scan_btn = QPushButton("Rescan")
        self.scan_btn.clicked.connect(self.scan)
        header.addWidget(self.scan_btn)
        outer.addLayout(header)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search your sourcebook titles...")
        self.search.textChanged.connect(self._refresh_gallery)
        outer.addWidget(self.search)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("FOLDERS"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemSelectionChanged.connect(self._select_folder)
        left_layout.addWidget(self.tree)
        splitter.addWidget(left)

        self.gallery = QListWidget()
        self.gallery.setViewMode(QListWidget.ViewMode.IconMode)
        self.gallery.setIconSize(QSize(132, 180))
        self.gallery.setGridSize(QSize(174, 242))
        self.gallery.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.gallery.setMovement(QListWidget.Movement.Static)
        self.gallery.setWordWrap(True)
        self.gallery.setSpacing(8)
        self.gallery.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.gallery.currentItemChanged.connect(self._select_book)
        self.gallery.itemDoubleClicked.connect(lambda _: self.open_book())
        splitter.addWidget(self.gallery)

        right = QWidget()
        self.preview_panel = right
        # Long filenames and deep folders must never resize the preview pane.
        right.setFixedWidth(285)
        splitter.setCollapsible(2, False)
        splitter.setStretchFactor(1, 1)
        details = QVBoxLayout(right)
        details.addWidget(QLabel("BOOK DETAILS"))
        self.detail_cover = QLabel("Select a book")
        self.detail_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_cover.setFixedHeight(245)
        details.addWidget(self.detail_cover)
        self.detail_title = QLabel("")
        self.detail_title.setWordWrap(False)
        self.detail_title.setFixedHeight(35)
        self.detail_title.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.detail_title.setObjectName("detailTitle")
        details.addWidget(self.detail_title)
        self.detail_info = QLabel("")
        self.detail_info.setWordWrap(False)
        self.detail_info.setFixedHeight(100)
        details.addWidget(self.detail_info)
        self.detail_warning = QLabel("")
        self.detail_warning.setFixedHeight(22)
        details.addWidget(self.detail_warning)
        self.copy_path_btn = QPushButton("Copy full file path")
        self.copy_path_btn.setEnabled(False)
        self.copy_path_btn.clicked.connect(self.copy_full_path)
        details.addWidget(self.copy_path_btn)
        self.open_btn = QPushButton("Read inside A.T.L.A.S.")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self.open_book)
        details.addWidget(self.open_btn)
        self.external_btn = QPushButton("Open in default PDF reader")
        self.external_btn.setEnabled(False)
        self.external_btn.clicked.connect(self.open_external)
        details.addWidget(self.external_btn)
        details.addStretch()
        splitter.addWidget(right)
        splitter.setSizes([205, 765, 285])

        self.status = QLabel("Choose a library folder to get started.")
        outer.addWidget(self.status)

    def _set_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #171c24; color: #ece6d9; font-size: 13px; }
            QLabel#appTitle { color: #deb879; font-weight: 700; font-size: 20px; }
            QLabel#detailTitle { color: #f0d6a1; font-weight: 650; font-size: 16px; }
            QPushButton { background: #374253; border: 1px solid #516076;
                          padding: 9px 14px; border-radius: 6px; }
            QPushButton:hover { background: #4a586c; }
            QPushButton:disabled { color: #7c8290; }
            QLineEdit { background: #222b37; border: 1px solid #435166;
                        padding: 10px; border-radius: 6px; }
            QListWidget, QTreeWidget { background: #202834; border: 1px solid #39475a; }
            QListWidget::item:selected, QTreeWidget::item:selected {
                background: #596075; color: white; }
            QSplitter::handle { background: #313b4b; }
            QTabWidget::pane { border: 1px solid #39475a; }
            QTabBar::tab { background: #252f3d; color: #e4dccb;
                           padding: 10px 15px; }
            QTabBar::tab:selected { background: #46536a; }
            QComboBox, QTreeView { background: #222b37; color: #ece6d9;
                                   border: 1px solid #435166; padding: 5px; }
        """)

    def _load_existing(self):
        self.books = {book["path"]: book for book in self.catalog.available(self.root)}
        self._refresh_folders()
        self._refresh_gallery()
        self.status.setText(f"{len(self.books)} cataloged books | {self.root}")

    def choose_folder(self):
        if self.worker is not None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Select library folder", str(self.root))
        if not folder:
            return
        self.root = Path(folder).resolve()
        self.settings.setValue("library_root", str(self.root))
        self.active_folder = ""
        self.selected_path = ""
        self._load_existing()
        self.scan()

    def scan(self):
        if self.worker is not None:
            return
        if not self.root.is_dir():
            self.status.setText("Folder not found. Use Choose Folder to select your books.")
            return
        self.scan_btn.setEnabled(False)
        self.choose_btn.setEnabled(False)
        self.status.setText("Scanning PDFs and preparing covers... originals remain untouched.")
        self._scan_count = 0
        worker = ScanThread(self.root, self.database_path, self.cache_dir)
        self.worker = worker
        worker.found.connect(self._on_found)
        worker.completed.connect(self._scan_finished)
        worker.finished.connect(self._worker_finished)
        worker.start()

    def _on_found(self, book):
        self.books[book["path"]] = book
        self._scan_count += 1
        if self._scan_count % 10 == 0:
            self.status.setText(f"Indexed {self._scan_count} PDFs...")
            self._refresh_folders()
            self._refresh_gallery()

    def _scan_finished(self, count, error):
        self._load_existing()
        if error:
            self.status.setText(f"Scan ended after {count} PDFs: {error}")
        else:
            self.status.setText(f"Ready — {len(self.books)} PDFs in {self.root}")

    def _worker_finished(self):
        self.worker = None
        self.scan_btn.setEnabled(True)
        self.choose_btn.setEnabled(True)

    def _refresh_folders(self):
        selected = self.active_folder
        self.tree.blockSignals(True)
        self.tree.clear()
        top = QTreeWidgetItem(["All Books"])
        top.setData(0, Qt.ItemDataRole.UserRole, "")
        self.tree.addTopLevelItem(top)
        nodes = {"": top}
        for book in self.books.values():
            try:
                relative = Path(book["path"]).relative_to(self.root)
            except ValueError:
                continue
            parent = ""
            for part in relative.parts[:-1]:
                current = f"{parent}/{part}" if parent else part
                if current not in nodes:
                    node = QTreeWidgetItem([part.replace("_", " ")])
                    node.setData(0, Qt.ItemDataRole.UserRole, current)
                    nodes[parent].addChild(node)
                    nodes[current] = node
                parent = current
        self.tree.expandToDepth(0)
        chosen = nodes.get(selected, top)
        self.tree.setCurrentItem(chosen)
        self.tree.blockSignals(False)

    def _select_folder(self):
        item = self.tree.currentItem()
        self.active_folder = str(item.data(0, Qt.ItemDataRole.UserRole) or "") if item else ""
        self._refresh_gallery()

    def _refresh_gallery(self):
        query = self.search.text().strip().casefold()
        self.gallery.blockSignals(True)
        self.gallery.clear()
        visible = 0
        for book in sorted(self.books.values(), key=lambda b: b["title"].casefold()):
            try:
                relative = Path(book["path"]).relative_to(self.root)
            except ValueError:
                continue
            if self.active_folder:
                prefix = self.active_folder.replace("/", "\\") if sys.platform == "win32" else self.active_folder
                relative_parent = str(relative.parent)
                if relative_parent != prefix and not relative_parent.startswith(prefix + ("\\" if sys.platform == "win32" else "/")):
                    continue
            if query and query not in book["title"].casefold() and query not in relative.as_posix().casefold():
                continue
            pix = QPixmap(book["cover_path"]) if book["cover_path"] else QPixmap()
            icon = QIcon(pix) if not pix.isNull() else self._empty_cover
            item = QListWidgetItem(icon, book["title"])
            item.setData(Qt.ItemDataRole.UserRole, book["path"])
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            item.setToolTip(str(relative))
            self.gallery.addItem(item)
            visible += 1
            if book["path"] == self.selected_path:
                self.gallery.setCurrentItem(item)
        self.gallery.blockSignals(False)
        if self.gallery.currentItem() is None:
            self.selected_path = ""
            self.open_btn.setEnabled(False)
            self.external_btn.setEnabled(False)
            self.copy_path_btn.setEnabled(False)
            self.detail_warning.setText("")
            self.detail_warning.setToolTip("")
            self.detail_title.setText("")
            self.detail_info.setText("")
            self.detail_cover.clear()
        self.status.setText(f"{visible} visible books | {len(self.books)} cataloged")

    def _select_book(self, current, _previous):
        if not current:
            return
        path = current.data(Qt.ItemDataRole.UserRole)
        book = self.books.get(path)
        if not book:
            return
        self.selected_path = path
        self.detail_title.setText(
            QFontMetrics(self.detail_title.font()).elidedText(
                book["title"], Qt.TextElideMode.ElideRight, 255
            )
        )
        self.detail_title.setToolTip(book["title"])
        size_mb = book["size_bytes"] / (1024 * 1024)
        pages = book["page_count"] if book["page_count"] is not None else "Unknown"
        try:
            folder = str(Path(path).parent.relative_to(self.root))
        except ValueError:
            folder = str(Path(path).parent)
        if folder == ".":
            folder = "(Library root)"
        folder_display = QFontMetrics(self.detail_info.font()).elidedText(
            folder, Qt.TextElideMode.ElideMiddle, 252
        )
        self.detail_info.setText(
            f"Pages: {pages}\nSize: {size_mb:.1f} MB\n\nFolder:\n{folder_display}"
        )
        self.detail_info.setToolTip(str(path))
        if book["scan_error"]:
            self.detail_warning.setText("Cover preview unavailable")
            self.detail_warning.setToolTip(book["scan_error"])
        else:
            self.detail_warning.clear()
            self.detail_warning.setToolTip("")
        pix = QPixmap(book["cover_path"]) if book["cover_path"] else QPixmap()
        if not pix.isNull():
            self.detail_cover.setPixmap(
                pix.scaled(180, 240, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            )
        else:
            self.detail_cover.setText("No cover preview")
        self.open_btn.setEnabled(Path(path).is_file())
        self.external_btn.setEnabled(Path(path).is_file())
        self.copy_path_btn.setEnabled(True)

    def copy_full_path(self):
        if self.selected_path:
            QApplication.clipboard().setText(self.selected_path)

    def open_book(self):
        """Open one PDF in an internal tab, without launching another app."""
        if not self.selected_path:
            return
        path = Path(self.selected_path)
        if not path.is_file():
            QMessageBox.warning(self, "Missing file", "The PDF is no longer at its cataloged path.")
            return
        key = str(path)
        existing = self.reader_tabs.get(key)
        if existing is not None:
            self.tabs.setCurrentWidget(existing)
            return
        try:
            reader = PdfReader(path, self.catalog, self.tabs)
        except Exception as exc:
            QMessageBox.warning(self, "Reader error", f"Unable to open PDF:\n{exc}")
            return
        self.reader_tabs[key] = reader
        index = self.tabs.addTab(reader, path.stem[:34])
        self.tabs.setTabToolTip(index, str(path))
        self.tabs.setCurrentIndex(index)

    def open_external(self):
        if not self.selected_path:
            return
        path = Path(self.selected_path)
        if not path.is_file():
            QMessageBox.warning(self, "Missing file", "The PDF is no longer at its cataloged path.")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, "Could not open", "Windows could not open this PDF.")

    def _close_reader_tab(self, index: int):
        if index == 0:
            return
        reader = self.tabs.widget(index)
        if isinstance(reader, PdfReader):
            reader.dispose()
            self.reader_tabs.pop(str(reader.path), None)
        self.tabs.removeTab(index)
        reader.deleteLater()

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.worker.wait()
        for reader in tuple(self.reader_tabs.values()):
            reader.save_state()
        self.catalog.close()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("ATLAS Digital Library")
    app.setOrganizationName("ATLAS")
    data = Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    ))
    data.mkdir(parents=True, exist_ok=True)
    settings = QSettings("ATLAS", "DigitalLibrary")
    default = Path.home() / "Downloads" / "Telegram Desktop"
    root = Path(str(settings.value("library_root", str(default))))
    window = LibraryWindow(data, root)
    window.show()
    return app.exec()
