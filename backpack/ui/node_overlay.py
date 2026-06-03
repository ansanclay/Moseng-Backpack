"""Temporary node-graph overlay (toggled with Tab).

Every open dock panel is a node, drawn *over* the live layout and anchored to
the panel's dock tab. Each Explorer is independent — it has two input ports:

    • address (upper-left) ← Assets / Project   (which folder it shows)
    • filter  (lower-left) ← Filters            (tag / resolution filter)
    • output  (right)       → Inspector          (selection feeds the detail)

Wiring is per-instance and reconfigures real data flow on the LibrarySession.
Drag from a panel's output port to a target's input port to connect; click a
wire to remove it. Tab / Esc closes.
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPointF, QRectF, QPropertyAnimation, QEasingCurve, Property
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QFont


# Which output role can connect to which inputs — list of (target_role, port).
# An Explorer fans out to the Inspector, the Synapse graph, and the Collection.
_SRC_TO_TARGET = {
    "assets":   [("explorer", "address")],
    "project":  [("explorer", "address")],
    "filters":  [("explorer", "filter")],
    "explorer": [("inspector", "select"),
                 ("synapse", "items"),
                 ("collection", "collect")],
}
_HAS_OUT = set(_SRC_TO_TARGET.keys())

_PORT_R = 6
_HIT    = 18


class NodeGraphOverlay(QWidget):
    def __init__(self, parent, main, session, accent: str = "#C4A84A", bg: str = "#171E1B"):
        super().__init__(parent)
        self._main = main
        self._session = session
        self._accent = QColor(accent)
        self._bg = QColor(bg)
        self._wire_from = None         # (src_key, src_role, out_point) — left drag
        self._cursor = QPointF()
        self._cutting = False          # right-drag edge-cut stroke
        self._cut_pts: list = []
        self.setMouseTracking(True)

        # Open/close motion: the editor (dim + wires) fades in over the LIVE
        # panels — the panels keep running, they are not frozen. _t: 0 → 1.
        self._t = 0.0
        self._closing = False
        self._anim = QPropertyAnimation(self, b"fadeProgress", self)
        self._anim.setDuration(180)
        self._anim.finished.connect(self._on_anim_finished)

        # Magnet: while dragging a wire, snap to a compatible input port and
        # animate that port filling/growing ("approach to fill round").
        self._hover_target = None      # (key, port, point)
        self._grow = 0.0
        self._grow_anim = QPropertyAnimation(self, b"growProgress", self)
        self._grow_anim.setDuration(140)

    _MAGNET_R = 48       # snap radius while dragging a wire

    def _get_grow(self):
        return self._grow

    def _set_grow(self, v):
        self._grow = v
        self.update()

    growProgress = Property(float, _get_grow, _set_grow)

    def _magnet_target(self, npos: QPointF):
        """Nearest compatible input port within the magnet radius, else None."""
        if self._wire_from is None:
            return None
        targets = _SRC_TO_TARGET.get(self._wire_from[1])
        if not targets:
            return None
        role_port = {role: port for (role, port) in targets}
        best, best_d = None, float(self._MAGNET_R)
        for n in self._nodes():
            port = role_port.get(n["role"])
            if port is None:
                continue
            pt = self._in_point(n, port)
            if pt is None:
                continue
            d = (pt - npos).manhattanLength()
            if d < best_d:
                best_d = d
                best = (n["key"], port, pt)
        return best

    def _animate_grow(self, on: bool):
        self._grow_anim.stop()
        self._grow_anim.setEasingCurve(QEasingCurve.OutBack if on else QEasingCurve.InCubic)
        self._grow_anim.setStartValue(self._grow)
        self._grow_anim.setEndValue(1.0 if on else 0.0)
        self._grow_anim.start()

    # ── open/close animation ────────────────────────────────────────────────
    def _get_t(self):
        return self._t

    def _set_t(self, v):
        self._t = v
        self.update()

    fadeProgress = Property(float, _get_t, _set_t)

    def animate_in(self):
        self._closing = False
        self._wire_from = None
        self._hover_target = None
        self._grow = 0.0
        self._cutting = False
        self._cut_pts = []
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def animate_out(self):
        self._closing = True
        self._anim.stop()
        self._anim.setEasingCurve(QEasingCurve.InCubic)
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_anim_finished(self):
        if self._closing and self._t <= 0.02:
            self.hide()
            self._closing = False

    def set_accent(self, color: str):
        self._accent = QColor(color)
        self.update()

    def set_bg(self, color: str):
        self._bg = QColor(color)
        self.update()

    def sync_from_session(self):
        self.update()

    # ── geometry ──────────────────────────────────────────────────────────────
    def _nodes(self) -> list:
        return self._main.node_anchors()

    @staticmethod
    def _out_port(rect: QRectF) -> QPointF:
        # Output on the BOTTOM edge, inputs on the TOP edge → vertical flow.
        return QPointF(rect.center().x(), rect.bottom())

    @staticmethod
    def _in_ports(node: dict) -> dict:
        """Input ports (top edge), keyed by port name."""
        r = node["rect"]
        if node["role"] == "explorer":
            return {"address": QPointF(r.left() + r.width() * 0.30, r.top()),
                    "filter":  QPointF(r.left() + r.width() * 0.70, r.top())}
        if node["role"] == "inspector":
            return {"select": QPointF(r.center().x(), r.top())}
        if node["role"] == "synapse":
            return {"items": QPointF(r.center().x(), r.top())}
        if node["role"] == "collection":
            return {"collect": QPointF(r.center().x(), r.top())}
        return {}

    def _in_point(self, node: dict, port: str):
        return self._in_ports(node).get(port)

    # ── painting ────────────────────────────────────────────────────────────────
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        t = max(0.0, min(1.0, self._t))

        # Translucent dim over the LIVE panels (they keep running, not frozen).
        dim = QColor(self._bg)
        dim.setAlpha(int(150 * t))
        p.fillRect(self.rect(), dim)

        p.setOpacity(t)                       # nodes + wires fade in/out
        nodes = self._nodes()
        by_key = {n["key"]: n for n in nodes}

        for (sk, dk, port) in self._session.edges():
            if sk in by_key and dk in by_key:
                dst_pt = self._in_point(by_key[dk], port)
                if dst_pt is not None:
                    self._draw_edge(p, self._out_port(by_key[sk]["rect"]), dst_pt,
                                    self._accent, 2.6)

        if self._wire_from is not None:
            end = self._hover_target[2] if self._hover_target else self._cursor
            self._draw_edge(p, self._wire_from[2], end, QColor("#cdd0df"), 1.8)
            if self._hover_target is not None:
                pt = self._hover_target[2]
                g = max(0.0, self._grow)
                halo = QColor(self._accent); halo.setAlpha(int(80 * min(1.0, g)))
                p.setPen(Qt.NoPen); p.setBrush(halo)
                p.drawEllipse(pt, _PORT_R + 7 * g, _PORT_R + 7 * g)
                p.setPen(QPen(self._accent, 1.5)); p.setBrush(self._accent)
                p.drawEllipse(pt, _PORT_R + 3 * g, _PORT_R + 3 * g)

        if self._cutting and len(self._cut_pts) > 1:
            p.setPen(QPen(QColor("#ff5a5a"), 2))
            p.setBrush(Qt.NoBrush)
            cut = QPainterPath(self._cut_pts[0])
            for pt in self._cut_pts[1:]:
                cut.lineTo(pt)
            p.drawPath(cut)

        for n in nodes:
            ring = n["rect"].adjusted(-3, -2, 3, 2)
            path = QPainterPath()
            path.addRoundedRect(ring, 6, 6)
            p.setPen(QPen(self._accent, 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            if n["role"] in _HAS_OUT:
                self._draw_port(p, self._out_port(n["rect"]),
                                self._out_filled(n["key"]))
            for port, pt in self._in_ports(n).items():
                self._draw_port(p, pt, self._in_filled(n["key"], port))

        p.setOpacity(1.0)
        p.setPen(QColor("#9a9da8"))
        p.setFont(QFont("DM Sans", 9))
        p.drawText(QRectF(0, 8, self.width(), 18), Qt.AlignHCenter,
                   "Tab / Esc to close    ·    left-drag a port to wire    ·    "
                   "right-drag across wires to cut")
        p.end()

    def _out_filled(self, key) -> bool:
        return any(s == key for (s, d, port) in self._session.edges())

    def _in_filled(self, key, port) -> bool:
        return any(d == key and pp == port for (s, d, pp) in self._session.edges())

    @staticmethod
    def _lift(a: QPointF, b: QPointF) -> float:
        # S-curve depth (capped so the upper bend stays on-screen).
        return min(85.0, 45.0 + abs(b.x() - a.x()) * 0.06)

    def _draw_edge(self, p, a: QPointF, b: QPointF, color, width):
        lift = self._lift(a, b)
        path = QPainterPath(a)
        # S-curve: exit the output downward, enter the input from above.
        path.cubicTo(a.x(), a.y() + lift, b.x(), b.y() - lift, b.x(), b.y())
        p.setPen(QPen(color, width))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def _draw_port(self, p, pt: QPointF, filled: bool):
        p.setPen(QPen(self._accent, 1.5))
        p.setBrush(self._accent if filled else QColor("#0b0d12"))
        p.drawEllipse(pt, _PORT_R, _PORT_R)

    # ── interaction ─────────────────────────────────────────────────────────────
    def _out_at(self, pos: QPointF, nodes: list):
        for n in nodes:
            if n["role"] in _HAS_OUT:
                pt = self._out_port(n["rect"])
                if (pt - pos).manhattanLength() < _HIT:
                    return n["key"], n["role"], pt
        return None

    def mousePressEvent(self, event):
        pos = event.position()
        if event.button() == Qt.LeftButton:
            # Left button: ONLY start a wire from an output port (never removes).
            out = self._out_at(pos, self._nodes())
            if out is not None:
                self._wire_from = out
                self._cursor = pos
                self.update()
        elif event.button() == Qt.RightButton:
            # Right button: begin an edge-cut stroke (Houdini-style).
            self._cutting = True
            self._cut_pts = [pos]
            self.update()

    def mouseMoveEvent(self, event):
        if self._wire_from is not None:
            self._cursor = event.position()
            tgt = self._magnet_target(self._cursor)
            key = (tgt[0], tgt[1]) if tgt else None
            cur = (self._hover_target[0], self._hover_target[1]) if self._hover_target else None
            if key != cur:
                self._hover_target = tgt
                self._animate_grow(tgt is not None)
            self.update()
        elif self._cutting:
            self._cut_pts.append(event.position())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._wire_from is not None:
            src_key = self._wire_from[0]
            tgt = self._hover_target          # magnet-snapped target wins
            self._wire_from = None
            self._hover_target = None
            self._grow = 0.0
            if tgt is not None:
                self._session.set_edge(src_key, tgt[0], tgt[1], True)
            self.update()
        elif event.button() == Qt.RightButton and self._cutting:
            self._finish_cut()
            self._cutting = False
            self._cut_pts = []
            self.update()

    # ── edge cut (right-drag) ────────────────────────────────────────────────
    def _finish_cut(self):
        nodes = self._nodes()
        by_key = {n["key"]: n for n in nodes}
        pts = self._cut_pts
        length = sum((pts[i] - pts[i - 1]).manhattanLength() for i in range(1, len(pts)))
        # A plain right-click (no real drag) removes the wire under the cursor.
        if length < 6:
            if pts:
                self._remove_at(pts[-1], by_key)
            return
        for (s, d, port) in list(self._session.edges()):
            if s in by_key and d in by_key:
                dp = self._in_point(by_key[d], port)
                if dp is None:
                    continue
                bez = self._bezier_points(self._out_port(by_key[s]["rect"]), dp)
                if self._stroke_crosses(pts, bez):
                    self._session.set_edge(s, d, port, False)

    def _remove_at(self, pos: QPointF, by_key: dict):
        for (s, d, port) in self._session.edges():
            if s in by_key and d in by_key:
                dp = self._in_point(by_key[d], port)
                if dp is None:
                    continue
                bez = self._bezier_points(self._out_port(by_key[s]["rect"]), dp)
                for i in range(1, len(bez)):
                    if self._seg_dist(bez[i - 1], bez[i], pos) < 7.0:
                        self._session.set_edge(s, d, port, False)
                        return

    def _bezier_points(self, a: QPointF, b: QPointF, n: int = 24) -> list:
        lift = self._lift(a, b)
        c1 = QPointF(a.x(), a.y() + lift)
        c2 = QPointF(b.x(), b.y() - lift)
        pts = [a]
        for i in range(1, n + 1):
            t = i / n
            mt = 1 - t
            x = mt**3 * a.x() + 3*mt*mt*t * c1.x() + 3*mt*t*t * c2.x() + t**3 * b.x()
            y = mt**3 * a.y() + 3*mt*mt*t * c1.y() + 3*mt*t*t * c2.y() + t**3 * b.y()
            pts.append(QPointF(x, y))
        return pts

    @classmethod
    def _stroke_crosses(cls, stroke: list, bez: list) -> bool:
        for i in range(1, len(stroke)):
            for j in range(1, len(bez)):
                if cls._seg_intersect(stroke[i - 1], stroke[i], bez[j - 1], bez[j]):
                    return True
        return False

    @staticmethod
    def _ccw(a: QPointF, b: QPointF, c: QPointF) -> bool:
        return (c.y() - a.y()) * (b.x() - a.x()) > (b.y() - a.y()) * (c.x() - a.x())

    @classmethod
    def _seg_intersect(cls, a, b, c, d) -> bool:
        return (cls._ccw(a, c, d) != cls._ccw(b, c, d)
                and cls._ccw(a, b, c) != cls._ccw(a, b, d))

    @staticmethod
    def _seg_dist(a: QPointF, b: QPointF, p: QPointF) -> float:
        dx, dy = b.x() - a.x(), b.y() - a.y()
        if dx == 0 and dy == 0:
            return ((p.x() - a.x())**2 + (p.y() - a.y())**2) ** 0.5
        t = max(0.0, min(1.0, ((p.x() - a.x())*dx + (p.y() - a.y())*dy) / (dx*dx + dy*dy)))
        cx, cy = a.x() + t*dx, a.y() + t*dy
        return ((p.x() - cx)**2 + (p.y() - cy)**2) ** 0.5
