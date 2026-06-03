"""Quick-Open palette — fuzzy-jump to any folder in the library (Ctrl+K).

A centered command-palette overlay (VS Code style). Type to filter every folder
in the BACKPACK tree by name/breadcrumb; ↑/↓ to move, Enter or click to jump
there, Esc or click-outside to dismiss. It drives the same navigation path as
clicking a folder in the Assets tree, so all wired Explorers update.
"""

from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
)
from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtGui import QPainter, QColor


def _subsequence(q: str, s: str) -> bool:
    """True if every char of q appears in s in order (fuzzy match)."""
    it = iter(s)
    return all(c in it for c in q)


class QuickOpenPalette(QWidget):
    chosen = Signal(object)   # disk_path (Path) of the selected folder

    _MAX_RESULTS = 200

    def __init__(self, parent, accent: str = "#C4A84A", bg: str = "#171E1B"):
        super().__init__(parent)
        self._bg = QColor(bg)
        self._entries: list = []          # [(label, path), ...]
        self.hide()

        self._box = QFrame(self)
        self._box.setObjectName("quickOpen")
        box = QVBoxLayout(self._box)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(8)

        self._input = QLineEdit()
        self._input.setObjectName("quickOpenInput")
        self._input.setPlaceholderText("Jump to folder…")
        self._input.setClearButtonEnabled(True)
        box.addWidget(self._input)

        self._list = QListWidget()
        self._list.setObjectName("quickOpenList")
        self._list.setUniformItemSizes(True)
        self._list.setFocusPolicy(Qt.NoFocus)   # keep typing in the search field
        box.addWidget(self._list, stretch=1)

        self._input.textChanged.connect(self._refilter)
        self._input.installEventFilter(self)         # ↑/↓/Enter/Esc handling
        self._list.itemClicked.connect(lambda _i: self._choose())

    # ── open / layout ─────────────────────────────────────────────────────────
    def open(self, entries: list):
        self._entries = entries or []
        self.setGeometry(self.parent().rect())
        self._input.clear()
        self._refilter("")
        self.show()
        self.raise_()
        self._reposition()
        self._input.setFocus()

    def _reposition(self):
        w = max(360, min(580, self.width() - 80))
        h = max(220, min(460, self.height() - 120))
        self._box.setGeometry((self.width() - w) // 2,
                              max(48, (self.height() - h) // 3), w, h)

    def resizeEvent(self, _event):
        self._reposition()

    # ── filtering ──────────────────────────────────────────────────────────────
    def _refilter(self, text: str):
        q = text.strip().lower()
        self._list.clear()
        if not q:
            results = self._entries[:self._MAX_RESULTS]
        else:
            scored = []
            for label, path in self._entries:
                ll = label.lower()
                pos = ll.find(q)
                if pos >= 0:
                    scored.append((0, pos, len(label), label, path))   # substring
                elif _subsequence(q, ll):
                    scored.append((1, 0, len(label), label, path))     # fuzzy
            scored.sort(key=lambda t: (t[0], t[1], t[2]))
            results = [(lbl, p) for _, _, _, lbl, p in scored[:self._MAX_RESULTS]]

        for label, path in results:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, path)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    # ── interaction ──────────────────────────────────────────────────────────
    def eventFilter(self, obj, ev):
        if obj is self._input and ev.type() == QEvent.Type.KeyPress:
            k = ev.key()
            if k in (Qt.Key_Down, Qt.Key_Up) and self._list.count():
                row = self._list.currentRow() + (1 if k == Qt.Key_Down else -1)
                self._list.setCurrentRow(max(0, min(self._list.count() - 1, row)))
                return True
            if k in (Qt.Key_Return, Qt.Key_Enter):
                self._choose()
                return True
            if k == Qt.Key_Escape:
                self.hide()
                return True
        return False

    def _choose(self):
        item = self._list.currentItem()
        if item is not None:
            self.chosen.emit(item.data(Qt.UserRole))
        self.hide()

    def mousePressEvent(self, event):
        # Clicking the dim area (outside the box) dismisses the palette.
        if not self._box.geometry().contains(event.position().toPoint()):
            self.hide()

    def paintEvent(self, _event):
        p = QPainter(self)
        dim = QColor(self._bg)
        dim.setAlpha(175)
        p.fillRect(self.rect(), dim)
        p.end()
