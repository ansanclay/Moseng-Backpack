"""Main application window - folder-based navigation."""

import os
import re
import shutil
import time
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStatusBar, QMessageBox, QApplication, QFileDialog, QInputDialog,
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QFileSystemWatcher
from PySide6.QtGui import QShortcut, QKeySequence, QPalette, QColor, QPainter, QRadialGradient

from backpack.core.settings import AppSettings, save_settings
from backpack.core.scanner import (
    scan_folder_recursive, sync_json_files,
    ScannedMaterial, ScannedAsset,
)
from backpack.core.downscale import detect_resolution_tag
from backpack.core.metadata import (
    delete_asset_meta, delete_material_meta,
    read_asset_meta, write_asset_meta, write_material_meta,
    JSON_DIR_NAME,
)
from backpack.core.preview import (
    sync_previews, clean_orphaned_previews,
    ensure_preview, generate_previews_for_folder,
)
from backpack.constants import QUIXEL_PREVIEW_PATTERNS
from backpack.core.tag_registry import (
    load_tag_registry, save_tag_registry, get_or_create_tag, set_tag_head, TagInfo,
)
from backpack.core.folder_model import FolderNode, build_folder_tree
from backpack.ui.folder_tree import FolderTreeWidget, FolderAddressBar
from backpack.ui.tag_bar import SidebarPanel
from backpack.ui.asset_browser import AssetBrowser, AssetSubToolbar
from backpack.ui.asset_detail import AssetDetailPanel
from backpack.ui.drop_zone import DropOverlay
from backpack.ui.dialogs.import_dialog import ImportDialog
from backpack.ui.dialogs.settings_dialog import SettingsDialog


class _OrbBackground(QWidget):
    """Central widget that paints soft ambient orb gradients behind all panels."""

    def __init__(self, primary: str = "#002aff", parent=None):
        super().__init__(parent)
        self._primary = primary
        self.setAttribute(Qt.WA_StyledBackground, False)

    def set_primary(self, color: str):
        self._primary = color
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # Base fill -- matches surface #04060f
        p.fillRect(self.rect(), QColor("#04060f"))

        def orb(cx_frac, cy_frac, radius_frac, hex_color, alpha):
            cx = w * cx_frac
            cy = h * cy_frac
            r  = max(w, h) * radius_frac
            g  = QRadialGradient(cx, cy, r)
            c0 = QColor(hex_color)
            c0.setAlpha(alpha)
            c1 = QColor(hex_color)
            c1.setAlpha(0)
            g.setColorAt(0.0, c0)
            g.setColorAt(1.0, c1)
            p.fillRect(self.rect(), g)

        # v2 design orb positions / colors
        # oklch(45% 0.26 254) ~ #0028cc  — strong blue bottom-left
        # oklch(40% 0.20 290) ~ #4020a8  — purple top-right
        # oklch(40% 0.18 330) ~ #8020a0  — pink-purple mid-right
        # oklch(40% 0.20 230) ~ #0050c8  — cyan-blue top-center
        orb(0.05, 0.80, 0.55, self._primary, 32)   # accent blue — bottom-left
        orb(0.90, 0.08, 0.45, "#4020a8",    24)   # purple      — top-right
        orb(0.88, 0.55, 0.32, "#8020a0",    18)   # pink-purple — mid-right
        orb(0.45, -0.10, 0.28, "#0050c8",   16)   # cyan-blue   — top-center

        p.end()


class _ScanWorker(QThread):
    """Background thread: scans a folder node and emits results."""
    scan_ready = Signal(list, list, int)   # (materials, assets, generation)

    def __init__(self, node, backpack_root: Path, generation: int):
        super().__init__()
        self._node = node
        self._root = backpack_root
        self._gen  = generation

    def run(self):
        mats, assets = scan_folder_recursive(self._node, self._root)
        self.scan_ready.emit(mats, assets, self._gen)


