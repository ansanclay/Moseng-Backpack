"""Panel helpers for the QtAds dock shell."""

from PySide6.QtWidgets import QWidget
import PySide6QtAds as ads


def make_dock(manager: ads.CDockManager, title: str, widget: QWidget,
              object_name: str) -> ads.CDockWidget:
    """Wrap a content widget in a CDockWidget.

    A stable objectName is required so CDockManager.saveState()/restoreState()
    can round-trip the layout across sessions.
    """
    dock = ads.CDockWidget(manager, title)
    dock.setObjectName(object_name)
    dock.setWidget(widget)
    return dock
