"""Tag editor dialog for creating, editing, and deleting tags."""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QColorDialog,
    QMessageBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap, QIcon

from backpack.db.connection import DatabaseManager
from backpack.db import queries
from backpack.models.tag import Tag


class TagEditorDialog(QDialog):
    """Dialog for managing tags: create, rename, recolor, delete."""

    def __init__(self, db: DatabaseManager, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Tag Manager")
        self.setMinimumSize(400, 500)
        self._selected_color = "#888888"
        self._setup_ui()
        self._load_tags()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("Manage Tags")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        # Tag list
        self.tag_list = QListWidget()
        self.tag_list.currentItemChanged.connect(self._on_tag_selected)
        layout.addWidget(self.tag_list)

        # Edit section
        edit_layout = QHBoxLayout()

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Tag name...")
        edit_layout.addWidget(self.name_input, stretch=1)

        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(32, 32)
        self.color_btn.clicked.connect(self._pick_color)
        self._update_color_btn()
        edit_layout.addWidget(self.color_btn)

        layout.addLayout(edit_layout)

        # Action buttons
        btn_layout = QHBoxLayout()

        self.btn_add = QPushButton("Add New")
        self.btn_add.setObjectName("primaryButton")
        self.btn_add.clicked.connect(self._add_tag)
        btn_layout.addWidget(self.btn_add)

        self.btn_update = QPushButton("Update")
        self.btn_update.clicked.connect(self._update_tag)
        self.btn_update.setEnabled(False)
        btn_layout.addWidget(self.btn_update)

        self.btn_delete = QPushButton("Delete")
        self.btn_delete.clicked.connect(self._delete_tag)
        self.btn_delete.setEnabled(False)
        btn_layout.addWidget(self.btn_delete)

        layout.addLayout(btn_layout)

        # Close
        close_layout = QHBoxLayout()
        close_layout.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        close_layout.addWidget(btn_close)
        layout.addLayout(close_layout)

    def _load_tags(self):
        self.tag_list.clear()
        tags = queries.get_all_tags(self.db)
        for tag in tags:
            count = queries.get_tag_asset_count(self.db, tag.id)
            item = QListWidgetItem(f"{tag.name} ({count})")
            item.setData(Qt.UserRole, tag)
            item.setIcon(self._color_icon(tag.color))
            self.tag_list.addItem(item)

    def _on_tag_selected(self, current, previous):
        if current:
            tag: Tag = current.data(Qt.UserRole)
            self.name_input.setText(tag.name)
            self._selected_color = tag.color
            self._update_color_btn()
            self.btn_update.setEnabled(True)
            self.btn_delete.setEnabled(True)
        else:
            self.btn_update.setEnabled(False)
            self.btn_delete.setEnabled(False)

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(self._selected_color), self)
        if color.isValid():
            self._selected_color = color.name()
            self._update_color_btn()

    def _update_color_btn(self):
        pix = QPixmap(24, 24)
        pix.fill(QColor(self._selected_color))
        self.color_btn.setIcon(QIcon(pix))

    def _add_tag(self):
        name = self.name_input.text().strip()
        if not name:
            return
        existing = queries.get_tag_by_name(self.db, name)
        if existing:
            QMessageBox.warning(self, "Duplicate", f"Tag '{name}' already exists.")
            return
        queries.create_tag(self.db, name, self._selected_color)
        self.name_input.clear()
        self._load_tags()

    def _update_tag(self):
        current = self.tag_list.currentItem()
        if not current:
            return
        tag: Tag = current.data(Qt.UserRole)
        name = self.name_input.text().strip()
        if not name:
            return
        queries.update_tag(self.db, tag.id, name, self._selected_color)
        self._load_tags()

    def _delete_tag(self):
        current = self.tag_list.currentItem()
        if not current:
            return
        tag: Tag = current.data(Qt.UserRole)
        reply = QMessageBox.question(
            self, "Delete Tag",
            f"Delete tag '{tag.name}'? It will be removed from all assets.",
        )
        if reply == QMessageBox.Yes:
            queries.delete_tag(self.db, tag.id)
            self._load_tags()

    def _color_icon(self, color_hex: str) -> QIcon:
        pix = QPixmap(16, 16)
        pix.fill(QColor(color_hex))
        return QIcon(pix)


class AssetTagDialog(QDialog):
    """Dialog for editing tags on a specific asset."""

    def __init__(self, db: DatabaseManager, asset_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.asset_id = asset_id
        self.setWindowTitle("Edit Asset Tags")
        self.setMinimumSize(350, 400)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        asset = queries.get_asset_by_id(self.db, self.asset_id)
        if asset:
            title = QLabel(f"Tags for: {asset.filename}")
            title.setObjectName("sectionTitle")
            layout.addWidget(title)

        self.tag_list = QListWidget()
        layout.addWidget(self.tag_list)

        self._load_tags()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_close = QPushButton("Done")
        btn_close.setObjectName("primaryButton")
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def _load_tags(self):
        self.tag_list.clear()
        all_tags = queries.get_all_tags(self.db)
        asset_tags = queries.get_asset_tag_names(self.db, self.asset_id)

        for tag in all_tags:
            item = QListWidgetItem(tag.name)
            item.setData(Qt.UserRole, tag.id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if tag.name in asset_tags else Qt.Unchecked
            )
            item.setIcon(self._color_icon(tag.color))
            self.tag_list.addItem(item)

        self.tag_list.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, item):
        tag_id = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            queries.add_tag_to_asset(self.db, self.asset_id, tag_id)
        else:
            queries.remove_tag_from_asset(self.db, self.asset_id, tag_id)

    def _color_icon(self, color_hex: str) -> QIcon:
        pix = QPixmap(16, 16)
        pix.fill(QColor(color_hex))
        return QIcon(pix)
