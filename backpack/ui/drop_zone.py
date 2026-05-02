"""Drop overlay - full-screen translucent overlay shown when files are dragged over the window."""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import (
    QDragEnterEvent, QDragMoveEvent, QDropEvent, QPainter, QColor, QPen,
    QPainterPath, QFont,
)


class DropOverlay(QWidget):
    """Translucent overlay that appears over the main window when dragging files."""

    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("dropOverlay")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.hide()

        # Timer to debounce drag-leave: hides overlay only if no re-enter within 100ms
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(100)
        self._hide_timer.timeout.connect(self._do_hide)

    def show_overlay(self):
        """Resize to fill parent and show."""
        self._hide_timer.stop()
        if self.parent():
            self.setGeometry(self.parent().rect())
        self.raise_()
        self.show()

    def hide_overlay(self):
        # Debounce: schedule hide, cancel if drag re-enters
        self._hide_timer.start()

    def _do_hide(self):
        self.hide()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasFormat("application/x-backpack-internal"):
            event.ignore()
            return
        if event.mimeData().hasUrls():
            self._hide_timer.stop()
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.hide_overlay()

    def dropEvent(self, event: QDropEvent):
        self._hide_timer.stop()
        self.hide()
        urls = event.mimeData().urls()
        paths = [url.toLocalFile() for url in urls if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Darken background
        painter.fillRect(self.rect(), QColor(0, 0, 0, 160))

        # Dashed border inset
        r = self.rect().adjusted(32, 32, -32, -32)
        pen = QPen(QColor("#002aff"), 3, Qt.DashLine)
        pen.setDashPattern([8, 6])
        painter.setPen(pen)
        painter.setBrush(QColor(0, 42, 255, 15))
        path = QPainterPath()
        path.addRoundedRect(r.x(), r.y(), r.width(), r.height(), 20, 20)
        painter.drawPath(path)

        # Icon
        icon_font = QFont("Segoe UI", 48, QFont.Light)
        painter.setFont(icon_font)
        painter.setPen(QColor("#002aff"))
        icon_r = r.adjusted(0, 0, 0, -40)
        painter.drawText(icon_r, Qt.AlignCenter, "\u2b07")

        # Text
        text_font = QFont("Segoe UI", 18, QFont.DemiBold)
        painter.setFont(text_font)
        painter.setPen(QColor("#d4d6db"))
        text_r = r.adjusted(0, 60, 0, 0)
        painter.drawText(text_r, Qt.AlignCenter, "Drop to Import")

        sub_font = QFont("Segoe UI", 12)
        painter.setFont(sub_font)
        painter.setPen(QColor("#6b6e76"))
        sub_r = r.adjusted(0, 100, 0, 0)
        painter.drawText(sub_r, Qt.AlignCenter, "Files or folders will be imported to your Backpack")

        painter.end()
