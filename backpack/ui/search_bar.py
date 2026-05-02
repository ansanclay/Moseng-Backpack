"""Top bar: search, type filter pills, settings gear, refresh + import buttons."""

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QPushButton, QButtonGroup,
)
from PySide6.QtCore import Signal, QTimer, QSize
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from PySide6.QtSvg import QSvgRenderer


def _svg_icon(svg_path: str, normal_color: str, hover_color: str,
              size: int = 18) -> QIcon:
    """Load an SVG and return a two-mode QIcon (Normal + Active).

    The SVG must use fill="white" as a placeholder; this function
    replaces it with the desired tint colors at render time.
    """
    try:
        raw = Path(svg_path).read_bytes()
    except OSError:
        return QIcon()

    def _render(color: str) -> QPixmap:
        colored = raw.replace(b'fill="white"',
                              ("fill=\"%s\"" % color).encode())
        renderer = QSvgRenderer(colored)
        pix = QPixmap(size, size)
        pix.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pix)
        renderer.render(painter)
        painter.end()
        return pix

    icon = QIcon()
    icon.addPixmap(_render(normal_color), QIcon.Normal, QIcon.Off)
    icon.addPixmap(_render(hover_color),  QIcon.Active, QIcon.Off)
    return icon


_ICONS_DIR = Path(__file__).parent / "resources" / "icons"


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
            QPushButton:hover { color: #d4d6db; border-color: #002aff; }
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
                QPushButton:checked { background-color: #002aff; color: #ffffff; font-weight: 600; }
            """)
            self.type_group.addButton(btn)
            layout.addWidget(btn)
        self.type_group.buttons()[0].setChecked(True)
        self.type_group.buttonClicked.connect(self._on_type)

        layout.addSpacing(8)

        # Refresh button — SVG icon
        self.refresh_btn = QPushButton()
        self.refresh_btn.setFixedSize(34, 34)
        self.refresh_btn.setToolTip("Refresh & sync JSON files + previews")

        _icon = _svg_icon(
            str(_ICONS_DIR / "refresh.svg"),
            normal_color="#8b8e96",
            hover_color="#d4d6db",
            size=16,
        )
        if not _icon.isNull():
            self.refresh_btn.setIcon(_icon)
            self.refresh_btn.setIconSize(QSize(16, 16))
        else:
            self.refresh_btn.setText("↻")   # fallback glyph

        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background: #23262e;
                border: 1px solid #2a2d35; border-radius: 6px;
            }
            QPushButton:hover { border-color: #002aff; }
            QPushButton:pressed { background: #1a1d24; }
        """)
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        layout.addWidget(self.refresh_btn)

        # Import button
        self.import_btn = QPushButton("+ Import")
        self.import_btn.setObjectName("primaryBtn")
        self.import_btn.setFixedHeight(34)
        self.import_btn.setMinimumWidth(90)
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
