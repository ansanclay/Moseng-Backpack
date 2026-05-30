"""Folder navigation widgets.

FolderTreeWidget  — QTreeWidget showing the BACKPACK folder tree (left sidebar, above tags).
FolderAddressBar  — Breadcrumb bar at the top (replaces SearchBar).
"""

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QSizePolicy, QMenu, QInputDialog,
    QMessageBox, QProxyStyle, QStyle, QStyledItemDelegate, QHeaderView,
)
from PySide6.QtCore import Qt, Signal, QRect, QSize, QTimer
from PySide6.QtGui import QFont, QColor, QPainter
from PySide6.QtWidgets import QApplication as _QApp

from backpack.ui._smooth_scroll import install_smooth_scroll
from backpack.core.folder_model import (
    FolderNode, build_folder_tree, build_project_tree,
    add_user_folder, remove_user_folder,
)


# Style primitives that paint selection / focus chrome we don't want
# anywhere — selection, row backgrounds, focus rects, branch arrows, drop hints.
_SUPPRESSED_PRIMITIVES = frozenset({
    QStyle.PE_IndicatorBranch,
    QStyle.PE_PanelItemViewItem,
    QStyle.PE_PanelItemViewRow,
    QStyle.PE_FrameFocusRect,
    QStyle.PE_IndicatorItemViewItemDrop,
})


class _TreeStyle(QProxyStyle):
    """Fusion base with all item-view chrome suppressed.

    The delegate paints every row (background + text), so the only job left
    for the style is to *not* draw the default selection/focus/branch chrome
    on top of it.
    """

    def __init__(self):
        super().__init__("fusion")

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.SH_ItemView_ShowDecorationSelected:
            return 0
        return super().styleHint(hint, option, widget, returnData)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element in _SUPPRESSED_PRIMITIVES:
            return
        super().drawPrimitive(element, option, painter, widget)


class _FolderItemDelegate(QStyledItemDelegate):
    """Single-pass row painter: one rounded selection/hover bar spanning the
    full row, text indented by depth, colour chosen per state and node type.

    Replaces the old QSS + drawBranches + palette approach (which painted the
    selection in two overlapping pieces and let per-item foreground colours
    fight the stylesheet text colour)."""

    ROW_H  = 28
    INDENT = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self.accent     = QColor("#C4A84A")   # selection bar (primary)
        self.sel_text   = QColor("#171E1B")   # text on selection (== background)
        self._text      = QColor("#B8B4A0")   # normal leaf text
        self._text_dim  = QColor("#7A7868")   # category headers
        self._hover_bg  = QColor(255, 255, 255, 20)
        self._hover_txt = QColor("#F2EEDC")

    def set_theme(self, accent: str, bg: str) -> None:
        self.accent   = QColor(accent)
        self.sel_text = QColor(bg)

    @staticmethod
    def _depth(index) -> int:
        d, p = 0, index.parent()
        while p.isValid():
            d += 1
            p = p.parent()
        return d

    def sizeHint(self, option, index):
        return QSize(60, self.ROW_H)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        node      = index.data(Qt.UserRole)
        is_cat    = bool(getattr(node, "is_category", False))
        is_quixel = bool(getattr(node, "is_quixel", False))
        is_root   = not index.parent().isValid()
        selected  = bool(option.state & QStyle.State_Selected)
        hover     = bool(option.state & QStyle.State_MouseOver)

        rect = option.rect
        # One rounded bar: from the left edge to a small right margin.
        bar = QRect(rect.x() + 3, rect.y() + 1, rect.width() - 9, rect.height() - 2)
        if selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.accent)
            painter.drawRoundedRect(bar, 5, 5)
        elif hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._hover_bg)
            painter.drawRoundedRect(bar, 5, 5)

        # Text — indented by tree depth (indentation is 0 on the view).
        tx = rect.x() + 12 + self._depth(index) * self.INDENT
        tw = max(0, bar.right() - tx - 6)

        font = QFont("DM Sans", 10)
        font.setStyleHint(QFont.SansSerif)
        if is_cat or is_root:
            font.setWeight(QFont.DemiBold)
        painter.setFont(font)

        if selected:
            col = self.sel_text
        elif is_cat:
            col = self._text_dim
        elif is_quixel:
            col = self.accent
        elif hover:
            col = self._hover_txt
        else:
            col = self._text
        painter.setPen(col)

        fm   = painter.fontMetrics()
        name = index.data(Qt.DisplayRole) or ""
        painter.drawText(tx, rect.y(), tw, rect.height(),
                         Qt.AlignVCenter | Qt.AlignLeft,
                         fm.elidedText(name, Qt.ElideRight, tw))
        painter.restore()


