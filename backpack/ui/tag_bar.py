"""Left sidebar - tag filters with tag-head preview overlay.

Tags are collected from all JSON sidecar files. Colors and head assets are stored
in the global tag registry (.backpack_tags.json).
"""

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QHBoxLayout, QMenu,
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap

from backpack.constants import tag_color_for_name

from backpack.core.scanner import ScannedMaterial, ScannedAsset
from backpack.core.tag_registry import TagInfo
from backpack.core.downscale import detect_resolution_tag


def _tag_dot_icon(color_hex: str, size: int = 8) -> QIcon:
    """Create a small rounded-square icon filled with color_hex."""
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color_hex))
    painter.drawRoundedRect(0, 0, size, size, 2, 2)
    painter.end()
    return QIcon(px)


class TagChip(QPushButton):
    """Tag chip with a colored dot icon and tag-color-aware checked state."""

    def __init__(self, name: str, count: int, tag_info: TagInfo | None = None,
                 accent: str = "#002aff", backpack_root: Path | None = None, parent=None):
        label = f"{name}  {count}" if count else name
        super().__init__(label, parent)
        self.tag_name = name
        self.setCheckable(True)
        self.setMinimumHeight(28)
        self.setCursor(Qt.PointingHandCursor)

        # Resolve tag color
        color = (tag_info.color if tag_info and tag_info.color
                 else tag_color_for_name(name))

        # Colored dot icon
        self.setIcon(_tag_dot_icon(color))
        self.setIconSize(QSize(8, 8))

        # Color-aware checked state
        c = QColor(color)
        dim = f"rgba({c.red()},{c.green()},{c.blue()},36)"   # ~14% alpha bg
        bd  = f"rgba({c.red()},{c.green()},{c.blue()},71)"   # ~28% alpha border

        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: #6f7280;
                border: none;
                border-radius: 5px;
                padding: 4px 12px;
                text-align: left;
                font-size: 11.5px;
                font-family: "DM Sans", "Inter", "Segoe UI", sans-serif;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,10);
                color: #cdd0df;
            }}
            QPushButton:checked {{
                background: {dim};
                color: {color};
                border: 1px solid {bd};
            }}
        """)


_RES_ORDER = ["8K", "4K", "2K", "1K"]


class ResolutionChip(QPushButton):
    """Resolution filter chip — pure QSS button, no custom painting."""

    def __init__(self, res: str, count: int, parent=None):
        label = f"{res}  {count}" if count else res
        super().__init__(label, parent)
        self.res_name = res
        self.setCheckable(True)
        self.setMinimumHeight(26)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #6f7280;
                border: none;
                border-radius: 5px;
                padding: 3px 12px;
                text-align: left;
                font-size: 11px;
                font-family: "DM Sans", "Inter", "Segoe UI", sans-serif;
            }
            QPushButton:hover {
                background: rgba(255,255,255,10);
                color: #cdd0df;
            }
            QPushButton:checked {
                background: #05091e;
                color: #1a3fff;
                border: 1px solid rgba(0,42,255,40);
            }
        """)


