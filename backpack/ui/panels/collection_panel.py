"""Collection panel — a personal tray for gathering assets from any Explorer.

Select items in an Explorer, click "Add Selection", and they collect here for
quick access no matter which folder you browse to next. Double-click opens an
item; the toolbar removes the tray-selected items or clears the whole tray.

The collection is session-scoped: it is intentionally not persisted across
restarts (the items are the live scanned objects, not re-scanned paths). The
embedded grid has its context menu disabled so there is no path to file
deletion — removal only ever affects the tray, never the disk.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
)
from PySide6.QtCore import Qt, Signal

from backpack.ui.asset_browser import AssetBrowser


class CollectionPanel(QWidget):
    add_requested = Signal()      # add the active Explorer's current selection
    remove_requested = Signal()   # remove the items selected in the tray
    clear_requested = Signal()    # empty the tray

    def __init__(self, card_size: int = 150, parent=None):
        super().__init__(parent)
        self.setObjectName("collectionPanel")
        self._sel: list = []   # current selection within the tray (kind, item)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(12, 8, 8, 6)
        bar.setSpacing(6)
        self._count = QLabel("Empty")
        self._count.setObjectName("collectionCount")
        bar.addWidget(self._count)
        bar.addStretch()

        self._btn_add = QPushButton("Add Selection")
        self._btn_add.setObjectName("collectionAdd")
        self._btn_add.setCursor(Qt.PointingHandCursor)
        self._btn_add.setToolTip("Add the items selected in an Explorer to the collection")
        self._btn_add.clicked.connect(self.add_requested.emit)
        bar.addWidget(self._btn_add)

        self._btn_remove = QPushButton("Remove")
        self._btn_remove.setObjectName("collectionRemove")
        self._btn_remove.setCursor(Qt.PointingHandCursor)
        self._btn_remove.setToolTip("Remove the items selected in the collection")
        self._btn_remove.setEnabled(False)
        self._btn_remove.clicked.connect(self.remove_requested.emit)
        bar.addWidget(self._btn_remove)

        self._btn_clear = QPushButton("Clear")
        self._btn_clear.setObjectName("collectionClear")
        self._btn_clear.setCursor(Qt.PointingHandCursor)
        self._btn_clear.setEnabled(False)
        self._btn_clear.clicked.connect(self.clear_requested.emit)
        bar.addWidget(self._btn_clear)
        lay.addLayout(bar)

        # Stack: empty hint  ↔  the asset grid.
        self._stack = QStackedWidget()
        self._empty = QLabel(
            "Your collection is empty.\n\n"
            "Select assets in an Explorer, then click “Add Selection”.")
        self._empty.setObjectName("collectionEmpty")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setWordWrap(True)
        self._stack.addWidget(self._empty)

        self.browser = AssetBrowser()
        self.browser.set_card_size(card_size)
        self.browser.setContextMenuPolicy(Qt.NoContextMenu)   # no file ops in the tray
        self.browser.selection_changed.connect(self._on_sel)
        self._stack.addWidget(self.browser)
        lay.addWidget(self._stack, stretch=1)

    def current_selection(self) -> list:
        return list(self._sel)

    def display(self, materials: list, assets: list):
        """Show the current collection contents (called by the session)."""
        n = len(materials) + len(assets)
        self._count.setText(f"{n} item{'s' if n != 1 else ''}" if n else "Empty")
        self._btn_clear.setEnabled(bool(n))
        if n:
            self.browser.display_items(materials, assets)
            self._stack.setCurrentWidget(self.browser)
        else:
            self._sel = []
            self._btn_remove.setEnabled(False)
            self._stack.setCurrentWidget(self._empty)

    def _on_sel(self, count: int, items: list):
        self._sel = items
        self._btn_remove.setEnabled(count > 0)