# ── FolderTreeWidget ───────────────────────────────────────────────────────

class FolderTreeWidget(QWidget):
    """Tree view of the BACKPACK folder structure."""

    folder_selected = Signal(object)   # emits FolderNode
    import_requested = Signal()        # Import button in the panel header

    def __init__(self, accent: str = "#C4A84A", parent=None):
        super().__init__(parent)
        self.accent = accent
        self._bg_color: str = "#1C2420"   # used for selected text colour
        self._backpack_root: Path | None = None
        self._quixel_enabled: bool = False
        self._root_node: FolderNode | None = None
        self._current_node: FolderNode | None = None

        self.setObjectName("folderTreePanel")
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Header row — title + Import button (top-right)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(16, 10, 8, 6)
        title = QLabel("ASSETS")
        title.setObjectName("sidebarTitle")
        header_row.addWidget(title)
        header_row.addStretch()
        self._btn_import = QPushButton("+ Import")
        self._btn_import.setObjectName("primaryBtn")
        self._btn_import.setFixedHeight(26)
        self._btn_import.clicked.connect(self.import_requested.emit)
        header_row.addWidget(self._btn_import)
        lay.addLayout(header_row)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(0)          # delegate handles depth indent
        self._tree.setRootIsDecorated(False)
        self._tree.setAnimated(True)
        self._tree.setMouseTracking(True)     # enables hover (State_MouseOver)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.setVerticalScrollMode(QTreeWidget.ScrollPerPixel)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tree.header().setStretchLastSection(True)
        self._tree.setStyleSheet("QTreeWidget { background: transparent; border: none; }")
        install_smooth_scroll(self._tree, px_per_notch=80, duration_ms=200)

        self._style = _TreeStyle()
        self._tree.setStyle(self._style)
        self._tree.viewport().setStyle(self._style)

        self._delegate = _FolderItemDelegate(self._tree)
        self._tree.setItemDelegate(self._delegate)

        self._apply_accent()
        lay.addWidget(self._tree, stretch=1)

    def load_tree(self, backpack_root: Path, quixel_enabled: bool = False):
        self._backpack_root = backpack_root
        self._quixel_enabled = quixel_enabled
        self._root_node = build_folder_tree(backpack_root, quixel_enabled)
        self._rebuild_tree()

    def refresh(self):
        if self._backpack_root:
            self.load_tree(self._backpack_root, self._quixel_enabled)

    def _rebuild_tree(self):
        self._tree.clear()
        if not self._root_node:
            return

        root_item = QTreeWidgetItem(self._tree, [self._root_node.display_name])
        root_item.setData(0, Qt.UserRole, self._root_node)
        root_item.setExpanded(True)

        for child in self._root_node.children:
            self._add_node(root_item, child)

        root_item.setExpanded(True)
        self._tree.expandAll()

        # Re-select previously selected node
        if self._current_node:
            self._reselect_node(self._current_node.disk_path)

    def _add_node(self, parent_item: QTreeWidgetItem, node: FolderNode):
        # Visual styling (font weight, colour, indent) is decided by
        # _FolderItemDelegate from the node stored at Qt.UserRole.
        item = QTreeWidgetItem(parent_item, [node.display_name])
        item.setData(0, Qt.UserRole, node)
        for child in node.children:
            self._add_node(item, child)

    def _on_item_clicked(self, item: QTreeWidgetItem, col: int):
        node: FolderNode = item.data(0, Qt.UserRole)
        if node:
            self._current_node = node
            self.folder_selected.emit(node)

    def _on_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if not item:
            return
        node: FolderNode = item.data(0, Qt.UserRole)
        if not node:
            return

        menu = QMenu(self)

        # Category nodes: allow adding subfolder
        if node.is_category and not node.is_quixel and node.disk_name != "BACKPACK":
            act_add = menu.addAction("Add subfolder…")
            act_add.triggered.connect(lambda: self._add_subfolder(node))

        # User-added leaf nodes: allow removal
        if node.user_added:
            act_del = menu.addAction("Remove folder")
            act_del.triggered.connect(lambda: self._remove_subfolder(node))

        if not menu.isEmpty():
            menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _add_subfolder(self, category_node: FolderNode):
        if not self._backpack_root:
            return
        name, ok = QInputDialog.getText(
            self, "Add Subfolder",
            f"New folder name under {category_node.display_name}:\n"
            "(Use underscores for spaces, e.g. My_Materials)"
        )
        if not ok or not name.strip():
            return
        name = name.strip().replace(" ", "_")
        add_user_folder(self._backpack_root, category_node.disk_name, name)
        self.refresh()

    def _remove_subfolder(self, node: FolderNode):
        if not self._backpack_root or not node.parent:
            return
        reply = QMessageBox.question(
            self, "Remove Folder",
            f'Remove "{node.display_name}" from the tree?\n\n'
            f'The folder on disk is NOT deleted.',
        )
        if reply != QMessageBox.Yes:
            return
        remove_user_folder(self._backpack_root, node.parent.disk_name, node.disk_name)
        self.refresh()

    def _reselect_node(self, disk_path: Path):
        """After rebuild, re-select the item whose node has the given disk_path."""
        def _walk(item: QTreeWidgetItem):
            node: FolderNode = item.data(0, Qt.UserRole)
            if node and node.disk_path == disk_path:
                self._tree.setCurrentItem(item)
                return True
            for i in range(item.childCount()):
                if _walk(item.child(i)):
                    return True
            return False

        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            _walk(root.child(i))

    def select_node(self, node: FolderNode):
        """Programmatically select a node (e.g. from breadcrumb click)."""
        self._current_node = node
        self._reselect_node(node.disk_path)

    def set_accent(self, color: str):
        """Update the accent colour and re-apply stylesheet + palette."""
        self.accent = color
        self._apply_accent()

    def set_bg_color(self, bg: str):
        """Selected-item text color tracks the global background colour."""
        self._bg_color = bg
        self._apply_accent()

    def _apply_accent(self):
        # Selection bar = primary; selected text reads as the canvas background.
        self._delegate.set_theme(self.accent, self._bg_color)
        self._tree.viewport().update()

    @property
    def current_node(self) -> FolderNode | None:
        return self._current_node


