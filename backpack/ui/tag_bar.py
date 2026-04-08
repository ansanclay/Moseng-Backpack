"""Left sidebar - tag filters with tag-head preview overlay.

Tags are collected from all JSON sidecar files. Colors and head assets are stored
in the global tag registry (.backpack_tags.json).
"""

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QHBoxLayout,
)
from PySide6.QtCore import Qt, Signal, QRect, QRectF
from PySide6.QtGui import QPainter, QPixmap, QColor, QFont, QImage, QPainterPath

from backpack.core.scanner import ScannedMaterial, ScannedAsset
from backpack.core.tag_registry import TagInfo


class TagChip(QPushButton):
    """Tag chip with colored background, white text, and optional grayscale head overlay."""

    def __init__(self, name: str, count: int, tag_info: TagInfo | None = None,
                 accent: str = "#4a9eff", backpack_root: Path | None = None, parent=None):
        super().__init__(parent)
        self.tag_name = name
        self.setCheckable(True)
        self._count = count
        self._label_text = f"{name}  {count}" if count else name
        self.setText(self._label_text)
        self.setMinimumHeight(32)

        self._bg_color = "#4a9eff"
        self._head_pixmap: QPixmap | None = None

        is_fav = name.lower() == "favorites"
        if is_fav:
            bg = "#f59e0b"
            self._bg_color = bg
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {bg}25;
                    color: {bg};
                    border: 1px solid {bg}40;
                    border-radius: 14px; padding: 4px 12px;
                    font-size: 11px; font-weight: 600; min-height: 28px;
                }}
                QPushButton:checked {{
                    background-color: {bg}; color: #ffffff; border: none;
                }}
                QPushButton:hover {{ background-color: {bg}40; }}
            """)
        else:
            bg = (tag_info.color if tag_info and tag_info.color else accent)
            self._bg_color = bg

            # Load head preview as grayscale pixmap
            if tag_info and tag_info.head_preview and backpack_root:
                head_path = backpack_root / tag_info.head_preview
                if head_path.exists():
                    self._head_pixmap = QPixmap(str(head_path))

            # Use paintEvent for custom rendering when we have a head image
            if self._head_pixmap and not self._head_pixmap.isNull():
                self.setStyleSheet(f"""
                    QPushButton {{
                        background-color: transparent;
                        color: #ffffff;
                        border: none;
                        border-radius: 14px; padding: 4px 12px;
                        font-size: 11px; font-weight: 600; min-height: 28px;
                    }}
                    QPushButton:checked {{
                        border: 2px solid #ffffff;
                    }}
                """)
            else:
                self.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {bg}; color: #ffffff;
                        border: none;
                        border-radius: 14px; padding: 4px 12px;
                        font-size: 11px; font-weight: 600; min-height: 28px;
                    }}
                    QPushButton:checked {{
                        background-color: {bg}; color: #ffffff;
                        border: 2px solid #ffffff;
                    }}
                    QPushButton:hover {{ background-color: {bg}dd; }}
                """)

    def paintEvent(self, event):
        if not self._head_pixmap or self._head_pixmap.isNull() or self.tag_name.lower() == "favorites":
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        r = self.rect()
        radius = 14

        # Clip to rounded rect
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(r), radius, radius)
        painter.setClipPath(clip)

        # Draw base color
        painter.fillRect(r, QColor(self._bg_color))

        # Draw grayscale head image with multiply blend
        # Convert to grayscale, then draw semi-transparent
        img = self._head_pixmap.toImage().convertToFormat(QImage.Format_Grayscale8)
        gray_pix = QPixmap.fromImage(img)

        # Scale to cover the chip
        scaled = gray_pix.scaled(r.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        xo = (scaled.width() - r.width()) // 2
        yo = (scaled.height() - r.height()) // 2
        cropped = scaled.copy(xo, yo, r.width(), r.height())

        # Draw with low opacity to simulate multiply
        painter.setOpacity(0.25)
        painter.setCompositionMode(QPainter.CompositionMode_Multiply)
        painter.drawPixmap(0, 0, cropped)

        # Reset
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setOpacity(1.0)

        # Selected border
        if self.isChecked():
            painter.setPen(QColor("#ffffff"))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(r.adjusted(1, 1, -1, -1), radius, radius)

        # Text
        painter.setPen(QColor("#ffffff"))
        font = QFont("Segoe UI", 11)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.drawText(r.adjusted(12, 0, -12, 0), Qt.AlignVCenter | Qt.AlignLeft, self._label_text)

        painter.end()


class SidebarPanel(QWidget):
    tags_changed = Signal(list)   # selected tag names
    add_tag_requested = Signal()
    refresh_requested = Signal()

    def __init__(self, accent_color: str = "#4a9eff", parent=None):
        super().__init__(parent)
        self.accent = accent_color
        self.setObjectName("sidebar")
        self.setFixedWidth(210)
        self._chips: list[TagChip] = []
        self._tag_registry: dict[str, TagInfo] = {}
        self._backpack_root: Path | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        tag_header = QHBoxLayout()
        tag_header.setContentsMargins(16, 14, 8, 6)
        tag_title = QLabel("TAGS")
        tag_title.setObjectName("sidebarTitle")
        tag_header.addWidget(tag_title)
        tag_header.addStretch()

        btn_add = QPushButton("+")
        btn_add.setFixedSize(26, 26)
        btn_add.setToolTip("Add new tag")
        btn_add.setStyleSheet(f"""
            QPushButton {{
                background-color: #23262e; color: {self.accent};
                border: 1px solid #2a2d35; border-radius: 13px;
                font-size: 16px; font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: #2a2d35; border-color: {self.accent};
            }}
        """)
        btn_add.clicked.connect(self.add_tag_requested.emit)
        tag_header.addWidget(btn_add)
        layout.addLayout(tag_header)

        # Scroll area for chips
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)

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
        """Collect all unique tags from scanned items and display them."""
        for c in self._chips:
            self._layout.removeWidget(c)
            c.deleteLater()
        self._chips.clear()

        tag_counts: dict[str, int] = {}

        # Collect from materials
        for mat in materials:
            for t in mat.meta.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1
            if mat.meta.favorite:
                tag_counts["Favorites"] = tag_counts.get("Favorites", 0) + 1

        # Collect from assets
        for asset in assets:
            for t in asset.meta.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1
            if asset.meta.favorite:
                tag_counts["Favorites"] = tag_counts.get("Favorites", 0) + 1

        # Favorites first, then sorted
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
            self._layout.insertWidget(self._layout.count() - 1, chip)
            self._chips.append(chip)

    def _on_toggled(self, checked):
        selected = [c.tag_name for c in self._chips if c.isChecked()]
        self.tags_changed.emit(selected)

    def clear_selection(self):
        for c in self._chips:
            c.setChecked(False)
