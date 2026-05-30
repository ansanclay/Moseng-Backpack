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
    QPixmapCache, QLinearGradient, QPainterPath,
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

# v2 design palette tokens (mirrored from theme.py to avoid import cost in paint)
_C_ACCENT        = QColor("#002aff")
_C_ACCENT_HI     = QColor("#1a3fff")
_C_ACCENT_BG     = QColor("#05091e")
_C_SURFACE       = QColor(4, 6, 15)       # #04060f -- main bg for gradient fade
_C_SURFACE_PH    = QColor("#07080d")      # placeholder fill
_C_TEXT_MID      = QColor("#6f7280")
_C_TEXT_LOW      = QColor("#4c4e58")


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
        import imageio.v3 as iio
        from PIL import Image

        arr = iio.imread(path).astype(np.float32)
        if arr.ndim == 2:
            arr = np.stack([arr] * 3, axis=-1)
        elif arr.shape[2] == 4:
            arr = arr[:, :, :3]

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
        img = _decode_hdr(self._path, self._max) if ext in _HDR_EXTS \
              else _decode_standard(self._path, self._max)
        self._signals.ready.emit(self._key, img)


# Delegate

class ThumbnailDelegate(QStyledItemDelegate):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.card_width  = 200
        self.card_height = 230
        self.tag_registry: dict = {}

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

    def _pixmap(self, path: str) -> "QPixmap | None":
        if not path:
            return None

        thumb_h  = int(self.card_height * 0.68)
        tw       = self.card_width - 2
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

    def sizeHint(self, option, index):
        return QSize(self.card_width, self.card_height)

    def paint(self, painter: QPainter, option, index):
        # Invisible spacer -- leave cell empty
        if index.data(SPACER_ROLE):
            return

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
        thumb_h = int(rect.height() * 0.68)
        radius  = 10

        is_sel = bool(option.state & QStyle.State_Selected)
        is_hov = bool(option.state & QStyle.State_MouseOver)

        # Card path
        card = QPainterPath()
        card.addRoundedRect(QRectF(rect), radius, radius)

        # Card background -- v2 design: near-transparent glass
        if is_sel:
            # accentBg fill + accent border ~35% alpha
            painter.setPen(QPen(QColor(0, 42, 255, 90), 1.0))
            painter.setBrush(_C_ACCENT_BG)
        elif is_hov:
            # 4% white fill + 10% white border
            painter.setPen(QPen(QColor(255, 255, 255, 25), 1.0))
            painter.setBrush(QColor(255, 255, 255, 10))
        else:
            # 2% white fill + 6% white border
            painter.setPen(QPen(QColor(255, 255, 255, 15), 1.0))
            painter.setBrush(QColor(255, 255, 255, 5))
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

            # Gradient fade at bottom of thumb -- matches surface bg #04060f
            g = QLinearGradient(tr.x(), tr.bottom() - 44, tr.x(), tr.bottom() + 1)
            fade_top = QColor(_C_SURFACE); fade_top.setAlpha(0)
            fade_bot = QColor(_C_SURFACE); fade_bot.setAlpha(235)
            g.setColorAt(0.0, fade_top)
            g.setColorAt(1.0, fade_bot)
            painter.fillRect(QRect(tr.x(), tr.bottom() - 44, tr.width(), 45), g)
            painter.restore()
        else:
            # Placeholder while loading -- type icon
            painter.save()
            painter.setClipPath(card)
            painter.fillRect(tr, _C_SURFACE_PH)
            asset_type = index.data(Qt.UserRole + 2) or "?"
            syms = {"texture": "▦", "hdri": "☀", "gobo": "◎",
                    "model": "⬢", "scene": "⬚"}
            cols = {"texture": "#002aff", "hdri": "#b08820", "gobo": "#7850a8",
                    "model": "#287850", "scene": "#a83050"}
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
            painter.setPen(QPen(QColor(0, 42, 255, 70), 0.8))
            painter.setBrush(_C_ACCENT_BG)
            painter.drawRoundedRect(rx, ry, rw, rh, 3, 3)
            painter.setPen(_C_ACCENT_HI)
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
            painter.setPen(QPen(QColor(255, 255, 255, 20), 0.8))
            painter.setBrush(QColor(4, 6, 15, 190))
            painter.drawRoundedRect(icon_rect, 4, 4)
            f = QFont("Segoe UI", 8, QFont.Bold)
            painter.setFont(f)
            painter.setPen(_C_TEXT_LOW)
            painter.drawText(icon_rect, Qt.AlignCenter,
                             "▼" if is_expanded else "▶")

        # Child-of-material indicator -- left accent bar
        if index.data(Qt.UserRole + 8):
            painter.setPen(Qt.NoPen)
            painter.setBrush(_C_ACCENT)
            painter.drawRect(rect.x(), rect.y() + radius, 2, rect.height() - radius * 2)

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
        painter.setPen(_C_ACCENT_HI if is_sel else _C_TEXT_MID)
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

    # Click handling

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease:
            if index.data(Qt.UserRole + 6):  # is_material
                M         = 6
                rect      = option.rect.adjusted(M, M, -M, -M)
                isz       = 20
                icon_rect = QRect(rect.right() - isz - 5, rect.y() + 6, isz, isz)
                pos = event.position().toPoint() if hasattr(event, "position") \
                      else event.pos().toPoint()
                if icon_rect.contains(pos):
                    data = index.data(Qt.UserRole)
                    if data:
                        view = self.parent()
                        if hasattr(view, "toggle_material_expand"):
                            view.toggle_material_expand(data[1])
                            return True
        return super().editorEvent(event, model, option, index)