class MainWindow(QMainWindow):

    def __init__(self, settings: AppSettings):
        super().__init__()
        self.settings = settings
        self._backpack_root: Path | None = None

        # All scanned data for current folder
        self._materials: list[ScannedMaterial] = []
        self._assets: list[ScannedAsset] = []
        self._tag_registry: dict[str, TagInfo] = {}

        # Current navigation state
        self._current_node: FolderNode | None = None
        self._active_tags: list[str] = []
        self._active_resolutions: list[str] = []
        self._active_search: str = ""

        # Async scan support
        self._scan_generation: int = 0
        self._active_scans: list[_ScanWorker] = []  # keep alive until finished
        self._animate_next_scan: bool = False  # True only on explicit folder navigation

        # Filesystem watcher — auto-reload on external file changes
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(self._on_dir_changed)
        self._auto_reload_timer = QTimer(self)
        self._auto_reload_timer.setSingleShot(True)
        self._auto_reload_timer.setInterval(700)   # 700 ms debounce
        self._auto_reload_timer.timeout.connect(self._reload_current_folder)

        self.setWindowTitle("Moseng Backpack")
        self.setMinimumSize(1100, 700)
        self.resize(settings.window_width, settings.window_height)

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        central = _OrbBackground(self.settings.accent_color)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Top bar — folder address breadcrumb
        self.address_bar = FolderAddressBar(self.settings.accent_color)
        main_layout.addWidget(self.address_bar)

        # Content row
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)

        # Left sidebar: folder tree (top) + tag panel (bottom)
        # SidebarPanel already sets fixedWidth(210); left_panel inherits that width.
        left_panel = QWidget()
        left_panel.setObjectName("sidebar")   # picks up #sidebar { background: $surface_low }
        left_panel.setAutoFillBackground(True)
        _pal = left_panel.palette()
        _pal.setColor(QPalette.Window, QColor("#07080d"))
        left_panel.setPalette(_pal)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self.folder_tree = FolderTreeWidget(self.settings.accent_color)
        left_layout.addWidget(self.folder_tree, stretch=2)

        self.sidebar = SidebarPanel(self.settings.accent_color)
        left_layout.addWidget(self.sidebar, stretch=1)

        content.addWidget(left_panel)

        # Center: browser
        center = QWidget()
        center.setAutoFillBackground(True)
        _pal2 = center.palette()
        _pal2.setColor(QPalette.Window, QColor("#04060f"))
        center.setPalette(_pal2)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        self.sub_toolbar = AssetSubToolbar()
        center_layout.addWidget(self.sub_toolbar)

        self.browser = AssetBrowser()
        self.browser.set_card_size(self.settings.grid_card_size)
        center_layout.addWidget(self.browser, stretch=1)

        self.sub_toolbar.sort_changed.connect(self._on_sort_changed)
        self.sub_toolbar.quick_filter_changed.connect(self._on_quick_filter_changed)

        content.addWidget(center, stretch=1)

        # Right detail panel
        self.detail = AssetDetailPanel()
        content.addWidget(self.detail)

        main_layout.addLayout(content, stretch=1)

        # Drop overlay
        self.drop_overlay = DropOverlay(central)
        self.setAcceptDrops(True)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready")

        # Keyboard shortcut: Ctrl+F → focus search
        sc = QShortcut(QKeySequence("Ctrl+F"), self)
        sc.activated.connect(self.address_bar.focus_search)

    def _connect_signals(self):
        # Address bar
        self.address_bar.settings_requested.connect(self._open_settings)
        self.address_bar.refresh_requested.connect(self._refresh_sync)
        self.address_bar.import_requested.connect(self._on_import_btn)
        self.address_bar.reset_requested.connect(self._reset_metadata)
        self.address_bar.folder_selected.connect(self._on_folder_selected)
        self.address_bar.search_changed.connect(self._on_search_changed)

        # Folder tree
        self.folder_tree.folder_selected.connect(self._on_folder_selected)

        # Sidebar tags + resolution
        self.sidebar.tags_changed.connect(self._on_tags_changed)
        self.sidebar.resolutions_changed.connect(self._on_resolutions_changed)
        self.sidebar.add_tag_requested.connect(self._add_global_tag)
        self.sidebar.tag_delete_requested.connect(self._delete_tag)

        # Browser
        self.browser.asset_double_clicked.connect(self._open_asset)
        self.browser.material_double_clicked.connect(self._open_material)
        self.browser.selection_changed.connect(self._on_selection_changed)
        self.browser.delete_requested.connect(self._delete_items)

        # Drop overlay
        self.drop_overlay.files_dropped.connect(self._on_drop)

        # Detail panel
        self.detail.refresh_requested.connect(self._reload_current_folder)
        self.detail.tag_head_changed.connect(self._on_tag_head_changed)

    # ── Initialization ──────────────────────────────────────────────────────

    def init_drive(self, drive_letter: str):
        root = Path(f"{drive_letter}:/BACKPACK")
        root.mkdir(parents=True, exist_ok=True)

        self._backpack_root = root
        self._tag_registry = load_tag_registry(root)
        self.sidebar.set_tag_registry(self._tag_registry, root)
        self.detail.set_tag_registry(self._tag_registry, root)
        self.status.showMessage(f"BACKPACK: {root}")

        # Build folder tree
        self.folder_tree.load_tree(root, self.settings.quixel_enabled)

        # Restore last folder or select first leaf
        last = self.settings.last_folder_path
        if last:
            self._navigate_to_path(Path(last))
        else:
            self._select_first_leaf()

    # ── Folder navigation ───────────────────────────────────────────────────

    def _on_folder_selected(self, node: FolderNode):
        self._current_node = node
        self.settings.last_folder_path = str(node.disk_path)
        self.address_bar.set_node(node)
        self.address_bar.clear_search()      # reset search on folder change
        self._active_search = ""
        self.folder_tree.select_node(node)
        self._update_watcher(node)
        self._animate_next_scan = True       # folder navigation → animate cards in
        self._reload_current_folder()

    def _reload_current_folder(self):
        """Start an async scan of the current node; results applied in _on_scan_done."""
        if not self._current_node or not self._backpack_root:
            return

        self._scan_generation += 1
        gen = self._scan_generation
        self.status.showMessage("Scanning…")

        worker = _ScanWorker(self._current_node, self._backpack_root, gen)
        worker.scan_ready.connect(self._on_scan_done)
        worker.finished.connect(lambda w=worker: self._active_scans.remove(w)
                                 if w in self._active_scans else None)
        self._active_scans.append(worker)
        worker.start()

    def _on_scan_done(self, materials: list, assets: list, generation: int):
        """Called on main thread when background scan finishes."""
        if generation != self._scan_generation:
            return   # stale result — a newer scan is in flight

        self._materials = materials
        self._assets    = assets
        self._sync_tag_registry()
        self.sidebar.set_tag_registry(self._tag_registry, self._backpack_root)
        self.detail.set_tag_registry(self._tag_registry, self._backpack_root)
        self.browser.set_tag_registry(self._tag_registry)
        self.sidebar.load_tags_from_scan(self._materials, self._assets)
        animate = self._animate_next_scan
        self._animate_next_scan = False     # consume: only fires once per navigation
        self._apply_filters(animate=animate)

    def _navigate_to_path(self, disk_path: Path):
        """Rebuild tree and navigate to the node with the given disk_path."""
        if not self._backpack_root:
            return
        root_node = build_folder_tree(self._backpack_root, self.settings.quixel_enabled)
        node = self._find_node(root_node, disk_path)
        if node:
            self._on_folder_selected(node)
        else:
            self._select_first_leaf()

    def _find_node(self, node: FolderNode, disk_path: Path) -> FolderNode | None:
        if node.disk_path == disk_path:
            return node
        for child in node.children:
            found = self._find_node(child, disk_path)
            if found:
                return found
        return None

    def _select_first_leaf(self):
        """Navigate to the BACKPACK root node (shows all items)."""
        if not self._backpack_root:
            return
        root_node = build_folder_tree(self._backpack_root, self.settings.quixel_enabled)
        self._on_folder_selected(root_node)

    # ── Scanning / refresh ──────────────────────────────────────────────────

    def _full_refresh(self):
        """Rebuild the folder tree and reload current folder."""
        if not self._backpack_root:
            return
        self.folder_tree.load_tree(self._backpack_root, self.settings.quixel_enabled)
        self._reload_current_folder()

    def _sync_tag_registry(self):
        if not self._backpack_root:
            return
        changed = False

        used_tags: set[str] = set()
        for mat in self._materials:
            for t in mat.meta.tags:
                used_tags.add(t)
                if t not in self._tag_registry:
                    head_path = mat.preview_path if mat.preview_path else None
                    get_or_create_tag(self._backpack_root, self._tag_registry, t,
                                      self.settings.accent_color, head_path)
                    changed = True
        for asset in self._assets:
            for t in asset.meta.tags:
                used_tags.add(t)
                if t not in self._tag_registry:
                    head_path = asset.path if asset.path.suffix.lower() in (
                        ".png", ".jpg", ".jpeg", ".bmp", ".tga", ".tif", ".tiff", ".exr", ".hdr") else None
                    get_or_create_tag(self._backpack_root, self._tag_registry, t,
                                      self.settings.accent_color, head_path)
                    changed = True

        # Also ensure default tags for this node are in registry
        if self._current_node:
            for tag_name in self._current_node.default_tags:
                if tag_name not in self._tag_registry:
                    get_or_create_tag(self._backpack_root, self._tag_registry, tag_name,
                                      self.settings.accent_color, None)
                    changed = True

        if changed:
            save_tag_registry(self._backpack_root, self._tag_registry)

    def _refresh_sync(self):
        if not self._backpack_root:
            return

        # Load last sync timestamp
        sync_stamp = self._backpack_root / ".backpack_last_sync"
        since: float | None = None
        if sync_stamp.exists():
            try:
                since = float(sync_stamp.read_text(encoding="utf-8").strip())
            except ValueError:
                since = None

        self.status.showMessage("Syncing JSON metadata...")
        QApplication.processEvents()

        sync_json_files(self._backpack_root, since=since)

        self.status.showMessage("Generating preview cache...")
        QApplication.processEvents()

        previews = sync_previews(self._backpack_root, since=since)
        orphans = clean_orphaned_previews(self._backpack_root)

        # Save current time as last sync
        sync_stamp.write_text(str(time.time()), encoding="utf-8")

        self._full_refresh()
        self.status.showMessage(
            f"Sync complete — {previews} preview(s) cached, {orphans} stale removed"
        )

    def _reset_metadata(self):
        """Delete all .json metadata folders/files under the backpack root."""
        if not self._backpack_root:
            return

        # Count before asking
        json_dirs = list(self._backpack_root.rglob(JSON_DIR_NAME))
        json_dirs = [d for d in json_dirs if d.is_dir()]
        total = sum(len(list(d.glob("*_backpack.json"))) for d in json_dirs)

        reply = QMessageBox.warning(
            self,
            "Reset Metadata",
            f"이 작업은 BACKPACK 내 모든 메타데이터 JSON 파일 {total}개를 삭제합니다.\n\n"
            f"태그, 별점, 노트, 즐겨찾기가 모두 초기화됩니다.\n\n"
            f"계속하시겠습니까?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply != QMessageBox.Yes:
            return

        removed = 0
        for d in json_dirs:
            for jp in d.glob("*_backpack.json"):
                jp.unlink()
                removed += 1
            try:
                d.rmdir()   # remove empty .json dir
            except OSError:
                pass

        # Also reset the sync timestamp so next Refresh does a full scan
        sync_stamp = self._backpack_root / ".backpack_last_sync"
        if sync_stamp.exists():
            sync_stamp.unlink()

        self._full_refresh()
        self.status.showMessage(f"Reset complete — {removed} metadata file(s) deleted")

    # ── Filtering ───────────────────────────────────────────────────────────

    def _on_tags_changed(self, tags):
        self._active_tags = tags
        self._apply_filters()

    def _on_resolutions_changed(self, resolutions):
        self._active_resolutions = resolutions
        self._apply_filters()

    def _on_search_changed(self, text: str):
        self._active_search = text.lower()
        self._apply_filters()

    def _on_sort_changed(self, mode: str) -> None:
        self._apply_filters()

    def _on_quick_filter_changed(self, mode: str) -> None:
        self._apply_filters()

    def _apply_filters(self, animate: bool = False):
        """Apply tag + resolution + text-search + quick-filter, then sort, then display."""
        q     = self._active_search
        qf    = self.sub_toolbar.active_filter()   # "all" | "4k" | "fav"
        sort  = self.sub_toolbar.active_sort()     # "name" | "size"

        filtered_mats = [
            m for m in self._materials
            if self._match_tags(m.meta.tags, m.meta.favorite)
            and self._match_resolution_mat(m)
            and self._match_search(q, m.name, m.meta.tags, m.meta.notes,
                                   m.meta.surface_type)
            and self._match_quick_filter_mat(m, qf)
        ]
        filtered_assets = [
            a for a in self._assets
            if self._match_tags(a.meta.tags, a.meta.favorite)
            and self._match_resolution_asset(a)
            and self._match_search(q, a.filename, a.meta.tags, a.meta.notes,
                                   a.sub_type)
            and self._match_quick_filter_asset(a, qf)
        ]

        # ── Sort ─────────────────────────────────────────────────────────────
        if sort == "size":
            def _mat_size(m):
                try:
                    return sum(mp.path.stat().st_size for mp in m.maps)
                except OSError:
                    return 0
            def _asset_size(a):
                try:
                    return a.path.stat().st_size
                except OSError:
                    return 0
            filtered_mats   = sorted(filtered_mats,   key=_mat_size,   reverse=True)
            filtered_assets = sorted(filtered_assets, key=_asset_size,  reverse=True)
        else:
            filtered_mats   = sorted(filtered_mats,   key=lambda m: m.name.lower())
            filtered_assets = sorted(filtered_assets, key=lambda a: a.filename.lower())

        self.browser.display_items(filtered_mats, filtered_assets, animate=animate)
        count = len(filtered_mats) + len(filtered_assets)
        self.sub_toolbar.set_count(count)
        path_str = self._current_node.breadcrumb_display() if self._current_node else ""
        suffix = f'  (filtered: "{self._active_search}")' if q else ""
        self.status.showMessage(f"{path_str}  —  {count} item(s){suffix}")

    @staticmethod
    def _match_quick_filter_mat(mat, mode: str) -> bool:
        if mode == "all":
            return True
        if mode == "fav":
            return mat.meta.favorite
        if mode == "4k":
            from backpack.core.downscale import detect_resolution_tag
            return any(detect_resolution_tag(mp.filename) == "4K" for mp in mat.maps)
        return True

    @staticmethod
    def _match_quick_filter_asset(asset, mode: str) -> bool:
        if mode == "all":
            return True
        if mode == "fav":
            return asset.meta.favorite
        if mode == "4k":
            from backpack.core.downscale import detect_resolution_tag
            return detect_resolution_tag(asset.filename) == "4K"
        return True

    @staticmethod
    def _match_search(query: str, name: str, tags: list,
                      notes: str, sub_type: str = "") -> bool:
        """True if query is empty or matches name / tags / notes / sub_type."""
        if not query:
            return True
        return (query in name.lower()
                or query in notes.lower()
                or query in sub_type.lower()
                or any(query in t.lower() for t in tags))

    def _match_resolution_mat(self, mat: ScannedMaterial) -> bool:
        if not self._active_resolutions:
            return True
        for a in mat.maps:
            tag = detect_resolution_tag(a.filename)
            if tag and tag in self._active_resolutions:
                return True
        return False

    def _match_resolution_asset(self, asset: ScannedAsset) -> bool:
        if not self._active_resolutions:
            return True
        tag = detect_resolution_tag(asset.filename)
        return tag in self._active_resolutions if tag else False

    def _match_tags(self, item_tags: list[str], is_fav: bool) -> bool:
        if not self._active_tags:
            return True
        for t in self._active_tags:
            if t == "Favorites":
                if not is_fav:
                    return False
            elif t not in item_tags:
                return False
        return True

    # ── Filesystem watcher ──────────────────────────────────────────────────

    def _update_watcher(self, node: FolderNode):
        """Watch the current node's folder (and its direct subdirs for materials)."""
        old = self._fs_watcher.directories()
        if old:
            self._fs_watcher.removePaths(old)

        paths = []
        if node.disk_path.exists():
            paths.append(str(node.disk_path))
            # Also watch each immediate subdirectory (material folders, subfolders)
            try:
                for sub in node.disk_path.iterdir():
                    if sub.is_dir() and not sub.name.startswith("."):
                        paths.append(str(sub))
            except PermissionError:
                pass

        if paths:
            self._fs_watcher.addPaths(paths)

    def _on_dir_changed(self, _path: str):
        """Debounce filesystem changes and trigger a soft reload."""
        self._auto_reload_timer.start()

    # ── Selection ───────────────────────────────────────────────────────────

    def _on_selection_changed(self, count: int, items: list):
        if count == 0:
            return
        elif count == 1 and items:
            kind, obj = items[0]
            if kind == "material":
                self.detail.show_material(obj)
            else:
                self.detail.show_asset(obj)
        else:
            self.detail.show_multi_selection(count, items)

    def _open_asset(self, asset: ScannedAsset):
        if asset.path.exists():
            os.startfile(str(asset.path))

    def _open_material(self, mat: ScannedMaterial):
        if mat.path.exists():
            os.startfile(str(mat.path))

    # ── Import ──────────────────────────────────────────────────────────────

    def _on_drop(self, paths):
        self._run_import(paths)

    def _on_import_btn(self):
        paths = QFileDialog.getOpenFileNames(
            self, "Select files to import", "", "All Files (*.*)"
        )[0]
        if paths:
            self._run_import(paths)

    def _run_import(self, paths: list[str]):
        if not self._backpack_root:
            QMessageBox.warning(self, "No Drive", "Please set a drive in Settings first.")
            return

        dlg = ImportDialog(paths, self._backpack_root, self)
        if dlg.exec():
            self.status.showMessage(f"Imported {dlg.imported_count} file(s) — generating previews...")
            QApplication.processEvents()

            sync_json_files(self._backpack_root)

            if dlg.chosen_type == "material" and dlg.imported_folders:
                for folder in dlg.imported_folders:
                    self._generate_material_preview(folder)
            elif dlg.imported_dest_folder:
                generate_previews_for_folder(dlg.imported_dest_folder)

            self._reload_current_folder()
            self.status.showMessage(f"Imported {dlg.imported_count} file(s)")

    def _generate_material_preview(self, folder: Path):
        _IMG = {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif", ".tiff", ".exr", ".hdr"}
        files = [f for f in folder.iterdir() if f.is_file()]
        # 1. Quixel preview pattern
        preview_file = next(
            (f for f in files
             if f.suffix.lower() in _IMG
             and any(p.search(f.stem) for p in QUIXEL_PREVIEW_PATTERNS)),
            None,
        )
        # 2. Albedo / diffuse name
        if not preview_file:
            preview_file = next(
                (f for f in files
                 if re.search(r"(diffuse|diff|albedo|base_?color|col)\b", f.stem, re.I)
                 and f.suffix.lower() in _IMG),
                None,
            )
        # 3. Any image file
        if not preview_file:
            preview_file = next((f for f in files if f.suffix.lower() in _IMG), None)
        if preview_file:
            ensure_preview(preview_file)

    # ── Delete ──────────────────────────────────────────────────────────────

    def _delete_items(self, items: list[tuple[str, object]]):
        count = len(items)
        reply = QMessageBox.question(
            self, "Delete",
            f"Delete {count} item(s) from disk?\n\nThis cannot be undone.",
        )
        if reply != QMessageBox.Yes:
            return

        for kind, obj in items:
            try:
                if kind == "material":
                    mat: ScannedMaterial = obj
                    if mat.path.exists():
                        shutil.rmtree(str(mat.path))
                elif kind == "asset":
                    asset: ScannedAsset = obj
                    delete_asset_meta(asset.path)
                    if asset.path.exists():
                        asset.path.unlink()
            except Exception as e:
                self.status.showMessage(f"Error deleting: {e}")

        self.detail.hide()
        self._reload_current_folder()

    # ── Tags ────────────────────────────────────────────────────────────────

    def _on_tag_head_changed(self, tag_name: str, head_path):
        if not self._backpack_root:
            return
        head_path = Path(head_path) if not isinstance(head_path, Path) else head_path
        ensure_preview(head_path)
        set_tag_head(self._backpack_root, self._tag_registry, tag_name, head_path)
        save_tag_registry(self._backpack_root, self._tag_registry)
        self._reload_current_folder()

    def _delete_tag(self, tag_name: str):
        reply = QMessageBox.question(
            self, "Delete Tag",
            f'Delete tag "{tag_name}" from all items?\n\nThis cannot be undone.',
        )
        if reply != QMessageBox.Yes:
            return

        for mat in self._materials:
            if tag_name in mat.meta.tags:
                mat.meta.tags.remove(tag_name)
                write_material_meta(mat.path, mat.meta)
        for asset in self._assets:
            if tag_name in asset.meta.tags:
                asset.meta.tags.remove(tag_name)
                write_asset_meta(asset.path, asset.meta)

        if tag_name in self._tag_registry:
            del self._tag_registry[tag_name]
            save_tag_registry(self._backpack_root, self._tag_registry)

        self._reload_current_folder()

    def _add_global_tag(self):
        QInputDialog.getText(self, "Info",
                             "Tags are added per-item in the detail panel.\n"
                             "Select an item and click + in the Tags section.")

    # ── Settings ────────────────────────────────────────────────────────────

    def _open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        dlg.settings_changed.connect(self._apply_settings)
        if dlg.exec():
            self._apply_settings()

    def _apply_settings(self):
        save_settings(self.settings)

        # Re-apply stylesheet so $primary tokens reflect the new accent colour
        from backpack.app import load_stylesheet   # lazy: avoids circular import
        load_stylesheet(QApplication.instance(), self.settings.accent_color)

        self.sidebar.set_accent(self.settings.accent_color)
        self.folder_tree.set_accent(self.settings.accent_color)
        self.centralWidget().set_primary(self.settings.accent_color)

        if self.settings.drive_letter:
            root = Path(f"{self.settings.drive_letter}:/BACKPACK")
            if root != self._backpack_root:
                self.init_drive(self.settings.drive_letter)
            else:
                # Quixel toggle may have changed — rebuild tree
                self.folder_tree.load_tree(root, self.settings.quixel_enabled)
                self._reload_current_folder()

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
            self.drop_overlay.setGeometry(self.centralWidget().rect())

    def closeEvent(self, event):
        self.settings.window_width = self.width()
        self.settings.window_height = self.height()
        self.settings.grid_card_size = self.browser._card_size
        save_settings(self.settings)
        super().closeEvent(event)
