"""Asset detail panel - right side, shows selected asset or material info.

Supports: X to close, multi-select count, tag editing via JSON.
"""

import subprocess
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QScrollArea, QFrame, QInputDialog, QComboBox, QMessageBox,
    QLayout, QSizePolicy, QLineEdit, QStackedWidget, QMenu,
    QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Qt, Signal, QRect, QSize, QEvent, QRunnable, QThreadPool, QObject, QTimer
from PySide6.QtGui import (
    QPixmap, QImage, QPainter, QColor, QFont, QImageReader,
    QFontMetrics, QPixmapCache, QAction,
)

from backpack.core.scanner import ScannedAsset, ScannedMaterial
from backpack.core.metadata import (
    read_asset_meta, write_asset_meta, read_material_meta, write_material_meta,
)
from backpack.core.downscale import (
    get_available_resolutions, downscale_material, half_resolution,
)
from backpack.constants import random_blue, random_tag_color


class FlowLayout(QLayout):
    """Flow layout that wraps widgets horizontally."""
    def __init__(self, parent=None, margin=0, spacing=4):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect)

    def sizeHint(self):
        return QSize(200, 30)

    def minimumSize(self):
        return QSize(0, 0)

    def _do_layout(self, rect, test_only=False):
        x = rect.x()
        y = rect.y()
        line_h = 0

        for item in self._items:
            w = item.widget()
            if not w:
                continue
            sz = w.sizeHint()
            next_x = x + sz.width() + self._spacing
            if next_x - self._spacing > rect.right() and line_h > 0:
                x = rect.x()
                y += line_h + self._spacing
                next_x = x + sz.width() + self._spacing
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(x, y, sz.width(), sz.height()))
            x = next_x
            line_h = max(line_h, sz.height())

        return y + line_h - rect.y()


class TagSuggestOverlay(QFrame):
    """Floating overlay showing existing tags as selectable chips."""
    tag_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("QFrame { background: #0f1018; border: 1px solid #18192a; border-radius: 10px; }")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 140))
        self.setGraphicsEffect(shadow)

        self._tag_registry = {}
        self._all_tags: list[str] = []
        self._excluded: set[str] = set()  # already-added tags to hide

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)

        hint = QLabel("Existing tags — click to add")
        hint.setStyleSheet("color: #4c4e58; font-size: 10px; margin-bottom: 6px; background: transparent;")
        layout.addWidget(hint)

        self._flow_widget = QWidget()
        self._flow_widget.setStyleSheet("background: transparent;")
        self._flow = FlowLayout(self._flow_widget, margin=0, spacing=4)
        layout.addWidget(self._flow_widget)

    def show_near(self, widget: QWidget):
        pos = widget.mapToGlobal(widget.rect().bottomLeft())
        pos.setY(pos.y() + 4)
        self.move(pos)
        self.show()
        self.raise_()

    def populate(self, tags: list[str], tag_registry: dict,
                 excluded: set[str] | None = None, filter_text: str = ""):
        self._all_tags = tags
        self._tag_registry = tag_registry
        self._excluded = excluded or set()
        self._rebuild(filter_text)

    def _rebuild(self, filter_text: str):
        while self._flow.count():
            item = self._flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        q = filter_text.lower()
        candidates = [t for t in self._all_tags if t not in self._excluded]
        matches = [t for t in candidates if q in t.lower()] if q else candidates

        fm = QFontMetrics(QFont("Segoe UI", 11))
        CHIP_PAD = 18   # horizontal padding per chip (9px each side)
        SPACING = 4
        MARGIN = 20     # overlay left+right margin (10px each)
        MAX_W = 300
        MIN_W = 120

        tag_widths = []
        for t in matches[:30]:
            info = self._tag_registry.get(t)
            color = info.color if info and info.color else "#002aff"
            btn = QPushButton(t)
            btn.setFixedHeight(22)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {color}; color: #ffffff;
                    border-radius: 11px; border: none;
                    font-size: 11px; font-weight: 600; padding: 0 9px;
                }}
                QPushButton:hover {{ background: {color}cc; }}
            """)
            btn.clicked.connect(lambda _, tag=t: self.tag_selected.emit(tag))
            self._flow.addWidget(btn)
            tag_widths.append(fm.horizontalAdvance(t) + CHIP_PAD)

        if not tag_widths:
            self.setFixedSize(MIN_W, 52)
            return

        # Compute the smallest width that keeps rows ≤ 3
        total = sum(w + SPACING for w in tag_widths) - SPACING
        single_row_w = total + MARGIN
        content_w = min(max(single_row_w, MIN_W - MARGIN), MAX_W - MARGIN)

        # Simulate row wrapping to find actual row count at content_w
        row_x, rows = 0, 1
        for w in tag_widths:
            if row_x and row_x + w + SPACING > content_w:
                rows += 1
                row_x = w + SPACING
            else:
                row_x += w + SPACING
        if rows > 3:
            content_w = MAX_W - MARGIN

        overlay_w = content_w + MARGIN
        self._flow_widget.setFixedWidth(content_w)
        flow_h = self._flow.heightForWidth(content_w)
        self._flow_widget.setFixedHeight(max(flow_h, 22))
        self.setFixedWidth(overlay_w)
        self.adjustSize()

    def filter(self, text: str):
        self._rebuild(text)


class TagAddWidget(QWidget):
    """Inline tag-add control: starts as '+ Add' button, expands to text input."""
    tag_requested = Signal(str)

    def __init__(self, tag_registry: dict, current_tags: list[str] | None = None, parent=None):
        super().__init__(parent)
        self._tag_registry = tag_registry
        self._current_tags: set[str] = set(current_tags or [])
        self._overlay: TagSuggestOverlay | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._btn = QPushButton("+ Add")
        self._btn.setObjectName("tagAddBtn")
        self._btn.setFixedHeight(24)
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.clicked.connect(self._enter_edit_mode)
        layout.addWidget(self._btn)

        self._edit = QLineEdit()
        self._edit.setFixedHeight(24)
        self._edit.setPlaceholderText("name new tag…")
        self._edit.setStyleSheet("""
            QLineEdit {
                background: rgba(255,255,255,8); color: #cdd0df;
                border: 1px solid #002aff;
                border-radius: 12px; font-size: 11px;
                padding: 0 10px;
                font-family: "DM Sans", "Inter", "Segoe UI", sans-serif;
            }
        """)
        self._edit.setMinimumWidth(110)
        self._edit.hide()
        self._edit.textChanged.connect(self._on_text_changed)
        self._edit.returnPressed.connect(self._on_confirm)
        self._edit.installEventFilter(self)
        layout.addWidget(self._edit)

    def update_registry(self, tag_registry: dict):
        self._tag_registry = tag_registry

    def _enter_edit_mode(self):
        self._btn.hide()
        self._edit.show()
        self._edit.clear()
        self._edit.setFocus()
        self._ensure_overlay()
        self._overlay.populate(
            sorted(self._tag_registry.keys()),
            self._tag_registry,
            excluded=self._current_tags,
        )
        self._overlay.show_near(self._edit)

    def _exit_edit_mode(self):
        if self._overlay:
            self._overlay.hide()
        self._edit.clear()
        self._edit.hide()
        self._btn.show()

    def _ensure_overlay(self):
        if not self._overlay:
            self._overlay = TagSuggestOverlay()
            self._overlay.tag_selected.connect(self._on_overlay_tag)

    def _on_text_changed(self, text: str):
        if self._overlay and self._overlay.isVisible():
            self._overlay._excluded = self._current_tags
            self._overlay.filter(text)

    def _on_confirm(self):
        text = self._edit.text().strip()
        if text:
            self.tag_requested.emit(text)
        self._exit_edit_mode()

    def _on_overlay_tag(self, tag: str):
        self.tag_requested.emit(tag)
        self._exit_edit_mode()

    def eventFilter(self, obj, event):
        if obj is self._edit:
            if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
                self._exit_edit_mode()
                return True
            if event.type() == QEvent.FocusOut:
                # Delay so overlay clicks register first
                QTimer.singleShot(150, self._check_focus_lost)
        return super().eventFilter(obj, event)

    def _check_focus_lost(self):
        if not self._edit.hasFocus():
            self._exit_edit_mode()


class TagLabel(QWidget):
    """Colored tag chip with hover-reveal X button for removal.
    partial=True renders with semi-transparent bg and '~' prefix (mixed state).
    """
    remove_requested = Signal(str)   # emits tag name

    def __init__(self, name: str, color: str, parent=None, partial: bool = False):
        super().__init__(parent)
        self.tag_name = name
        self._partial = partial
        self.setFixedHeight(24)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Colored pill background via a container widget
        self._pill = QWidget()
        self._pill.setFixedHeight(24)
        pill_layout = QHBoxLayout(self._pill)
        pill_layout.setContentsMargins(8, 0, 4, 0)
        pill_layout.setSpacing(3)

        display_name = f"~ {name}" if partial else name
        self._name_lbl = QLabel(display_name)
        lbl_style = "color: #cdd0df; font-size: 11px; font-weight: 500; background: transparent;"
        if partial:
            lbl_style = "color: rgba(205,208,223,140); font-size: 11px; font-weight: 500; background: transparent;"
        self._name_lbl.setStyleSheet(lbl_style)
        pill_layout.addWidget(self._name_lbl)

        self._x_btn = QPushButton("×")
        self._x_btn.setFixedSize(16, 16)
        self._x_btn.setCursor(Qt.PointingHandCursor)
        self._x_btn.setVisible(False)
        self._x_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0,0,0,40); color: #ffffff;
                border-radius: 8px; font-size: 13px; font-weight: 700;
                border: none; padding: 0;
            }
            QPushButton:hover { background: rgba(0,0,0,120); }
        """)
        self._x_btn.clicked.connect(lambda: self.remove_requested.emit(self.tag_name))
        pill_layout.addWidget(self._x_btn)

        if partial:
            # Mixed-state: translucent fill + dashed border
            self._pill.setStyleSheet(f"""
                QWidget {{
                    background-color: {color}28;
                    border-radius: 12px;
                    border: 1px dashed {color}80;
                }}
            """)
        else:
            # v2: muted semi-transparent chip — color at ~18% alpha
            self._pill.setStyleSheet(f"""
                QWidget {{
                    background-color: {color}30;
                    border-radius: 12px;
                    border: 1px solid {color}60;
                }}
            """)
        layout.addWidget(self._pill)

    def sizeHint(self):
        # Name width + padding + optional X button
        fm_w = self._name_lbl.fontMetrics().horizontalAdvance(self._name_lbl.text())
        return QSize(fm_w + 24, 24)

    def enterEvent(self, event):
        self._x_btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._x_btn.setVisible(False)
        super().leaveEvent(event)


