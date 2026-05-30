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
    Qt, QByteArray, QObject, QEvent, QPropertyAnimation, QPoint, QEasingCurve,
    QTimer,
)
from PySide6.QtGui import QShortcut, QKeySequence

import PySide6QtAds as ads

from backpack.core.settings import AppSettings, save_settings
from backpack.ui.folder_tree import FolderTreeWidget, FolderAddressBar, ProjectTreeWidget
from backpack.ui.tag_bar import SidebarPanel
from backpack.ui.asset_detail import AssetDetailPanel
from backpack.ui.drop_zone import DropOverlay
from backpack.ui.dialogs.settings_dialog import SettingsDialog
from backpack.ui.library_session import LibrarySession
from backpack.ui.panels.base import make_dock
from backpack.ui.panels.asset_grid_panel import AssetGridPanel
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


class MainWindow(QMainWindow):

    def __init__(self, settings: AppSettings):
        super().__init__()
        self.settings = settings
        self.session = LibrarySession(settings)

        self.setWindowTitle("Moseng Backpack")
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

        self.dock_manager = ads.CDockManager()
        # QtAds applies its own built-in (light) stylesheet to the manager
        # instance, which overrides our app-wide themed QSS. Clear it so the
        # `ads--*` rules in style.qss (driven by the user's colour variables)
        # apply instead.
        self.dock_manager.setStyleSheet("")
        # Give every dock area (current + future) a hover '+' add-panel button.
        self._add_fx: list[_AddPanelFx] = []
        self.dock_manager.dockAreaCreated.connect(self._on_dock_area_created)
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

        # Ctrl+F → focus the (first) Explorer search
        sc = QShortcut(QKeySequence("Ctrl+F"), self)
        sc.activated.connect(self.session.focus_search)

    def _build_top_bar(self, accent: str) -> QWidget:
        """Single strip: [Window▾] [Settings] [breadcrumb — expands]."""
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(34)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(2)

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

        return bar

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
        btn.setToolTip("Add panel to the right")
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

    def _populate_add_menu(self, menu, area):
        menu.clear()
        for kind in self._PANEL_KINDS:
            act = menu.addAction(kind)
            act.triggered.connect(
                lambda _=False, k=kind, a=area: self._add_panel_right(k, a))

    def _add_panel_right(self, kind: str, area):
        """Create a NEW instance of *kind* and dock it to the right of *area*.

        Every pick spawns a fresh panel, so the same panel type can be opened
        as many times as wanted (duplicate panels).
        """
        dock = self._create_panel(kind)
        self.dock_manager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea, dock, area)

    # ── Panel factory (initial set + duplicates) ────────────────────────────────

    _PANEL_KINDS = ["Assets", "Project", "Filters", "Explorer", "Inspector"]
    # Stable objectName base per kind for the FIRST instance (keeps previously
    # saved layouts restorable); duplicates get a "#n" suffix.
    _OBJ_BASE = {
        "Assets": "FoldersPanel",
        "Project": "ProjectPanel",
        "Filters": "FiltersPanel",
        "Explorer": "AssetsPanel",
        "Inspector": "InspectorPanel",
    }

    def _create_panel(self, kind: str):
        """Build a fresh panel of *kind*, register it with the session, wrap it
        in a dock with a unique objectName, and return the CDockWidget."""
        accent = self.settings.accent_color
        n = self._panel_counts.get(kind, 0)
        self._panel_counts[kind] = n + 1
        obj = self._OBJ_BASE[kind] if n == 0 else f"{self._OBJ_BASE[kind]}#{n + 1}"

        if kind == "Assets":
            w = FolderTreeWidget(accent)
            w.set_bg_color(self.settings.bg_color)
            self.session.register_assets(w)
        elif kind == "Project":
            w = ProjectTreeWidget(accent)
            w.set_bg_color(self.settings.bg_color)
            self.session.register_project(w)
        elif kind == "Filters":
            w = SidebarPanel(accent)
            self.session.register_filters(w)
        elif kind == "Explorer":
            w = AssetGridPanel(self.settings.grid_card_size)
            w.browser.set_accent(accent)
            self.session.register_explorer(w.browser, w.sub_toolbar)
        elif kind == "Inspector":
            w = AssetDetailPanel()
            self.session.register_inspector(w)
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
        if self.settings.dock_state:
            try:
                raw = base64.b64decode(self.settings.dock_state.encode("ascii"))
                self.dock_manager.restoreState(QByteArray(raw))
            except Exception:
                pass   # corrupt/incompatible saved layout → keep default

    def _reset_layout(self):
        self.dock_manager.restoreState(self._default_state)

    # ── Initialization (delegates to session) ─────────────────────────────────

    def init_drive(self, drive_letter: str):
        self.session.init_drive(drive_letter)

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

    def closeEvent(self, event):
        self.settings.window_width = self.width()
        self.settings.window_height = self.height()
        card = self.session.primary_card_size()
        if card is not None:
            self.settings.grid_card_size = card
        state = self.dock_manager.saveState()
        self.settings.dock_state = base64.b64encode(bytes(state)).decode("ascii")
        save_settings(self.settings)
        super().closeEvent(event)
