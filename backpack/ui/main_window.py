"""Main application window — a 3D-app-style dockable-panel shell.

MainWindow is now a thin chrome shell: a top address bar, a QtAds CDockManager
hosting four dockable panels (Folders, Filters, Assets, Inspector), a status bar
with a sync progress strip, a Window menu, and drag-drop import. All coordination
logic lives in LibrarySession (`library_session.py`).
"""

import base64
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStatusBar, QProgressBar,
    QApplication, QToolButton, QMenu, QPushButton, QLabel, QGraphicsOpacityEffect,
)
from PySide6.QtCore import (
    Qt, QByteArray, QObject, QEvent, QPropertyAnimation, QPoint, QRectF,
    QEasingCurve, QTimer,
)
from PySide6.QtGui import QShortcut, QKeySequence, QPalette, QColor, QIcon

import PySide6QtAds as ads

from backpack.core.settings import AppSettings, save_settings
from backpack.ui.folder_tree import FolderTreeWidget, FolderAddressBar, ProjectTreeWidget
from backpack.ui.tag_bar import SidebarPanel
from backpack.ui.asset_detail import AssetDetailPanel
from backpack.ui.drop_zone import DropOverlay
from backpack.ui.node_overlay import NodeGraphOverlay
from backpack.ui.quick_open import QuickOpenPalette
from backpack.ui.splitter_magnet import SplitterMagnet
from backpack.ui.dialogs.settings_dialog import SettingsDialog
from backpack.ui.library_session import LibrarySession
from backpack.ui.panels.base import make_dock
from backpack.ui.panels.asset_grid_panel import AssetGridPanel
from backpack.ui.panels.collection_panel import CollectionPanel
from backpack.ui.synapse_view import SynapseView
from backpack.ui import win_titlebar


class _TabHoverFx(QObject):
    """Smooth hover affordance for one dock tab.

    The close button is removed from the tab's layout and overlaid at the right
    edge, so the tab sizes to its text (no extra X slot, no expand on hover).
    The X is mouse-transparent + opacity 0 at rest; on hover it fades in (and
    becomes clickable) while the title text slides slightly left. One instance
    per tab; installs itself as the tab's event filter.
    """

    _SHIFT = 6        # px the title nudges left on hover
    _DURATION = 150   # ms
    _MARGIN = 4       # gap from the tab's right edge to the X

    def __init__(self, tab):
        super().__init__(tab)
        self._tab = tab
        self._btn = tab.findChild(QPushButton, "tabCloseButton")
        self._label = tab.findChild(QLabel, "dockWidgetTabLabel")
        self._base_pos = None

        if self._btn is not None:
            lay = tab.layout()
            if lay is not None:
                lay.removeWidget(self._btn)       # free the reserved slot → fit text
            self._btn.setParent(tab)
            self._btn.raise_()
            self._btn.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self._eff = QGraphicsOpacityEffect(self._btn)
            self._eff.setOpacity(0.0)
            self._btn.setGraphicsEffect(self._eff)
            self._op_anim = QPropertyAnimation(self._eff, b"opacity", self)
            self._op_anim.setDuration(self._DURATION)
            self._op_anim.setEasingCurve(QEasingCurve.OutCubic)

        if self._label is not None:
            self._pos_anim = QPropertyAnimation(self._label, b"pos", self)
            self._pos_anim.setDuration(self._DURATION)
            self._pos_anim.setEasingCurve(QEasingCurve.OutCubic)

        tab.installEventFilter(self)
        self._reposition()

    def _reposition(self):
        if self._btn is None:
            return
        x = self._tab.width() - self._btn.width() - self._MARGIN
        y = (self._tab.height() - self._btn.height()) // 2
        self._btn.move(max(0, x), max(0, y))

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.Resize:
            self._reposition()
        elif t == QEvent.Type.Enter:
            self._animate(True)
        elif t == QEvent.Type.Leave:
            self._animate(False)
        return False

    def _animate(self, on: bool):
        if self._btn is not None:
            self._reposition()
            self._btn.raise_()
            self._btn.setAttribute(Qt.WA_TransparentForMouseEvents, not on)
            self._op_anim.stop()
            self._op_anim.setStartValue(self._eff.opacity())
            self._op_anim.setEndValue(1.0 if on else 0.0)
            self._op_anim.start()
        if self._label is not None:
            if self._base_pos is None:        # capture resting position once
                self._base_pos = self._label.pos()
            target = QPoint(self._base_pos.x() - self._SHIFT, self._base_pos.y()) if on \
                else self._base_pos
            self._pos_anim.stop()
            self._pos_anim.setStartValue(self._label.pos())
            self._pos_anim.setEndValue(target)
            self._pos_anim.start()