# ── ProjectTreeWidget ───────────────────────────────────────────────────────

class ProjectTreeWidget(QWidget):
    """Folder tree for the active *project* directory.

    Mirrors the real on-disk folders (via build_project_tree). Selecting a
    folder loads its files into the Explorer. Header offers Open / New project.
    Reuses the same delegate + style as the Assets tree for a consistent look.
    """

    folder_selected       = Signal(object)   # FolderNode
    open_project_requested = Signal()
    new_project_requested  = Signal()

    def __init__(self, accent: str = "#C4A84A", parent=None):
        super().__init__(parent)
        self.accent = accent
        self._bg_color = "#1C2420"
        self._project_root: Path | None = None
        self._root_node: FolderNode | None = None
        self._current_node: FolderNode | None = None
        self.setObjectName("folderTreePanel")
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Header — title + Open / New buttons
        header = QHBoxLayout()
        header.setContentsMargins(16, 10, 8, 6)
        title = QLabel("PROJECT")
        title.setObjectName("sidebarTitle")
        header.addWidget(title)
        header.addStretch()
        self._btn_open = QPushButton("Open")
        self._btn_open.setObjectName("topBarBtn")
        self._btn_open.setFixedHeight(24)
        self._btn_open.setToolTip("Open an existing project folder")
        self._btn_open.clicked.connect(self.open_project_requested.emit)
        header.addWidget(self._btn_open)
        self._btn_new = QPushButton("+ New")
        self._btn_new.setObjectName("primaryBtn")
        self._btn_new.setFixedHeight(24)
        self._btn_new.setToolTip("Create a new project from your template")
        self._btn_new.clicked.connect(self.new_project_requested.emit)
        header.addWidget(self._btn_new)
        lay.addLayout(header)

        # Project name / hint line
        self._name_lbl = QLabel("No project open")
        self._name_lbl.setStyleSheet(
            "color: #6f7280; font-size: 11px; padding: 0 16px 6px 16px;"
            "font-family: 'DM Mono','Consolas',monospace;")
        lay.addWidget(self._name_lbl)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(0)
        self._tree.setRootIsDecorated(False)
        self._tree.setMouseTracking(True)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.setVerticalScrollMode(QTreeWidget.ScrollPerPixel)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tree.header().setStretchLastSection(True)
        self._tree.setStyleSheet("QTreeWidget { background: transparent; border: none; }")
        install_smooth_scroll(self._tree, px_per_notch=80, duration_ms=200)

        self._style = _TreeStyle()
        self._tree.setStyle(self._style)
        self._tree.viewport().setStyle(self._style)
        self._delegate = _FolderItemDelegate(self._tree)
        self._tree.setItemDelegate(self._delegate)
        self._apply_accent()
        lay.addWidget(self._tree, stretch=1)

    def load_project(self, project_root: "Path | None"):
        self._project_root = project_root
        self._tree.clear()
        if not project_root:
            self._name_lbl.setText("No project open")
            self._root_node = None
            return
        self._name_lbl.setText(str(project_root).replace("\\", "/"))
        self._root_node = build_project_tree(project_root)
        root_item = QTreeWidgetItem(self._tree, [self._root_node.display_name])
        root_item.setData(0, Qt.UserRole, self._root_node)
        for child in self._root_node.children:
            self._add_node(root_item, child)
        self._tree.expandAll()

    def refresh(self):
        if self._project_root:
            cur = self._current_node.disk_path if self._current_node else None
            self.load_project(self._project_root)
            if cur:
                self._reselect(cur)

    def _add_node(self, parent_item, node: FolderNode):
        item = QTreeWidgetItem(parent_item, [node.display_name])
        item.setData(0, Qt.UserRole, node)
        for child in node.children:
            self._add_node(item, child)

    def _on_item_clicked(self, item, col):
        node = item.data(0, Qt.UserRole)
        if node:
            self._current_node = node
            self.folder_selected.emit(node)

    def _reselect(self, disk_path: Path):
        def _walk(item):
            n = item.data(0, Qt.UserRole)
            if n and n.disk_path == disk_path:
                self._tree.setCurrentItem(item)
                return True
            for i in range(item.childCount()):
                if _walk(item.child(i)):
                    return True
            return False
        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            if _walk(root.child(i)):
                break

    def select_node(self, node: FolderNode):
        self._current_node = node
        self._reselect(node.disk_path)

    def set_accent(self, color: str):
        self.accent = color
        self._apply_accent()

    def set_bg_color(self, bg: str):
        self._bg_color = bg
        self._apply_accent()

    def _apply_accent(self):
        self._delegate.set_theme(self.accent, self._bg_color)
        self._tree.viewport().update()

    @property
    def current_node(self) -> FolderNode | None:
        return self._current_node


