"""Synapse view — a force-directed graph for grasping the project at a glance.

Every asset/material is a node. Two kinds of links connect them:

  • Folder links (structure) — each item links to the folder that contains it,
    and folders link up to their parent folders, so the project's folder tree
    forms the backbone of the graph.
  • Tag links (relationships) — each item links to a hub node per tag, so items
    that share tags cluster together across folders.

Item dots and their tag links are coloured by file type; folder nodes and the
structural links are a neutral steel. Hover spotlights a node's connections;
drag/pan/zoom; double-click an item to open it, or a folder to open it in the
wired Explorer.

Performance: the simulation runs on a timer that STOPS as soon as the layout
settles and whenever the panel is hidden, and the graph is node-capped.
"""

import math
import os
import random

from PySide6.QtWidgets import QWidget, QPushButton
from PySide6.QtCore import Qt, QTimer, QPointF, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QFont

from backpack.constants import tag_color_for_name


_TYPE_COLORS = {
    "material": "#5BC0A8", "texture": "#5FB4E6", "hdri": "#B98AE6",
    "gobo": "#E6C24A", "model": "#7FCB6B", "scene": "#E8883A",
    "cache": "#E87DB0",      # pink — sim/geo caches (bgeo, vdb, …)
    "backup": "#7E848C",     # grey — anything under a *backup* folder
    "other": "#9AA0A6",
}
_TYPE_LABELS = {
    "material": "Materials", "texture": "Images", "hdri": "HDRI",
    "gobo": "Gobos", "model": "Models", "scene": "Scene files",
    "cache": "Caches", "backup": "Backups", "other": "Other",
}
_TYPE_ORDER = ["material", "texture", "model", "scene", "cache",
               "hdri", "gobo", "backup", "other"]
_FOLDER_COLOR = "#7C8794"   # neutral steel for folder nodes + structural links

# Cache file extensions (checked against the end of the filename, so the
# double-extension "bgeo.sc" matches as well as plain "bgeo").
_CACHE_EXTS = ("bgeo.sc", "bgeo.gz", "vdb.sc", "bgeo", "vdb", "sim", "geo", "pc", "sc")


def _type_color(category: str) -> str:
    return _TYPE_COLORS.get(category, _TYPE_COLORS["other"])


def _category(base_type: str, path, is_folder: bool) -> str:
    """Synapse display category: a *backup* folder wins, then a cache extension,
    otherwise the asset's own type."""
    if path is not None:
        folder = path if is_folder else path.parent
        if any("backup" in part.lower() for part in folder.parts):
            return "backup"
        if not is_folder:
            low = path.name.lower()
            if any(low.endswith("." + ext) for ext in _CACHE_EXTS):
                return "cache"
    return base_type


class _Node:
    __slots__ = ("x", "y", "vx", "vy", "fx", "fy",
                 "label", "kind", "ref", "r", "color", "degree")

    def __init__(self, label, kind, ref, color, r):
        self.x = self.y = 0.0
        self.vx = self.vy = self.fx = self.fy = 0.0
        self.label = label
        self.kind = kind          # "tag" | "folder" | "material" | "asset"
        self.ref = ref            # folder disk_path, or the scanned item
        self.color = color
        self.r = r
        self.degree = 0