class _AddPanelFx(QObject):
    """Hover affordance for a dock area's '+' (add-panel) button.

    The button lives at the right end of the area's title bar. It is invisible
    and non-interactive at rest; hovering anywhere over the title bar fades it
    in (and makes it clickable). It stays visible while its menu is open.
    One instance per dock area; parented to the title bar so it dies with it.
    """

    _DURATION = 150   # ms

    def __init__(self, title_bar, button):
        super().__init__(title_bar)
        self._tb = title_bar
        self._btn = button
        self._eff = QGraphicsOpacityEffect(button)
        self._eff.setOpacity(0.0)
        button.setGraphicsEffect(self._eff)
        button.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._anim = QPropertyAnimation(self._eff, b"opacity", self)
        self._anim.setDuration(self._DURATION)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.timeout.connect(self._maybe_hide)
        title_bar.installEventFilter(self)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.Enter:
            self._leave_timer.stop()
            self._fade(True)
        elif t == QEvent.Type.Leave:
            self._leave_timer.start(60)   # debounce: tolerate moving onto child
        return False

    def _maybe_hide(self):
        menu = self._btn.menu()
        if (menu and menu.isVisible()) or self._tb.underMouse():
            return
        self._fade(False)

    def _fade(self, on: bool):
        self._btn.setAttribute(Qt.WA_TransparentForMouseEvents, not on)
        self._anim.stop()
        self._anim.setStartValue(self._eff.opacity())
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()


class _OverlayFade(QObject):
    """Fades a QtAds drop overlay in when it appears during a panel drag.

    QtAds pops the drop indicator (the white wash + direction cross) in
    instantly when the cursor enters a drop region. A short opacity ramp on
    each Show makes the panel-editing experience feel smooth instead of snappy.
    One instance per overlay; parented to the overlay so it dies with it.
    """

    _DURATION = 130   # ms

    def __init__(self, overlay):
        super().__init__(overlay)
        self._eff = QGraphicsOpacityEffect(overlay)
        self._eff.setOpacity(1.0)
        overlay.setGraphicsEffect(self._eff)
        self._anim = QPropertyAnimation(self._eff, b"opacity", self)
        self._anim.setDuration(self._DURATION)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        overlay.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Show:
            self._anim.stop()
            self._eff.setOpacity(0.0)
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.start()
        return False


class _TitleBar(QWidget):
    """Custom title bar for the frameless window. Dragging an empty area moves
    the window (native move/snap via startSystemMove); double-click maximises.
    Interactive children (buttons, breadcrumb) handle their own clicks."""

    def __init__(self, win):
        super().__init__()
        self.setObjectName("topBar")
        self._win = win

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            wh = self._win.windowHandle()
            if wh is not None:
                wh.startSystemMove()
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._win._toggle_max()
            event.accept()