# ── FolderAddressBar ───────────────────────────────────────────────────────

class _PathLabel(QLabel):
    """Read-only path label: click copies the path to clipboard with a brief flash."""

    _NORMAL = "color: #6f7280;"
    _COPIED = "color: #a0c8a0;"   # brief green tint to confirm copy

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.IBeamCursor)
        self.setToolTip("Click to copy path")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._restore_color)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.text():
            _QApp.clipboard().setText(self.text())
            self.setStyleSheet(self._base_style().replace(
                "color: #6f7280", "color: #a0c8a0"))
            self._timer.start(800)
        super().mousePressEvent(event)

    def _base_style(self) -> str:
        return """
            color: #6f7280;
            font-size: 11px;
            font-family: "DM Mono", "Consolas", "Courier New", monospace;
            background: transparent;
            padding: 0 4px;
        """

    def _restore_color(self):
        self.setStyleSheet(self._base_style())


class FolderAddressBar(QWidget):
    """Breadcrumb-only address strip — sits inside the main window top bar."""

    folder_selected = Signal(object)   # FolderNode (from breadcrumb click)

    def __init__(self, accent: str = "#002aff", parent=None):
        super().__init__(parent)
        self.accent = accent
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._setup_ui()

    def _setup_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(0)

        self._path_label = _PathLabel()
        self._path_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._path_label.setStyleSheet(self._path_label._base_style())
        lay.addWidget(self._path_label, stretch=1)

    def set_node(self, node: FolderNode | None):
        """Show the absolute disk path of the selected node."""
        if node is None:
            self._path_label.setText("")
            return
        path_str = str(node.disk_path).replace("\\", "/")
        self._path_label.setText(path_str)
