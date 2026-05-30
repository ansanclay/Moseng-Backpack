"""Folder navigation widgets.

FolderTreeWidget  — QTreeWidget showing the BACKPACK folder tree (left sidebar, above tags).
FolderAddressBar  — Breadcrumb bar at the top (replaces SearchBar).
"""

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QSizePolicy, QMenu, QInputDialog,
    QMessageBox, QLineEdit, QProxyStyle, QStyle, QStyleOptionViewItem,
)
from PySide6.QtCore import Qt, Signal, QTimer, QRectF
from PySide6.QtGui import QFont, QColor, QPainterPath, QPalette


class _NoBranchStyle(QProxyStyle):
    """Suppress all Windows-native branch/selection painting in QTreeWidget.

    On Windows Vista/Aero, the blue selection highlight is painted inside
    drawControl(CE_ItemViewItem) — QSS and PE_PanelItemViewItem overrides are
    ignored.  We intercept CE_ItemViewItem to paint our own background, then
    pass a copy of the option with State_Selected cleared so Windows never
    draws its blue rect.
    """
    def drawPrimitive(self, element, option, painter, widget=None):
        if element in (QStyle.PE_IndicatorBranch, QStyle.PE_PanelItemViewItem):
            return  # suppress entirely — drawControl handles background
        super().drawPrimitive(element, option, painter, widget)

    def drawControl(self, element, option, painter, widget=None):
        if element == QStyle.CE_ItemViewItem:
            sel = bool(option.state & QStyle.State_Selected)
            hov = bool(option.state & QStyle.State_MouseOver)

            # Paint our own row background — extend left to x=0 to cover branch column
            if sel or hov:
                full = option.rect
                full.setLeft(0)
                r = QRectF(full.adjusted(4, 1, -4, -1))
                path = QPainterPath()
                path.addRoundedRect(r, 5, 5)
                painter.save()
                painter.setRenderHint(painter.Antialiasing)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor("#05091e") if sel else QColor(255, 255, 255, 12))
                painter.drawPath(path)
                painter.restore()

            # 2px left accent strip for selected item (v2 design: borderLeft 2px solid accent)
            if sel:
                painter.save()
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor("#002aff"))
                painter.drawRect(0, option.rect.top(), 2, option.rect.height())
                painter.restore()

            # Strip selection flags so Windows doesn't paint its blue highlight
            opt2 = QStyleOptionViewItem(option)
            opt2.state = option.state & ~QStyle.State_Selected & ~QStyle.State_MouseOver
            if sel:
                # Keep text readable in accent colour
                opt2.palette.setColor(opt2.palette.Text, QColor("#1a3fff"))
            super().drawControl(element, opt2, painter, widget)
            return

        super().drawControl(element, option, painter, widget)

from backpack.core.folder_model import (
    FolderNode, build_folder_tree,
    add_user_folder, remove_user_folder,
)


class _CleanTree(QTreeWidget):
    """QTreeWidget that draws nothing in the branch column."""
    def drawBranches(self, painter, rect, index):
        pass  # no connecting lines, no expand arrows, no branch backgrounds


# ── FolderTreeWidget ───────────────────────────────────────────────────────

