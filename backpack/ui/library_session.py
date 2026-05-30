"""LibrarySession — the coordination 'brains' extracted from MainWindow.

Owns all shared, non-UI state (backpack root, settings, tag registry, scan
results, filter state, scan/sync threads, filesystem watcher) and the logic that
drives a scan→filter→display cycle.

It holds LISTS of the panel widgets (registered by the shell via `register_*`),
because the user can open multiple instances of any panel; the session
broadcasts every update to all registered instances. Dialogs are parented to
`self._win` (the shell window).
"""

import os
import re
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, QTimer, QFileSystemWatcher
from PySide6.QtWidgets import QMessageBox, QApplication, QFileDialog, QInputDialog

from backpack.core.settings import AppSettings, save_settings
from backpack.core.scanner import (
    scan_folder_recursive, scan_folder_node, sync_json_files,
    ScannedMaterial, ScannedAsset,
)
from backpack.core.downscale import detect_resolution_tag
from backpack.core.metadata import (
    delete_asset_meta,
    write_asset_meta, write_material_meta,
    set_backpack_root as _meta_set_root,
)
from backpack.core.preview import (
    sync_previews, clean_orphaned_previews,
    ensure_preview, generate_previews_for_folder,
    set_backpack_root as _preview_set_root,
)
from backpack.core.folder_model import JSON_DIR_NAME
from backpack.constants import QUIXEL_PREVIEW_PATTERNS
from backpack.core.tag_registry import (
    load_tag_registry, save_tag_registry, get_or_create_tag, set_tag_head, TagInfo,
)
from backpack.core.folder_model import FolderNode, build_folder_tree, scaffold_project
from backpack.ui.dialogs.import_dialog import ImportDialog


