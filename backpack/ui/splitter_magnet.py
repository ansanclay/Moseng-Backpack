"""Magnetic splitter resizing for the dock layout.

When the user drags a dock splitter, the moved boundary snaps when it lines up
with another panel edge (same-orientation splitter handle, anywhere in the
layout) or with a clean ratio (1/4, 1/3, 1/2, 2/3, 3/4). Gives panel boundaries
a tidy, aligned feel without constraining free resizing.

Usage:
    self._magnet = SplitterMagnet(dock_manager)
    self._magnet.refresh()   # call again whenever the layout changes
"""

from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtWidgets import QSplitter


_THRESHOLD = 12        # px — snap when the boundary is within this distance
_RATIOS = (0.25, 1 / 3, 0.5, 2 / 3, 0.75)


class SplitterMagnet(QObject):
    def __init__(self, dock_manager):
        super().__init__(dock_manager)
        self._dm = dock_manager
        self._connected: set = set()

    def refresh(self):
        """Connect any splitters created since the last call."""
        for sp in self._dm.findChildren(QSplitter):
            key = id(sp)
            if key in self._connected:
                continue
            self._connected.add(key)
            sp.splitterMoved.connect(
                lambda pos, idx, s=sp: self._on_moved(s, idx))

    # ── helpers ────────────────────────────────────────────────────────────────
    @staticmethod
    def _global_coord(sp: QSplitter, along: int) -> int:
        if sp.orientation() == Qt.Horizontal:
            return sp.mapToGlobal(QPoint(along, 0)).x()
        return sp.mapToGlobal(QPoint(0, along)).y()

    @staticmethod
    def _local_coord(sp: QSplitter, global_along: int) -> int:
        if sp.orientation() == Qt.Horizontal:
            return sp.mapFromGlobal(QPoint(global_along, 0)).x()
        return sp.mapFromGlobal(QPoint(0, global_along)).y()

    def _alignment_targets(self, sp: QSplitter) -> list:
        """Local-coordinate boundaries of every other same-orientation splitter
        handle, so dragging snaps when panel edges line up across the layout."""
        out = []
        for other in self._dm.findChildren(QSplitter):
            if other is sp or other.orientation() != sp.orientation():
                continue
            osizes = other.sizes()
            acc = 0
            for j in range(len(osizes) - 1):
                acc += osizes[j]
                g = self._global_coord(other, acc)
                out.append(self._local_coord(sp, g))
        return out

    # ── snap ─────────────────────────────────────────────────────────────────
    def _on_moved(self, sp: QSplitter, index: int):
        if sp.property("_magnet_busy"):
            return
        sizes = sp.sizes()
        if index < 1 or index >= len(sizes):
            return
        total = sum(sizes)
        if total <= 0:
            return
        boundary = sum(sizes[:index])

        candidates = [int(total * r) for r in _RATIOS]
        candidates += self._alignment_targets(sp)

        best, best_d = None, _THRESHOLD + 1
        for c in candidates:
            d = abs(c - boundary)
            if d < best_d:
                best, best_d = c, d
        if best is None or best_d > _THRESHOLD:
            return

        delta = best - boundary
        sizes[index - 1] += delta
        sizes[index] -= delta
        if sizes[index - 1] < 0 or sizes[index] < 0:
            return

        sp.setProperty("_magnet_busy", True)
        sp.setSizes(sizes)
        sp.setProperty("_magnet_busy", False)
