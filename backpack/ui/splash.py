"""Startup loading screen.

A small, themed splash shown while the workspace and asset library load. It
exposes set_status(text, progress) which updates the description line + the
accent progress strip and pumps the event loop so the screen stays responsive
during the (mostly synchronous) startup phases.
"""

from pathlib import Path

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QApplication
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QIcon, QPainterPath

from backpack.ui.theme import _blend


class StartupSplash(QWidget):
    """Frameless, centered loading card driven by set_status()."""

    def __init__(self, accent: str, bg: str, secondary: str,
                 icon_path: Path | None = None):
        super().__init__()
        self._accent = QColor(accent)
        self._card = QColor(_blend(bg, "#ffffff", 0.04))   # slight elevation
        self._progress = 0.0

        self.setWindowFlags(Qt.SplashScreen | Qt.WindowStaysOnTopHint
                            | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(480, 300)

        muted = _blend(secondary, bg, 0.45)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 46, 40, 40)
        lay.setSpacing(0)

        if icon_path and Path(icon_path).exists():
            ic = QLabel()
            ic.setAlignment(Qt.AlignCenter)
            ic.setPixmap(QIcon(str(icon_path)).pixmap(64, 64))
            lay.addWidget(ic)
            lay.addSpacing(18)

        title = QLabel("Moseng Backpack")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color:{secondary}; font-size:22px; font-weight:700; "
            "letter-spacing:0.5px; background:transparent;")
        lay.addWidget(title)

        subtitle = QLabel("3D ASSET LIBRARY")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet(
            f"color:{muted}; font-size:10px; letter-spacing:3px; "
            "background:transparent;")
        lay.addWidget(subtitle)

        lay.addStretch()

        self._status_label = QLabel("Starting…")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setStyleSheet(
            f"color:{muted}; font-size:11px; background:transparent;")
        lay.addWidget(self._status_label)
        lay.addSpacing(14)   # leave room for the painted progress strip

        self._center_on_screen()

    def _center_on_screen(self):
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - self.width() // 2,
                      geo.center().y() - self.height() // 2)

    def set_status(self, text: str, progress: float | None = None):
        """Update the description line and (optionally) the progress strip,
        then repaint + pump events so the splash reflects the change at once."""
        self._status_label.setText(text)
        if progress is not None:
            self._progress = max(0.0, min(1.0, progress))
        self.repaint()
        QApplication.processEvents()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.fillPath(path, self._card)

        border = QColor(self._accent)
        border.setAlpha(130)
        p.setPen(QPen(border, 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        # Progress strip near the bottom edge.
        track_y = self.height() - 30
        x, w = 40, self.width() - 80
        track = QColor(255, 255, 255, 28)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(x, track_y, w, 3), 1.5, 1.5)
        if self._progress > 0:
            p.setBrush(self._accent)
            p.drawRoundedRect(QRectF(x, track_y, w * self._progress, 3), 1.5, 1.5)
        p.end()