class ElidedLabel(QLabel):
    """QLabel that truncates text with '…' when it doesn't fit on one line."""

    def paintEvent(self, event):
        painter = QPainter(self)
        metrics = QFontMetrics(self.font())
        elided  = metrics.elidedText(self.text(), Qt.ElideRight, self.contentsRect().width())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.contentsRect(), self.alignment(), elided)


class StarRating(QWidget):
    rating_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rating = 0
        self._btns = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        for i in range(5):
            b = QPushButton()
            b.setFixedSize(22, 22)
            b.setFlat(True)
            b.clicked.connect(lambda _, idx=i: self._set(idx + 1))
            self._btns.append(b)
            lay.addWidget(b)
        lay.addStretch()
        self._update()

    def _set(self, r):
        self._rating = 0 if self._rating == r else r
        self._update()
        self.rating_changed.emit(self._rating)

    def set_rating(self, r):
        self._rating = r
        self._update()

    def _update(self):
        for i, b in enumerate(self._btns):
            on = i < self._rating
            b.setText("\u2605" if on else "\u2606")
            c = "#f0c050" if on else "#3a3d45"
            b.setStyleSheet(f"QPushButton {{ color: {c}; font-size: 15px; background: transparent; border: none; }}")


_MAP_COLORS = {
    "albedo":       "#5b9cf6",
    "normal":       "#9b8aff",
    "roughness":    "#50c878",
    "metallic":     "#c8c8d8",
    "specular":     "#7ecfff",
    "displacement": "#b0b0b0",
    "bump":         "#a0a4ff",
    "ao":           "#e0e0e0",
    "emissive":     "#f5c842",
    "opacity":      "#e07a8a",
    "gloss":        "#80e8c0",
    "translucency": "#99eedd",
    "cavity":       "#cc8888",
    "curvature":    "#ddaa66",
    "fuzz":         "#d4a0d0",
}


class MapBadge(QWidget):
    def __init__(self, sub_type: str, filename: str, parent=None):
        super().__init__(parent)
        from backpack.core.map_detector import SUB_TYPE_LABEL
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(6)

        dot = QLabel("\u25cf")
        color = _MAP_COLORS.get(sub_type, "#6b6e76")
        dot.setStyleSheet(f"color: {color}; font-size: 8px;")
        dot.setFixedWidth(12)
        lay.addWidget(dot)

        label = SUB_TYPE_LABEL.get(sub_type, sub_type.title() if sub_type else "Unknown")
        tl = QLabel(label)
        tl.setStyleSheet("color: #9ea0a8; font-size: 11px; font-weight: 600;")
        tl.setFixedWidth(110)
        lay.addWidget(tl)

        fl = QLabel(filename)
        fl.setStyleSheet("color: #4c4e58; font-size: 11px;")
        lay.addWidget(fl, stretch=1)


