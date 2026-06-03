"""Custom delegate for rendering cards - supports dynamic sizing via Ctrl+Wheel.

Thumbnail loading strategy
--------------------------
paintEvent must NEVER do disk I/O.  All decoding runs on a worker thread and
produces a QImage (thread-safe).  The main-thread slot converts QImage->QPixmap
and inserts it into QPixmapCache, then triggers a repaint.

This means the first paint of an uncached item shows a placeholder icon and the
real thumbnail appears asynchronously -- keeping the UI perfectly smooth regardless
of file size.
"""

import time
from pathlib import Path

from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from PySide6.QtCore import (
    Qt, QRect, QSize, QRectF, QEvent,
    QRunnable, QThreadPool, QObject, Signal, QThread, QTimer,
)
from PySide6.QtGui import (
    QPainter, QPixmap, QImage, QColor, QFont, QPen,
    QPixmapCache, QPainterPath,
    QImageReader,
)

_HDR_EXTS  = {".exr", ".hdr"}
_THUMB_MAX = 512    # max decode dimension (px)
_CACHE_MB  = 128    # QPixmapCache limit

# Card entrance animation constants
_ANIM_DURATION  = 0.30   # seconds per card
_ANIM_STAGGER   = 0.028  # seconds between each card (28 ms)
_ANIM_MAX_LAG   = 0.50   # total stagger capped at 500 ms

# Shared item-role constants (imported by asset_browser)
# Invisible spacer items are inserted around expanded child rows so that
# children always start and end on a grid-row boundary.
SPACER_ROLE = Qt.UserRole + 10   # bool -- item is an invisible spacer
SIZE_ROLE   = Qt.UserRole + 11   # int  -- file size in bytes (list view only)
DATE_ROLE   = Qt.UserRole + 12   # float -- modified time (unix ts, list view only)
DEPTH_ROLE  = Qt.UserRole + 13   # int  -- subfolder depth below selected folder

# Palette tokens — forest green theme (mirrored from theme.py to avoid import cost in paint)
_C_ACCENT        = QColor("#C4A84A")   # amber
_C_ACCENT_HI     = QColor("#D4BB60")   # amber hover
_C_ACCENT_BG     = QColor("#251E0E")   # dark amber bg
_C_SURFACE_PH    = QColor("#141A18")   # placeholder fill
_C_TEXT_MID      = QColor("#B8B4A0")   # muted cream
_C_TEXT_LOW      = QColor("#7A7868")   # dim cream


# ── Per-extension icons ─────────────────────────────────────────────────────
# Files with no image preview (scene / model / project files) show their OS
# file-type icon — the icon Windows registers for that extension (.hip, .fbx,
# .obj, …). Cached per extension; one lookup per type.
_icon_provider = None
_sys_icon_cache: dict = {}   # ext -> QIcon | None


def _system_icon(ext: str, sample_path: "str | None" = None):
    """Cached QIcon for *ext* (the OS file-type icon). The pixmap is rendered at
    the wanted size at draw time so it stays crisp / correctly-sized on HiDPI."""
    if ext in _sys_icon_cache:
        return _sys_icon_cache[ext]
    icon = None
    global _icon_provider
    try:
        from PySide6.QtWidgets import QFileIconProvider
        from PySide6.QtCore import QFileInfo
        if _icon_provider is None:
            _icon_provider = QFileIconProvider()
        info = QFileInfo(sample_path) if sample_path else QFileInfo("file" + ext)
        ic = _icon_provider.icon(info)
        icon = ic if not ic.isNull() else None
    except Exception:
        icon = None
    _sys_icon_cache[ext] = icon
    return icon


def _name_ext(name: str) -> str:
    """'.hip' from 'scene.hip' (lowercased), '' if no extension."""
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


# Thread-safe image decoders (return QImage, never QPixmap)