class MainWindow(QMainWindow):

    _RESIZE_MARGIN = 6     # px band around the frameless window edges for resizing

    def __init__(self, settings: AppSettings):
        super().__init__()
        self.settings = settings
        self.session = LibrarySession(settings)

        self.setWindowTitle("Moseng Backpack")
        self.setWindowFlag(Qt.FramelessWindowHint, True)   # custom title bar
        self._frameless = True
        self._resize_cursor = None
        self.setMinimumSize(1100, 700)
        self.resize(settings.window_width, settings.window_height)

        self._setup_ui()
        self._bind_session()
        self._restore_layout()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        accent = self.settings.accent_color

        # Central container: [top bar] [dock manager] [progress bar]
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # Unified top strip — replaces the native menu bar + address bar
        self.menuBar().hide()
        self._top_bar = self._build_top_bar(accent)
        vbox.addWidget(self._top_bar)

        self.address_bar = self._top_bar_address_bar

        # Remove the dock-area title-bar buttons (tabs-menu ▾, undock, close);
        # closing is done via the per-tab close button that appears on hover.
        _F = ads.CDockManager.eConfigFlag
        ads.CDockManager.setConfigFlag(_F.DockAreaHasCloseButton, False)
        ads.CDockManager.setConfigFlag(_F.DockAreaHasUndockButton, False)
        ads.CDockManager.setConfigFlag(_F.DockAreaHasTabsMenuButton, False)
        ads.CDockManager.setConfigFlag(_F.AllTabsHaveCloseButton, True)
        # Smoother panel-moving experience:
        #  • a live snapshot of the panel follows the cursor while dragging
        #  • that preview morphs to the target drop zone so you SEE where it lands
        #  • panels resize live (no ghost line) when dragging splitters
        #  • double-click a tab to float the panel
        ads.CDockManager.setConfigFlag(_F.DragPreviewShowsContentPixmap, True)
        ads.CDockManager.setConfigFlag(_F.DragPreviewIsDynamic, True)
        ads.CDockManager.setConfigFlag(_F.DragPreviewHasWindowFrame, False)
        ads.CDockManager.setConfigFlag(_F.OpaqueSplitterResize, True)
        ads.CDockManager.setConfigFlag(_F.DoubleClickUndocksWidget, True)
        # Highlight the panel you're interacting with (focused tab/title get a
        # styleable `focused="true"` state — see style.qss).
        ads.CDockManager.setConfigFlag(_F.FocusHighlighting, True)

        self.dock_manager = ads.CDockManager()
        # QtAds applies its own built-in (light) stylesheet to the manager
        # instance, which overrides our app-wide themed QSS. Clear it so the
        # `ads--*` rules in style.qss (driven by the user's colour variables)
        # apply instead.
        self.dock_manager.setStyleSheet("")
        # The drop-preview rectangle that washes over the target panel while
        # dragging is drawn from the overlay's palette Highlight (a clashing
        # bright blue by default). Recolor it to the theme's light tone so it
        # reads as a soft white overlay on the panel.
        self._theme_drop_overlays()
        # Fade the drop indicators in (instead of popping) while dragging panels.
        self._overlay_fades = [
            _OverlayFade(self.dock_manager.containerOverlay()),
            _OverlayFade(self.dock_manager.dockAreaOverlay()),
        ]
        # Give every dock area (current + future) a hover '+' add-panel button.
        self._add_fx: list[_AddPanelFx] = []
        self.dock_manager.dockAreaCreated.connect(self._on_dock_area_created)
        # Magnetic splitter resizing — boundaries snap to alignment + ratios.
        self._magnet = SplitterMagnet(self.dock_manager)
        vbox.addWidget(self.dock_manager, stretch=1)

        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(3)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.hide()
        vbox.addWidget(self._progress_bar)

        self.setCentralWidget(container)
        self._container = container

        # Per-kind instance counter → unique objectNames for duplicate panels.
        self._panel_counts: dict[str, int] = {}

        # Build the four initial panels via the same factory used by the '+'
        # button, so the default set and runtime duplicates are created and
        # registered identically.
        self._dock_folders   = self._create_panel("Assets")
        self._dock_project   = self._create_panel("Project")
        self._dock_filters   = self._create_panel("Filters")
        self._dock_assets    = self._create_panel("Explorer")
        self._dock_inspector = self._create_panel("Inspector")
        self._docks = [self._dock_folders, self._dock_project, self._dock_filters,
                       self._dock_assets, self._dock_inspector]

        # Default layout: left column [Assets+Project tabs / Filters], centre
        # [Explorer], right [Inspector].
        left_area = self.dock_manager.addDockWidget(
            ads.DockWidgetArea.LeftDockWidgetArea, self._dock_folders)
        self.dock_manager.addDockWidgetTabToArea(self._dock_project, left_area)
        self.dock_manager.addDockWidget(
            ads.DockWidgetArea.BottomDockWidgetArea, self._dock_filters, left_area)
        self.dock_manager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea, self._dock_assets)
        self.dock_manager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea, self._dock_inspector)
        self._dock_folders.setAsCurrentTab()

        # Smooth hover affordance per tab (fade-in X + title nudge, no resize).
        self._tab_fx = [_TabHoverFx(dock.tabWidget()) for dock in self._docks]

        # Capture the default layout so "Reset Layout" can restore it.
        self._default_state = self.dock_manager.saveState()

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready")

        # Window menu
        self._build_window_menu()

        # Drop overlay (drag-drop import)
        self.drop_overlay = DropOverlay(container)
        self.setAcceptDrops(True)

        # Node-graph overlay (Tab to toggle) — panels as nodes, wire data flow.
        self.node_overlay = NodeGraphOverlay(container, self, self.session, accent,
                                             self.settings.bg_color)
        self.node_overlay.hide()
        QApplication.instance().installEventFilter(self)

        # Ctrl+F → focus the (first) Explorer search
        sc = QShortcut(QKeySequence("Ctrl+F"), self)
        sc.activated.connect(self.session.focus_search)

        # Quick-Open palette (Ctrl+K) — fuzzy-jump to any folder in the library.
        self.quick_open = QuickOpenPalette(container, accent, self.settings.bg_color)
        self.quick_open.hide()
        self.quick_open.chosen.connect(self.session.navigate_to_path)
        sc_k = QShortcut(QKeySequence("Ctrl+K"), self)
        sc_k.activated.connect(self._open_quick_open)

    def _open_quick_open(self):
        folders = self.session.library_folders()
        if not folders:
            return
        self.quick_open.setGeometry(self._container.rect())
        self.quick_open.open(folders)

    def _build_top_bar(self, accent: str) -> QWidget:
        """Custom title bar (frameless window):
        [icon] Moseng Backpack  [Window▾] [Settings] [breadcrumb] [— ▢ ✕]."""
        bar = _TitleBar(self)
        bar.setFixedHeight(34)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 0, 0, 0)   # flush right edge for window buttons
        lay.setSpacing(2)

        # App icon + title (the drag handle)
        _ico = Path(__file__).parent / "resources" / "icon.ico"
        if _ico.exists():
            icon_lbl = QLabel(bar)
            icon_lbl.setPixmap(QIcon(str(_ico)).pixmap(16, 16))
            lay.addWidget(icon_lbl)
        title_lbl = QLabel("Moseng Backpack", bar)
        title_lbl.setObjectName("titleLabel")
        lay.addWidget(title_lbl)
        lay.addSpacing(12)

        # Window menu button
        self._panel_menu = QMenu(self)
        win_btn = QToolButton(bar)
        win_btn.setObjectName("topBarBtn")
        win_btn.setText("Window")
        win_btn.setToolTip("Panels")
        win_btn.setPopupMode(QToolButton.InstantPopup)
        win_btn.setMenu(self._panel_menu)
        lay.addWidget(win_btn)

        # Settings button
        settings_btn = QPushButton("Settings", bar)
        settings_btn.setObjectName("topBarBtn")
        settings_btn.setFixedHeight(24)
        settings_btn.clicked.connect(self._open_settings)
        lay.addWidget(settings_btn)

        lay.addSpacing(4)

        # Breadcrumb (expands to fill remaining space)
        self._top_bar_address_bar = FolderAddressBar(accent, bar)
        lay.addWidget(self._top_bar_address_bar, stretch=1)

        # Window controls: minimize / maximize-restore / close
        self._btn_min = QPushButton("–", bar)       # –
        self._btn_min.setObjectName("winMin")
        self._btn_min.setToolTip("Minimize")
        self._btn_min.clicked.connect(self.showMinimized)
        self._btn_max = QPushButton("□", bar)       # □
        self._btn_max.setObjectName("winMax")
        self._btn_max.setToolTip("Maximize")
        self._btn_max.clicked.connect(self._toggle_max)
        self._btn_close = QPushButton("✕", bar)     # ✕
        self._btn_close.setObjectName("winClose")
        self._btn_close.setToolTip("Close")
        self._btn_close.clicked.connect(self.close)
        for b in (self._btn_min, self._btn_max, self._btn_close):
            b.setFocusPolicy(Qt.NoFocus)
            b.setFixedSize(44, 34)
            lay.addWidget(b)

        return bar

    # ── Frameless window: maximise + edge-resize ──────────────────────────────
    def _toggle_max(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "_btn_max"):
            maxd = self.isMaximized()
            self._btn_max.setText("❐" if maxd else "□")   # ❐ / □
            self._btn_max.setToolTip("Restore" if maxd else "Maximize")

    def _edges_at(self, gpos):
        """Resize edges (Qt.Edges) for a global cursor position near the frame."""
        if self.isMaximized():
            return Qt.Edges()
        r = self.frameGeometry()
        m = self._RESIZE_MARGIN
        if not (r.left() - 1 <= gpos.x() <= r.right() + 1
                and r.top() - 1 <= gpos.y() <= r.bottom() + 1):
            return Qt.Edges()
        edges = Qt.Edges()
        if abs(gpos.x() - r.left()) <= m:
            edges |= Qt.LeftEdge
        if abs(gpos.x() - r.right()) <= m:
            edges |= Qt.RightEdge
        if abs(gpos.y() - r.top()) <= m:
            edges |= Qt.TopEdge
        if abs(gpos.y() - r.bottom()) <= m:
            edges |= Qt.BottomEdge
        return edges

    @staticmethod
    def _cursor_for(edges):
        L, R, T, B = Qt.LeftEdge, Qt.RightEdge, Qt.TopEdge, Qt.BottomEdge
        if (edges & (L | T)) == (L | T) or (edges & (R | B)) == (R | B):
            return Qt.SizeFDiagCursor
        if (edges & (R | T)) == (R | T) or (edges & (L | B)) == (L | B):
            return Qt.SizeBDiagCursor
        if edges & (L | R):
            return Qt.SizeHorCursor
        if edges & (T | B):
            return Qt.SizeVerCursor
        return None

    def _clear_resize_cursor(self):
        if self._resize_cursor is not None:
            QApplication.restoreOverrideCursor()
            self._resize_cursor = None

    def _frameless_hover(self, event):
        if self.isMaximized() or not self.isActiveWindow():
            self._clear_resize_cursor()
            return
        shape = self._cursor_for(self._edges_at(event.globalPosition().toPoint()))
        if shape is None:
            self._clear_resize_cursor()
        elif shape != self._resize_cursor:
            self._clear_resize_cursor()
            QApplication.setOverrideCursor(shape)
            self._resize_cursor = shape

    def _frameless_press(self, event) -> bool:
        if (self.isMaximized() or event.button() != Qt.LeftButton
                or not self.isActiveWindow()):
            return False
        edges = self._edges_at(event.globalPosition().toPoint())
        if not edges:
            return False
        wh = self.windowHandle()
        if wh is not None:
            self._clear_resize_cursor()
            wh.startSystemResize(edges)
            return True
        return False

    def _build_window_menu(self):
        # Populate the Window menu built inside _build_top_bar.
        for dock in self._docks:
            self._panel_menu.addAction(dock.toggleViewAction())
        self._panel_menu.addSeparator()
        self._panel_menu.addAction("Reset Layout", self._reset_layout)

    # ── Per-panel '+' add button ───────────────────────────────────────────────

    def _on_dock_area_created(self, area):
        """Add a hover '+' button to a newly-created dock area's title bar.

        Clicking it opens a menu of the other panels; choosing one docks it to
        the right of this area.
        """
        title_bar = area.titleBar()
        btn = QToolButton(title_bar)
        btn.setObjectName("addPanelBtn")
        btn.setText("＋")
        btn.setToolTip("Add panel here (as a tab)")
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedWidth(22)            # square button

        menu = QMenu(btn)
        menu.aboutToShow.connect(lambda m=menu, a=area: self._populate_add_menu(m, a))
        btn.setMenu(menu)

        # Place the button immediately after the tab(s): insert it just before
        # the expanding spacer that separates the tabs from the right-side area.
        lay = title_bar.layout()
        insert_at = lay.count()
        for i in range(lay.count()):
            if type(lay.itemAt(i).widget()).__name__ == "CSpacerWidget":
                insert_at = i
                break
        title_bar.insertWidget(insert_at, btn)
        self._add_fx.append(_AddPanelFx(title_bar, btn))
        self._magnet.refresh()

    def _populate_add_menu(self, menu, area):
        menu.clear()
        for kind in self._PANEL_KINDS:
            act = menu.addAction(kind)
            act.triggered.connect(
                lambda _=False, k=kind, a=area: self._add_panel_tab(k, a))

    def _add_panel_tab(self, kind: str, area):
        """Create a NEW instance of *kind* as a TAB in the same dock area whose
        '+' was clicked (not split to the right). Each pick spawns a fresh
        panel, so a type can be opened as many times as wanted."""
        dock = self._create_panel(kind)
        self.dock_manager.addDockWidgetTabToArea(dock, area)
        dock.setAsCurrentTab()

    # ── Panel factory (initial set + duplicates) ────────────────────────────────

    _PANEL_KINDS = ["Assets", "Project", "Filters", "Explorer", "Inspector",
                    "Collection", "Synapse"]
    # Stable objectName base per kind for the FIRST instance (keeps previously
    # saved layouts restorable); duplicates get a "#n" suffix.
    _OBJ_BASE = {
        "Assets": "FoldersPanel",
        "Project": "ProjectPanel",
        "Filters": "FiltersPanel",
        "Explorer": "AssetsPanel",
        "Inspector": "InspectorPanel",
        "Collection": "CollectionPanel",
        "Synapse": "SynapsePanel",
    }

    def _create_panel(self, kind: str, obj: "str | None" = None):
        """Build a fresh panel of *kind*, register it with the session, wrap it
        in a dock and return the CDockWidget. Pass *obj* to recreate a specific
        instance (layout restore); otherwise a unique objectName is generated."""
        accent = self.settings.accent_color
        if obj is None:
            n = self._panel_counts.get(kind, 0)
            self._panel_counts[kind] = n + 1
            obj = self._OBJ_BASE[kind] if n == 0 else f"{self._OBJ_BASE[kind]}#{n + 1}"
        else:
            # Keep the per-kind counter ahead of the restored instance index.
            idx = 1
            if "#" in obj:
                try:
                    idx = int(obj.split("#")[1])
                except ValueError:
                    idx = 1
            self._panel_counts[kind] = max(self._panel_counts.get(kind, 0), idx)

        if kind == "Assets":
            w = FolderTreeWidget(accent)
            w.set_bg_color(self.settings.bg_color)
            self.session.register_assets(w, obj)
        elif kind == "Project":
            w = ProjectTreeWidget(accent)
            w.set_bg_color(self.settings.bg_color)
            self.session.register_project(w, obj)
        elif kind == "Filters":
            w = SidebarPanel(accent)
            self.session.register_filters(w, obj)
        elif kind == "Explorer":
            w = AssetGridPanel(self.settings.grid_card_size)
            w.browser.set_accent(accent)
            self.session.register_explorer(w.browser, w.sub_toolbar, obj)
        elif kind == "Inspector":
            w = AssetDetailPanel()
            self.session.register_inspector(w, obj)
        elif kind == "Collection":
            w = CollectionPanel(min(self.settings.grid_card_size, 150))
            w.browser.set_accent(accent)
            self.session.register_collection(w, obj)
        elif kind == "Synapse":
            w = SynapseView(accent, self.settings.bg_color, self.settings.secondary_color)
            self.session.register_synapse(w, obj)
        else:
            raise ValueError(f"unknown panel kind: {kind}")

        return make_dock(self.dock_manager, kind, w, obj)

    def _bind_session(self):
        """Hand the shell chrome to the session. Panels were already registered
        with the session as they were created in _create_panel()."""
        self.session.bind(
            win=self,
            address_bar=self.address_bar,
            status=self.status,
            progress_bar=self._progress_bar,
        )
        self.drop_overlay.files_dropped.connect(self.session._on_drop)

    # ── Layout persistence ────────────────────────────────────────────────────

    def _restore_layout(self):
        if self.settings.keep_panels and self.settings.dock_state:
            self._recreate_saved_panels()
            try:
                raw = base64.b64decode(self.settings.dock_state.encode("ascii"))
                self.dock_manager.restoreState(QByteArray(raw))
            except Exception:
                pass   # corrupt/incompatible saved layout → keep default
            if self.settings.node_edges:
                self.session.set_edges(self.settings.node_edges)   # restore wiring
        self._magnet.refresh()

    def _recreate_saved_panels(self):
        """Recreate duplicate panels that existed last session so restoreState
        can place them (the default set already exists)."""
        existing = {d.objectName() for d in self.dock_manager.dockWidgets()}
        base_to_kind = {base: kind for kind, base in self._OBJ_BASE.items()}
        for obj in self.settings.panel_objects:
            if obj in existing:
                continue
            kind = base_to_kind.get(obj.split("#")[0])
            if kind is None:
                continue
            dock = self._create_panel(kind, obj)
            self.dock_manager.addDockWidget(ads.DockWidgetArea.RightDockWidgetArea, dock)
            existing.add(obj)

    def _reset_layout(self):
        self.dock_manager.restoreState(self._default_state)
        self._magnet.refresh()

    # ── Initialization (delegates to session) ─────────────────────────────────

    def init_drive(self, drive_letter: str, status_cb=None):
        self.session.init_drive(drive_letter, status_cb)

    # ── Settings ──────────────────────────────────────────────────────────────

    def _open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        dlg.settings_changed.connect(self._apply_settings)
        dlg.reset_requested.connect(self.session._reset_metadata)
        if dlg.exec():
            self._apply_settings()

    def _apply_settings(self):
        save_settings(self.settings)

        from backpack.app import load_stylesheet, configure_logging
        configure_logging(self.settings.debug_mode)
        load_stylesheet(
            QApplication.instance(),
            self.settings.accent_color,
            self.settings.secondary_color,
            self.settings.bg_color,
        )

        self._apply_titlebar_color()

        # Per-panel theme refresh (all instances)
        self.session.apply_theme(self.settings.accent_color, self.settings.bg_color)
        self.node_overlay.set_accent(self.settings.accent_color)
        self.node_overlay.set_bg(self.settings.bg_color)
        self._theme_drop_overlays()

        # Drive / Quixel change → reload via session
        if self.settings.drive_letter:
            root = Path(f"{self.settings.drive_letter}:/BACKPACK")
            if root != self.session.backpack_root:
                self.session.init_drive(self.settings.drive_letter)
            else:
                self.session._full_refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_titlebar_color()

    def _theme_drop_overlays(self):
        """Recolor the drag drop-preview rectangle (default: bright blue) to the
        theme's light tone, so it reads as a soft white wash over the panel."""
        hl = QColor(self.settings.secondary_color)
        for overlay in (self.dock_manager.containerOverlay(),
                        self.dock_manager.dockAreaOverlay()):
            pal = overlay.palette()
            pal.setColor(QPalette.Active, QPalette.Highlight, hl)
            pal.setColor(QPalette.Inactive, QPalette.Highlight, hl)
            overlay.setPalette(pal)

    def _apply_titlebar_color(self):
        win_titlebar.apply(
            int(self.winId()),
            self.settings.bg_color,
            self.settings.secondary_color,
        )

    # ── Drag overlay ────────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-backpack-internal"):
            event.ignore()
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.drop_overlay.show_overlay()

    def dragLeaveEvent(self, event):
        self.drop_overlay.hide_overlay()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "drop_overlay"):
            self.drop_overlay.setGeometry(self._container.rect())
        if getattr(self, "node_overlay", None) and self.node_overlay.isVisible():
            self.node_overlay.setGeometry(self._container.rect())
        if getattr(self, "quick_open", None) and self.quick_open.isVisible():
            self.quick_open.setGeometry(self._container.rect())

    # ── Node-graph overlay (Tab) ───────────────────────────────────────────────

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key_Tab and event.modifiers() == Qt.NoModifier \
                    and not self._is_text_focus():
                self._toggle_node_overlay()
                return True
            if key == Qt.Key_Escape and self.node_overlay.isVisible() \
                    and not self.node_overlay._closing:
                self.node_overlay.animate_out()
                return True
        elif self._frameless and et == QEvent.Type.MouseMove:
            self._frameless_hover(event)
        elif self._frameless and et == QEvent.Type.MouseButtonPress:
            if self._frameless_press(event):
                return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _is_text_focus() -> bool:
        fw = QApplication.focusWidget()
        if fw is None:
            return False
        return any(fw.inherits(cls) for cls in
                   ("QLineEdit", "QTextEdit", "QPlainTextEdit",
                    "QAbstractSpinBox", "QComboBox"))

    def _toggle_node_overlay(self):
        ov = self.node_overlay
        if ov.isVisible() and not ov._closing:
            ov.animate_out()
        else:
            ov.setGeometry(self._container.rect())
            ov.sync_from_session()
            ov.animate_in()

    def node_anchors(self) -> list:
        """Every open dock (including duplicates) is a node. Returns a list of
        {role, rect} — rect is the dock tab's rect in node-overlay coordinates.
        role is derived from the dock's objectName (so duplicate panels share a
        role and the same data-flow semantics)."""
        base_to_role = {base: kind.lower() for kind, base in self._OBJ_BASE.items()}
        nodes: list = []
        for dock in self.dock_manager.dockWidgets():
            role = base_to_role.get(dock.objectName().split("#")[0])
            if role is None:
                continue
            tab = dock.tabWidget()
            if tab is None or not tab.isVisible():
                continue
            tl = self.node_overlay.mapFromGlobal(tab.mapToGlobal(tab.rect().topLeft()))
            nodes.append({"key": dock.objectName(), "role": role,
                          "rect": QRectF(tl.x(), tl.y(), tab.width(), tab.height())})
        return nodes

    def closeEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        self.settings.window_width = self.width()
        self.settings.window_height = self.height()
        card = self.session.primary_card_size()
        if card is not None:
            self.settings.grid_card_size = card
        if self.settings.keep_panels:
            state = self.dock_manager.saveState()
            self.settings.dock_state = base64.b64encode(bytes(state)).decode("ascii")
            self.settings.panel_objects = [d.objectName()
                                           for d in self.dock_manager.dockWidgets()]
            self.settings.node_edges = [list(e) for e in self.session.edges()]
        else:
            self.settings.dock_state = ""        # forget layout → default next launch
            self.settings.panel_objects = []
            self.settings.node_edges = []
        save_settings(self.settings)
        super().closeEvent(event)