class AssetDetailPanel(QWidget):
    refresh_requested = Signal()
    tag_head_changed = Signal(str, object)  # tag_name, asset/material obj

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("detailPanel")
        self.setFixedWidth(270)
        self._current_asset: ScannedAsset | None = None
        self._current_material: ScannedMaterial | None = None
        self._multi_items: list = []  # list of (kind, obj) for multi-select
        self._tag_registry: dict = {}
        self._backpack_root: Path | None = None
        # Caches to skip redundant UI rebuilds
        self._last_tags_key: str = ""
        self._last_multi_key: str = ""
        self._hdr_sig_ref = None   # keeps HDR decode signal alive during background load

        # Notes auto-save: 1.5 s after last keystroke
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.setInterval(1500)
        self._note_timer.timeout.connect(self._save_notes)

        # Houdini live-status poller (runs every 3 s when panel is visible)
        self._hou_online: bool = False
        self._hou_poll_timer = QTimer(self)
        self._hou_poll_timer.setInterval(3000)
        self._hou_poll_timer.timeout.connect(self._poll_houdini_status)
        self._hou_poll_timer.start()

        self._setup_ui()
        self.show_empty()  # always visible — avoids browser reflow on show/hide

    def set_tag_registry(self, registry: dict, backpack_root: Path):
        self._tag_registry = registry
        self._backpack_root = backpack_root

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Preview stack (OUTSIDE scroll area — QOpenGLWidget can't live in QScrollArea) ──
        self._preview_stack = QStackedWidget()
        self._preview_stack.setFixedHeight(200)

        # Page 0: static image / multi-grid QLabel
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background-color: #07080d; padding: 4px;")
        self._preview_stack.addWidget(self.preview)   # index 0

        # Page 1: 3D viewer — created lazily on first use to avoid crash at startup
        self._viewer_3d = None

        outer.addWidget(self._preview_stack)

        # ── 3D toggle button row ──
        btn_row_w = QWidget()
        btn_row_w.setFixedHeight(32)
        btn_row_w.setStyleSheet("background: #07080d;")
        btn_row = QHBoxLayout(btn_row_w)
        btn_row.setContentsMargins(12, 4, 12, 4)
        btn_row.setSpacing(0)

        self._btn_3d = QPushButton("\u25b6  3D View")
        self._btn_3d.setFixedHeight(24)
        self._btn_3d.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_3d.setStyleSheet("""
            QPushButton {
                background: #002aff; color: #ffffff;
                border: none; border-radius: 5px;
                font-size: 11px; font-weight: 600; padding: 0 10px;
            }
            QPushButton:hover { background: #2244ff; }
            QPushButton:pressed { background: #0018cc; }
        """)
        self._btn_3d.clicked.connect(self._toggle_3d_view)
        self._btn_3d.hide()
        btn_row.addWidget(self._btn_3d)

        # Loaded-maps indicator (e.g. "A  N  R  S")
        self._map_indicator = QLabel()
        self._map_indicator.setStyleSheet("font-size: 10px; padding-left: 8px;")
        self._map_indicator.hide()
        btn_row.addWidget(self._map_indicator)
        btn_row.addStretch()

        outer.addWidget(btn_row_w)

        # ── Scrollable content ──
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: transparent; width: 3px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,18); border-radius: 2px; min-height: 20px;
            }
            QScrollBar::handle:vertical:hover { background: rgba(255,255,255,35); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,  QScrollBar::sub-page:vertical {
                background: none; height: 0; border: none;
            }
        """)
        outer.addWidget(scroll, stretch=1)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 10, 20, 16)
        layout.setSpacing(10)

        # Close button
        close_row = QHBoxLayout()
        close_row.addStretch()
        btn_close = QPushButton("\u2715")
        btn_close.setFixedSize(24, 24)
        btn_close.setStyleSheet(
            "QPushButton { color: #4c4e58; font-size: 14px; background: transparent; border: none; }"
            "QPushButton:hover { color: #cdd0df; }"
        )
        btn_close.clicked.connect(self.hide)
        close_row.addWidget(btn_close)
        layout.addLayout(close_row)

        # Name — single line, elides with "…" when too long
        self.name_label = ElidedLabel()
        self.name_label.setObjectName("heading")
        self.name_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.name_label)

        # Info labels — also elide so they never overflow
        self.type_label = ElidedLabel()
        self.type_label.setObjectName("subtext")
        self.type_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.type_label)

        self.size_label = ElidedLabel()
        self.size_label.setObjectName("subtext")
        self.size_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.size_label)

        self.dims_label = ElidedLabel()
        self.dims_label.setObjectName("subtext")
        self.dims_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.dims_label)

        # Multi-select label
        self.multi_label = QLabel()
        self.multi_label.setObjectName("heading")
        self.multi_label.setAlignment(Qt.AlignCenter)
        self.multi_label.hide()
        layout.addWidget(self.multi_label)

        # Rating
        rat_row = QHBoxLayout()
        rl = QLabel("Rating")
        rl.setStyleSheet("color: #4c4e58; font-size: 11px;")
        rat_row.addWidget(rl)
        self.stars = StarRating()
        self.stars.rating_changed.connect(self._on_rating)
        rat_row.addWidget(self.stars)
        rat_row.addStretch()
        layout.addLayout(rat_row)

        # Fav button
        self.fav_btn = QPushButton("\u2606 Favorite")
        self.fav_btn.setObjectName("favBtn")
        self.fav_btn.setCheckable(True)
        self.fav_btn.setFixedHeight(28)
        self.fav_btn.clicked.connect(self._toggle_fav)
        layout.addWidget(self.fav_btn)

        # Divider
        d1 = QFrame()
        d1.setFrameShape(QFrame.HLine)
        d1.setStyleSheet("color: #101118;")
        layout.addWidget(d1)

        # Maps
        self.maps_title = QLabel("TEXTURE MAPS")
        self.maps_title.setStyleSheet("color: #4c4e58; font-size: 9px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase;")
        self.maps_title.hide()
        layout.addWidget(self.maps_title)

        self.maps_container = QWidget()
        self.maps_layout = QVBoxLayout(self.maps_container)
        self.maps_layout.setContentsMargins(0, 0, 0, 0)
        self.maps_layout.setSpacing(0)
        self.maps_container.hide()
        layout.addWidget(self.maps_container)

        # Resolution section (materials only)
        self.res_container = QWidget()
        res_lay = QVBoxLayout(self.res_container)
        res_lay.setContentsMargins(0, 0, 0, 0)
        res_lay.setSpacing(6)

        res_title = QLabel("RESOLUTION")
        res_title.setStyleSheet("color: #4c4e58; font-size: 9px; font-weight: 700; letter-spacing: 1.5px;")
        res_lay.addWidget(res_title)

        self.res_combo = QComboBox()
        self.res_combo.setFixedHeight(28)
        self.res_combo.setStyleSheet("""
            QComboBox { background: rgba(255,255,255,8); color: #cdd0df; border: 1px solid #101118;
                        border-radius: 6px; padding: 2px 8px; font-size: 11.5px;
                        font-family: "DM Sans","Inter","Segoe UI",sans-serif; }
            QComboBox:focus { border-color: #002aff; }
            QComboBox::drop-down { border: none; width: 18px; }
            QComboBox::down-arrow { image: none; border-left: 4px solid transparent;
                                    border-right: 4px solid transparent; border-top: 4px solid #4c4e58; }
            QComboBox QAbstractItemView { background: #0f1018; color: #cdd0df;
                                          selection-background-color: #05091e;
                                          selection-color: #1a3fff;
                                          border: 1px solid #18192a; }
        """)
        res_lay.addWidget(self.res_combo)

        self.ds_half_btn = QPushButton("Copy as Half Resolution")
        self.ds_half_btn.setObjectName("dsHalfBtn")
        self.ds_half_btn.setFixedHeight(28)
        self.ds_half_btn.setEnabled(False)
        self.ds_half_btn.clicked.connect(self._do_downscale_half)
        res_lay.addWidget(self.ds_half_btn)
        self.res_combo.currentTextChanged.connect(self._update_half_btn)

        self.res_container.hide()
        layout.addWidget(self.res_container)

        # Tags
        d2 = QFrame()
        d2.setFrameShape(QFrame.HLine)
        d2.setStyleSheet("color: #101118;")
        layout.addWidget(d2)

        tag_header = QHBoxLayout()
        tl2 = QLabel("TAGS")
        tl2.setStyleSheet("color: #4c4e58; font-size: 9px; font-weight: 700; letter-spacing: 1.5px;")
        tag_header.addWidget(tl2)
        tag_header.addStretch()
        layout.addLayout(tag_header)

        self.tags_container = QWidget()
        self.tags_flow = FlowLayout(self.tags_container, margin=0, spacing=4)
        layout.addWidget(self.tags_container)

        # Notes
        nl = QLabel("NOTES")
        nl.setStyleSheet("color: #4c4e58; font-size: 9px; font-weight: 700; letter-spacing: 1.5px;")
        layout.addWidget(nl)
        self.notes = QTextEdit()
        self.notes.setMaximumHeight(70)
        self.notes.setPlaceholderText("Add notes…")
        self.notes.textChanged.connect(self._note_timer.start)
        layout.addWidget(self.notes)

        btn_save = QPushButton("Save Notes")
        btn_save.setObjectName("primaryBtn")
        btn_save.setFixedHeight(28)
        btn_save.clicked.connect(self._save_notes)
        layout.addWidget(btn_save)

        # ── Send to Houdini ──────────────────────────────────────────────────
        self._hou_row = QWidget()
        self._hou_row.hide()
        hou_col = QVBoxLayout(self._hou_row)
        hou_col.setContentsMargins(0, 6, 0, 0)
        hou_col.setSpacing(4)

        # Status indicator row
        hou_status_row = QHBoxLayout()
        hou_status_row.setContentsMargins(0, 0, 0, 0)
        hou_status_row.setSpacing(5)
        self._hou_dot   = QLabel("●")
        self._hou_dot.setFixedWidth(12)
        self._hou_status_lbl = QLabel("Houdini: checking…")
        self._hou_status_lbl.setStyleSheet(
            "color: #3a3d52; font-size: 10px;"
            "font-family: 'DM Sans','Inter','Segoe UI',sans-serif;"
        )
        hou_status_row.addWidget(self._hou_dot)
        hou_status_row.addWidget(self._hou_status_lbl)
        hou_status_row.addStretch()
        hou_col.addLayout(hou_status_row)

        # Send button
        self._btn_houdini = QPushButton("⬡  Send to Houdini")
        self._btn_houdini.setFixedHeight(30)
        self._btn_houdini.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_houdini.setToolTip(
            "Build a Redshift material network in the active Houdini session"
        )
        self._btn_houdini.setStyleSheet("""
            QPushButton {
                background: #0b1a2e;
                color: #4f8aff;
                border: 1px solid #1a2e4a;
                border-radius: 5px;
                font-size: 11px;
                font-family: "DM Sans", "Inter", "Segoe UI", sans-serif;
                padding: 0 10px;
                text-align: left;
            }
            QPushButton:hover  { background: #0e2448; border-color: #2a4a8f; color: #7aaaff; }
            QPushButton:pressed{ background: #071525; }
            QPushButton:disabled {
                background: #08101c;
                color: #1e2a3a;
                border-color: #0e161f;
            }
        """)
        self._btn_houdini.clicked.connect(self._send_to_houdini)
        hou_col.addWidget(self._btn_houdini)

        layout.addWidget(self._hou_row)
        self._update_hou_status_ui()   # set initial dot colour

        # Path
        self.path_label = QLabel()
        self.path_label.setStyleSheet("color: #4c4e58; font-size: 10px; padding-top: 8px; font-family: 'DM Mono','Consolas',monospace;")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        layout.addStretch()
        scroll.setWidget(content)

    def show_empty(self):
        """Show placeholder state when nothing is selected."""
        self._current_asset = None
        self._current_material = None
        self._multi_items = []
        self._last_tags_key = ""
        self._last_multi_key = ""

        self._preview_stack.setCurrentIndex(0)
        self.preview.setText("")
        self.preview.setStyleSheet("background-color: #09090f;")
        self.name_label.hide()
        self.type_label.hide()
        self.size_label.hide()
        self.dims_label.hide()
        self.multi_label.hide()
        self.maps_title.hide()
        self.maps_container.hide()
        self.res_container.hide()
        self.stars.hide()
        self.fav_btn.hide()
        self.notes.hide()
        self.tags_container.hide()
        self._btn_3d.hide()
        self._map_indicator.hide()
        self._hou_row.hide()
        self.path_label.setText("")

        # Clear tags flow
        while self.tags_flow.count():
            item = self.tags_flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _flush_notes(self):
        """If auto-save timer is pending, flush immediately before switching item."""
        if self._note_timer.isActive():
            self._note_timer.stop()
            self._save_notes()

    def show_asset(self, asset: ScannedAsset):
        self._flush_notes()
        self._current_asset = asset
        self._current_material = None
        self._multi_items = []
        self.multi_label.hide()

        # Reload meta from JSON
        asset.meta = read_asset_meta(asset.path)

        # Use 512x512 preview cache if available — avoids loading full 4K image
        if asset.preview_cache and asset.preview_cache.exists():
            self._set_preview_from_path(asset.preview_cache)
        else:
            self._set_preview_from_path(asset.path)
        self.name_label.setText(asset.filename)
        self.name_label.show()
        self.type_label.setText(f"{asset.asset_type.title()}  \u00b7  {asset.sub_type}" if asset.sub_type else asset.asset_type.title())
        self.type_label.show()

        try:
            sz = asset.path.stat().st_size / (1024 * 1024)
            self.size_label.setText(f"{sz:.1f} MB  \u00b7  {asset.path.suffix}")
        except:
            self.size_label.setText(asset.path.suffix)
        self.size_label.show()
        self.dims_label.hide()

        self.stars.set_rating(asset.meta.rating)
        self.stars.show()
        self.fav_btn.setChecked(asset.meta.favorite)
        self.fav_btn.setText("\u2605 Favorite" if asset.meta.favorite else "\u2606 Favorite")
        self.fav_btn.show()
        self._update_tags_display(asset.meta.tags)
        self.notes.setPlainText(asset.meta.notes)
        self.notes.show()
        self.path_label.setText(asset.rel_path)

        self._preview_stack.setCurrentIndex(0)
        self.maps_title.hide()
        self.maps_container.hide()
        self.res_container.hide()
        self._btn_3d.hide()
        self._map_indicator.hide()

        # Show Houdini row for texture assets — adds node to active Material Builder
        self._hou_row.setVisible(asset.asset_type == "texture")
        self._btn_houdini.setEnabled(True)
        self._btn_houdini.setText("→  Add to RS Builder")

    def show_material(self, mat: ScannedMaterial):
        self._flush_notes()
        self._current_material = mat
        self._current_asset = None
        self._multi_items = []
        self.multi_label.hide()

        # Always return to static preview when switching materials
        self._preview_stack.setCurrentIndex(0)

        mat.meta = read_material_meta(mat.path)

        # Prefer preview cache (512x512), fall back to original preview
        if mat.preview_cache and mat.preview_cache.exists():
            self._set_preview_from_path(mat.preview_cache)
        elif mat.preview_path and mat.preview_path.exists():
            self._set_preview_from_path(mat.preview_path)
        else:
            self.preview.setText("MATERIAL")

        self.name_label.setText(mat.name)
        self.name_label.show()
        parts = [mat.source.title()]
        if mat.meta.surface_type:
            parts.append(mat.meta.surface_type)
        parts.append(f"{len(mat.maps)} maps")
        self.type_label.setText("  \u00b7  ".join(parts))
        self.type_label.show()

        total = sum(m.path.stat().st_size for m in mat.maps if m.path.exists())
        self.size_label.setText(f"{total / (1024*1024):.1f} MB total")
        self.size_label.show()
        self.dims_label.hide()

        self.stars.set_rating(mat.meta.rating)
        self.stars.show()
        self.fav_btn.setChecked(mat.meta.favorite)
        self.fav_btn.setText("\u2605 Favorite" if mat.meta.favorite else "\u2606 Favorite")
        self.fav_btn.show()
        self._update_tags_display(mat.meta.tags)
        self.notes.setPlainText(mat.meta.notes)
        self.notes.show()
        self.path_label.setText(mat.rel_path)

        # Maps
        self._clear_maps()
        self.maps_title.show()
        self.maps_container.show()
        for a in mat.maps:
            self.maps_layout.addWidget(MapBadge(a.sub_type, a.filename))

        # Resolution
        self._update_resolution(mat)

        # 3D view button — material only, single selection
        self._btn_3d.setText("\u25b6  3D View")
        self._btn_3d.show()
        self._map_indicator.hide()

        # Send to Houdini \u2014 builds full OpenPBR network in /mat
        self._hou_row.show()
        self._btn_houdini.setEnabled(True)
        self._btn_houdini.setText("\u2b21  Build RS Material")

    def show_multi_selection(self, count: int, items: list | None = None):
        """Show multi-selection summary with shared tag editing."""
        self._flush_notes()
        self._current_asset = None
        self._current_material = None
        self._multi_items = items or []
        self._last_tags_key = ""  # multi items may differ even if tag key looks same

        # Build a key from the first 9 item paths to decide if preview needs rebuild
        _items = items or []
        multi_key = "|".join(
            str(obj.path) if hasattr(obj, "path") else getattr(obj, "name", "")
            for _, obj in _items[:9]
        )

        self._preview_stack.setCurrentIndex(0)
        if multi_key != self._last_multi_key:
            self._last_multi_key = multi_key
            grid_pix = self._build_multi_preview(_items, count)
            self.preview.setPixmap(grid_pix)
            self.preview.setStyleSheet("background-color: #09090f; padding: 0px;")
        self.name_label.hide()
        self.type_label.hide()
        self.size_label.hide()
        self.dims_label.hide()
        self.maps_title.hide()
        self.maps_container.hide()
        self._btn_3d.hide()
        self._map_indicator.hide()

        self.res_container.hide()
        self.multi_label.setText(f"{count} items selected")
        self.multi_label.show()

        # Show tags: common (all items) + partial (some items, mixed style)
        if items:
            common_tags = None
            union_tags: set[str] = set()
            for kind, obj in items:
                item_tags = set(obj.meta.tags) if obj.meta else set()
                union_tags |= item_tags
                if common_tags is None:
                    common_tags = item_tags
                else:
                    common_tags &= item_tags
            common_tags = common_tags or set()
            partial = sorted(union_tags - common_tags)
            self._update_tags_display(sorted(common_tags), partial)
        else:
            self._update_tags_display([])

        # Show common rating (0 if mixed), common favorite state
        if items:
            ratings = [obj.meta.rating for _, obj in items if obj.meta]
            common_rating = ratings[0] if ratings and all(r == ratings[0] for r in ratings) else 0
            self.stars.set_rating(common_rating)
            self.stars.show()

            all_fav = all(obj.meta.favorite for _, obj in items if obj.meta)
            self.fav_btn.setChecked(all_fav)
            self.fav_btn.setText("\u2605 Favorite" if all_fav else "\u2606 Favorite")
            self.fav_btn.show()
        else:
            self.stars.set_rating(0)
            self.fav_btn.setChecked(False)

        self.notes.setPlaceholderText("Notes (applies to all selected)...")
        self.notes.setPlainText("")
        self.notes.show()
        self.path_label.setText(f"{count} items")

    def _build_multi_preview(self, items: list, count: int) -> QPixmap:
        """Build a grid preview pixmap from multiple selected items.
        2–4 items → 2×2 grid, 5+ items → 3×3 grid.
        """
        cols = 3 if count >= 5 else 2
        PW, PH = 268, 180
        cell_w = PW // cols
        cell_h = PH // cols  # square cells

        result = QPixmap(PW, PH)
        result.fill(QColor("#07080d"))

        p = QPainter(result)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)

        _IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".tif", ".tiff", ".exr", ".hdr"}

        for i in range(cols * cols):
            col = i % cols
            row = i // cols
            x = col * cell_w
            y = row * cell_h

            # Cell background
            p.fillRect(x, y, cell_w, cell_h, QColor("#0a0b14"))

            if i < len(items):
                kind, obj = items[i]

                # Resolve preview path
                preview_path = None
                if kind == "material":
                    if obj.preview_cache and obj.preview_cache.exists():
                        preview_path = str(obj.preview_cache)
                    elif obj.preview_path and obj.preview_path.exists():
                        preview_path = str(obj.preview_path)
                else:
                    if obj.preview_cache and obj.preview_cache.exists():
                        preview_path = str(obj.preview_cache)
                    elif obj.path.suffix.lower() in _IMG_EXTS and obj.path.exists():
                        preview_path = str(obj.path)

                if preview_path:
                    # Check in-memory cache first (populated by thumbnail delegate)
                    pix = QPixmapCache.find(preview_path)
                    if pix is None or pix.isNull():
                        reader = QImageReader(preview_path)
                        orig = reader.size()
                        if orig.isValid():
                            reader.setScaledSize(
                                orig.scaled(cell_w, cell_h, Qt.KeepAspectRatioByExpanding)
                            )
                        img = reader.read()
                        pix = QPixmap.fromImage(img) if not img.isNull() else None
                    if pix and not pix.isNull():
                        sc = pix.scaled(cell_w, cell_h, Qt.KeepAspectRatioByExpanding,
                                        Qt.SmoothTransformation)
                        sx = max(0, (sc.width() - cell_w) // 2)
                        sy = max(0, (sc.height() - cell_h) // 2)
                        p.drawPixmap(x, y, sc, sx, sy, cell_w, cell_h)
                        self._draw_cell_separator(p, x, y, cell_w, cell_h, col, row)
                        continue

                # No image — draw type indicator
                p.setPen(QColor("#3a3d45"))
                p.setFont(QFont("Segoe UI", 8))
                label = obj.filename if hasattr(obj, "filename") else obj.name
                p.drawText(QRect(x, y, cell_w, cell_h), Qt.AlignCenter,
                           label[:12] + "…" if len(label) > 12 else label)
            else:
                # Empty cell
                p.setPen(QColor("#2a2d35"))
                p.setFont(QFont("Segoe UI", 8))
                p.drawText(QRect(x, y, cell_w, cell_h), Qt.AlignCenter, "empty")

            self._draw_cell_separator(p, x, y, cell_w, cell_h, col, row)

        p.end()
        return result

    @staticmethod
    def _draw_cell_separator(p: QPainter, x, y, w, h, col, row):
        """Draw 1px dark separator lines between cells."""
        p.setPen(QColor("#101118"))
        if col > 0:
            p.drawLine(x, y, x, y + h)
        if row > 0:
            p.drawLine(x, y, x + w, y)

    # ── HDR / EXR background loader ───────────────────────────────────────────

    class _HdrSignals(QObject):
        ready = Signal(object)   # QImage | None

    class _HdrDecodeJob(QRunnable):
        def __init__(self, path: str, w: int, h: int, signals):
            super().__init__()
            self.setAutoDelete(True)
            self._path    = path
            self._w       = w
            self._h       = h
            self._signals = signals

        def run(self):
            try:
                import numpy as np
                import imageio.v3 as iio
                from PIL import Image

                arr = iio.imread(self._path).astype(np.float32)
                if arr.ndim == 2:
                    arr = np.stack([arr] * 3, axis=-1)
                elif arr.shape[2] == 4:
                    arr = arr[:, :, :3]

                # Reinhard tone-mapping + gamma
                arr = arr / (1.0 + arr)
                arr = np.power(np.clip(arr, 0, 1), 1.0 / 2.2)
                arr = (arr * 255).astype(np.uint8)

                pil = Image.fromarray(arr, "RGB")
                pil.thumbnail((self._w, self._h), Image.Resampling.LANCZOS)

                data = pil.tobytes("raw", "RGB")
                qimg = QImage(data, pil.width, pil.height,
                              pil.width * 3, QImage.Format_RGB888)
                self._signals.ready.emit(qimg.copy())
            except Exception:
                self._signals.ready.emit(None)

    def _set_preview_from_path(self, path: Path):
        _STD_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".tif", ".tiff"}
        _HDR_EXTS = {".exr", ".hdr"}
        ext = path.suffix.lower()

        if not path.exists():
            self._show_ext_placeholder(ext)
            return

        if ext in _STD_EXTS:
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            sz = reader.size()
            if sz.isValid():
                reader.setScaledSize(sz.scaled(268, 180, Qt.KeepAspectRatio))
            img = reader.read()
            if not img.isNull():
                self.preview.setPixmap(QPixmap.fromImage(img))
                return
            self._show_ext_placeholder(ext)

        elif ext in _HDR_EXTS:
            # Show loading state immediately, decode in background
            self.preview.setText("Loading…")
            self.preview.setStyleSheet(
                "background-color: #07080d; color: #4c4e58; font-size: 13px; padding: 4px;"
            )
            self.preview.setPixmap(QPixmap())

            sig = self._HdrSignals()
            sig.ready.connect(lambda img, p=path, e=ext: self._on_hdr_ready(img, p, e))
            job = self._HdrDecodeJob(str(path), 268, 180, sig)
            # Keep reference so signals object isn't GC'd before job finishes
            self._hdr_sig_ref = sig
            QThreadPool.globalInstance().start(job)

        else:
            self._show_ext_placeholder(ext)

    def _on_hdr_ready(self, img, path: Path, ext: str):
        """Called on main thread when HDR decode finishes."""
        # Only apply if the panel still shows the same file
        cur = self._current_asset
        if cur is None or cur.path != path:
            return
        if img is not None and not img.isNull():
            self.preview.setText("")
            self.preview.setStyleSheet("background-color: #07080d; padding: 4px;")
            self.preview.setPixmap(QPixmap.fromImage(img))
        else:
            self._show_ext_placeholder(ext)

    def _show_ext_placeholder(self, ext: str):
        self.preview.setPixmap(QPixmap())
        self.preview.setText(ext.upper())
        self.preview.setStyleSheet(
            "background-color: #07080d; border-radius: 10px; "
            "color: #4c4e58; font-size: 20px; font-weight: bold; padding: 4px;"
        )

    def _clear_maps(self):
        while self.maps_layout.count():
            c = self.maps_layout.takeAt(0)
            if c.widget():
                c.widget().deleteLater()

    def _on_rating(self, r):
        if self._multi_items:
            for kind, obj in self._multi_items:
                obj.meta.rating = r
                if kind == "material":
                    write_material_meta(obj.path, obj.meta)
                else:
                    write_asset_meta(obj.path, obj.meta)
        elif self._current_asset:
            self._current_asset.meta.rating = r
            write_asset_meta(self._current_asset.path, self._current_asset.meta)
        elif self._current_material:
            self._current_material.meta.rating = r
            write_material_meta(self._current_material.path, self._current_material.meta)

    def _toggle_fav(self):
        fav = self.fav_btn.isChecked()
        self.fav_btn.setText("\u2605 Favorite" if fav else "\u2606 Favorite")
        if self._multi_items:
            for kind, obj in self._multi_items:
                obj.meta.favorite = fav
                if kind == "material":
                    write_material_meta(obj.path, obj.meta)
                else:
                    write_asset_meta(obj.path, obj.meta)
        elif self._current_asset:
            self._current_asset.meta.favorite = fav
            write_asset_meta(self._current_asset.path, self._current_asset.meta)
        elif self._current_material:
            self._current_material.meta.favorite = fav
            write_material_meta(self._current_material.path, self._current_material.meta)

    def _apply_tag(self, tag: str):
        """Add a tag by name to all currently targeted items."""
        tag = tag.strip()
        if not tag:
            return
        targets = self._get_tag_targets()
        if not targets:
            return

        for kind, obj in targets:
            if tag not in obj.meta.tags:
                obj.meta.tags.append(tag)
            if kind == "material":
                write_material_meta(obj.path, obj.meta)
            else:
                write_asset_meta(obj.path, obj.meta)

        self._refresh_tag_display()
        self.refresh_requested.emit()

    def _get_tag_targets(self) -> list:
        """Return list of (kind, obj) for current selection (single or multi)."""
        if self._multi_items:
            return self._multi_items
        if self._current_asset:
            return [("asset", self._current_asset)]
        if self._current_material:
            return [("material", self._current_material)]
        return []

    def _update_tags_display(self, tags: list[str], partial_tags: list[str] | None = None):
        """Update the tags area: colored chips + inline + Add button at the end.
        tags: common tags (on ALL selected items).
        partial_tags: tags present on SOME but not all items — shown with mixed style.
        """
        # Skip full widget recreation if content hasn't changed
        tags_key = "\x00".join(sorted(tags)) + "\x01" + "\x00".join(sorted(partial_tags or []))
        if tags_key == self._last_tags_key:
            self.tags_container.show()
            return
        self._last_tags_key = tags_key

        while self.tags_flow.count():
            item = self.tags_flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.tags_container.show()

        for t in tags:
            info = self._tag_registry.get(t)
            color = info.color if info and info.color else "#002aff"
            chip = TagLabel(t, color)
            chip.remove_requested.connect(self._remove_tag)
            chip.setContextMenuPolicy(Qt.CustomContextMenu)
            chip.customContextMenuRequested.connect(
                lambda pos, tag=t: self._tag_chip_context(tag))
            self.tags_flow.addWidget(chip)

        for t in (partial_tags or []):
            info = self._tag_registry.get(t)
            color = info.color if info and info.color else "#002aff"
            chip = TagLabel(t, color, partial=True)
            chip.remove_requested.connect(self._remove_tag)
            chip.setContextMenuPolicy(Qt.CustomContextMenu)
            chip.customContextMenuRequested.connect(
                lambda pos, tag=t: self._tag_chip_context(tag))
            chip.setToolTip("Mixed — not all selected items have this tag")
            self.tags_flow.addWidget(chip)

        # Inline TagAddWidget — exclude both common + partial tags from overlay
        all_current = list(tags) + list(partial_tags or [])
        add_widget = TagAddWidget(self._tag_registry, current_tags=all_current)
        add_widget.tag_requested.connect(self._apply_tag)
        self.tags_flow.addWidget(add_widget)

    def _tag_chip_context(self, tag_name: str):
        """Show context menu for a tag chip."""
        menu = QMenu(self)

        act_remove = QAction("Remove Tag", self)
        act_remove.triggered.connect(lambda: self._remove_tag(tag_name))
        menu.addAction(act_remove)

        # "Set as Tag Head" only meaningful for single selection
        if not self._multi_items:
            menu.addSeparator()
            act_head = QAction("Set as Tag Head", self)
            act_head.triggered.connect(lambda: self._set_as_tag_head(tag_name))
            menu.addAction(act_head)

        menu.exec(self.cursor().pos())

    def _remove_tag(self, tag_name: str):
        """Remove a tag from all currently targeted items."""
        targets = self._get_tag_targets()
        for kind, obj in targets:
            if tag_name in obj.meta.tags:
                obj.meta.tags.remove(tag_name)
            if kind == "material":
                write_material_meta(obj.path, obj.meta)
            else:
                write_asset_meta(obj.path, obj.meta)

        self._refresh_tag_display()
        self.refresh_requested.emit()

    def _refresh_tag_display(self):
        """Recompute and redraw the tag chips from current selection state."""
        if self._multi_items:
            common_tags = None
            union_tags: set[str] = set()
            for kind, obj in self._multi_items:
                item_tags = set(obj.meta.tags)
                union_tags |= item_tags
                common_tags = item_tags if common_tags is None else common_tags & item_tags
            common_tags = common_tags or set()
            partial = sorted(union_tags - common_tags)
            self._update_tags_display(sorted(common_tags), partial)
        elif self._current_asset:
            self._update_tags_display(self._current_asset.meta.tags)
        elif self._current_material:
            self._update_tags_display(self._current_material.meta.tags)

    def _set_as_tag_head(self, tag_name: str):
        """Promote the current asset/material to be the tag head."""
        head_path = None
        if self._current_material:
            head_path = self._current_material.preview_path
        elif self._current_asset:
            head_path = self._current_asset.path

        if head_path and self._backpack_root:
            self.tag_head_changed.emit(tag_name, head_path)

    def _toggle_3d_view(self):
        """Toggle between static preview (page 0) and 3D viewer (page 1)."""
        if self._preview_stack.currentIndex() == 1:
            self._preview_stack.setMaximumHeight(200)
            self._preview_stack.setMinimumHeight(200)
            self._preview_stack.setCurrentIndex(0)
            self._btn_3d.setText("\u25b6  3D View")
            self._map_indicator.hide()
            return

        mat = self._current_material
        if not mat:
            return

        # Lazy-create the 3D viewer on first use
        if self._viewer_3d is None:
            from backpack.ui.viewer_3d import MaterialViewer3D
            self._viewer_3d = MaterialViewer3D(self._preview_stack)
            self._preview_stack.addWidget(self._viewer_3d)  # index 1

        self._viewer_3d.load_material(mat)

        # Expand the preview stack to give the 3D view proper space
        self._preview_stack.setMinimumHeight(260)
        self._preview_stack.setMaximumHeight(300)
        self._preview_stack.setCurrentIndex(1)
        self._btn_3d.setText("\u25a0  Preview")

        # Map indicator after GL init (one frame needed)
        QTimer.singleShot(300, self._update_map_indicator)

    def _update_map_indicator(self):
        if self._viewer_3d is None:
            return
        roles = self._viewer_3d.loaded_roles()
        labels = {"albedo": "A", "normal": "N", "roughness": "R", "specular": "S"}
        parts = []
        for role, lbl in labels.items():
            if role in roles:
                parts.append(f'<span style="color:#002aff;font-weight:700">{lbl}</span>')
            else:
                parts.append(f'<span style="color:#2a2d35">{lbl}</span>')
        self._map_indicator.setText("  ".join(parts))
        self._map_indicator.setTextFormat(Qt.TextFormat.RichText)
        self._map_indicator.show()

    # ── Keyboard shortcuts ────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        """
        1–5 : set star rating
        F   : toggle Favorite
        O   : open file/folder in Explorer
        """
        key = event.key()
        if Qt.Key_1 <= key <= Qt.Key_5:
            rating = key - Qt.Key_0
            self.stars.set_rating(rating)
            self._on_rating(rating)
            event.accept()
            return
        if key == Qt.Key_F and not event.modifiers():
            self._toggle_fav()
            event.accept()
            return
        if key == Qt.Key_O and not event.modifiers():
            self._open_current_in_explorer()
            event.accept()
            return
        super().keyPressEvent(event)

    def _open_current_in_explorer(self):
        path = None
        if self._current_asset:
            path = self._current_asset.path
        elif self._current_material:
            path = self._current_material.path
        if path and path.exists():
            if path.is_dir():
                subprocess.Popen(["explorer", str(path)], creationflags=0x08000000)
            else:
                subprocess.Popen(["explorer", "/select,", str(path)],
                                 creationflags=0x08000000)

    def _save_notes(self):
        text = self.notes.toPlainText()
        if self._multi_items:
            for kind, obj in self._multi_items:
                obj.meta.notes = text
                if kind == "material":
                    write_material_meta(obj.path, obj.meta)
                else:
                    write_asset_meta(obj.path, obj.meta)
        elif self._current_asset:
            self._current_asset.meta.notes = text
            write_asset_meta(self._current_asset.path, self._current_asset.meta)
        elif self._current_material:
            self._current_material.meta.notes = text
            write_material_meta(self._current_material.path, self._current_material.meta)

    # ── Houdini live status ───────────────────────────────────────────────────

    def _poll_houdini_status(self) -> None:
        """Background-safe poll — check if Houdini is listening."""
        from backpack.ui.houdini_bridge import is_houdini_available
        online = is_houdini_available()
        if online != self._hou_online:
            self._hou_online = online
            self._update_hou_status_ui()

    def _update_hou_status_ui(self) -> None:
        if self._hou_online:
            self._hou_dot.setStyleSheet("color: #22c55e; font-size: 9px;")   # green
            self._hou_status_lbl.setText("Houdini: connected")
            self._hou_status_lbl.setStyleSheet(
                "color: #22c55e; font-size: 10px;"
                "font-family: 'DM Sans','Inter','Segoe UI',sans-serif;"
            )
            self._btn_houdini.setEnabled(True)
        else:
            self._hou_dot.setStyleSheet("color: #3a3d52; font-size: 9px;")   # grey
            self._hou_status_lbl.setText("Houdini: offline")
            self._hou_status_lbl.setStyleSheet(
                "color: #3a3d52; font-size: 10px;"
                "font-family: 'DM Sans','Inter','Segoe UI',sans-serif;"
            )
            self._btn_houdini.setEnabled(False)

    # ── Send to Houdini ───────────────────────────────────────────────────────

    def _send_to_houdini(self) -> None:
        """Dispatch to image-mode or full-material-mode depending on selection."""
        if self._current_material:
            self._send_material_to_houdini()
        elif self._current_asset:
            self._send_image_to_houdini()

    def _send_material_to_houdini(self) -> None:
        """Build a full OpenPBR RS material network in Houdini /mat."""
        from backpack.ui.houdini_bridge import send_material
        mat  = self._current_material
        maps : dict[str, str] = {}
        for a in mat.maps:
            st = a.sub_type or "albedo"
            if st not in maps:
                maps[st] = str(a.path.resolve())
        preview = str(mat.preview_path.resolve()) if (mat.preview_path and mat.preview_path.exists()) else ""

        self._btn_houdini.setEnabled(False)
        self._btn_houdini.setText("↑  Sending…")
        try:
            resp  = send_material(mat.name, maps, preview)
            node  = resp.get("node", "")
            label = ("✓  Building" + (f"  ·  {node}" if node else ""))[:38]
            self._btn_houdini.setText(label)
            QTimer.singleShot(3000, self._reset_hou_btn)
        except OSError:
            self._hou_online = False
            self._update_hou_status_ui()
            self._show_houdini_offline_msg()
            self._reset_hou_btn()

    def _send_image_to_houdini(self) -> None:
        """Add a single rsTexture node to the active RS Material Builder."""
        from backpack.ui.houdini_bridge import send_image
        a  = self._current_asset
        st = a.sub_type or "albedo"

        self._btn_houdini.setEnabled(False)
        self._btn_houdini.setText("↑  Sending…")
        try:
            resp  = send_image(str(a.path.resolve()), st, a.filename)
            msg   = resp.get("message", "")
            label = ("✓  " + msg)[:38] if msg else "✓  Added to Builder"
            self._btn_houdini.setText(label)
            QTimer.singleShot(3000, self._reset_hou_btn)
        except OSError:
            self._hou_online = False
            self._update_hou_status_ui()
            self._show_houdini_offline_msg()
            self._reset_hou_btn()

    def _show_houdini_offline_msg(self) -> None:
        QMessageBox.warning(
            self,
            "Houdini Not Found",
            "Backpack for Houdini is not running.\n\n"
            "In Houdini, open the Backpack shelf and press  Start Backpack.",
        )

    def _reset_hou_btn(self) -> None:
        if self._current_material:
            self._btn_houdini.setText("⬡  Build RS Material")
        else:
            self._btn_houdini.setText("→  Add to RS Builder")
        self._btn_houdini.setEnabled(self._hou_online)

    def _update_resolution(self, mat: ScannedMaterial):
        """Populate the resolution combo and update the half-resolution button."""
        resolutions = get_available_resolutions(mat.path)
        # Block signal while repopulating to avoid premature _update_half_btn calls
        self.res_combo.blockSignals(True)
        self.res_combo.clear()
        if resolutions:
            for r in resolutions:
                self.res_combo.addItem(r)
            self.res_combo.setCurrentIndex(len(resolutions) - 1)  # select highest
        self.res_combo.blockSignals(False)

        if resolutions:
            self.res_container.show()
            self._update_half_btn(self.res_combo.currentText())
        else:
            self.res_container.hide()

    def _update_half_btn(self, current_res: str):
        """Update the half-resolution button label and enabled state."""
        target = half_resolution(current_res)
        if target:
            self.ds_half_btn.setText(f"Copy as {target}  ({current_res} → {target})")
            self.ds_half_btn.setEnabled(True)
        else:
            self.ds_half_btn.setText("Copy as Half Resolution")
            self.ds_half_btn.setEnabled(False)

    def _do_downscale_half(self):
        """Downscale the current material to half its selected resolution."""
        mat = self._current_material
        if not mat:
            return
        current_res = self.res_combo.currentText()
        target = half_resolution(current_res)
        if not target:
            return
        self._do_downscale(target)

    def _do_downscale(self, target_res: str):
        """Downscale the current material to target resolution."""
        mat = self._current_material
        if not mat:
            return

        created, errors = downscale_material(mat.path, target_res)

        if errors:
            QMessageBox.warning(self, "Downscale",
                                f"Created {created} files.\nErrors:\n" + "\n".join(errors))
        elif created > 0:
            QMessageBox.information(self, "Downscale",
                                    f"Created {created} {target_res} texture(s).")
        else:
            QMessageBox.information(self, "Downscale",
                                    f"{target_res} textures already exist or source is too small.")

        # Refresh to pick up new files
        self.refresh_requested.emit()
