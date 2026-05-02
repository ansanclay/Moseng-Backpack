"""Tag picker dialog - select from existing tags or create a new one."""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem,
)
from PySide6.QtCore import Qt


class TagPickerDialog(QDialog):
    def __init__(self, existing_tags: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Tag")
        self.setMinimumSize(320, 400)
        self._selected = ""
        self._existing = existing_tags
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # New tag input
        header = QLabel("Create new tag or select existing:")
        header.setStyleSheet("color: #d4d6db; font-size: 13px; font-weight: 600;")
        layout.addWidget(header)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a new tag name...")
        self._input.textChanged.connect(self._filter_list)
        layout.addWidget(self._input)

        # Existing tags list
        if self._existing:
            list_label = QLabel("Existing tags:")
            list_label.setStyleSheet("color: #6b6e76; font-size: 11px; font-weight: 700; letter-spacing: 1px;")
            layout.addWidget(list_label)

            self._list = QListWidget()
            self._list.setStyleSheet("""
                QListWidget { background: #23262e; border: 1px solid #2a2d35; border-radius: 6px; }
                QListWidget::item { padding: 6px 10px; color: #d4d6db; }
                QListWidget::item:selected { background: #002aff; color: #ffffff; }
                QListWidget::item:hover { background: #2a2d35; }
            """)
            for tag in self._existing:
                self._list.addItem(tag)
            self._list.itemClicked.connect(self._on_item_click)
            self._list.itemDoubleClicked.connect(self._on_item_dblclick)
            layout.addWidget(self._list, stretch=1)
        else:
            self._list = None
            layout.addStretch()

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        self._btn_ok = QPushButton("Add Tag")
        self._btn_ok.setObjectName("primaryBtn")
        self._btn_ok.clicked.connect(self._accept)
        self._btn_ok.setEnabled(False)
        btn_row.addWidget(self._btn_ok)

        layout.addLayout(btn_row)

        self._input.returnPressed.connect(self._accept)

    def _filter_list(self, text: str):
        self._btn_ok.setEnabled(bool(text.strip()))
        if self._list:
            for i in range(self._list.count()):
                item = self._list.item(i)
                item.setHidden(text.lower() not in item.text().lower())

    def _on_item_click(self, item: QListWidgetItem):
        self._input.setText(item.text())
        self._btn_ok.setEnabled(True)

    def _on_item_dblclick(self, item: QListWidgetItem):
        self._input.setText(item.text())
        self._accept()

    def _accept(self):
        tag = self._input.text().strip()
        if tag:
            self._selected = tag
            self.accept()

    def selected_tag(self) -> str:
        return self._selected
