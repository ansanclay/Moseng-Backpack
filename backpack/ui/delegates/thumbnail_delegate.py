"""Custom delegate for rendering cards - supports dynamic sizing via Ctrl+Wheel."""

from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from PySide6.QtCore import Qt, QRect, QSize, QRectF
from PySide6.QtGui import (
    QPainter, QPixmap, QColor, QFont, QPen,
    QPixmapCache, QLinearGradient, QPainterPath,
)


class ThumbnailDelegate(QStyledItemDelegate):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.card_width = 200
        self.card_height = 230

    def sizeHint(self, option, index):
        return QSize(self.card_width, self.card_height)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        M = 5
        rect = option.rect.adjusted(M, M, -M, -M)
        thumb_h = int(rect.height() * 0.7)
        radius = 10

        is_sel = bool(option.state & QStyle.State_Selected)
        is_hov = bool(option.state & QStyle.State_MouseOver)

        # Card bg
        card = QPainterPath()
        card.addRoundedRect(QRectF(rect), radius, radius)

        if is_sel:
            painter.setPen(QPen(QColor("#4a9eff"), 2))
            painter.setBrush(QColor("#1e2430"))
        elif is_hov:
            painter.setPen(QPen(QColor("#2a2d35"), 1))
            painter.setBrush(QColor("#22252d"))
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#1e2028"))
        painter.drawPath(card)

        # Thumb area
        tr = QRect(rect.x() + 1, rect.y() + 1, rect.width() - 2, thumb_h)
        thumb_path = index.data(Qt.UserRole + 1)
        pix = self._pixmap(thumb_path)

        if pix and not pix.isNull():
            sc = pix.scaled(tr.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            xo = (sc.width() - tr.width()) // 2
            yo = (sc.height() - tr.height()) // 2
            cropped = sc.copy(xo, yo, tr.width(), tr.height())
            painter.save()
            painter.setClipPath(card)
            painter.drawPixmap(tr.topLeft(), cropped)
            painter.restore()

            # Gradient at bottom of thumb
            g = QLinearGradient(tr.x(), tr.bottom() - 30, tr.x(), tr.bottom())
            g.setColorAt(0, QColor(30, 32, 40, 0))
            g.setColorAt(1, QColor(30, 32, 40, 200))
            painter.save()
            painter.setClipPath(card)
            painter.fillRect(QRect(tr.x(), tr.bottom() - 30, tr.width(), 30), g)
            painter.restore()
        else:
            painter.save()
            painter.setClipPath(card)
            painter.fillRect(tr, QColor("#15171d"))
            asset_type = index.data(Qt.UserRole + 2) or "?"
            syms = {"texture": "\u25a6", "hdri": "\u2600", "gobo": "\u25ce",
                    "model": "\u2b22", "scene": "\u2b1a"}
            cols = {"texture": "#4a9eff", "hdri": "#f0c050", "gobo": "#b080e0",
                    "model": "#50c878", "scene": "#e06070"}
            painter.setFont(QFont("Segoe UI", max(16, self.card_width // 8), QFont.Light))
            painter.setPen(QColor(cols.get(asset_type, "#3a3d45")))
            painter.drawText(tr, Qt.AlignCenter, syms.get(asset_type, "\u25a1"))
            painter.restore()

        # Map count badge
        mc = index.data(Qt.UserRole + 4)
        if mc and mc > 0:
            txt = f"{mc} maps"
            f = QFont("Segoe UI", 8)
            painter.setFont(f)
            tw = painter.fontMetrics().horizontalAdvance(txt) + 10
            br = QRect(rect.x() + 6, rect.y() + 8, tw, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 140))
            painter.drawRoundedRect(br, 4, 4)
            painter.setPen(QColor("#d4d6db"))
            painter.drawText(br, Qt.AlignCenter, txt)

        # Text
        ty = rect.y() + thumb_h + 6
        tx = rect.x() + 10
        tw = rect.width() - 20

        name = index.data(Qt.DisplayRole) or ""
        nf = QFont("Segoe UI", max(9, self.card_width // 20), QFont.DemiBold)
        painter.setFont(nf)
        painter.setPen(QColor("#ffffff") if is_sel else QColor("#d4d6db"))
        elided = painter.fontMetrics().elidedText(name, Qt.ElideRight, tw)
        painter.drawText(tx, ty + painter.fontMetrics().ascent(), elided)

        sub = index.data(Qt.UserRole + 3) or ""
        if sub:
            sf = QFont("Segoe UI", max(8, self.card_width // 24))
            painter.setFont(sf)
            painter.setPen(QColor("#6b6e76"))
            el2 = painter.fontMetrics().elidedText(sub, Qt.ElideRight, tw)
            painter.drawText(tx, ty + nf.pointSize() + sf.pointSize() + 8, el2)

        painter.restore()

    def _pixmap(self, path) -> QPixmap | None:
        if not path:
            return None
        pm = QPixmapCache.find(path)
        if pm:
            return pm
        pm = QPixmap(path)
        if not pm.isNull():
            QPixmapCache.insert(path, pm)
            return pm
        return None