class SidebarPanel(QWidget):
    tags_changed = Signal(list)         # selected tag names
    resolutions_changed = Signal(list)  # selected resolution strings e.g. ["4K"]
    add_tag_requested = Signal()
    refresh_requested = Signal()
    tag_delete_requested = Signal(str)

    def __init__(self, accent_color: str = "#002aff", parent=None):
        super().__init__(parent)
        self.accent = accent_color
        self.setObjectName("sidebar")
        self.setFixedWidth(210)
        self._chips: list[TagChip] = []
        self._res_chips: list[ResolutionChip] = []
        self._tag_registry: dict[str, TagInfo] = {}
        self._backpack_root: Path | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Resolution section ──
        res_header = QHBoxLayout()
        res_header.setContentsMargins(16, 10, 8, 4)
        res_title = QLabel("RESOLUTION")
        res_title.setObjectName("sidebarTitle")
        res_header.addWidget(res_title)
        res_header.addStretch()
        layout.addLayout(res_header)

        self._res_container = QWidget()
        self._res_layout = QVBoxLayout(self._res_container)
        self._res_layout.setContentsMargins(8, 0, 8, 4)
        self._res_layout.setSpacing(3)
        layout.addWidget(self._res_container)

        # ── Tags section ──
        tag_header = QHBoxLayout()
        tag_header.setContentsMargins(16, 10, 8, 6)
        tag_title = QLabel("TAGS")
        tag_title.setObjectName("sidebarTitle")
        tag_header.addWidget(tag_title)
        tag_header.addStretch()

        btn_add = QPushButton("+")
        btn_add.setFixedSize(24, 24)
        btn_add.setToolTip("Add new tag")
        btn_add.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255,255,255,10); color: {self.accent};
                border: 1px solid #101118; border-radius: 12px;
                font-size: 15px; font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: rgba(255,255,255,18); border-color: #18192a;
            }}
        """)
        btn_add.clicked.connect(self.add_tag_requested.emit)
        tag_header.addWidget(btn_add)
        layout.addLayout(tag_header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: transparent; width: 3px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,15); border-radius: 2px; min-height: 20px;
            }
            QScrollBar::handle:vertical:hover { background: rgba(255,255,255,30); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,  QScrollBar::sub-page:vertical {
                background: none; height: 0; border: none;
            }
        """)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(8, 4, 8, 8)
        self._layout.setSpacing(3)
        self._layout.addStretch()

        scroll.setWidget(self._container)
        layout.addWidget(scroll, stretch=1)

    def set_accent(self, color: str):
        self.accent = color

    def set_tag_registry(self, registry: dict[str, TagInfo], backpack_root: Path):
        """Set the tag registry reference (managed by MainWindow)."""
        self._tag_registry = registry
        self._backpack_root = backpack_root

    def load_tags_from_scan(self, materials: list[ScannedMaterial], assets: list[ScannedAsset]):
        """Collect tags and resolutions from scanned items and display them."""
        # ── Resolution chips ──
        for c in self._res_chips:
            self._res_layout.removeWidget(c)
            c.deleteLater()
        self._res_chips.clear()

        res_counts: dict[str, int] = {}
        for mat in materials:
            for a in mat.maps:
                tag = detect_resolution_tag(a.filename)
                if tag:
                    res_counts[tag] = res_counts.get(tag, 0) + 1
                    break  # one count per material
        for asset in assets:
            tag = detect_resolution_tag(asset.filename)
            if tag:
                res_counts[tag] = res_counts.get(tag, 0) + 1

        for res in _RES_ORDER:
            if res in res_counts:
                chip = ResolutionChip(res, res_counts[res])
                chip.toggled.connect(self._on_res_toggled)
                self._res_layout.addWidget(chip)
                self._res_chips.append(chip)

        # ── Tag chips ──
        for c in self._chips:
            self._layout.removeWidget(c)
            c.deleteLater()
        self._chips.clear()

        tag_counts: dict[str, int] = {}
        for mat in materials:
            for t in mat.meta.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1
            if mat.meta.favorite:
                tag_counts["Favorites"] = tag_counts.get("Favorites", 0) + 1
        for asset in assets:
            for t in asset.meta.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1
            if asset.meta.favorite:
                tag_counts["Favorites"] = tag_counts.get("Favorites", 0) + 1

        sorted_tags = []
        if "Favorites" in tag_counts:
            sorted_tags.append(("Favorites", tag_counts.pop("Favorites")))
        for name in sorted(tag_counts.keys()):
            sorted_tags.append((name, tag_counts[name]))

        for name, count in sorted_tags:
            tag_info = self._tag_registry.get(name)
            chip = TagChip(name, count, tag_info=tag_info, accent=self.accent,
                          backpack_root=self._backpack_root)
            chip.toggled.connect(self._on_toggled)
            chip.setContextMenuPolicy(Qt.CustomContextMenu)
            chip.customContextMenuRequested.connect(
                lambda _pos, n=name: self._chip_context_menu(n))
            self._layout.insertWidget(self._layout.count() - 1, chip)
            self._chips.append(chip)

    def _on_toggled(self, checked):
        selected = [c.tag_name for c in self._chips if c.isChecked()]
        self.tags_changed.emit(selected)

    def _on_res_toggled(self, checked):
        selected = [c.res_name for c in self._res_chips if c.isChecked()]
        self.resolutions_changed.emit(selected)

    def _chip_context_menu(self, tag_name: str):
        # Skip Favorites — it's a virtual tag, not deletable
        if tag_name.lower() == "favorites":
            return
        menu = QMenu(self)
        act = QAction(f'Delete tag "{tag_name}"', self)
        act.triggered.connect(lambda: self.tag_delete_requested.emit(tag_name))
        menu.addAction(act)
        menu.exec(self.cursor().pos())

    def clear_selection(self):
        for c in self._chips:
            c.setChecked(False)
        for c in self._res_chips:
            c.setChecked(False)
