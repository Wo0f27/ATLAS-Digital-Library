"""Native bookshelf UI (Milestone 1)."""

from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, QStandardPaths, Qt, QThread, QUrl, Signal, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSplitter,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QAbstractItemView,
    QTabWidget, QTabBar, QComboBox, QCheckBox, QDialog, QInputDialog,
)

from .catalog import Catalog
from .scanner import scan_library
from .reader import PdfReader
from .organizer import MetadataDialog, CollectionMembershipDialog
from .search_panel import SearchPanel


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
        self._nav_refreshing = False
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

        find_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search your sourcebook titles...")
        self.search.textChanged.connect(self._refresh_gallery)
        find_row.addWidget(self.search, 1)
        self.view_filter = QComboBox()
        self.view_filter.addItem("All Books", "all")
        self.view_filter.addItem("Favorites", "favorites")
        self.view_filter.addItem("Currently Reading", "reading")
        self.view_filter.addItem("Finished", "finished")
        self.view_filter.currentIndexChanged.connect(self._refresh_gallery)
        self.view_filter.setFixedWidth(175)
        find_row.addWidget(self.view_filter)
        outer.addLayout(find_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.navigation_tabs = QTabWidget()
        left_layout.addWidget(self.navigation_tabs)

        folders_page = QWidget()
        folders_layout = QVBoxLayout(folders_page)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemSelectionChanged.connect(self._select_folder)
        folders_layout.addWidget(self.tree)
        self.navigation_tabs.addTab(folders_page, "Folders")

        collections_page = QWidget()
        collections_layout = QVBoxLayout(collections_page)
        self.collection_list = QListWidget()
        self.collection_list.currentItemChanged.connect(
            lambda _new, _old: self._refresh_gallery()
        )
        collections_layout.addWidget(self.collection_list, 1)
        collection_actions = QHBoxLayout()
        self.collection_create_btn = QPushButton("+")
        self.collection_create_btn.setToolTip("Create a virtual shelf")
        self.collection_create_btn.clicked.connect(self._create_collection)
        collection_actions.addWidget(self.collection_create_btn)
        self.collection_rename_btn = QPushButton("Rename")
        self.collection_rename_btn.clicked.connect(self._rename_collection)
        collection_actions.addWidget(self.collection_rename_btn)
        self.collection_delete_btn = QPushButton("Delete")
        self.collection_delete_btn.clicked.connect(self._delete_collection)
        collection_actions.addWidget(self.collection_delete_btn)
        collections_layout.addLayout(collection_actions)
        self.navigation_tabs.addTab(collections_page, "Shelves")

        tags_page = QWidget()
        tags_layout = QVBoxLayout(tags_page)
        self.tag_list = QListWidget()
        self.tag_list.currentItemChanged.connect(
            lambda _new, _old: self._refresh_gallery()
        )
        tags_layout.addWidget(self.tag_list)
        self.navigation_tabs.addTab(tags_page, "Tags")
        self.navigation_tabs.currentChanged.connect(self._refresh_gallery)
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
        self.favorite_check = QCheckBox("Favorite")
        self.favorite_check.setEnabled(False)
        self.favorite_check.toggled.connect(self._change_favorite)
        details.addWidget(self.favorite_check)
        self.reading_state = QComboBox()
        self.reading_state.addItem("Unread", "unread")
        self.reading_state.addItem("Currently Reading", "reading")
        self.reading_state.addItem("Finished", "finished")
        self.reading_state.setEnabled(False)
        self.reading_state.currentIndexChanged.connect(self._change_reading_status)
        details.addWidget(self.reading_state)
        self.edit_metadata_btn = QPushButton("Edit book details / tags")
        self.edit_metadata_btn.setEnabled(False)
        self.edit_metadata_btn.clicked.connect(self._edit_metadata)
        details.addWidget(self.edit_metadata_btn)
        self.assign_shelves_btn = QPushButton("Add to virtual shelves")
        self.assign_shelves_btn.setEnabled(False)
        self.assign_shelves_btn.clicked.connect(self._edit_book_collections)
        details.addWidget(self.assign_shelves_btn)
        self.copy_path_btn = QPushButton("Copy full file path")
        self.copy_path_btn.setEnabled(False)
        self.copy_path_btn.clicked.connect(self.copy_full_path)
        details.addWidget(self.copy_path_btn)
        self.open_btn = QPushButton("Read inside A.T.L.A.S.")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(lambda: self.open_book())
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

        self.search_panel = SearchPanel(
            self.data_dir / "search.sqlite3",
            root_getter=lambda: self.root,
            books_getter=lambda: self.books,
            open_result=self._open_search_result,
            parent=self.tabs,
            catalog_getter=lambda: self.catalog,
        )
        self.tabs.addTab(self.search_panel, "Full-Text Search")
        self.tabs.currentChanged.connect(
            lambda index: self.search_panel.refresh_filters() if index == 1 else None
        )
        self.tabs.tabBar().setTabButton(
            1, QTabBar.ButtonPosition.RightSide, None
        )

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
        self._refresh_navigation()
        self._refresh_gallery()
        self.search_panel.refresh_filters()
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

    def _refresh_navigation(self):
        """Refresh virtual shelves and tags while keeping valid selections."""
        self._nav_refreshing = True
        try:
            selected_collection = self._selected_collection()
            selected_tag = self._selected_tag()
            self.collection_list.blockSignals(True)
            self.collection_list.clear()
            for collection in self.catalog.list_collections():
                item = QListWidgetItem(collection["name"])
                item.setData(Qt.ItemDataRole.UserRole, collection["id"])
                self.collection_list.addItem(item)
                if collection["id"] == selected_collection:
                    self.collection_list.setCurrentItem(item)
            self.collection_list.blockSignals(False)

            self.tag_list.blockSignals(True)
            self.tag_list.clear()
            for tag in self.catalog.list_tags(self.root):
                item = QListWidgetItem(tag)
                item.setData(Qt.ItemDataRole.UserRole, tag)
                self.tag_list.addItem(item)
                if tag.casefold() == (selected_tag or "").casefold():
                    self.tag_list.setCurrentItem(item)
            self.tag_list.blockSignals(False)
        finally:
            self.collection_list.blockSignals(False)
            self.tag_list.blockSignals(False)
            self._nav_refreshing = False

    def _selected_collection(self) -> int | None:
        item = self.collection_list.currentItem()
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _selected_tag(self) -> str | None:
        item = self.tag_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _create_collection(self):
        name, ok = QInputDialog.getText(self, "New virtual shelf", "Shelf name:")
        if not ok:
            return
        try:
            new_id = self.catalog.create_collection(name)
        except (ValueError, sqlite3.IntegrityError) as exc:
            QMessageBox.warning(self, "Cannot create shelf", str(exc))
            return
        self._refresh_navigation()
        self.navigation_tabs.setCurrentIndex(1)
        for index in range(self.collection_list.count()):
            item = self.collection_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == new_id:
                self.collection_list.setCurrentItem(item)
                break

    def _rename_collection(self):
        item = self.collection_list.currentItem()
        if item is None:
            return
        name, ok = QInputDialog.getText(
            self, "Rename virtual shelf", "Shelf name:", text=item.text()
        )
        if not ok:
            return
        try:
            self.catalog.rename_collection(
                item.data(Qt.ItemDataRole.UserRole), name
            )
        except (ValueError, sqlite3.IntegrityError) as exc:
            QMessageBox.warning(self, "Cannot rename shelf", str(exc))
            return
        self._refresh_navigation()
        self._refresh_gallery()

    def _delete_collection(self):
        item = self.collection_list.currentItem()
        if item is None:
            return
        answer = QMessageBox.question(
            self, "Delete virtual shelf",
            f"Delete the virtual shelf “{item.text()}”?\n"
            "This removes only its shelf membership. No PDFs will be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.catalog.delete_collection(item.data(Qt.ItemDataRole.UserRole))
        self._refresh_navigation()
        self._refresh_gallery()

    def _select_folder(self):
        item = self.tree.currentItem()
        self.active_folder = str(item.data(0, Qt.ItemDataRole.UserRole) or "") if item else ""
        self._refresh_gallery()

    def _refresh_gallery(self):
        if self._nav_refreshing:
            return
        query = self.search.text().strip().casefold()
        nav_index = self.navigation_tabs.currentIndex()
        collection_id = self._selected_collection() if nav_index == 1 else None
        tag = self._selected_tag() if nav_index == 2 else None
        collection_paths = (
            self.catalog.get_collection_paths(collection_id)
            if collection_id is not None else None
        )
        tag_paths = self.catalog.paths_for_tag(tag) if tag else None
        self.gallery.blockSignals(True)
        self.gallery.clear()
        visible = 0
        for book in sorted(
            self.books.values(),
            key=lambda b: (b.get("display_title") or b["title"]).casefold(),
        ):
            try:
                relative = Path(book["path"]).relative_to(self.root)
            except ValueError:
                continue
            if nav_index == 0 and self.active_folder:
                prefix = self.active_folder.replace("/", "\\") if sys.platform == "win32" else self.active_folder
                relative_parent = str(relative.parent)
                if relative_parent != prefix and not relative_parent.startswith(prefix + ("\\" if sys.platform == "win32" else "/")):
                    continue
            title = book.get("display_title") or book["title"]
            if query and not any(
                query in value.casefold() for value in (
                    title, book["title"], relative.as_posix(),
                )
            ):
                continue
            if collection_paths is not None and book["path"] not in collection_paths:
                continue
            if tag_paths is not None and book["path"] not in tag_paths:
                continue
            profile = self.catalog.get_profile(book["path"])
            filter_by = self.view_filter.currentData()
            if filter_by == "favorites" and not profile["favorite"]:
                continue
            if filter_by in ("reading", "finished") and profile["reading_status"] != filter_by:
                continue
            pix = QPixmap(book["cover_path"]) if book["cover_path"] else QPixmap()
            icon = QIcon(pix) if not pix.isNull() else self._empty_cover
            item = QListWidgetItem(icon, ("★ " if profile["favorite"] else "") + title)
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
            self.edit_metadata_btn.setEnabled(False)
            self.assign_shelves_btn.setEnabled(False)
            self.favorite_check.blockSignals(True)
            self.favorite_check.setChecked(False)
            self.favorite_check.setEnabled(False)
            self.favorite_check.blockSignals(False)
            self.reading_state.blockSignals(True)
            self.reading_state.setCurrentIndex(0)
            self.reading_state.setEnabled(False)
            self.reading_state.blockSignals(False)
            self.detail_warning.setText("")
            self.detail_warning.setToolTip("")
            self.detail_title.setText("")
            self.detail_info.setText("")
            self.detail_cover.clear()
        self.status.setText(f"{visible} visible books | {len(self.books)} cataloged")
        if self.gallery.currentItem() is not None:
            self._select_book(self.gallery.currentItem(), None)

    def _select_book(self, current, _previous):
        if not current:
            return
        path = current.data(Qt.ItemDataRole.UserRole)
        book = self.books.get(path)
        if not book:
            return
        self.selected_path = path
        display_title = book.get("display_title") or book["title"]
        self.detail_title.setText(
            QFontMetrics(self.detail_title.font()).elidedText(
                display_title, Qt.TextElideMode.ElideRight, 255
            )
        )
        self.detail_title.setToolTip(display_title)
        profile = self.catalog.get_profile(path)
        self.favorite_check.blockSignals(True)
        self.favorite_check.setChecked(profile["favorite"])
        self.favorite_check.setEnabled(True)
        self.favorite_check.blockSignals(False)
        self.reading_state.blockSignals(True)
        self.reading_state.setCurrentIndex(
            max(0, self.reading_state.findData(profile["reading_status"]))
        )
        self.reading_state.setEnabled(True)
        self.reading_state.blockSignals(False)
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
        self.edit_metadata_btn.setEnabled(True)
        self.assign_shelves_btn.setEnabled(True)

    def copy_full_path(self):
        if self.selected_path:
            QApplication.clipboard().setText(self.selected_path)

    def _edit_metadata(self):
        if not self.selected_path:
            return
        path = self.selected_path
        book = self.books.get(path)
        if not book:
            return
        dialog = MetadataDialog(
            path, book["title"], self.catalog.get_metadata(path), self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.catalog.save_metadata(path, **dialog.values())
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Cannot save metadata", str(exc))
            return
        # Never overwrite the scanned filename title or change the PDF itself.
        self._load_existing()
        if path in self.books:
            self.selected_path = path
            self._refresh_gallery()

    def _edit_book_collections(self):
        if not self.selected_path:
            return
        path = self.selected_path
        book = self.books.get(path)
        if not book:
            return
        dialog = CollectionMembershipDialog(
            book.get("display_title") or book["title"],
            self.catalog.list_collections(),
            self.catalog.get_book_collections(path),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.catalog.set_book_collections(path, dialog.selected_ids())
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Cannot update virtual shelves", str(exc))
            return
        self._refresh_gallery()

    def _change_favorite(self, checked: bool):
        if not self.selected_path:
            return
        self.catalog.set_favorite(self.selected_path, checked)
        self._refresh_gallery()

    def _change_reading_status(self, _index: int):
        if not self.selected_path:
            return
        status = self.reading_state.currentData()
        if status is None:
            return
        self.catalog.set_reading_status(self.selected_path, status)
        self._refresh_gallery()

    def _open_search_result(self, path: str, page_index: int):
        if path not in self.books:
            QMessageBox.warning(
                self, "Book unavailable", "This indexed PDF is not in the active library."
            )
            return
        self.selected_path = path
        self.open_book(target_page=int(page_index))

    def open_book(self, target_page: int | None = None):
        """Open a PDF internally; optionally navigate to a 0-based search hit."""
        if not self.selected_path:
            return
        path = Path(self.selected_path)
        if not path.is_file():
            QMessageBox.warning(self, "Missing file", "The PDF is no longer at its cataloged path.")
            return
        key = str(path)
        profile = self.catalog.get_profile(key)
        if profile["reading_status"] == "unread":
            self.catalog.set_reading_status(key, "reading")
            selected = self.gallery.currentItem()
            if selected and selected.data(Qt.ItemDataRole.UserRole) == key:
                self.reading_state.blockSignals(True)
                self.reading_state.setCurrentIndex(
                    self.reading_state.findData("reading")
                )
                self.reading_state.blockSignals(False)
            self._refresh_gallery()
        existing = self.reader_tabs.get(key)
        if existing is not None:
            self.tabs.setCurrentWidget(existing)
            if target_page is not None:
                if existing._loaded:
                    existing.goto_page(target_page)
                else:
                    QTimer.singleShot(0, lambda: existing.goto_page(target_page))
            return
        try:
            reader = PdfReader(
                path, self.catalog, self.tabs, start_page=target_page
            )
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
        if index <= 1:
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
        self.search_panel.shutdown()
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