class FolderTreeWidget(QWidget):
    """Tree view of the BACKPACK folder structure."""

    folder_selected = Signal(object)   # emits FolderNode

    def __init__(self, accent: str = "#002aff", parent=None):
        super().__init__(parent)
        self.accent = accent
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

        # Header row
        header_row = QHBoxLayout()
        header_row.setContentsMargins(16, 12, 8, 6)
        title = QLabel("FOLDERS")
        title.setObjectName("sidebarTitle")
        header_row.addWidget(title)
        header_row.addStretch()
        lay.addLayout(header_row)

        # Tree — _CleanTree overrides drawBranches to draw nothing
        self._tree = _CleanTree()
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(14)
        self._tree.setAnimated(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.itemClicked.connect(self._on_item_clicked)
        # Block Windows native branch drawing (lines + branch-area bg).
        self._tree.setStyle(_NoBranchStyle(self._tree.style()))
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
        self._set_item_font(root_item, bold=True)
        root_item.setExpanded(True)

        for child in self._root_node.children:
            self._add_node(root_item, child)

        root_item.setExpanded(True)
        self._tree.expandAll()

        # Re-select previously selected node
        if self._current_node:
            self._reselect_node(self._current_node.disk_path)

    def _add_node(self, parent_item: QTreeWidgetItem, node: FolderNode):
        item = QTreeWidgetItem(parent_item, [node.display_name])
        item.setData(0, Qt.UserRole, node)

        if node.is_category:
            self._set_item_font(item, bold=True, color="#8b8e96")
        elif node.is_quixel:
            self._set_item_font(item, color="#a78bfa")  # purple tint for Quixel
        else:
            self._set_item_font(item)

        for child in node.children:
            self._add_node(item, child)

    def _set_item_font(self, item: QTreeWidgetItem, bold=False, color: str | None = None):
        font = QFont("DM Sans", 11)
        font.setStyleHint(QFont.SansSerif)
        if bold:
            font.setWeight(QFont.DemiBold)
        item.setFont(0, font)
        if color:
            item.setForeground(0, QColor(color))

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
        """Update the primary/accent colour and re-apply stylesheet + palette."""
        self.accent = color
        self._apply_accent()

    def _apply_accent(self):
        """Apply the current accent colour to the tree's stylesheet and palette."""
        a = self.accent
        # v2 design: selected item uses accentBg fill + left 2px accent border,
        # NOT a solid accent fill. Hover uses subtle white overlay.
        self._tree.setStyleSheet(f"""
            QTreeWidget {{
                background-color: transparent;
                border: none;
                color: #6f7280;
                font-family: "DM Sans", "Inter", "Segoe UI", sans-serif;
                font-size: 11.5px;
                outline: none;
                selection-background-color: transparent;
                selection-color: #1a3fff;
            }}
            QTreeWidget::item {{
                height: 28px;
                padding-left: 6px;
                border-radius: 5px;
                margin: 1px 6px;
            }}
            QTreeWidget::item:hover {{
                background-color: rgba(255,255,255,12);
                color: #cdd0df;
            }}
            QTreeWidget::item:selected,
            QTreeWidget::item:selected:active,
            QTreeWidget::item:selected:!active {{
                background-color: #05091e;
                color: #1a3fff;
            }}
            QTreeWidget::branch,
            QTreeWidget::branch:hover,
            QTreeWidget::branch:selected,
            QTreeWidget::branch:selected:active,
            QTreeWidget::branch:has-children,
            QTreeWidget::branch:has-children:selected,
            QTreeWidget::branch:has-siblings,
            QTreeWidget::branch:has-siblings:selected {{
                background: transparent;
                border: none;
                image: none;
            }}
        """)
        # Set palette highlight to accentBg so Windows can't override with system colour.
        pal = self._tree.palette()
        pal.setColor(QPalette.Highlight,       QColor("#05091e"))
        pal.setColor(QPalette.HighlightedText, QColor("#1a3fff"))
        self._tree.setPalette(pal)

    @property
    def current_node(self) -> FolderNode | None:
        return self._current_node


# ── FolderAddressBar ───────────────────────────────────────────────────────

class FolderAddressBar(QWidget):
    """Top bar: breadcrumb address + Settings / Refresh / Import buttons."""

    folder_selected  = Signal(object)   # FolderNode (from breadcrumb click)
    settings_requested = Signal()
    refresh_requested  = Signal()
    import_requested   = Signal()
    reset_requested    = Signal()
    search_changed     = Signal(str)    # text query (debounced, 200 ms)

    def __init__(self, accent: str = "#002aff", parent=None):
        super().__init__(parent)
        self.accent = accent
        self._crumb_widgets: list[QWidget] = []

        self.setObjectName("topBar")
        self.setFixedHeight(48)
        self._setup_ui()

        # Search debounce timer
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(
            lambda: self.search_changed.emit(self._search.text().strip())
        )

    def _setup_ui(self):
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(16, 0, 16, 0)
        self._layout.setSpacing(6)

        # Settings button
        self._btn_settings = QPushButton("  Settings")
        self._btn_settings.setFixedHeight(32)
        self._btn_settings.setMinimumWidth(88)
        self._btn_settings.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,10); color: #6f7280;
                border: 1px solid #101118; border-radius: 6px;
                font-size: 11.5px; padding: 4px 12px;
                font-family: "DM Sans", "Inter", "Segoe UI", sans-serif;
            }
            QPushButton:hover { color: #cdd0df; border-color: #18192a; background: rgba(255,255,255,18); }
        """)
        self._btn_settings.clicked.connect(self.settings_requested.emit)
        self._layout.addWidget(self._btn_settings)

        self._layout.addSpacing(4)

        # Breadcrumb area (stretches)
        self._crumb_container = QWidget()
        self._crumb_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._crumb_layout = QHBoxLayout(self._crumb_container)
        self._crumb_layout.setContentsMargins(8, 0, 8, 0)
        self._crumb_layout.setSpacing(0)
        self._crumb_layout.addStretch()
        self._layout.addWidget(self._crumb_container, stretch=1)

        self._layout.addSpacing(4)

        # Search input
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(28)
        self._search.setFixedWidth(180)
        self._search.setStyleSheet("""
            QLineEdit {
                background: rgba(255,255,255,8); color: #cdd0df;
                border: 1px solid #101118; border-radius: 14px;
                font-size: 11.5px; padding: 0 10px;
                font-family: "DM Sans", "Inter", "Segoe UI", sans-serif;
            }
            QLineEdit:focus { border-color: #002aff; background: rgba(255,255,255,12); }
        """)
        self._search.textChanged.connect(lambda: self._search_timer.start())
        self._layout.addWidget(self._search)

        self._layout.addSpacing(4)

        # Refresh button
        self._btn_refresh = QPushButton("\u21bb")
        self._btn_refresh.setFixedSize(32, 32)
        self._btn_refresh.setToolTip("Refresh & sync")
        self._btn_refresh.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,10); color: #6f7280;
                border: 1px solid #101118; border-radius: 6px;
                font-size: 15px;
            }
            QPushButton:hover { color: #cdd0df; border-color: #18192a; background: rgba(255,255,255,18); }
            QPushButton:pressed { color: #002aff; }
        """)
        self._btn_refresh.clicked.connect(self.refresh_requested.emit)
        self._layout.addWidget(self._btn_refresh)

        # Reset button
        self._btn_reset = QPushButton("Reset")
        self._btn_reset.setFixedHeight(32)
        self._btn_reset.setMinimumWidth(68)
        self._btn_reset.setToolTip("Delete all .json metadata files")
        self._btn_reset.setStyleSheet("""
            QPushButton {
                background: rgba(180,30,30,10); color: #6a2828;
                border: 1px solid rgba(180,30,30,25); border-radius: 6px;
                font-size: 11.5px; padding: 4px 12px;
                font-family: "DM Sans", "Inter", "Segoe UI", sans-serif;
            }
            QPushButton:hover { color: #c04040; border-color: rgba(200,40,40,60); background: rgba(180,30,30,18); }
            QPushButton:pressed { background: rgba(180,30,30,28); }
        """)
        self._btn_reset.clicked.connect(self.reset_requested.emit)
        self._layout.addWidget(self._btn_reset)

        # Import button
        self._btn_import = QPushButton("+ Import")
        self._btn_import.setObjectName("primaryBtn")
        self._btn_import.setFixedHeight(32)
        self._btn_import.setMinimumWidth(88)
        self._btn_import.clicked.connect(self.import_requested.emit)
        self._layout.addWidget(self._btn_import)

    def focus_search(self):
        """Focus the search input and select all text (Ctrl+F shortcut target)."""
        self._search.setFocus()
        self._search.selectAll()

    def clear_search(self):
        """Clear the search input without emitting (used on folder navigation)."""
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)

    def set_node(self, node: FolderNode | None):
        """Update breadcrumb display for the given node."""
        # Clear existing crumbs
        for w in self._crumb_widgets:
            self._crumb_layout.removeWidget(w)
            w.deleteLater()
        self._crumb_widgets.clear()

        if node is None:
            self._crumb_layout.addStretch()
            return

        crumbs = node.breadcrumb()
        for i, crumb in enumerate(crumbs):
            # Separator (›) before each crumb except the first
            if i > 0:
                sep = QLabel("›")
                sep.setStyleSheet("color: #4c4e58; font-size: 13px; padding: 0 2px;")
                self._crumb_layout.insertWidget(self._crumb_layout.count() - 1
                                                 if self._crumb_layout.count() else 0,
                                                 sep)
                self._crumb_widgets.append(sep)

            is_last = (i == len(crumbs) - 1)
            btn = QPushButton(crumb.display_name)
            btn.setFlat(True)
            btn.setCursor(Qt.PointingHandCursor if not is_last else Qt.ArrowCursor)
            if is_last:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        color: #cdd0df; background: transparent; border: none;
                        font-size: 12px; font-weight: 600; padding: 2px 4px;
                        font-family: "DM Sans", "Inter", "Segoe UI", sans-serif;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        color: #4c4e58; background: transparent; border: none;
                        font-size: 12px; font-weight: 500; padding: 2px 4px;
                        font-family: "DM Sans", "Inter", "Segoe UI", sans-serif;
                    }}
                    QPushButton:hover {{ color: {self.accent}; }}
                """)
                _node = crumb  # capture for lambda
                btn.clicked.connect(lambda _=False, n=_node: self.folder_selected.emit(n))

            insert_pos = self._crumb_layout.count() - 1
            self._crumb_layout.insertWidget(insert_pos, btn)
            self._crumb_widgets.append(btn)