def _natural_key(s: str):
    """Sort key that orders embedded numbers numerically, so 'bak2' precedes
    'bak10' (instead of lexicographic 'bak10' < 'bak2')."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", s)]


_VER_TOKEN_RE = re.compile(r"(?i)[._\-]?v\d+")
_BAK_TOKEN_RE = re.compile(r"(?i)[._\-]?bak\d+")


def _scene_group_key(stem: str) -> str:
    """Group key for 'latest' collapsing: strip version (v001) and Houdini
    backup (bak1) tokens so every version/backup of the same scene shares a key.

        Balance_Shot_v001_bak1 / …_bak28   →  'balance_shot'
        Shot_v001 / Shot_v002 / Shot_v003  →  'shot'
    """
    s = _VER_TOKEN_RE.sub("", stem)
    s = _BAK_TOKEN_RE.sub("", s)
    return s.strip("._- ").lower()


class _ScanWorker(QThread):
    """Background thread: scans a folder node and emits results."""
    scan_ready = Signal(list, list, int)   # (materials, assets, generation)

    def __init__(self, node, backpack_root: Path, generation: int, recursive: bool = True):
        super().__init__()
        self._node = node
        self._root = backpack_root
        self._gen  = generation
        self._recursive = recursive

    def run(self):
        if self._recursive:
            mats, assets = scan_folder_recursive(self._node, self._root)
        else:
            mats, assets = scan_folder_node(self._node, self._root)
        self.scan_ready.emit(mats, assets, self._gen)


class _SyncWorker(QThread):
    """Background thread: generates JSON metadata and preview thumbnails."""
    tick     = Signal(int, int, str)
    message  = Signal(str)
    finished = Signal(int, int)

    def __init__(self, backpack_root: Path, since: float | None):
        super().__init__()
        self._root  = backpack_root
        self._since = since

    def run(self):
        self.message.emit("Syncing JSON…")

        def _json_progress(cur: int, total: int, label: str):
            self.tick.emit(cur, total, label)

        sync_json_files(self._root, since=self._since, on_progress=_json_progress)

        self.message.emit("Generating previews…")

        def _prev_progress(cur: int, total: int, label: str):
            self.tick.emit(cur, total, label)

        previews = sync_previews(self._root, since=self._since, on_progress=_prev_progress)
        orphans  = clean_orphaned_previews(self._root)

        self.finished.emit(previews, orphans)


class LibrarySession(QObject):
    """Shared state + scan/filter/import orchestration for the asset library."""

    # Emitted when the folder tree should be rebuilt (root, quixel_enabled).
    tree_reload_requested = Signal(object, bool)

    def __init__(self, settings: AppSettings):
        super().__init__()
        self.settings = settings
        self._backpack_root: Path | None = None

        # Panel widget references. The shell may register MULTIPLE instances of
        # each panel type (duplicate panels), so these are lists; the session
        # broadcasts updates to every registered instance.
        self.address_bar = None     # single (top bar)
        self._folder_trees: list = []
        self._sidebars: list = []
        self._browsers: list = []
        self._sub_toolbars: list = []
        self._details: list = []
        self._project_trees: list = []
        self.status = None          # QStatusBar
        self._progress_bar = None   # QProgressBar
        self._win = None            # shell QMainWindow (dialog parent)

        # All scanned data for current folder
        self._materials: list[ScannedMaterial] = []
        self._assets: list[ScannedAsset] = []
        self._tag_registry: dict[str, TagInfo] = {}

        # Active project (Project panel) — separate from the asset library.
        self._project_root: Path | None = None
        # "Include subfolders" toggle (Explorer) — when on, a project folder
        # shows every item from it AND all its subfolders.
        self._include_subfolders: bool = False
        # "Latest" toggle — collapse versioned SCENE files to the newest only.
        self._latest_only: bool = False

        # Current navigation / filter state
        self._current_node: FolderNode | None = None
        # Root the current node is scanned against (backpack_root for the asset
        # library, project_root for project folders) + recursion flag.
        self._current_scan_root: Path | None = None
        self._current_recursive: bool = True
        self._active_tags: list[str] = []
        self._active_resolutions: list[str] = []
        self._active_search: str = ""
        self._active_sort: str = "name"     # session-owned (shared by all toolbars)

        # Async scan support
        self._scan_generation: int = 0
        self._active_scans: list[_ScanWorker] = []
        self._animate_next_scan: bool = False

        # Filesystem watcher — auto-reload on external file changes
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(self._on_dir_changed)
        self._auto_reload_timer = QTimer(self)
        self._auto_reload_timer.setSingleShot(True)
        self._auto_reload_timer.setInterval(700)
        self._auto_reload_timer.timeout.connect(self._reload_current_folder)

        self._sync_worker: "_SyncWorker | None" = None

    # ── Binding & wiring ──────────────────────────────────────────────────────

    def bind(self, *, win, address_bar, status, progress_bar):
        """Inject the shell window + shared chrome. Panels register separately."""
        self._win = win
        self.address_bar = address_bar
        self.status = status
        self._progress_bar = progress_bar
        self.address_bar.folder_selected.connect(self._on_folder_selected)

    # ── Panel registration (supports duplicates) ───────────────────────────────
    # Each register_* connects a panel instance's signals and seeds it with the
    # current state, so panels added at runtime immediately match the others.

    def register_assets(self, folder_tree):
        self._folder_trees.append(folder_tree)
        folder_tree.folder_selected.connect(self._on_folder_selected)
        folder_tree.import_requested.connect(self._on_import_btn)
        if self._backpack_root:
            folder_tree.load_tree(self._backpack_root, self.settings.quixel_enabled)
            if self._current_node:
                folder_tree.select_node(self._current_node)

    def register_filters(self, sidebar):
        self._sidebars.append(sidebar)
        sidebar.tags_changed.connect(self._on_tags_changed)
        sidebar.resolutions_changed.connect(self._on_resolutions_changed)
        sidebar.add_tag_requested.connect(self._add_global_tag)
        sidebar.tag_delete_requested.connect(self._delete_tag)
        if self._backpack_root:
            sidebar.set_tag_registry(self._tag_registry, self._backpack_root)
            sidebar.load_tags_from_scan(self._materials, self._assets)

    def register_explorer(self, browser, sub_toolbar):
        self._browsers.append(browser)
        self._sub_toolbars.append(sub_toolbar)
        browser.asset_double_clicked.connect(self._open_asset)
        browser.material_double_clicked.connect(self._open_material)
        browser.selection_changed.connect(self._on_selection_changed)
        browser.delete_requested.connect(self._delete_items)
        sub_toolbar.sort_changed.connect(self._on_sort_changed)
        sub_toolbar.search_changed.connect(self._on_search_changed)
        sub_toolbar.refresh_requested.connect(self._refresh_sync)
        sub_toolbar.recursive_changed.connect(self._on_recursive_changed)
        sub_toolbar.latest_changed.connect(self._on_latest_changed)
        if self._backpack_root:
            browser.set_tag_registry(self._tag_registry)
            self._apply_filters()   # populate this new grid with current items

    def register_inspector(self, detail):
        self._details.append(detail)
        detail.refresh_requested.connect(self._reload_current_folder)
        detail.tag_head_changed.connect(self._on_tag_head_changed)
        if self._backpack_root:
            detail.set_tag_registry(self._tag_registry, self._backpack_root)

    def register_project(self, project_tree):
        self._project_trees.append(project_tree)
        project_tree.folder_selected.connect(self._on_project_folder_selected)
        project_tree.open_project_requested.connect(self._open_project)
        project_tree.new_project_requested.connect(self._new_project)
        if self._project_root:
            project_tree.load_project(self._project_root)

    @property
    def backpack_root(self) -> Path | None:
        return self._backpack_root

    # ── Cross-cutting helpers (apply to all registered instances) ───────────────

    def apply_theme(self, accent: str, bg: str) -> None:
        for ft in self._folder_trees:
            ft.set_accent(accent)
            ft.set_bg_color(bg)
        for pt in self._project_trees:
            pt.set_accent(accent)
            pt.set_bg_color(bg)
        for sb in self._sidebars:
            sb.set_accent(accent)
        for br in self._browsers:
            br.set_accent(accent)

    def focus_search(self) -> None:
        if self._sub_toolbars:
            self._sub_toolbars[0].focus_search()

    def primary_card_size(self) -> int | None:
        if self._browsers:
            return self._browsers[0]._card_size
        return None

    # ── Initialization ────────────────────────────────────────────────────────

    def init_drive(self, drive_letter: str):
        root = Path(f"{drive_letter}:/BACKPACK")
        root.mkdir(parents=True, exist_ok=True)

        self._backpack_root = root
        _meta_set_root(root)
        _preview_set_root(root)
        self._tag_registry = load_tag_registry(root)
        for sb in self._sidebars:
            sb.set_tag_registry(self._tag_registry, root)
        for dt in self._details:
            dt.set_tag_registry(self._tag_registry, root)
        self.status.showMessage(f"BACKPACK: {root}")

        for ft in self._folder_trees:
            ft.load_tree(root, self.settings.quixel_enabled)

        # Restore the active project (if any) into the Project panel(s).
        if self.settings.project_root and Path(self.settings.project_root).exists():
            self._project_root = Path(self.settings.project_root)
            for pt in self._project_trees:
                pt.load_project(self._project_root)

        last = self.settings.last_folder_path
        if last:
            self._navigate_to_path(Path(last))
        else:
            self._select_first_leaf()

    # ── Project management ────────────────────────────────────────────────────

    def set_project_root(self, path: Path):
        self._project_root = path
        self.settings.project_root = str(path)
        save_settings(self.settings)
        for pt in self._project_trees:
            pt.load_project(path)
        self.status.showMessage(f"Project: {path}")

    def _open_project(self):
        start = str(self._project_root or self._backpack_root or Path.home())
        path = QFileDialog.getExistingDirectory(
            self._win, "Open Project Folder", start)
        if path:
            self.set_project_root(Path(path))

    def _new_project(self):
        start = str(self._project_root.parent if self._project_root
                    else (self._backpack_root or Path.home()))
        parent = QFileDialog.getExistingDirectory(
            self._win, "Choose location for the new project", start)
        if not parent:
            return
        name, ok = QInputDialog.getText(
            self._win, "New Project", "Project name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            project = scaffold_project(Path(parent), name, self.settings.project_template)
        except OSError as e:
            QMessageBox.warning(self._win, "New Project", f"Could not create project:\n{e}")
            return
        self.set_project_root(project)

    # ── Folder navigation ─────────────────────────────────────────────────────

    def _on_folder_selected(self, node: FolderNode):
        # Asset-library selection — scanned recursively against backpack_root.
        self._current_scan_root = self._backpack_root
        self._current_recursive = True
        self._current_node = node
        self.settings.last_folder_path = str(node.disk_path)
        self.address_bar.set_node(node)
        for tb in self._sub_toolbars:
            tb.clear_search()
        self._active_search = ""
        for ft in self._folder_trees:
            ft.select_node(node)
        self._update_watcher(node)
        self._animate_next_scan = True
        self._reload_current_folder()

    def _on_project_folder_selected(self, node: FolderNode):
        # Project selection — show this folder's files. Recursion follows the
        # "include subfolders" toggle (off → direct files only).
        self._current_scan_root = self._project_root
        self._current_recursive = self._include_subfolders
        self._current_node = node
        self.address_bar.set_node(node)
        for tb in self._sub_toolbars:
            tb.clear_search()
        self._active_search = ""
        for pt in self._project_trees:
            pt.select_node(node)
        self._update_watcher(node)
        self._animate_next_scan = True
        self._reload_current_folder()

    def _reload_current_folder(self):
        if not self._current_node or not self._current_scan_root:
            return

        self._scan_generation += 1
        gen = self._scan_generation
        self.status.showMessage("Scanning…")

        worker = _ScanWorker(self._current_node, self._current_scan_root, gen,
                             recursive=self._current_recursive)
        worker.scan_ready.connect(self._on_scan_done)
        worker.finished.connect(lambda w=worker: self._active_scans.remove(w)
                                 if w in self._active_scans else None)
        self._active_scans.append(worker)
        worker.start()

    def _on_scan_done(self, materials: list, assets: list, generation: int):
        if generation != self._scan_generation:
            return

        self._materials = materials
        self._assets    = assets
        self._sync_tag_registry()
        for sb in self._sidebars:
            sb.set_tag_registry(self._tag_registry, self._backpack_root)
            sb.load_tags_from_scan(self._materials, self._assets)
        for dt in self._details:
            dt.set_tag_registry(self._tag_registry, self._backpack_root)
        for br in self._browsers:
            br.set_tag_registry(self._tag_registry)
        animate = self._animate_next_scan
        self._animate_next_scan = False
        self._apply_filters(animate=animate)

    def _navigate_to_path(self, disk_path: Path):
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
        if not self._backpack_root:
            return
        root_node = build_folder_tree(self._backpack_root, self.settings.quixel_enabled)
        self._on_folder_selected(root_node)

    # ── Scanning / refresh ──────────────────────────────────────────────────

    def _full_refresh(self):
        if not self._backpack_root:
            return
        for ft in self._folder_trees:
            ft.load_tree(self._backpack_root, self.settings.quixel_enabled)
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
        if self._sync_worker and self._sync_worker.isRunning():
            return

        sync_stamp = self._backpack_root / ".backpack_last_sync"
        since: float | None = None
        if sync_stamp.exists():
            try:
                since = float(sync_stamp.read_text(encoding="utf-8").strip())
            except ValueError:
                since = None

        self._sync_since = since
        self._sync_stamp = sync_stamp

        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.show()

        self._sync_phase = "json"

        self._sync_worker = _SyncWorker(self._backpack_root, since)
        self._sync_worker.tick.connect(self._on_sync_tick)
        self._sync_worker.message.connect(self.status.showMessage)
        self._sync_worker.finished.connect(self._on_sync_finished)
        self._sync_worker.start()

    def _on_sync_tick(self, current: int, total: int, label: str):
        if total <= 0:
            return
        pct = int(current / total * 50)
        phase_offset = 0 if self._sync_phase == "json" else 50
        self._progress_bar.setValue(phase_offset + pct)
        if label:
            self.status.showMessage(
                f"{'JSON' if self._sync_phase == 'json' else 'Previews'}  "
                f"{current}/{total} — {label}"
            )
        if self._sync_phase == "json" and current >= total:
            self._sync_phase = "previews"
            self._progress_bar.setValue(50)

    def _on_sync_finished(self, previews: int, orphans: int):
        self._progress_bar.setValue(100)
        self._sync_stamp.write_text(str(time.time()), encoding="utf-8")
        self._full_refresh()
        QTimer.singleShot(600, self._progress_bar.hide)
        self.status.showMessage(
            f"Sync complete — {previews} preview(s) cached, {orphans} stale removed"
        )

    def _reset_metadata(self):
        if not self._backpack_root:
            return

        json_root = self._backpack_root / JSON_DIR_NAME
        total = sum(1 for _ in json_root.rglob("*.json")) if json_root.exists() else 0

        reply = QMessageBox.warning(
            self._win,
            "Reset Metadata",
            f"이 작업은 BACKPACK 내 모든 메타데이터 JSON 파일 {total}개를 삭제합니다.\n\n"
            f"태그, 별점, 노트, 즐겨찾기가 모두 초기화됩니다.\n\n"
            f"계속하시겠습니까?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply != QMessageBox.Yes:
            return

        removed = total
        if json_root.exists():
            shutil.rmtree(json_root, ignore_errors=True)

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
        self._active_sort = mode
        self._apply_filters()

    def _on_latest_changed(self, on: bool) -> None:
        self._latest_only = on
        self._apply_filters()   # re-filter cached items; no rescan needed

    @staticmethod
    def _collapse_latest(assets: list) -> list:
        """Keep only the latest version/backup of each SCENE file. 'Latest' is
        the most recently modified file in the group; the version/backup numbers
        break ties (highest wins). Non-scene assets pass through untouched."""
        groups: dict = {}   # (parent, group_key) -> (rank, asset)
        result: list = []
        for a in assets:
            if a.asset_type != "scene":
                result.append(a)
                continue
            gkey = (str(a.path.parent), _scene_group_key(a.path.stem))
            try:
                mtime = a.path.stat().st_mtime
            except OSError:
                mtime = 0.0
            nums = [int(n) for n in re.findall(r"\d+", a.path.stem)]
            rank = (mtime, nums)
            cur = groups.get(gkey)
            if cur is None or rank > cur[0]:
                groups[gkey] = (rank, a)
        result.extend(v[1] for v in groups.values())
        return result

    def _on_recursive_changed(self, on: bool) -> None:
        self._include_subfolders = on
        # Only project folders honour this; the asset library is always recursive.
        if (self._project_root is not None
                and self._current_scan_root == self._project_root
                and self._current_node is not None):
            self._current_recursive = on
            self._animate_next_scan = True
            self._reload_current_folder()

    def _apply_filters(self, animate: bool = False):
        q     = self._active_search
        sort  = self._active_sort

        filtered_mats = [
            m for m in self._materials
            if self._match_tags(m.meta.tags, m.meta.favorite)
            and self._match_resolution_mat(m)
            and self._match_search(q, m.name, m.meta.tags, m.meta.notes,
                                   m.meta.surface_type)
        ]
        filtered_assets = [
            a for a in self._assets
            if self._match_tags(a.meta.tags, a.meta.favorite)
            and self._match_resolution_asset(a)
            and self._match_search(q, a.filename, a.meta.tags, a.meta.notes,
                                   a.sub_type)
        ]

        if self._latest_only:
            filtered_assets = self._collapse_latest(filtered_assets)

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
            filtered_mats   = sorted(filtered_mats,   key=lambda m: _natural_key(m.name))
            filtered_assets = sorted(filtered_assets, key=lambda a: _natural_key(a.filename))

        list_root = self._current_node.disk_path if self._current_node else None
        for br in self._browsers:
            br.set_list_root(list_root)
            br.display_items(filtered_mats, filtered_assets, animate=animate)
        count = len(filtered_mats) + len(filtered_assets)
        for tb in self._sub_toolbars:
            tb.set_count(count)
        path_str = self._current_node.breadcrumb_display() if self._current_node else ""
        suffix = f'  (filtered: "{self._active_search}")' if q else ""
        self.status.showMessage(f"{path_str}  —  {count} item(s){suffix}")

    @staticmethod
    def _match_search(query: str, name: str, tags: list,
                      notes: str, sub_type: str = "") -> bool:
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

    # ── Filesystem watcher ────────────────────────────────────────────────────

    def _update_watcher(self, node: FolderNode):
        old = self._fs_watcher.directories()
        if old:
            self._fs_watcher.removePaths(old)

        paths = []
        if node.disk_path.exists():
            paths.append(str(node.disk_path))
            try:
                for sub in node.disk_path.iterdir():
                    if sub.is_dir() and not sub.name.startswith("."):
                        paths.append(str(sub))
            except PermissionError:
                pass

        if paths:
            self._fs_watcher.addPaths(paths)

    def _on_dir_changed(self, _path: str):
        self._auto_reload_timer.start()

    # ── Selection ─────────────────────────────────────────────────────────────

    def _on_selection_changed(self, count: int, items: list):
        if count == 0:
            return
        elif count == 1 and items:
            kind, obj = items[0]
            for dt in self._details:
                if kind == "material":
                    dt.show_material(obj)
                else:
                    dt.show_asset(obj)
        else:
            for dt in self._details:
                dt.show_multi_selection(count, items)

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
            self._win, "Select files to import", "", "All Files (*.*)"
        )[0]
        if paths:
            self._run_import(paths)

    def _run_import(self, paths: list[str]):
        if not self._backpack_root:
            QMessageBox.warning(self._win, "No Drive", "Please set a drive in Settings first.")
            return

        dlg = ImportDialog(paths, self._backpack_root, self._win)
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
        preview_file = next(
            (f for f in files
             if f.suffix.lower() in _IMG
             and any(p.search(f.stem) for p in QUIXEL_PREVIEW_PATTERNS)),
            None,
        )
        if not preview_file:
            preview_file = next(
                (f for f in files
                 if re.search(r"(diffuse|diff|albedo|base_?color|col)\b", f.stem, re.I)
                 and f.suffix.lower() in _IMG),
                None,
            )
        if not preview_file:
            preview_file = next((f for f in files if f.suffix.lower() in _IMG), None)
        if preview_file:
            ensure_preview(preview_file)

    # ── Delete ──────────────────────────────────────────────────────────────

    def _delete_items(self, items: list[tuple[str, object]]):
        count = len(items)
        reply = QMessageBox.question(
            self._win, "Delete",
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

        for dt in self._details:
            dt.hide()
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
            self._win, "Delete Tag",
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
        QInputDialog.getText(self._win, "Info",
                             "Tags are added per-item in the detail panel.\n"
                             "Select an item and click + in the Tags section.")
