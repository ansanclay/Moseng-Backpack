"""Asset Grid panel — the sort/filter toolbar stacked above the thumbnail grid.

This is the only composite panel; the folder tree, filters, and inspector are
single widgets the shell docks directly.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout

from backpack.ui.asset_browser import AssetBrowser, AssetSubToolbar


class AssetGridPanel(QWidget):
    """Container exposing `.sub_toolbar` and `.browser` for the session to wire."""

    def __init__(self, card_size: int = 200, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sub_toolbar = AssetSubToolbar()
        layout.addWidget(self.sub_toolbar)

        self.browser = AssetBrowser()
        self.browser.set_card_size(card_size)
        layout.addWidget(self.browser, stretch=1)

        # View mode + compact are per-Explorer: this toolbar drives only this browser.
        self.sub_toolbar.view_changed.connect(self.browser.set_view_mode)
        self.sub_toolbar.compact_changed.connect(self.browser.set_compact)