def _decode_standard(path: str, max_px: int) -> "QImage | None":
    """Decode any QImageReader-supported format at thumbnail size."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    orig = reader.size()
    if not orig.isValid():
        return None
    if orig.width() > max_px or orig.height() > max_px:
        reader.setScaledSize(orig.scaled(max_px, max_px, Qt.KeepAspectRatio))
    img = reader.read()
    return img if not img.isNull() else None


def _decode_hdr(path: str, max_px: int) -> "QImage | None":
    """Decode EXR/HDR with Reinhard tone-mapping -> QImage (thread-safe)."""
    try:
        import numpy as np
        from PIL import Image
        from backpack.utils.image_utils import read_exr_float32

        ext = Path(path).suffix.lower()
        if ext == ".exr":
            arr = read_exr_float32(Path(path))
        else:
            import imageio.v3 as iio
            arr = iio.imread(path).astype(np.float32)

        if arr is None:
            return None

        # Normalise to (H, W, 3)
        if arr.ndim == 2:
            arr = np.stack([arr] * 3, axis=-1)
        elif arr.ndim == 3:
            nc = arr.shape[2]
            if nc == 1:
                arr = np.repeat(arr, 3, axis=2)
            elif nc == 2:
                arr = np.stack([arr[:, :, 0], arr[:, :, 1], arr[:, :, 0]], axis=-1)
            elif nc >= 4:
                arr = arr[:, :, :3]

        arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=0.0)

        # Reinhard + gamma
        arr = arr / (1.0 + arr)
        arr = np.power(np.clip(arr, 0, 1), 1.0 / 2.2)
        arr = (arr * 255).astype(np.uint8)

        pil = Image.fromarray(arr, "RGB")
        pil.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)

        w, h = pil.size
        data = pil.tobytes("raw", "RGB")
        qimg = QImage(data, w, h, w * 3, QImage.Format_RGB888)
        return qimg.copy()
    except Exception:
        return None


# Worker

class _Signals(QObject):
    """Cross-thread signals (must live on the main thread)."""
    ready = Signal(str, object)   # (cache_key, QImage)


class _DecodeJob(QRunnable):
    """Decodes one image on a worker thread, emits QImage back to main thread."""

    def __init__(self, cache_key: str, path: str, max_px: int, signals: _Signals):
        super().__init__()
        self.setAutoDelete(True)
        self._key      = cache_key
        self._path     = path
        self._max      = max_px
        self._signals  = signals

    def run(self):
        ext = Path(self._path).suffix.lower()
        if ext in _HDR_EXTS:
            # Generate PREVIEWS/<rel>/<stem>.jpeg on first view, then decode that.
            # This is fast on repeat visits and avoids re-running tone-mapping.
            from backpack.core.preview import ensure_preview
            preview = ensure_preview(Path(self._path))
            img = (_decode_standard(str(preview), self._max)
                   if preview and preview.exists()
                   else _decode_hdr(self._path, self._max))
        else:
            img = _decode_standard(self._path, self._max)
        self._signals.ready.emit(self._key, img)


# Delegate

class ThumbnailDelegate(QStyledItemDelegate):

    def set_accent(self, color: str):
        """Re-derive the card accent colours from the theme primary colour."""
        from backpack.ui.theme import _blend
        self._c_accent    = QColor(color)
        self._c_accent_hi = QColor(_blend(color, "#ffffff", 0.12))
        self._c_accent_bg = QColor(_blend(color, "#000000", 0.86))
        p = self.parent()
        if p is not None and hasattr(p, "viewport"):
            p.viewport().update()

    # Row heights for the non-grid view modes.
    LIST_ROW_H    = 54
    COMPACT_ROW_H = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.card_width  = 200
        self.card_height = 230
        self.view_mode   = "grid"   # "grid" | "list"
        self.compact     = False    # grid → hide text; list → dense rows
        self.tag_registry: dict = {}

        # Accent colours (primary) — overridden via set_accent() to follow theme.
        self._c_accent    = QColor(_C_ACCENT)
        self._c_accent_hi = QColor(_C_ACCENT_HI)
        self._c_accent_bg = QColor(_C_ACCENT_BG)


        self._signals  = _Signals()
        self._pending: set[str] = set()
        self._priority: int = 0   # monotonically increasing; latest request = highest
        self._signals.ready.connect(self._on_image_ready)

        self._pool = QThreadPool()
        # Image decoding is mostly I/O + libjpeg — scales well with more threads.
        self._pool.setMaxThreadCount(max(4, min(8, QThread.idealThreadCount())))

        QPixmapCache.setCacheLimit(_CACHE_MB * 1024)

        # Debounce viewport repaints: batch all image-ready signals into one
        # update call instead of repainting for every single decoded thumbnail.
        self._repaint_timer = QTimer()
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(16)   # ≤1 frame @ 60 fps
        self._repaint_timer.timeout.connect(self._do_repaint)

        self._anim_start: dict[int, float] = {}
        self._anim_tick = QTimer()
        self._anim_tick.setInterval(14)   # ~70 fps
        self._anim_tick.timeout.connect(self._on_anim_tick)

    # Card entrance animation

    @staticmethod
    def _ease_in_out(t: float) -> float:
        if t <= 0.0: return 0.0
        if t >= 1.0: return 1.0
        if t < 0.5:
            return 4.0 * t * t * t
        return 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0

    def start_animations(self, rows: list) -> None:
        self._anim_tick.stop()
        self._anim_start.clear()
        if not rows:
            return
        now = time.perf_counter()
        for i, row in enumerate(rows):
            offset = min(i * _ANIM_STAGGER, _ANIM_MAX_LAG)
            self._anim_start[row] = now + offset
        self._anim_tick.start()

    def _anim_progress(self, row: int) -> float:
        start = self._anim_start.get(row)
        if start is None:
            return 1.0
        elapsed = time.perf_counter() - start
        if elapsed < 0:
            return 0.0
        return self._ease_in_out(min(elapsed / _ANIM_DURATION, 1.0))

    def _on_anim_tick(self) -> None:
        now = time.perf_counter()
        still_running = any(
            now < (start + _ANIM_DURATION)
            for start in self._anim_start.values()
        )
        if not still_running:
            self._anim_tick.stop()
            self._anim_start.clear()
        view = self.parent()
        if view and hasattr(view, "viewport"):
            view.viewport().update()

    # Slot -- called on main thread

    def _on_image_ready(self, cache_key: str, img):
        self._pending.discard(cache_key)
        if img is not None and not img.isNull():
            QPixmapCache.insert(cache_key, QPixmap.fromImage(img))
        # Debounce: schedule one repaint for all images that arrived this frame
        self._repaint_timer.start()

    def _do_repaint(self):
        view = self.parent()
        if view and hasattr(view, "viewport"):
            view.viewport().update()

    # Cache-first pixmap lookup — returns pixmap pre-scaled to thumbnail size.
    # A size-keyed entry is created on first use so paint() never calls
    # pix.scaled() (SmoothTransformation) on every frame.

    def _pixmap(self, path: str, tw: int | None = None,
                th: int | None = None) -> "QPixmap | None":
        if not path:
            return None

        if th is None:
            th = int(self.card_height * 0.68)
        if tw is None:
            tw = self.card_width - 2
        thumb_h  = th
        sized_key = f"{path}\x00{tw}x{thumb_h}"

        # Fast path: sized pixmap already cached
        pm = QPixmapCache.find(sized_key)
        if pm:
            return pm

        # Full-size pixmap in cache → scale once and store
        full_pm = QPixmapCache.find(path)
        if full_pm:
            scaled = full_pm.scaled(
                QSize(tw, thumb_h),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            QPixmapCache.insert(sized_key, scaled)
            return scaled

        # Not decoded yet — submit with ever-increasing priority so the most
        # recently *visible* item always runs before older queued items.
        if path not in self._pending:
            self._pending.add(path)
            self._priority += 1
            self._pool.start(
                _DecodeJob(path, path, _THUMB_MAX, self._signals),
                self._priority,
            )
        return None

    def prefetch(self, path: str) -> None:
        """Submit a decode job immediately without waiting for paint().

        Called for items that are about to be visible so their thumbnails
        start loading before the first paintEvent fires.
        """
        if not path:
            return
        th = int(self.card_height * 0.68)
        tw = self.card_width - 2
        # Skip if already cached at display size or full size
        if QPixmapCache.find(f"{path}\x00{tw}x{th}") or QPixmapCache.find(path):
            return
        if path not in self._pending:
            self._pending.add(path)
            self._priority += 1
            self._pool.start(
                _DecodeJob(path, path, _THUMB_MAX, self._signals),
                self._priority,
            )

    def cancel_pending(self) -> None:
        """Drop queued-but-not-yet-started jobs (e.g. on folder navigation).

        Jobs already running complete normally; their results are dropped if
        the path is no longer in the model.
        """
        self._pool.clear()   # removes queued jobs that haven't started
        self._pending.clear()

    # Paint

    def row_height(self) -> int:
        return self.COMPACT_ROW_H if self.compact else self.LIST_ROW_H

    @staticmethod
    def _draw_ext_icon(painter: QPainter, rect: QRect, index, frac: float = 0.8) -> bool:
        """Draw the file's OS type icon centred in *rect*. Returns True if an
        icon was drawn (caller then skips the generic glyph)."""
        ext = _name_ext(index.data(Qt.DisplayRole) or "")
        data = index.data(Qt.UserRole)
        path = str(data[1].path) if data and hasattr(data[1], "path") else None
        icon = _system_icon(ext, path)
        if icon is None or icon.isNull():
            return False
        side = max(12, int(min(rect.width(), rect.height()) * frac))
        pm = icon.pixmap(side, side)   # DPR-aware → crisp at the wanted size
        if pm.isNull():
            return False
        x = rect.x() + (rect.width()  - side) // 2
        y = rect.y() + (rect.height() - side) // 2
        # Draw into a logical side×side rect so HiDPI doesn't halve the size.
        painter.drawPixmap(QRect(x, y, side, side), pm)
        return True

    def _row_width(self) -> int:
        p = self.parent()
        if p is not None and hasattr(p, "viewport"):
            return max(80, p.viewport().width())
        return 400

    def _expand_rect(self, option, index) -> "QRect | None":
        """Hit/draw rect for a material's expand toggle, per view mode."""
        if not index.data(Qt.UserRole + 6):   # not a material
            return None
        if self.view_mode == "grid":
            M = 6
            rect = option.rect.adjusted(M, M, -M, -M)
            isz = 20
            return QRect(rect.right() - isz - 5, rect.y() + 6, isz, isz)
        rect = option.rect
        isz = 18
        return QRect(rect.x() + 4, rect.y() + (rect.height() - isz) // 2, isz, isz)

    def sizeHint(self, option, index):
        if self.view_mode == "grid":
            # Compact grid hides the text strip → near-square, image-only cards.
            h = self.card_width if self.compact else self.card_height
            return QSize(self.card_width, h)
        return QSize(self._row_width(), self.row_height())

    def paint(self, painter: QPainter, option, index):
        # Invisible spacer -- leave cell empty
        if index.data(SPACER_ROLE):
            return
        if self.view_mode == "list":
            self._paint_row(painter, option, index, compact=self.compact)
            return

        hide_text = self.compact   # grid + compact → image only

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        # Entrance animation: scale 50%->100%, opacity 0->1
        prog = self._anim_progress(index.row())
        if prog < 1.0:
            scale = 0.5 + 0.5 * prog
            cx = float(option.rect.center().x())
            cy = float(option.rect.center().y())
            painter.setOpacity(prog)
            painter.translate(cx, cy)
            painter.scale(scale, scale)
            painter.translate(-cx, -cy)

        M       = 6
        rect    = option.rect.adjusted(M, M, -M, -M)
        thumb_h = rect.height() if hide_text else int(rect.height() * 0.68)
        radius  = 10

        is_sel = bool(option.state & QStyle.State_Selected)
        is_hov = bool(option.state & QStyle.State_MouseOver)

        # Card path
        card = QPainterPath()
        card.addRoundedRect(QRectF(rect), radius, radius)

        # Card background — forest green theme
        if is_sel:
            # primary-tinted bg + primary border
            _border = QColor(self._c_accent); _border.setAlpha(90)
            painter.setPen(QPen(_border, 1.0))
            painter.setBrush(self._c_accent_bg)
        elif is_hov:
            # subtle cream fill + cream border
            painter.setPen(QPen(QColor(242, 238, 220, 28), 1.0))
            painter.setBrush(QColor(242, 238, 220, 12))
        else:
            # very subtle cream fill + cream border
            painter.setPen(QPen(QColor(242, 238, 220, 15), 1.0))
            painter.setBrush(QColor(242, 238, 220, 6))
        painter.drawPath(card)

        # Thumbnail
        tr  = QRect(rect.x() + 1, rect.y() + 1, rect.width() - 2, thumb_h)
        pix = self._pixmap(index.data(Qt.UserRole + 1))

        if pix and not pix.isNull():
            # pixmap is already scaled to thumbnail size by _pixmap()
            xo = (pix.width()  - tr.width())  // 2
            yo = (pix.height() - tr.height()) // 2
            painter.save()
            painter.setClipPath(card)
            painter.drawPixmap(tr.topLeft(), pix, QRect(xo, yo, tr.width(), tr.height()))
            painter.restore()
        else:
            # Placeholder while loading — branded ext icon, else a type glyph
            painter.save()
            painter.setClipPath(card)
            painter.fillRect(tr, _C_SURFACE_PH)
            if not self._draw_ext_icon(painter, tr, index):
                asset_type = index.data(Qt.UserRole + 2) or "?"
                syms = {"texture": "▦", "hdri": "☀", "gobo": "◎",
                        "model": "⬢", "scene": "⬚"}
                cols = {"texture": "#4A8C6A", "hdri": "#C4A84A", "gobo": "#8A7A4A",
                        "model": "#5A8C6A", "scene": "#8C6A4A"}
                icon_pt = max(14, self.card_width // 9)
                f = QFont("Segoe UI Symbol", icon_pt, QFont.Light)
                painter.setFont(f)
                painter.setPen(QColor(cols.get(asset_type, "#2a2d35")))
                painter.drawText(tr, Qt.AlignCenter, syms.get(asset_type, "□"))
            painter.restore()

        # Resolution badge (bottom-left of thumb) -- accentBg + accentBd border
        res = index.data(Qt.UserRole + 9)
        if res:
            rf = QFont("DM Mono", 7, QFont.DemiBold)
            rf.setStyleHint(QFont.Monospace)
            painter.setFont(rf)
            fm    = painter.fontMetrics()
            PAD   = 4
            BM    = 5
            rw    = fm.horizontalAdvance(res) + PAD * 2
            rh    = 15
            rx    = rect.x() + BM
            ry    = rect.y() + thumb_h - rh - BM
            painter.save()
            painter.setClipPath(card)
            _rb = QColor(self._c_accent); _rb.setAlpha(70)
            painter.setPen(QPen(_rb, 0.8))
            painter.setBrush(self._c_accent_bg)
            painter.drawRoundedRect(rx, ry, rw, rh, 3, 3)
            painter.setPen(self._c_accent_hi)
            painter.drawText(rx, ry, rw, rh, Qt.AlignCenter, res)
            painter.restore()

        # Expand/collapse icon (materials)
        is_material = index.data(Qt.UserRole + 6)
        is_expanded = index.data(Qt.UserRole + 7)
        if is_material:
            isz       = 20
            ix        = rect.right() - isz - 5
            iy        = rect.y() + 6
            icon_rect = QRect(ix, iy, isz, isz)
            painter.setPen(QPen(QColor(242, 238, 220, 20), 0.8))
            painter.setBrush(QColor(20, 26, 24, 210))
            painter.drawRoundedRect(icon_rect, 4, 4)
            f = QFont("Segoe UI", 8, QFont.Bold)
            painter.setFont(f)
            painter.setPen(_C_TEXT_LOW)
            painter.drawText(icon_rect, Qt.AlignCenter,
                             "▼" if is_expanded else "▶")

        # Child-of-material indicator -- left accent bar
        if index.data(Qt.UserRole + 8):
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._c_accent)
            painter.drawRect(rect.x(), rect.y() + radius, 2, rect.height() - radius * 2)

        # Compact grid: image only — no text strip, but show tag color squares
        # in the bottom-right corner of the image.
        if hide_text:
            tags = index.data(Qt.UserRole + 5)
            if tags:
                SQ, SP, BM = 9, 3, 5
                sx = rect.right() - BM - SQ
                sy = rect.bottom() - BM - SQ
                painter.save()
                painter.setClipPath(card)
                painter.setPen(Qt.NoPen)
                for t in reversed(list(tags[:6])):
                    if sx < rect.x() + BM:
                        break
                    info  = self.tag_registry.get(t)
                    color = QColor(info.color) if info and info.color else QColor("#002aff")
                    color.setAlpha(220)
                    painter.setBrush(color)
                    painter.drawRoundedRect(sx, sy, SQ, SQ, 2, 2)
                    sx -= SQ + SP
                painter.restore()
            painter.restore()
            return

        # Text area
        TEXT_PAD = 9
        ty = rect.y() + thumb_h + 6
        tx = rect.x() + TEXT_PAD
        tw = rect.width() - TEXT_PAD * 2

        name = index.data(Qt.DisplayRole) or ""
        # DM Mono filename -- v2 design monospace filename style
        nf = QFont("DM Mono", max(8, self.card_width // 22))
        nf.setStyleHint(QFont.Monospace)
        painter.setFont(nf)
        painter.setPen(self._c_accent_hi if is_sel else _C_TEXT_MID)
        fm_n = painter.fontMetrics()
        painter.drawText(tx, ty + fm_n.ascent(),
                         fm_n.elidedText(name, Qt.ElideRight, tw))

        sub = index.data(Qt.UserRole + 3) or ""
        if sub:
            sf = QFont("DM Sans", max(7, self.card_width // 26))
            painter.setFont(sf)
            painter.setPen(_C_TEXT_LOW)
            sub_y = ty + fm_n.height() + 2
            fm_s  = painter.fontMetrics()
            painter.drawText(tx, sub_y + fm_s.ascent(),
                             fm_s.elidedText(sub, Qt.ElideRight, tw))

        # Tag color dots (bottom of text area)
        # v2 design: small 6px filled dots, NOT text pill badges
        tags = index.data(Qt.UserRole + 5)
        if tags and self.card_width >= 120:
            DOT_R   = 3
            DOT_D   = DOT_R * 2
            SPACING = 4
            dot_y   = rect.bottom() - DOT_R - 5
            dot_x   = tx

            display   = list(tags[:7])
            remaining = len(tags) - 7

            painter.save()
            painter.setClipPath(card)
            painter.setPen(Qt.NoPen)

            for t in display:
                if dot_x + DOT_D > rect.right() - TEXT_PAD:
                    break
                info  = self.tag_registry.get(t)
                color = QColor(info.color) if info and info.color else QColor("#002aff")
                color.setAlpha(190)
                painter.setBrush(color)
                painter.drawEllipse(dot_x, dot_y - DOT_R, DOT_D, DOT_D)
                dot_x += DOT_D + SPACING

            if remaining > 0 and dot_x + 16 <= rect.right() - TEXT_PAD:
                f = QFont("DM Sans", 7)
                painter.setFont(f)
                painter.setPen(_C_TEXT_LOW)
                painter.drawText(dot_x, dot_y - DOT_R, 20, DOT_D,
                                 Qt.AlignVCenter | Qt.AlignLeft, f"+{remaining}")
            painter.restore()

        painter.restore()

    # Row painter (list / compact modes)

    _ROW_SYMS = {"texture": "▦", "hdri": "☀", "gobo": "◎", "model": "⬢", "scene": "⬚"}
    _ROW_COLS = {"texture": "#4A8C6A", "hdri": "#C4A84A", "gobo": "#8A7A4A",
                 "model": "#5A8C6A", "scene": "#8C6A4A"}

    @staticmethod
    def _fmt_size(n) -> str:
        if n is None:
            return ""
        f = float(n)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if f < 1024 or unit == "TB":
                return f"{int(f)} {unit}" if unit == "B" else f"{f:.1f} {unit}"
            f /= 1024
        return ""

    @staticmethod
    def _fmt_date(ts) -> str:
        if not ts:
            return ""
        try:
            return time.strftime("%Y-%m-%d", time.localtime(ts))
        except (ValueError, OSError):
            return ""

    def _paint_row(self, painter: QPainter, option, index, compact: bool):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        # Subfolder items shift right as a whole box: indent the row rect's
        # left edge (right edge stays at the panel edge).
        depth  = index.data(DEPTH_ROLE) or 0
        indent = min(depth, 5) * 16
        rect   = option.rect.adjusted(2 + indent, 1, -2, -1)
        h      = rect.height()
        is_sel = bool(option.state & QStyle.State_Selected)
        is_hov = bool(option.state & QStyle.State_MouseOver)

        row = QPainterPath()
        row.addRoundedRect(QRectF(rect), 5, 5)
        if is_sel:
            b = QColor(self._c_accent); b.setAlpha(90)
            painter.setPen(QPen(b, 1.0)); painter.setBrush(self._c_accent_bg)
        elif is_hov:
            painter.setPen(QPen(QColor(242, 238, 220, 24), 1.0))
            painter.setBrush(QColor(242, 238, 220, 10))
        else:
            painter.setPen(Qt.NoPen); painter.setBrush(QColor(242, 238, 220, 5))
        painter.drawPath(row)

        left = rect.x() + 8

        # Expand toggle (materials) — far left
        er = self._expand_rect(option, index)
        if er is not None:
            painter.setPen(QPen(_C_TEXT_LOW))
            painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
            painter.drawText(er, Qt.AlignCenter,
                             "▼" if index.data(Qt.UserRole + 7) else "▶")
            left = er.right() + 6
        elif index.data(Qt.UserRole + 8):   # child of a material → indent
            left += 18

        # Thumbnail / icon (list mode only)
        if not compact:
            sq = h - 8
            pix = self._pixmap(index.data(Qt.UserRole + 1), sq, sq)
            if pix and not pix.isNull():
                # Image preview: rounded square, inset.
                tr = QRect(left, rect.y() + 4, sq, sq)
                clip = QPainterPath(); clip.addRoundedRect(QRectF(tr), 4, 4)
                painter.save(); painter.setClipPath(clip)
                xo = (pix.width()  - tr.width())  // 2
                yo = (pix.height() - tr.height()) // 2
                painter.drawPixmap(tr.topLeft(), pix, QRect(xo, yo, tr.width(), tr.height()))
                painter.restore()
                left = tr.right() + 10
            else:
                # File-type icon: use (nearly) the full row height so it's large.
                isz = h - 2
                irect = QRect(left, rect.y() + 1, isz, isz)
                if not self._draw_ext_icon(painter, irect, index, frac=0.9):
                    painter.fillRect(QRect(left, rect.y() + 4, sq, sq), _C_SURFACE_PH)
                    at = index.data(Qt.UserRole + 2) or "?"
                    painter.setFont(QFont("Segoe UI Symbol", max(12, isz // 2), QFont.Light))
                    painter.setPen(QColor(self._ROW_COLS.get(at, "#2a2d35")))
                    painter.drawText(irect, Qt.AlignCenter, self._ROW_SYMS.get(at, "□"))
                left = irect.right() + 10

        # Right cluster (right→left): date, size, resolution badge, tag dots.
        right = rect.right() - 8

        # Date + size columns — shown in both list and compact rows.
        painter.setFont(QFont("DM Sans", 8))
        painter.setPen(_C_TEXT_LOW)
        dtxt = self._fmt_date(index.data(DATE_ROLE))
        if dtxt:
            DW = 76
            painter.drawText(right - DW, rect.y(), DW, h,
                             Qt.AlignVCenter | Qt.AlignRight, dtxt)
            right -= DW + 10
        stxt = self._fmt_size(index.data(SIZE_ROLE))
        if stxt:
            SW = 62
            painter.drawText(right - SW, rect.y(), SW, h,
                             Qt.AlignVCenter | Qt.AlignRight, stxt)
            right -= SW + 12

        res = index.data(Qt.UserRole + 9)
        if res:
            rf = QFont("DM Mono", 7, QFont.DemiBold); rf.setStyleHint(QFont.Monospace)
            painter.setFont(rf)
            fm = painter.fontMetrics()
            rw = fm.horizontalAdvance(res) + 8
            rh = 15
            rx = right - rw
            ry = rect.y() + (h - rh) // 2
            _rb = QColor(self._c_accent); _rb.setAlpha(70)
            painter.setPen(QPen(_rb, 0.8)); painter.setBrush(self._c_accent_bg)
            painter.drawRoundedRect(rx, ry, rw, rh, 3, 3)
            painter.setPen(self._c_accent_hi)
            painter.drawText(rx, ry, rw, rh, Qt.AlignCenter, res)
            right = rx - 8

        tags = index.data(Qt.UserRole + 5)
        if tags:
            DOT_D = 6; SP = 4
            dx = right - DOT_D
            dy = rect.y() + h // 2
            painter.setPen(Qt.NoPen)
            for t in reversed(list(tags[:6])):
                if dx < left:
                    break
                info  = self.tag_registry.get(t)
                color = QColor(info.color) if info and info.color else QColor("#002aff")
                color.setAlpha(190)
                painter.setBrush(color)
                painter.drawEllipse(dx, dy - DOT_D // 2, DOT_D, DOT_D)
                dx -= DOT_D + SP
            right = dx - 4

        # Name (+ sub-type on a second line in list mode)
        name = index.data(Qt.DisplayRole) or ""
        avail = max(20, right - left)
        nf = QFont("DM Mono", 8 if compact else 9); nf.setStyleHint(QFont.Monospace)
        painter.setFont(nf)
        painter.setPen(self._c_accent_hi if is_sel else _C_TEXT_MID)
        fm_n = painter.fontMetrics()
        if compact:
            painter.drawText(left, rect.y(), avail, h,
                             Qt.AlignVCenter | Qt.AlignLeft,
                             fm_n.elidedText(name, Qt.ElideRight, avail))
        else:
            painter.drawText(left, rect.y() + 7, avail, fm_n.height(),
                             Qt.AlignLeft, fm_n.elidedText(name, Qt.ElideRight, avail))
            sub = index.data(Qt.UserRole + 3) or ""
            if sub:
                sf = QFont("DM Sans", 8); painter.setFont(sf); painter.setPen(_C_TEXT_LOW)
                fm_s = painter.fontMetrics()
                painter.drawText(left, rect.y() + 7 + fm_n.height(), avail, fm_s.height(),
                                 Qt.AlignLeft, fm_s.elidedText(sub, Qt.ElideRight, avail))

        painter.restore()

    # Click handling

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease:
            er = self._expand_rect(option, index)
            if er is not None:
                pos = event.position().toPoint() if hasattr(event, "position") \
                      else event.pos().toPoint()
                if er.contains(pos):
                    data = index.data(Qt.UserRole)
                    if data:
                        view = self.parent()
                        if hasattr(view, "toggle_material_expand"):
                            view.toggle_material_expand(data[1])
                            return True
        return super().editorEvent(event, model, option, index)
