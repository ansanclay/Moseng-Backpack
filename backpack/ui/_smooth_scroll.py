"""Smooth animated wheel-scroll for any QAbstractScrollArea.

Usage:
    install_smooth_scroll(widget)            # 120 px/notch, 200 ms
    install_smooth_scroll(widget, px=80, ms=180)
"""

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QAbstractItemView


class _SmoothScrollFilter(QObject):
    def __init__(self, widget: QAbstractItemView, px_per_notch: int, duration_ms: int):
        super().__init__(widget)
        self._widget = widget
        self._px = px_per_notch
        self._target: int = 0

        sb = widget.verticalScrollBar()
        self._anim = QPropertyAnimation(sb, b"value", self)
        self._anim.setDuration(duration_ms)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        widget.viewport().installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if event.type() != QEvent.Type.Wheel:
            return False
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return False  # let Ctrl+wheel (zoom) pass through

        delta = event.angleDelta().y()
        if delta == 0:
            return False

        sb = self._widget.verticalScrollBar()
        base = (
            self._target
            if self._anim.state() == QPropertyAnimation.State.Running
            else sb.value()
        )
        px_delta = int(-delta / 120.0 * self._px)
        self._target = max(sb.minimum(), min(sb.maximum(), base + px_delta))

        self._anim.stop()
        self._anim.setStartValue(sb.value())
        self._anim.setEndValue(self._target)
        self._anim.start()
        return True  # event consumed


def install_smooth_scroll(
    widget: QAbstractItemView,
    px_per_notch: int = 120,
    duration_ms: int = 200,
) -> None:
    """Attach animated smooth scrolling to *widget*."""
    _SmoothScrollFilter(widget, px_per_notch, duration_ms)
