"""Dialogs for personal metadata and virtual collection membership.

All edits are kept in local SQLite and do not change source PDF metadata.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPlainTextEdit, QVBoxLayout,
)


class MetadataDialog(QDialog):
    def __init__(self, path: Path | str, original_title: str, current: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit library metadata")
        self.resize(640, 680)
        self.setMinimumWidth(500)
        outer = QVBoxLayout(self)

        heading = QLabel(
            "These details belong to your personal catalog. Your PDF will not be modified."
        )
        heading.setWordWrap(True)
        outer.addWidget(heading)

        path_label = QLabel(Path(path).name)
        path_label.setToolTip(str(path))
        path_label.setWordWrap(True)
        outer.addWidget(path_label)

        form = QFormLayout()
        outer.addLayout(form)

        self.title = QLineEdit(current.get("display_title", ""))
        self.title.setPlaceholderText(f"Automatic filename title: {original_title[:80]}")
        self.title.setMaxLength(350)
        form.addRow("Display title:", self.title)

        self.authors = QLineEdit(current.get("authors", ""))
        self.authors.setMaxLength(500)
        form.addRow("Author(s):", self.authors)

        self.publisher = QLineEdit(current.get("publisher", ""))
        self.publisher.setMaxLength(250)
        form.addRow("Publisher:", self.publisher)

        self.edition = QLineEdit(current.get("edition", ""))
        self.edition.setMaxLength(150)
        self.edition.setPlaceholderText("e.g. 2014, 2024, 2nd edition, printing ...")
        form.addRow("Edition / printing:", self.edition)

        self.year = QLineEdit(
            str(current["publication_year"]) if current.get("publication_year") else ""
        )
        self.year.setValidator(QIntValidator(1000, 2100, self))
        self.year.setPlaceholderText("Optional")
        form.addRow("Publication year:", self.year)

        self.game_system = QLineEdit(current.get("game_system", ""))
        self.game_system.setMaxLength(150)
        self.game_system.setPlaceholderText("e.g. D&D 5e, Pathfinder 2e")
        form.addRow("System:", self.game_system)

        self.tags = QLineEdit(", ".join(current.get("tags", [])))
        self.tags.setPlaceholderText("Separate tags with commas")
        self.tags.setToolTip("Tags are case-insensitive. Separate individual tags with commas.")
        form.addRow("Tags:", self.tags)

        self.description = QPlainTextEdit(current.get("description", ""))
        self.description.setPlaceholderText("Book description")
        self.description.setFixedHeight(95)
        form.addRow("Description:", self.description)

        self.notes = QPlainTextEdit(current.get("notes", ""))
        self.notes.setPlaceholderText("Personal research or catalog notes")
        self.notes.setFixedHeight(95)
        form.addRow("Personal notes:", self.notes)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)

    def values(self) -> dict:
        year_text = self.year.text().strip()
        return {
            "display_title": self.title.text(),
            "authors": self.authors.text(),
            "publisher": self.publisher.text(),
            "edition": self.edition.text(),
            "publication_year": int(year_text) if year_text else None,
            "game_system": self.game_system.text(),
            "description": self.description.toPlainText(),
            "notes": self.notes.toPlainText(),
            "tags": self.tags.text().split(","),
        }


class CollectionMembershipDialog(QDialog):
    def __init__(self, book_title: str, collections: list[dict], selected: set[int], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose virtual shelves")
        self.resize(450, 450)
        outer = QVBoxLayout(self)
        heading = QLabel(
            f"Assign “{book_title}” to one or more virtual shelves. "
            "The PDF stays in its original folder."
        )
        heading.setWordWrap(True)
        outer.addWidget(heading)
        self.list = QListWidget()
        for collection in collections:
            item = QListWidgetItem(collection["name"])
            item.setData(Qt.ItemDataRole.UserRole, collection["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if collection["id"] in selected
                else Qt.CheckState.Unchecked
            )
            self.list.addItem(item)
        outer.addWidget(self.list)
        if not collections:
            message = QLabel("Create a shelf in the Collections tab first.")
            outer.addWidget(message)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def selected_ids(self) -> set[int]:
        return {
            int(item.data(Qt.ItemDataRole.UserRole))
            for i in range(self.list.count())
            if (item := self.list.item(i)).checkState() == Qt.CheckState.Checked
        }
