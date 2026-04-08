"""Top bar: search, type filter pills, settings gear, refresh + import buttons."""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QPushButton, QButtonGroup,
)
from PySide6.QtCore import Signal, QTimer


class SearchBar(QWidget):
    search_changed = Signal(str)
    type_filter_changed = Signal(str)
    settings_requested = Signal()
    refresh_requested = Signal()
    import_requested = Signal()

    TYPE_FILTERS = [
        ("All", ""),
        ("Materials", "material"),
        ("Textures", "texture"),
        ("Gobos", "gobo"),
        ("Other", "other"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setFixedHeight(48)
        self._debounce = QTimer()
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._emit_search)
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(10)

        # Settings gear
        self.settings_btn = QPushButton("  Settings")
        self.settings_btn.setFixedHeight(34)
        self.settings_btn.setMinimumWidth(90)
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.setStyleSheet("""
            QPushButton {
                background: #23262e; color: #8b8e96;
                border: 1px solid #2a2d35; border-radius: 6px;
                font-size: 12px; padding: 4px 12px;
            }
            QPushButton:hover { color: #d4d6db; border-color: #4a9eff; }
        """)
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self.settings_btn)

        # Search input
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Search assets...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(200)
        self.search_input.textChanged.connect(lambda: self._debounce.start())
        layout.addWidget(self.search_input, stretch=1)

        layout.addSpacing(8)

        # Type pills
        self.type_group = QButtonGroup(self)
        self.type_group.setExclusive(True)
        for label, val in self.TYPE_FILTERS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("class", "typePill")
            btn.setProperty("type_value", val)
            btn.setFixedHeight(28)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent; color: #6b6e76;
                    border: none; border-radius: 14px; padding: 4px 12px;
                    font-size: 12px; font-weight: 500;
                }
                QPushButton:hover { color: #d4d6db; background-color: #23262e; }
                QPushButton:checked { background-color: #4a9eff; color: #ffffff; font-weight: 600; }
            """)
            self.type_group.addButton(btn)
            layout.addWidget(btn)
        self.type_group.buttons()[0].setChecked(True)
        self.type_group.buttonClicked.connect(self._on_type)

        layout.addSpacing(8)

        # Refresh button
        self.refresh_btn = QPushButton("  Refresh")
        self.refresh_btn.setFixedHeight(34)
        self.refresh_btn.setMinimumWidth(90)
        self.refresh_btn.setToolTip("Refresh & sync JSON files + previews")
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background: #23262e; color: #8b8e96;
                border: 1px solid #2a2d35; border-radius: 6px;
                font-size: 12px; padding: 4px 12px;
            }
            QPushButton:hover { color: #d4d6db; border-color: #4a9eff; }
        """)
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        layout.addWidget(self.refresh_btn)

        # Import button
        self.import_btn = QPushButton("+ Import")
        self.import_btn.setObjectName("primaryBtn")
        self.import_btn.setFixedHeight(34)
        self.import_btn.setMinimumWidth(90)
        self.import_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a9eff; color: #ffffff;
                border: none; border-radius: 6px;
                font-size: 12px; font-weight: 600; padding: 4px 16px;
            }
            QPushButton:hover { background-color: #5aabff; }
            QPushButton:pressed { background-color: #3a8eef; }
        """)
        self.import_btn.clicked.connect(self.import_requested.emit)
        layout.addWidget(self.import_btn)

    def _emit_search(self):
        self.search_changed.emit(self.search_input.text().strip())

    def _on_type(self, btn):
        self.type_filter_changed.emit(btn.property("type_value"))

    def current_type_filter(self) -> str:
        btn = self.type_group.checkedButton()
        return btn.property("type_value") if btn else ""

    def current_search(self) -> str:
        return self.search_input.text().strip()
