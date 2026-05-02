"""Asset browser - grid view reading from scanned filesystem data.

Supports Ctrl+Wheel zoom.
"""

import subprocess
import time
from pathlib import Path

from PySide6.QtWidgets import (
    QListView, QMenu, QAbstractItemView, QApplication, QWidget,
    QPushButton, QHBoxLayout, QLabel,
)
from PySide6.QtCore import Qt, Signal, QSize, QMimeData, QUrl, QPoint, QTimer, QRect, QRectF
from PySide6.QtGui import (
    QStandardItemModel, QStandardItem, QAction, QKeyEvent, QDrag,
    QPixmap, QPixmapCache, QPainter, QPainterPath, QColor, QFont,
    QLinearGradient, QImageReader,
)

from backpack.core.scanner import ScannedMaterial, ScannedAsset
from backpack.core.downscale import detect_resolution_tag
from backpack.ui.delegates.thumbnail_delegate import ThumbnailDelegate, SPACER_ROLE


# ── Hover preview popup ───────────────────────────────────────────────────────

_PREVIEW_ANIM_DURATION = 0.18   # seconds  (pop-in speed)
_PREVIEW_ANIM_SCALE_FROM = 0.88  # start at 88% → 100%

class HoverPreview(QWidget):
    """Frameless floating preview shown after 0.5 s hover on a thumbnail card.

    Appears beside the card (right-preferred), never under the cursor.
    Plays a quick ease-out pop-in animation on show.
    """

    _W      = 520    # preview width  (px)
    _H      = 520    # image area height
    _NAME_H = 38     # filename bar height
    _R      = 14     # corner radius

    def __init__(self):
        super().__init__(
            None,
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self._W, self._H + self._NAME_H)
        self._pix:  QPixmap | None = None
        self._name: str = ""

        # Pop-in animation
        self._anim_start: float = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(14)   # ~70 fps
        self._anim_timer.timeout.connect(self.update)

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, path: str, name: str) -> None:
        """Load image from path (QPixmapCache → QImageReader fallback)."""
        self._name = name

        pix = QPixmapCache.find(path) if path else None

        if pix is None or pix.isNull():
            # Sync read — only reached when thumbnail not yet cached (rare after 0.5 s)
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            orig = reader.size()
            if orig.isValid():
                reader.setScaledSize(
                    orig.scaled(self._W, self._H, Qt.KeepAspectRatio)
                )
            img = reader.read()
            pix = QPixmap.fromImage(img) if not img.isNull() else None

        self._pix = pix
        self.update()

    def show_near(self, anchor: QRect, screen_geo: QRect) -> None:
        """Position next to anchor (global coords) and show with pop-in animation."""
        W, H = self.width(), self.height()

        # Prefer right of card; fall back to left
        x = anchor.right() + 12
        if x + W > screen_geo.right():
            x = anchor.left() - W - 12

        # Align top with card, clamp to screen
        y = anchor.top()
        if y + H > screen_geo.bottom():
            y = screen_geo.bottom() - H
        y = max(screen_geo.top(), y)

        self.move(x, y)

        # Kick off pop-in
        self._anim_start = time.perf_counter()
        self._anim_timer.start()

        self.show()
        self.raise_()

    def hideEvent(self, event):
        self._anim_timer.stop()
        super().hideEvent(event)

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        # Pop-in animation: ease-out quad, opacity 0→1, scale 88%→100%
        elapsed = time.perf_counter() - self._anim_start
        t = min(elapsed / _PREVIEW_ANIM_DURATION, 1.0)
        eased = 1.0 - (1.0 - t) ** 2   # ease-out quadratic
        if t >= 1.0:
            self._anim_timer.stop()
        p.setOpacity(eased)
        if eased < 1.0:
            scale = _PREVIEW_ANIM_SCALE_FROM + (1.0 - _PREVIEW_ANIM_SCALE_FROM) * eased
            cx = self.width()  / 2.0
            cy = self.height() / 2.0
            p.translate(cx, cy)
            p.scale(scale, scale)
            p.translate(-cx, -cy)

        W     = self.width()
        H     = self.height()
        img_h = H - self._NAME_H

        # Clip to rounded rect
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, W, H), self._R, self._R)
        p.setClipPath(clip)

        # Image (or dark placeholder)
        if self._pix and not self._pix.isNull():
            sc = self._pix.scaled(W, img_h,
                                  Qt.KeepAspectRatioByExpanding,
                                  Qt.SmoothTransformation)
            xo = max(0, (sc.width()  - W) // 2)
            yo = max(0, (sc.height() - img_h) // 2)
            p.drawPixmap(0, 0, sc, xo, yo, W, img_h)
        else:
            p.fillRect(0, 0, W, img_h, QColor("#1a1c23"))
            p.setFont(QFont("Segoe UI", 13))
            p.setPen(QColor("#3a3d45"))
            p.drawText(QRectF(0, 0, W, img_h), Qt.AlignCenter, "No preview")

        # Gradient vignette at bottom of image
        grad = QLinearGradient(0, img_h - 50, 0, img_h)
        grad.setColorAt(0, QColor(15, 17, 22, 0))
        grad.setColorAt(1, QColor(15, 17, 22, 160))
        p.fillRect(0, img_h - 50, W, 50, grad)

        # Filename bar
        p.fillRect(0, img_h, W, self._NAME_H, QColor(13, 15, 20, 250))
        p.setFont(QFont("Segoe UI", 11, QFont.DemiBold))
        p.setPen(QColor("#e8eaf0"))
        name = p.fontMetrics().elidedText(self._name, Qt.ElideRight, W - 24)
        p.drawText(QRectF(14, img_h, W - 28, self._NAME_H),
                   Qt.AlignVCenter | Qt.AlignLeft, name)

        p.end()


class AssetSubToolbar(QWidget):
    """Compact toolbar above the asset grid: result count, sort, view toggle, quick filters.

    Signals
    -------
    sort_changed(str)          "name" | "size"
    view_changed(str)          "grid" | "list"
    quick_filter_changed(str)  "all" | "4k" | "fav"
    """

    sort_changed          = Signal(str)
    view_changed          = Signal(str)
    quick_filter_changed  = Signal(str)

    _BTN_SS = """
        QPushButton {{
            background: transparent;
            color: {fg};
            border: none;
            border-radius: 4px;
            padding: 3px 9px;
            font-size: 11px;
            font-family: "DM Sans", "Inter", "Segoe UI", sans-serif;
            min-height: 22px;
        }}
        QPushButton:hover {{ background: rgba(255,255,255,10); color: #cdd0df; }}
        QPushButton:checked {{
            background: rgba(0,42,255,36);
            color: #4060ff;
            border: 1px solid rgba(0,42,255,60);
        }}
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setObjectName("assetSubToolbar")

        self._active_sort   = "name"
        self._active_view   = "grid"
        self._active_filter = "all"

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 8, 0)
        row.setSpacing(4)

        # Count label
        self._count_lbl = QLabel("0 items")
        self._count_lbl.setStyleSheet(
            "color: #4a4f66; font-size: 11px;"
            "font-family: 'DM Sans','Inter','Segoe UI',sans-serif;"
        )
        row.addWidget(self._count_lbl)
        row.addStretch()

        # Sort buttons
        _dim = "#4a4f66"
        self._btn_sort_name = QPushButton("Name")
        self._btn_sort_name.setCheckable(True)
        self._btn_sort_name.setChecked(True)
        self._btn_sort_name.setStyleSheet(self._BTN_SS.format(fg=_dim))

        self._btn_sort_size = QPushButton("Size")
        self._btn_sort_size.setCheckable(True)
        self._btn_sort_size.setStyleSheet(self._BTN_SS.format(fg=_dim))

        self._btn_sort_name.clicked.connect(lambda: self._set_sort("name"))
        self._btn_sort_size.clicked.connect(lambda: self._set_sort("size"))

        row.addWidget(self._btn_sort_name)
        row.addWidget(self._btn_sort_size)

        # Separator
        sep = QLabel("|")
        sep.setStyleSheet("color: #2a2d3a; font-size: 13px;")
        row.addWidget(sep)

        # View toggle buttons
        self._btn_grid = QPushButton("⊞")
        self._btn_grid.setCheckable(True)
        self._btn_grid.setChecked(True)
        self._btn_grid.setFixedWidth(28)
        self._btn_grid.setStyleSheet(self._BTN_SS.format(fg=_dim))

        self._btn_list = QPushButton("☰")
        self._btn_list.setCheckable(True)
        self._btn_list.setFixedWidth(28)
        self._btn_list.setStyleSheet(self._BTN_SS.format(fg=_dim))

        self._btn_grid.clicked.connect(lambda: self._set_view("grid"))
        self._btn_list.clicked.connect(lambda: self._set_view("list"))

        row.addWidget(self._btn_grid)
        row.addWidget(self._btn_list)

        # Separator
        sep2 = QLabel("|")
        sep2.setStyleSheet("color: #2a2d3a; font-size: 13px;")
        row.addWidget(sep2)

        # Quick-filter chips
        self._btn_all = QPushButton("All")
        self._btn_all.setCheckable(True)
        self._btn_all.setChecked(True)
        self._btn_all.setStyleSheet(self._BTN_SS.format(fg=_dim))

        self._btn_4k = QPushButton("4K")
        self._btn_4k.setCheckable(True)
        self._btn_4k.setStyleSheet(self._BTN_SS.format(fg=_dim))

        self._btn_fav = QPushButton("★ Fav")
        self._btn_fav.setCheckable(True)
        self._btn_fav.setStyleSheet(self._BTN_SS.format(fg=_dim))

        self._btn_all.clicked.connect(lambda: self._set_filter("all"))
        self._btn_4k.clicked.connect(lambda: self._set_filter("4k"))
        self._btn_fav.clicked.connect(lambda: self._set_filter("fav"))

        row.addWidget(self._btn_all)
        row.addWidget(self._btn_4k)
        row.addWidget(self._btn_fav)

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_count(self, n: int) -> None:
        self._count_lbl.setText(f"{n} item{'s' if n != 1 else ''}")

    def active_sort(self) -> str:
        return self._active_sort

    def active_view(self) -> str:
        return self._active_view

    def active_filter(self) -> str:
        return self._active_filter

    # ── Internal ───────────────────────────────────────────────────────────────

    def _set_sort(self, mode: str) -> None:
        self._active_sort = mode
        self._btn_sort_name.setChecked(mode == "name")
        self._btn_sort_size.setChecked(mode == "size")
        self.sort_changed.emit(mode)

    def _set_view(self, mode: str) -> None:
        self._active_view = mode
        self._btn_grid.setChecked(mode == "grid")
        self._btn_list.setChecked(mode == "list")
        self.view_changed.emit(mode)

    def _set_filter(self, mode: str) -> None:
        self._active_filter = mode
        self._btn_all.setChecked(mode == "all")
        self._btn_4k.setChecked(mode == "4k")
        self._btn_fav.setChecked(mode == "fav")
        self.quick_filter_changed.emit(mode)


class AssetBrowser(QListView):
    asset_selected = Signal(object)          # ScannedAsset
    asset_double_clicked = Signal(object)    # ScannedAsset
    material_selected = Signal(object)       # ScannedMaterial
    material_double_clicked = Signal(object) # ScannedMaterial
    selection_changed = Signal(int, list)    # (count, list of (kind, item))
    delete_requested = Signal(list)          # list of (kind, item)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("assetGrid")

        self._card_size = 200
        self._expanded_materials: set[str] = set()  # rel_paths of expanded materials
        self._current_materials: list[ScannedMaterial] = []
        self._current_assets: list[ScannedAsset] = []
        self._drag_start_pos: QPoint | None = None
        self._last_sel_ids: frozenset = frozenset()  # identity cache for selection
        self._last_cols: int = 0   # track column count for resize-triggered rebuild

        # Hover preview
        self._hover_preview  = HoverPreview()
        self._hover_timer    = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(500)
        self._hover_timer.timeout.connect(self._show_hover_preview)
        self._hover_index    = None   # QModelIndex currently under cursor

        # Init timer BEFORE setModel — setModel triggers selectionChanged() immediately
        self._sel_timer = QTimer(self)
        self._sel_timer.setSingleShot(True)
        self._sel_timer.setInterval(50)
        self._sel_timer.timeout.connect(self._emit_selection)

        self._model = QStandardItemModel(self)
        self.setModel(self._model)

        self.delegate = ThumbnailDelegate(self)
        self.setItemDelegate(self.delegate)

        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setSpacing(4)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setDragEnabled(False)  # We handle drag manually
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

        self._update_grid_size()

        # NOTE: selection is handled by selectionChanged() override (covers click + rubber-band)
        self.doubleClicked.connect(self._on_dblclick)
        self.customContextMenuRequested.connect(self._context_menu)

        # Hide preview on scroll
        self.verticalScrollBar().valueChanged.connect(self._hide_preview)
        self.horizontalScrollBar().valueChanged.connect(self._hide_preview)

    def set_tag_registry(self, registry: dict):
        """Pass tag registry to delegate for tag color lookup."""
        self.delegate.tag_registry = registry

    def set_card_size(self, size: int):
        self._card_size = max(120, min(400, size))
        self.delegate.card_width = self._card_size
        self.delegate.card_height = int(self._card_size * 1.15)
        self._update_grid_size()
        self.viewport().update()

    def _update_grid_size(self):
        w = self._card_size + 4
        h = int(self._card_size * 1.15) + 4
        self.setGridSize(QSize(w, h))

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            step = 20 if delta > 0 else -20
            self.set_card_size(self._card_size + step)
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Recalculate spacer padding when the column count changes
        new_cols = self._col_count()
        if new_cols != self._last_cols and (self._current_materials or self._current_assets):
            self._rebuild_model(animate=False)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Drag threshold
        if (event.buttons() & Qt.LeftButton) and self._drag_start_pos:
            dist = (event.pos() - self._drag_start_pos).manhattanLength()
            if dist >= QApplication.startDragDistance():
                self._start_drag()
                self._drag_start_pos = None
                return

        # Hover preview tracking — skip invisible spacer cells
        idx = self.indexAt(event.pos())
        if idx.isValid() and not idx.data(SPACER_ROLE):
            if idx != self._hover_index:
                self._hover_index = idx
                self._hover_timer.start()
                self._hover_preview.hide()
        else:
            self._hover_index = None
            self._hover_timer.stop()
            self._hover_preview.hide()

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self._hover_timer.stop()
        self._hover_preview.hide()
        self._hover_index = None
        super().leaveEvent(event)

    def _show_hover_preview(self):
        """Called after 0.5 s hover — loads image and shows floating preview."""
        idx = self._hover_index
        if idx is None or not idx.isValid():
            return

        path = idx.data(Qt.UserRole + 1)   # thumbnail / image path
        name = idx.data(Qt.DisplayRole) or ""
        if not path:
            return

        # Anchor rect in global screen coordinates
        vp_rect = self.visualRect(idx)
        anchor  = QRect(
            self.viewport().mapToGlobal(vp_rect.topLeft()),
            self.viewport().mapToGlobal(vp_rect.bottomRight()),
        )

        # Screen available geometry (multi-monitor aware)
        screen = QApplication.screenAt(anchor.center()) or QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()

        self._hover_preview.load(path, name)
        self._hover_preview.show_near(anchor, screen_geo)

    def _start_drag(self):
        """Initiate a file drag with selected items for external drop targets."""
        items = self.get_selected_items()
        if not items:
            return

        urls = []
        text_paths = []
        for kind, obj in items:
            if kind == "material":
                # For materials, add the folder path
                if obj.path.exists():
                    urls.append(QUrl.fromLocalFile(str(obj.path)))
                    text_paths.append(str(obj.path))
            elif kind == "asset":
                if obj.path.exists():
                    urls.append(QUrl.fromLocalFile(str(obj.path)))
                    text_paths.append(str(obj.path))

        if not urls:
            return

        mime = QMimeData()
        mime.setUrls(urls)
        mime.setText("\n".join(text_paths))
        # Mark as internal drag so the import overlay ignores it
        mime.setData("application/x-backpack-internal", b"1")

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Delete:
            self._delete_selected()
        else:
            super().keyPressEvent(event)

    def _hide_preview(self):
        self._hover_timer.stop()
        self._hover_preview.hide()
        self._hover_index = None

    def display_items(self, materials: list[ScannedMaterial], assets: list[ScannedAsset],
                      animate: bool = False):
        """Populate the grid with scanned materials and loose assets.

        animate=True plays the card entrance animation — only set this on a
        genuine folder load, NOT on in-place metadata refreshes (tag edits,
        rating changes, filter toggles) to avoid re-triggering the pop-in.
        """
        self._hide_preview()
        # Drop queued decode jobs from the previous folder so worker threads
        # focus immediately on thumbnails for the new content.
        if animate:
            self.delegate.cancel_pending()
        self._current_materials = materials
        self._current_assets = assets
        self._rebuild_model(animate=animate)

    @staticmethod
    def _res_tag(name: str) -> str:
        """Return resolution tag from filename (e.g. '4K'), or '' if none."""
        return detect_resolution_tag(name) or ""

    @staticmethod
    def _mat_res(mat: ScannedMaterial) -> str:
        """Highest resolution found across a material's maps."""
        _order = {"8K": 4, "4K": 3, "2K": 2, "1K": 1}
        best = ""
        for a in mat.maps:
            tag = detect_resolution_tag(a.filename)
            if tag and _order.get(tag, 0) > _order.get(best, 0):
                best = tag
        return best

    def _col_count(self) -> int:
        """How many card columns fit in the current viewport width."""
        gw = self.gridSize().width()
        return max(1, self.viewport().width() // gw) if gw > 0 else 1

    def _rebuild_model(self, animate: bool = False):
        """Rebuild the model from current materials/assets, respecting expanded state.

        When a material is expanded its child items are padded with invisible
        spacer items so that they always start and end on a row boundary —
        children never share a row with sibling materials.

        Uses batch insertion (appendRows) to emit a single layoutChanged instead of
        one rowsInserted signal per item — critical for large libraries.
        """
        self._last_sel_ids = frozenset()  # row indices will change

        cols = self._col_count()
        self._last_cols = cols

        # Build all QStandardItems first (pure Python, no Qt signals)
        all_rows: list[QStandardItem] = []
        cur = 0   # current grid position (0-based, wraps at cols)

        _IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".tif", ".tiff", ".exr", ".hdr")

        def _make_spacer() -> QStandardItem:
            s = QStandardItem()
            s.setData(True, SPACER_ROLE)
            s.setFlags(Qt.ItemIsEnabled)   # not selectable, not editable
            return s

        def _pad_to_row_end():
            """Insert spacers to fill the rest of the current row."""
            nonlocal cur
            n = (cols - cur % cols) % cols
            for _ in range(n):
                all_rows.append(_make_spacer())
                cur += 1

        for mat in self._current_materials:
            is_expanded = mat.rel_path in self._expanded_materials

            item = QStandardItem()
            item.setData(mat.name, Qt.DisplayRole)
            item.setData(("material", mat), Qt.UserRole)
            if mat.preview_cache and mat.preview_cache.exists():
                thumb = str(mat.preview_cache)
            elif mat.preview_path and mat.preview_path.exists():
                thumb = str(mat.preview_path)
            else:
                thumb = ""
            item.setData(thumb, Qt.UserRole + 1)
            item.setData("texture", Qt.UserRole + 2)
            item.setData(mat.meta.surface_type or mat.source, Qt.UserRole + 3)
            item.setData(len(mat.maps), Qt.UserRole + 4)
            item.setData(mat.meta.tags, Qt.UserRole + 5)
            item.setData(True, Qt.UserRole + 6)
            item.setData(is_expanded, Qt.UserRole + 7)
            item.setData(self._mat_res(mat), Qt.UserRole + 9)
            item.setEditable(False)
            all_rows.append(item)
            cur += 1

            if is_expanded:
                # ── Pad to next row, then children, then pad to next row ──────
                _pad_to_row_end()

                for a in mat.maps:
                    child = QStandardItem()
                    child.setData(a.filename, Qt.DisplayRole)
                    child.setData(("asset", a), Qt.UserRole)
                    ct = (str(a.preview_cache) if a.preview_cache and a.preview_cache.exists()
                          else str(a.path) if a.path.suffix.lower() in _IMG_EXTS else "")
                    child.setData(ct, Qt.UserRole + 1)
                    child.setData("texture", Qt.UserRole + 2)
                    child.setData(a.sub_type, Qt.UserRole + 3)
                    child.setData(0, Qt.UserRole + 4)
                    child.setData(a.meta.tags, Qt.UserRole + 5)
                    child.setData(False, Qt.UserRole + 6)
                    child.setData(False, Qt.UserRole + 7)
                    child.setData(True, Qt.UserRole + 8)
                    child.setData(self._res_tag(a.filename), Qt.UserRole + 9)
                    child.setEditable(False)
                    all_rows.append(child)
                    cur += 1

                _pad_to_row_end()

        for asset in self._current_assets:
            item = QStandardItem()
            item.setData(asset.filename, Qt.DisplayRole)
            item.setData(("asset", asset), Qt.UserRole)
            thumb = (str(asset.preview_cache) if asset.preview_cache and asset.preview_cache.exists()
                     else str(asset.path) if asset.path.suffix.lower() in _IMG_EXTS else "")
            item.setData(thumb, Qt.UserRole + 1)
            item.setData(asset.asset_type, Qt.UserRole + 2)
            item.setData(asset.sub_type, Qt.UserRole + 3)
            item.setData(0, Qt.UserRole + 4)
            item.setData(asset.meta.tags, Qt.UserRole + 5)
            item.setData(False, Qt.UserRole + 6)
            item.setData(False, Qt.UserRole + 7)
            item.setData(self._res_tag(asset.filename), Qt.UserRole + 9)
            item.setEditable(False)
            all_rows.append(item)
            cur += 1

        # Single atomic update: clear + batch-insert → one layoutChanged signal
        self._model.clear()
        if all_rows:
            self._model.invisibleRootItem().appendRows(all_rows)
            if animate:
                content_rows = [i for i, it in enumerate(all_rows)
                                if not it.data(SPACER_ROLE)]
                self.delegate.start_animations(content_rows)

            # Pre-fetch thumbnails for the first visible page immediately —
            # before paint() is called — so images start decoding right away.
            cols          = self._col_count()
            visible_rows  = max(2, self.viewport().height() // max(1, self.gridSize().height()) + 1)
            limit         = cols * visible_rows
            fetched       = 0
            for item in all_rows:
                if fetched >= limit:
                    break
                if not item.data(SPACER_ROLE):
                    path = item.data(Qt.UserRole + 1)
                    if path:
                        self.delegate.prefetch(path)
                        fetched += 1

    def toggle_material_expand(self, mat: ScannedMaterial):
        """Toggle expansion of a material folder."""
        if mat.rel_path in self._expanded_materials:
            self._expanded_materials.discard(mat.rel_path)
        else:
            self._expanded_materials.add(mat.rel_path)
        self._rebuild_model()

    def selectionChanged(self, selected, deselected):
        """Override to catch all selection changes: click, rubber-band drag, keyboard."""
        super().selectionChanged(selected, deselected)
        sel_count = len(self.selectionModel().selectedIndexes())
        if sel_count == 0:
            self._sel_timer.stop()
            self._last_sel_ids = frozenset()
            self.selection_changed.emit(0, [])
        elif sel_count == 1:
            self._sel_timer.start(0)    # next tick — feels immediate
        else:
            self._sel_timer.start(150)  # debounce during rubber-band drag

    def _emit_selection(self):
        """Emit selection_changed after Qt has finished painting."""
        sel = self.selectionModel().selectedIndexes()
        count = len(sel)
        if count == 0:
            return

        # Build identity from row indices — skip if selection hasn't changed
        sel_ids = frozenset(idx.row() for idx in sel)
        if sel_ids == self._last_sel_ids:
            return
        self._last_sel_ids = sel_ids

        items = []
        for idx in sel:
            d = idx.data(Qt.UserRole)
            if d:
                items.append(d)
        self.selection_changed.emit(count, items)
        if count == 1 and items:
            kind, obj = items[0]
            if kind == "material":
                self.material_selected.emit(obj)
            else:
                self.asset_selected.emit(obj)

    def _on_dblclick(self, index):
        data = index.data(Qt.UserRole)
        if not data:
            return
        kind, obj = data
        if kind == "material":
            self.material_double_clicked.emit(obj)
        else:
            self.asset_double_clicked.emit(obj)

    def _context_menu(self, pos):
        indices = self.selectionModel().selectedIndexes()
        if not indices:
            return

        menu = QMenu(self)

        if len(indices) == 1:
            data = indices[0].data(Qt.UserRole)
            if data:
                kind, obj = data
                if kind == "asset":
                    act_open = QAction("Open File Location", self)
                    act_open.triggered.connect(lambda: self._open_location(obj))
                    menu.addAction(act_open)

                    act_copy = QAction("Copy Path", self)
                    act_copy.triggered.connect(lambda: QApplication.clipboard().setText(str(obj.path)))
                    menu.addAction(act_copy)
                elif kind == "material":
                    act_open = QAction("Open Material Folder", self)
                    act_open.triggered.connect(lambda: self._open_location(obj))
                    menu.addAction(act_open)

        menu.addSeparator()
        act_del = QAction(f"Delete ({len(indices)} item(s))", self)
        act_del.triggered.connect(self._delete_selected)
        menu.addAction(act_del)

        menu.exec(self.mapToGlobal(pos))

    def _delete_selected(self):
        items = []
        for idx in self.selectionModel().selectedIndexes():
            data = idx.data(Qt.UserRole)
            if data:
                items.append(data)
        if items:
            self.delete_requested.emit(items)

    def _open_location(self, obj):
        path = obj.path if hasattr(obj, "path") else None
        if path and path.exists():
            if path.is_dir():
                subprocess.Popen(["explorer", str(path)], creationflags=0x08000000)
            else:
                subprocess.Popen(["explorer", "/select,", str(path)], creationflags=0x08000000)

    def trigger_reflow(self):
        """Force QListView IconMode to reflow items to the current width.
        Resizing by 1px and back guarantees a resizeEvent with oldSize != newSize,
        which makes Qt call doDelayedItemsLayout() reliably."""
        w = self.width()
        h = self.height()
        if w > 1:
            self.resize(w + 1, h)
            self.resize(w, h)

    def get_selected_items(self) -> list:
        """Return list of (kind, obj) for selected items."""
        items = []
        for idx in self.selectionModel().selectedIndexes():
            data = idx.data(Qt.UserRole)
            if data:
                items.append(data)
        return items