class SynapseView(QWidget):
    folder_navigated = Signal(object)   # folder disk_path (double-click a folder)
    node_activated = Signal(object)     # scanned item (double-click an item)

    MAX_ITEMS = 140        # default cap (most-connected item LEAVES)
    _SHOW_ALL_MAX = 300    # item-leaf ceiling when "Show all" is on
    _FOLDER_MAX = 300      # folder-node ceiling (folders come from ALL items)
    _MAX_FOLDER_DEPTH = 24
    _CLICK_PX = 5

    _K_REP = 5200.0
    _K_ATTR = 0.02
    _REST = 60.0
    _K_CENTER = 0.012
    _DAMP = 0.86
    _MAX_SPEED = 28.0
    _SETTLE = 0.04

    def __init__(self, accent="#C4A84A", bg="#171E1B", secondary="#F2EEDC", parent=None):
        super().__init__(parent)
        self.setObjectName("synapsePanel")
        self._accent = QColor(accent)
        self._bg = QColor(bg)
        self._fg = QColor(secondary)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._nodes: list[_Node] = []
        self._edges: list[tuple[int, int, bool]] = []   # (a, b, is_structural)
        self._adj: list[set] = []
        self._present_cats: set = set()
        self._truncated = (0, 0)
        self._materials: list = []      # full item lists (for re-cap on toggle)
        self._assets: list = []
        self._folder_count = 0

        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._pan_ready = False
        self._hover = -1
        self._drag_node = -1
        self._panning = False
        self._press_pos = QPointF()
        self._press_node = -1

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._step)

        # Floating toggle: keep every connection at full strength (no hover focus).
        self._show_all = False
        self._btn_all = QPushButton("Show all connections", self)
        self._btn_all.setObjectName("synapseShowAll")
        self._btn_all.setCheckable(True)
        self._btn_all.setCursor(Qt.PointingHandCursor)
        self._btn_all.setToolTip("Graph every item in the folder "
                                 "(lift the most-connected display cap)")
        self._btn_all.toggled.connect(self._on_show_all)
        self._btn_all.adjustSize()

    def _on_show_all(self, checked):
        self._show_all = checked
        self._rebuild()    # re-cap: show every item (or fall back to the cap)

    def _place_button(self):
        self._btn_all.adjustSize()
        self._btn_all.move(self.width() - self._btn_all.width() - 10, 8)
        self._btn_all.raise_()

    def set_theme(self, accent, bg, secondary=None):
        self._accent = QColor(accent)
        self._bg = QColor(bg)
        if secondary:
            self._fg = QColor(secondary)
        self.update()

    def has_data(self) -> bool:
        return bool(self._nodes)

    # ── build ──────────────────────────────────────────────────────────────────
    def build(self, materials: list, assets: list):
        self._materials = list(materials)
        self._assets = list(assets)
        self._rebuild()

    def _rebuild(self):
        self._timer.stop()
        raw = [("material", m, m.name, list(m.meta.tags), m.meta.favorite,
                _category("material", getattr(m, "path", None), True),
                getattr(m, "path", None)) for m in self._materials]
        raw += [("asset", a, a.filename, list(a.meta.tags), a.meta.favorite,
                 _category(getattr(a, "asset_type", "other") or "other",
                           getattr(a, "path", None), False),
                 getattr(a, "path", None)) for a in self._assets]
        total = len(raw)

        nodes: list[_Node] = []
        edges: list[tuple[int, int, bool]] = []
        edge_set: set = set()
        tag_idx: dict[str, int] = {}
        folder_idx: dict[str, int] = {}

        def tag_node(tag):
            i = tag_idx.get(tag)
            if i is None:
                i = len(nodes)
                tag_idx[tag] = i
                nodes.append(_Node(tag, "tag", None, QColor(tag_color_for_name(tag)), 7.0))
            return i

        def folder_node(path):
            key = str(path)
            i = folder_idx.get(key)
            if i is None:
                if len(folder_idx) >= self._FOLDER_MAX:
                    return None
                i = len(nodes)
                folder_idx[key] = i
                label = (path.name or str(path)).replace("_", " ")
                nodes.append(_Node(label, "folder", path, QColor(_FOLDER_COLOR), 6.0))
            return i

        def link_edge(a, b, structural):
            e = (a, b, structural)
            if e not in edge_set:
                edge_set.add(e)
                edges.append(e)
                nodes[a].degree += 1
                nodes[b].degree += 1

        # Folder backbone stops at the common ancestor of ALL item folders.
        parents = [t[6].parent for t in raw if t[6] is not None]
        common = None
        if parents:
            try:
                common = os.path.commonpath([str(p) for p in parents])
            except (ValueError, OSError):
                common = None

        def folder_chain(parent):
            """Folder node for *parent* plus the edges up to the common ancestor."""
            fi = folder_node(parent)
            cur = parent
            steps = 0
            while (common is not None and str(cur) != str(common)
                   and cur != cur.parent and steps < self._MAX_FOLDER_DEPTH):
                pi = folder_node(cur.parent)
                ci = folder_idx.get(str(cur))
                if pi is not None and ci is not None:
                    link_edge(ci, pi, True)
                cur = cur.parent
                steps += 1
            return fi

        # 1) Build the FULL folder structure from ALL items, so every folder
        #    shows regardless of the item-leaf cap below.
        for t in raw:
            if t[6] is not None:
                folder_chain(t[6].parent)
        self._folder_count = len(folder_idx)

        # 2) Item leaf nodes are capped (they dominate the O(n^2) layout cost).
        cap = self._SHOW_ALL_MAX if self._show_all else self.MAX_ITEMS
        items = raw
        if total > cap:
            items = sorted(raw, key=lambda t: len(t[3]) + (1 if t[4] else 0),
                           reverse=True)[:cap]
        self._truncated = (total, len(items))
        self._present_cats = set()

        # 3) Items + tag hubs, linked to their (already-built) folder nodes.
        for kind, obj, label, tags, fav, cat, path in items:
            self._present_cats.add(cat)
            idx = len(nodes)
            nodes.append(_Node(label, kind, obj, QColor(_type_color(cat)), 5.0))
            for tg in list(tags) + (["Favorites"] if fav else []):
                link_edge(idx, tag_node(tg), False)
            if path is not None:
                fi = folder_node(path.parent)
                if fi is not None:
                    link_edge(idx, fi, True)

        for n in nodes:
            if n.kind == "tag":
                n.r = 6.0 + math.sqrt(n.degree) * 2.2
            elif n.kind == "folder":
                n.r = 6.0 + math.sqrt(n.degree) * 1.8

        self._nodes = nodes
        self._edges = edges
        self._adj = [set() for _ in nodes]
        for a, b, _s in edges:
            self._adj[a].add(b)
            self._adj[b].add(a)
        self._hover = self._drag_node = -1

        rnd = random.Random(1234)
        for i, n in enumerate(nodes):
            ang = (i / max(1, len(nodes))) * math.tau
            rad = 40 + rnd.uniform(0, 240)
            n.x = math.cos(ang) * rad + rnd.uniform(-20, 20)
            n.y = math.sin(ang) * rad + rnd.uniform(-20, 20)
            n.vx = n.vy = 0.0

        if nodes:
            self._start_sim()
        self.update()

    def _start_sim(self):
        if self.isVisible() and not self._timer.isActive():
            self._timer.start()

    # ── physics ──────────────────────────────────────────────────────────────
    def _step(self):
        nodes = self._nodes
        n = len(nodes)
        if n == 0:
            self._timer.stop()
            return
        for nd in nodes:
            nd.fx = nd.fy = 0.0
        krep = self._K_REP
        for i in range(n):
            a = nodes[i]
            ax, ay = a.x, a.y
            for j in range(i + 1, n):
                b = nodes[j]
                dx = ax - b.x
                dy = ay - b.y
                d2 = dx * dx + dy * dy
                if d2 < 0.01:
                    dx = (i - j) * 0.1 + 0.1
                    dy = 0.1
                    d2 = dx * dx + dy * dy
                inv = 1.0 / math.sqrt(d2)
                f = krep / d2
                fx = dx * inv * f
                fy = dy * inv * f
                a.fx += fx
                a.fy += fy
                b.fx -= fx
                b.fy -= fy
        kattr, rest = self._K_ATTR, self._REST
        for ia, ib, _s in self._edges:
            a = nodes[ia]
            b = nodes[ib]
            dx = b.x - a.x
            dy = b.y - a.y
            dist = math.sqrt(dx * dx + dy * dy) + 1e-6
            f = kattr * (dist - rest)
            ux = dx / dist * f
            uy = dy / dist * f
            a.fx += ux
            a.fy += uy
            b.fx -= ux
            b.fy -= uy
        kc, damp, vmax = self._K_CENTER, self._DAMP, self._MAX_SPEED
        ke = 0.0
        for i, nd in enumerate(nodes):
            if i == self._drag_node:
                nd.vx = nd.vy = 0.0
                continue
            nd.fx += -nd.x * kc
            nd.fy += -nd.y * kc
            vx = (nd.vx + nd.fx) * damp
            vy = (nd.vy + nd.fy) * damp
            sp = math.hypot(vx, vy)
            if sp > vmax:
                vx *= vmax / sp
                vy *= vmax / sp
            nd.vx, nd.vy = vx, vy
            nd.x += vx
            nd.y += vy
            ke += vx * vx + vy * vy
        if ke / n < self._SETTLE:
            self._timer.stop()
        self.update()

    # ── transforms ──────────────────────────────────────────────────────────────
    def _ensure_pan(self):
        if not self._pan_ready and self.width() > 0:
            self._pan = QPointF(self.width() / 2.0, self.height() / 2.0)
            self._pan_ready = True

    def _to_screen(self, x, y):
        return QPointF(x * self._zoom + self._pan.x(), y * self._zoom + self._pan.y())

    def _to_world(self, p):
        return QPointF((p.x() - self._pan.x()) / self._zoom,
                       (p.y() - self._pan.y()) / self._zoom)

    def _node_at(self, p):
        w = self._to_world(p)
        best, bestd = -1, 1e18
        for i, nd in enumerate(self._nodes):
            dx = nd.x - w.x()
            dy = nd.y - w.y()
            d = dx * dx + dy * dy
            if d <= (nd.r + 5) ** 2 and d < bestd:
                best, bestd = i, d
        return best

    # ── events ──────────────────────────────────────────────────────────────────
    def showEvent(self, e):
        super().showEvent(e)
        self._ensure_pan()
        self._place_button()
        if self._nodes:
            self._start_sim()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def resizeEvent(self, e):
        if not self._pan_ready:
            self._ensure_pan()
        self._place_button()
        super().resizeEvent(e)

    def wheelEvent(self, e):
        before = self._to_world(e.position())
        self._zoom = max(0.2, min(4.0, self._zoom * (1.0015 ** e.angleDelta().y())))
        after = self._to_world(e.position())
        self._pan += QPointF((after.x() - before.x()) * self._zoom,
                             (after.y() - before.y()) * self._zoom)
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self._ensure_pan()
        self._press_pos = e.position()
        idx = self._node_at(e.position())
        if idx >= 0:
            self._drag_node = idx
        else:
            self._panning = True

    def mouseMoveEvent(self, e):
        if self._drag_node >= 0:
            w = self._to_world(e.position())
            nd = self._nodes[self._drag_node]
            nd.x, nd.y = w.x(), w.y()
            nd.vx = nd.vy = 0.0
            self._start_sim()
            self.update()
        elif self._panning:
            self._pan += e.position() - self._press_pos
            self._press_pos = e.position()
            self.update()
        else:
            idx = self._node_at(e.position())
            if idx != self._hover:
                self._hover = idx
                self.update()

    def mouseReleaseEvent(self, e):
        self._drag_node = -1
        self._panning = False

    def mouseDoubleClickEvent(self, e):
        idx = self._node_at(e.position())
        if idx < 0:
            return
        nd = self._nodes[idx]
        if nd.kind == "folder" and nd.ref is not None:
            self.folder_navigated.emit(nd.ref)
        elif nd.ref is not None:
            self.node_activated.emit(nd.ref)

    # ── paint ──────────────────────────────────────────────────────────────────
    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), self._bg)
        p.setRenderHint(QPainter.Antialiasing, True)
        if not self._nodes:
            p.setPen(QColor(150, 154, 146))
            p.setFont(QFont("DM Sans", 11))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Nothing to graph.\nWire an Explorer into this panel "
                       "and open a folder.")
            p.end()
            return
        self._ensure_pan()
        nodes = self._nodes
        hover = self._hover
        hl = self._adj[hover] if hover >= 0 else None

        for ia, ib, structural in self._edges:
            a = nodes[ia]
            b = nodes[ib]
            pa = self._to_screen(a.x, a.y)
            pb = self._to_screen(b.x, b.y)
            active = hover >= 0 and (ia == hover or ib == hover)
            base = QColor(_FOLDER_COLOR) if structural else a.color
            if active:
                col = QColor(self._accent); col.setAlpha(220); w = 1.8
            elif hover >= 0:
                col = QColor(base); col.setAlpha(20); w = 1.0
            else:
                col = QColor(base); col.setAlpha(110 if structural else 85)
                w = 1.4 if structural else 1.1
            p.setPen(QPen(col, w))
            p.drawLine(pa, pb)

        p.setFont(QFont("DM Sans", 8))
        for i, nd in enumerate(nodes):
            ps = self._to_screen(nd.x, nd.y)
            r = nd.r * self._zoom
            dim = hover >= 0 and i != hover and (hl is None or i not in hl)
            fill = QColor(nd.color)
            if dim:
                fill.setAlpha(70)
            p.setBrush(fill)
            if i == hover:
                p.setPen(QPen(self._accent, 2.0))
            elif nd.kind == "folder":
                p.setPen(QPen(QColor(_FOLDER_COLOR).darker(150), 1.0))
            else:
                p.setPen(QPen(QColor(0, 0, 0, 120), 1.0))
            # folders drawn as rounded squares, items/tags as circles
            if nd.kind == "folder":
                p.drawRoundedRect(ps.x() - r, ps.y() - r, r * 2, r * 2, 3, 3)
            else:
                p.drawEllipse(ps, r, r)

            show = (nd.kind in ("tag", "folder") and self._zoom > 0.5) or i == hover \
                or (hl is not None and i in hl) or self._zoom > 1.6
            if show and not dim:
                lbl = nd.label if len(nd.label) <= 22 else nd.label[:21] + "…"
                if nd.kind == "folder":
                    p.setPen(self._fg)
                elif nd.kind == "tag":
                    p.setPen(self._fg)
                else:
                    p.setPen(QColor(170, 174, 166))
                p.drawText(QPointF(ps.x() + r + 4, ps.y() + 3), lbl)

        # Legend: file-type colour key + folder swatch.
        present = [c for c in _TYPE_ORDER if c in self._present_cats]
        yy = 16.0
        p.setFont(QFont("DM Sans", 8))
        for cat in present:
            p.setBrush(QColor(_TYPE_COLORS[cat])); p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(16, yy), 5, 5)
            p.setPen(QColor(190, 194, 186)); p.drawText(QPointF(28, yy + 4), _TYPE_LABELS[cat])
            yy += 18
        if any(n.kind == "folder" for n in nodes):
            p.setBrush(QColor(_FOLDER_COLOR)); p.setPen(Qt.NoPen)
            p.drawRoundedRect(11, yy - 5, 10, 10, 2, 2)
            p.setPen(QColor(190, 194, 186)); p.drawText(QPointF(28, yy + 4), "Folders")

        total, shown = self._truncated
        if total > shown:
            note = f"Showing {shown} of {total} items"
            if not self._show_all:
                note += "  ·  ‘Show all connections’ to graph them all"
            p.setPen(QColor(150, 154, 146))
            p.setFont(QFont("DM Sans", 9))
            p.drawText(QPointF(10, self.height() - 10), note)
        p.end()
