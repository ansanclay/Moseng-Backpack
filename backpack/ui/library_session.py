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
from functools import lru_cache
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


@lru_cache(maxsize=8192)
def _natural_key(s: str):
    """Sort key that orders embedded numbers numerically, so 'bak2' precedes
    'bak10' (instead of lexicographic 'bak10' < 'bak2'). Cached + immutable
    (tuple) since it runs per-item on every name-sort re-apply."""
    return tuple(int(t) if t.isdigit() else t.lower()
                 for t in re.split(r"(\d+)", s))


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
    """Background thread: scans a folder node and emits results for one Explorer."""
    scan_ready = Signal(list, list, int, str)   # (materials, assets, generation, exp_key)

    def __init__(self, node, backpack_root: Path, generation: int,
                 recursive: bool = True, exp_key: str = ""):
        super().__init__()
        self._node = node
        self._root = backpack_root
        self._gen  = generation
        self._recursive = recursive
        self._exp_key = exp_key

    def run(self):
        if self._recursive:
            mats, assets = scan_folder_recursive(self._node, self._root)
        else:
            mats, assets = scan_folder_node(self._node, self._root)
        self.scan_ready.emit(mats, assets, self._gen, self._exp_key)


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

        self._tag_registry: dict[str, TagInfo] = {}

        # Active project (Project panel) — separate from the asset library.
        self._project_root: Path | None = None

        # ── Panel-graph data flow (Tab node overlay) — INSTANCE based ──────────
        # Every panel instance is a node, keyed by its dock objectName. Edges:
        #   (src_key, exp_key, "address")  source folder selection feeds Explorer
        #   (filter_key, exp_key, "filter") Filters tags/res feed Explorer
        #   (exp_key, insp_key, "select")  Explorer selection feeds Inspector
        # Each Explorer therefore works independently (its own source + scan).
        self._edges: set = set()
        self._key_role: dict = {}        # key -> role
        self._tree_by_key: dict = {}     # key -> FolderTreeWidget / ProjectTreeWidget
        self._sidebar_by_key: dict = {}  # key -> SidebarPanel
        self._browser_by_key: dict = {}  # key -> AssetBrowser
        self._toolbar_by_key: dict = {}  # key -> AssetSubToolbar
        self._detail_by_key: dict = {}   # key -> AssetDetailPanel
        self._source_sel: dict = {}      # source_key -> (FolderNode, scan_root)
        self._filter_state: dict = {}    # filter_key -> (tags, resolutions)
        self._explorers: dict = {}       # exp_key -> per-Explorer state dict

        # Collection (tray) panels — a session-scoped set of gathered items.
        self._collection: list = []          # global tray: list of (kind, item)
        self._collection_panels: dict = {}   # key -> CollectionPanel
        self._last_sel_items: list = []      # most recent non-empty Explorer selection

        # Synapse (graph) panels — each reflects the Explorer wired to it.
        self._synapse_panels: dict = {}      # key -> SynapseView

        # Cache file sizes for the "size" sort so re-filtering/re-searching does
        # not re-stat() every file each time. Cleared whenever a scan completes.
        self._size_cache: dict = {}

        # Flat folder index for the Quick-Open palette (built lazily, cleared on
        # drive (re)load so it reflects the current tree).
        self._folder_index: list | None = None

        # Last folder selected anywhere (drives the breadcrumb + fs-watcher).
        self._current_node: FolderNode | None = None
        self._current_scan_root: Path | None = None

        # Async scan support
        self._scan_generation: int = 0
        self._active_scans: list[_ScanWorker] = []

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
        self.address_bar.folder_selected.connect(self._on_breadcrumb_selected)

    # ── Panel registration (instance-keyed; supports duplicates) ────────────────

    def _count_role(self, role: str) -> int:
        return sum(1 for r in self._key_role.values() if r == role)

    def _first_key(self, role: str, exclude: str = "") -> "str | None":
        for k, r in self._key_role.items():
            if r == role and k != exclude:
                return k
        return None

    def _add_default_edges_for(self, key: str, role: str):
        """Wire the FIRST instance of each role together (works out of the box).
        Runtime duplicates start unwired → independent, as requested."""
        if role == "explorer" and self._count_role("explorer") == 1:
            for r in ("assets", "project"):
                s = self._first_key(r, key)
                if s:
                    self._edges.add((s, key, "address"))
            f = self._first_key("filters", key)
            if f:
                self._edges.add((f, key, "filter"))
        elif role in ("assets", "project") and self._count_role(role) == 1:
            e = self._first_key("explorer", key)
            if e:
                self._edges.add((key, e, "address"))
        elif role == "filters" and self._count_role("filters") == 1:
            e = self._first_key("explorer", key)
            if e:
                self._edges.add((key, e, "filter"))
        elif role == "inspector" and self._count_role("inspector") == 1:
            e = self._first_key("explorer", key)
            if e:
                self._edges.add((e, key, "select"))
        elif role == "synapse" and self._count_role("synapse") == 1:
            e = self._first_key("explorer", key)
            if e:
                self._edges.add((e, key, "items"))
        elif role == "collection" and self._count_role("collection") == 1:
            e = self._first_key("explorer", key)
            if e:
                self._edges.add((e, key, "collect"))

    def register_assets(self, folder_tree, key: str):
        self._folder_trees.append(folder_tree)
        self._key_role[key] = "assets"
        self._tree_by_key[key] = folder_tree
        folder_tree.folder_selected.connect(lambda node, k=key: self._on_source_selected(node, k))
        folder_tree.import_requested.connect(self._on_import_btn)
        self._add_default_edges_for(key, "assets")
        if self._backpack_root:
            folder_tree.load_tree(self._backpack_root, self.settings.quixel_enabled)

    def register_project(self, project_tree, key: str):
        self._project_trees.append(project_tree)
        self._key_role[key] = "project"
        self._tree_by_key[key] = project_tree
        project_tree.folder_selected.connect(lambda node, k=key: self._on_source_selected(node, k))
        project_tree.open_project_requested.connect(self._open_project)
        project_tree.new_project_requested.connect(self._new_project)
        self._add_default_edges_for(key, "project")
        if self._project_root:
            project_tree.load_project(self._project_root)

    def register_filters(self, sidebar, key: str):
        self._sidebars.append(sidebar)
        self._key_role[key] = "filters"
        self._sidebar_by_key[key] = sidebar
        self._filter_state[key] = ([], [])
        sidebar.tags_changed.connect(lambda tags, k=key: self._on_tags_changed(tags, k))
        sidebar.resolutions_changed.connect(lambda res, k=key: self._on_resolutions_changed(res, k))
        sidebar.add_tag_requested.connect(self._add_global_tag)
        sidebar.tag_delete_requested.connect(self._delete_tag)
        self._add_default_edges_for(key, "filters")
        if self._backpack_root:
            sidebar.set_tag_registry(self._tag_registry, self._backpack_root)

    def register_explorer(self, browser, sub_toolbar, key: str):
        self._browsers.append(browser)
        self._sub_toolbars.append(sub_toolbar)
        self._key_role[key] = "explorer"
        self._browser_by_key[key] = browser
        self._toolbar_by_key[key] = sub_toolbar
        self._explorers[key] = dict(node=None, scan_root=None, recursive=True,
                                    mats=[], assets=[], sort="name", search="",
                                    incl_sub=False, latest=False, gen=0, animate=False,
                                    sel=[])
        browser.asset_double_clicked.connect(self._open_asset)
        browser.material_double_clicked.connect(self._open_material)
        browser.selection_changed.connect(lambda c, items, k=key: self._on_selection_changed(c, items, k))
        browser.delete_requested.connect(self._delete_items)
        sub_toolbar.sort_changed.connect(lambda m, k=key: self._on_sort_changed(m, k))
        sub_toolbar.search_changed.connect(lambda t, k=key: self._on_search_changed(t, k))
        sub_toolbar.refresh_requested.connect(self._refresh_sync)
        sub_toolbar.recursive_changed.connect(lambda on, k=key: self._on_recursive_changed(on, k))
        sub_toolbar.latest_changed.connect(lambda on, k=key: self._on_latest_changed(on, k))
        self._add_default_edges_for(key, "explorer")
        if self._backpack_root:
            browser.set_tag_registry(self._tag_registry)
            self._seed_explorer(key)

    def register_inspector(self, detail, key: str):
        self._details.append(detail)
        self._key_role[key] = "inspector"
        self._detail_by_key[key] = detail
        detail.refresh_requested.connect(self._reload_current_folder)
        detail.tag_head_changed.connect(self._on_tag_head_changed)
        self._add_default_edges_for(key, "inspector")
        if self._backpack_root:
            detail.set_tag_registry(self._tag_registry, self._backpack_root)

    def register_synapse(self, panel, key: str):
        self._key_role[key] = "synapse"
        self._synapse_panels[key] = panel
        panel.node_activated.connect(self._open_graph_item)
        panel.folder_navigated.connect(self.navigate_to_path)
        panel.set_theme(self.settings.accent_color, self.settings.bg_color,
                        self.settings.secondary_color)
        self._add_default_edges_for(key, "synapse")
        self._refresh_synapse(key)

    def _open_graph_item(self, obj):
        if isinstance(obj, ScannedMaterial):
            self._open_material(obj)
        else:
            self._open_asset(obj)

    def _synapse_source(self, syn_key: str) -> "str | None":
        """The Explorer key wired into this Synapse's 'items' port, or None."""
        for (s, d, p) in self._edges:
            if d == syn_key and p == "items" and s in self._explorers:
                return s
        return None

    def _refresh_synapse(self, syn_key: str):
        """Rebuild one Synapse graph from the Explorer wired to its 'items' port."""
        panel = self._synapse_panels.get(syn_key)
        if panel is None:
            return
        exp = self._synapse_source(syn_key)
        st = self._explorers.get(exp) if exp else None
        panel.build(st.get("mats", []) if st else [],
                    st.get("assets", []) if st else [])

    def _push_synapse_from(self, exp_key: str):
        """An Explorer's content changed → rebuild the Synapse graphs wired to it."""
        for (s, d, p) in self._edges:
            if s == exp_key and p == "items" and d in self._synapse_panels:
                self._refresh_synapse(d)

    def register_collection(self, panel, key: str):
        self._key_role[key] = "collection"
        self._collection_panels[key] = panel
        panel.add_requested.connect(lambda k=key: self._add_selection_to_collection(k))
        panel.clear_requested.connect(self._clear_collection)
        panel.remove_requested.connect(lambda k=key: self._remove_from_collection(k))
        # Double-click opens the file (same as an Explorer); NO delete wiring.
        panel.browser.asset_double_clicked.connect(self._open_asset)
        panel.browser.material_double_clicked.connect(self._open_material)
        if self._backpack_root:
            panel.browser.set_tag_registry(self._tag_registry)
        self._add_default_edges_for(key, "collection")
        self._refresh_collection_panel(key)

    # ── Collection (tray) ─────────────────────────────────────────────────────
    def _add_selection_to_collection(self, coll_key: str = ""):
        """Add a selection to the tray. Prefers the selection of the Explorer(s)
        wired into this Collection's 'collect' port; falls back to the most
        recent selection anywhere if the Collection is not wired."""
        items: list = []
        if coll_key:
            for (s, d, p) in self._edges:
                if d == coll_key and p == "collect" and s in self._explorers:
                    items.extend(self._explorers[s].get("sel", []))
        if not items:
            items = self._last_sel_items
        if not items:
            return
        seen = {(k, str(o.path)) for (k, o) in self._collection}
        added = 0
        for kind, obj in items:
            sig = (kind, str(obj.path))
            if sig not in seen:
                self._collection.append((kind, obj))
                seen.add(sig)
                added += 1
        if added:
            self._refresh_all_collections()

    def _remove_from_collection(self, key: str):
        panel = self._collection_panels.get(key)
        if panel is None:
            return
        drop = {(k, str(o.path)) for (k, o) in panel.current_selection()}
        if not drop:
            return
        self._collection = [(k, o) for (k, o) in self._collection
                            if (k, str(o.path)) not in drop]
        self._refresh_all_collections()

    def _clear_collection(self):
        if self._collection:
            self._collection.clear()
            self._refresh_all_collections()

    def _refresh_all_collections(self):
        for key in self._collection_panels:
            self._refresh_collection_panel(key)

    def _refresh_collection_panel(self, key: str):
        panel = self._collection_panels.get(key)
        if panel is None:
            return
        mats = [o for (kind, o) in self._collection if kind == "material"]
        assets = [o for (kind, o) in self._collection if kind == "asset"]
        panel.display(mats, assets)

    @property
    def backpack_root(self) -> Path | None:
        return self._backpack_root

    # ── Quick-Open navigation ─────────────────────────────────────────────────
    def library_folders(self) -> list:
        """Flat [(breadcrumb_label, disk_path), ...] of every folder in the
        library tree, for the Quick-Open palette. Built once and cached."""
        if self._folder_index is None:
            out: list = []
            if self._backpack_root:
                root = build_folder_tree(self._backpack_root, self.settings.quixel_enabled)
                stack = list(root.children)
                while stack:
                    n = stack.pop()
                    out.append((n.breadcrumb_display(), n.disk_path))
                    stack.extend(n.children)
                out.sort(key=lambda t: t[0].lower())
            self._folder_index = out
        return self._folder_index

    def navigate_to_path(self, disk_path: Path) -> None:
        """Public navigation entry (used by the Quick-Open palette)."""
        self._navigate_to_path(Path(disk_path))

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
        for panel in self._collection_panels.values():
            panel.browser.set_accent(accent)
        for panel in self._synapse_panels.values():
            panel.set_theme(accent, bg, self.settings.secondary_color)

    def focus_search(self) -> None:
        if self._sub_toolbars:
            self._sub_toolbars[0].focus_search()

    def primary_card_size(self) -> int | None:
        if self._browsers:
            return self._browsers[0]._card_size
        return None

    # ── Panel-graph data flow (instance edges; node overlay) ─────────────────
    def role_of(self, key: str) -> "str | None":
        return self._key_role.get(key)

    def edges(self) -> list:
        return list(self._edges)

    def set_edges(self, edges_list: list) -> None:
        """Replace the wiring with a saved set (keys that no longer exist are
        dropped). Used to restore node connections on launch."""
        valid = set()
        for e in edges_list:
            if len(e) == 3 and e[0] in self._key_role and e[1] in self._key_role:
                valid.add((e[0], e[1], e[2]))
        self._edges = valid
        for syn_key in self._synapse_panels:   # reflect restored wiring
            self._refresh_synapse(syn_key)

    def is_edge(self, src_key: str, dst_key: str, port: str) -> bool:
        return (src_key, dst_key, port) in self._edges

    def set_edge(self, src_key: str, dst_key: str, port: str, on: bool) -> None:
        edge = (src_key, dst_key, port)
        if on:
            self._edges.add(edge)
        else:
            self._edges.discard(edge)

        if port == "address" and dst_key in self._explorers:
            if on:
                sel = self._source_sel.get(src_key)
                if sel:
                    self._scan_explorer(dst_key, sel[0], sel[1], animate=True)
            elif not any(p == "address" and d == dst_key for (s, d, p) in self._edges):
                self._clear_explorer(dst_key)
        elif port == "filter" and dst_key in self._explorers:
            self._apply_explorer(dst_key)
        elif port == "items" and dst_key in self._synapse_panels:
            self._refresh_synapse(dst_key)   # (re)build from new source, or clear
        elif port == "select" and on and src_key in self._explorers:
            pass   # next selection in that Explorer will flow through

    def _explorers_wired_from(self, src_key: str, port: str) -> list:
        return [d for (s, d, p) in self._edges
                if s == src_key and p == port and d in self._explorers]

    def _filter_source_for(self, exp_key: str) -> "str | None":
        for (s, d, p) in self._edges:
            if d == exp_key and p == "filter" and s in self._filter_state:
                return s
        return None

    def _seed_explorer(self, exp_key: str):
        """On (re)registration, scan from any already-wired+selected source."""
        for (s, d, p) in self._edges:
            if d == exp_key and p == "address":
                sel = self._source_sel.get(s)
                if sel:
                    self._scan_explorer(exp_key, sel[0], sel[1], animate=False)
                    return

    def _clear_explorer(self, exp_key: str):
        st = self._explorers.get(exp_key)
        if not st:
            return
        st["node"] = None; st["mats"] = []; st["assets"] = []
        self._browser_by_key[exp_key].display_items([], [], animate=False)
        self._toolbar_by_key[exp_key].set_count(0)

    def _scan_explorer(self, exp_key: str, node: FolderNode, scan_root: Path,
                       animate: bool = False):
        st = self._explorers.get(exp_key)
        if st is None:
            return
        st["node"] = node
        st["scan_root"] = scan_root
        # Asset library is always recursive; project follows the per-Explorer
        # "include subfolders" toggle.
        st["recursive"] = st["incl_sub"] if scan_root == self._project_root else True
        if not node or not scan_root:
            return
        self._scan_generation += 1
        st["gen"] = self._scan_generation
        st["animate"] = animate
        self.status.showMessage("Scanning…")
        worker = _ScanWorker(node, scan_root, self._scan_generation,
                             recursive=st["recursive"], exp_key=exp_key)
        worker.scan_ready.connect(self._on_scan_done)
        worker.finished.connect(lambda w=worker: self._active_scans.remove(w)
                                if w in self._active_scans else None)
        self._active_scans.append(worker)
        worker.start()

    # ── Initialization ────────────────────────────────────────────────────────

    def init_drive(self, drive_letter: str, status_cb=None):
        def _phase(text: str):
            if status_cb:
                status_cb(text)

        root = Path(f"{drive_letter}:/BACKPACK")
        root.mkdir(parents=True, exist_ok=True)

        self._backpack_root = root
        _meta_set_root(root)
        _preview_set_root(root)
        _phase("Loading tag library…")
        self._tag_registry = load_tag_registry(root)
        for sb in self._sidebars:
            sb.set_tag_registry(self._tag_registry, root)
        for dt in self._details:
            dt.set_tag_registry(self._tag_registry, root)
        for panel in self._collection_panels.values():
            panel.browser.set_tag_registry(self._tag_registry)
        self.status.showMessage(f"BACKPACK: {root}")

        _phase("Building folder tree…")
        self._folder_index = None   # rebuild Quick-Open index for the new tree
        for ft in self._folder_trees:
            ft.load_tree(root, self.settings.quixel_enabled)

        # Restore the active project (if any) into the Project panel(s).
        if self.settings.project_root and Path(self.settings.project_root).exists():
            self._project_root = Path(self.settings.project_root)
            _phase("Restoring project…")
            for pt in self._project_trees:
                pt.load_project(self._project_root)

        _phase("Opening last folder…")
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

    def _on_source_selected(self, node: FolderNode, src_key: str):
        """A folder was picked in an Assets/Project panel. Drive only the
        Explorers wired to THAT source (each Explorer is independent)."""
        role = self._key_role.get(src_key)
        scan_root = self._project_root if role == "project" else self._backpack_root
        self._source_sel[src_key] = (node, scan_root)

        # Shared chrome reflects the latest selection anywhere.
        self._current_node = node
        self._current_scan_root = scan_root
        if role == "assets":
            self.settings.last_folder_path = str(node.disk_path)
        self.address_bar.set_node(node)
        self._update_watcher(node)
        tree = self._tree_by_key.get(src_key)
        if tree:
            tree.select_node(node)

        for exp_key in self._explorers_wired_from(src_key, "address"):
            self._toolbar_by_key[exp_key].clear_search()
            self._explorers[exp_key]["search"] = ""
            self._scan_explorer(exp_key, node, scan_root, animate=True)

    def _on_breadcrumb_selected(self, node: FolderNode):
        # Top breadcrumb isn't a node — route it through the first Assets panel.
        key = self._first_key("assets")
        if key:
            self._on_source_selected(node, key)

    def _reload_current_folder(self):
        # Re-scan every Explorer from its own current folder (used by import,
        # delete, the fs-watcher and the Inspector refresh button).
        for exp_key, st in self._explorers.items():
            if st["node"] and st["scan_root"]:
                self._scan_explorer(exp_key, st["node"], st["scan_root"], animate=False)

    def _on_scan_done(self, materials: list, assets: list, generation: int, exp_key: str):
        st = self._explorers.get(exp_key)
        if st is None or generation != st["gen"]:
            return   # stale scan or explorer gone
        st["mats"] = materials
        st["assets"] = assets
        self._size_cache.clear()   # fresh scan → file sizes may have changed
        if self._synapse_panels:   # rebuild Synapse graphs wired to this Explorer
            self._push_synapse_from(exp_key)
        self._sync_tag_registry(materials, assets)
        # Refresh the wired Filters panel's chips from this Explorer's content.
        fkey = self._filter_source_for(exp_key)
        if fkey and fkey in self._sidebar_by_key:
            sb = self._sidebar_by_key[fkey]
            sb.set_tag_registry(self._tag_registry, self._backpack_root)
            sb.load_tags_from_scan(materials, assets)
        for dt in self._details:
            dt.set_tag_registry(self._tag_registry, self._backpack_root)
        self._browser_by_key[exp_key].set_tag_registry(self._tag_registry)
        self._apply_explorer(exp_key)

    def _navigate_to_path(self, disk_path: Path):
        if not self._backpack_root:
            return
        key = self._first_key("assets")
        if not key:
            return
        root_node = build_folder_tree(self._backpack_root, self.settings.quixel_enabled)
        node = self._find_node(root_node, disk_path)
        self._on_source_selected(node or root_node, key)

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
        key = self._first_key("assets")
        if not key:
            return
        root_node = build_folder_tree(self._backpack_root, self.settings.quixel_enabled)
        self._on_source_selected(root_node, key)

    # ── Scanning / refresh ──────────────────────────────────────────────────

    def _full_refresh(self):
        if not self._backpack_root:
            return
        for ft in self._folder_trees:
            ft.load_tree(self._backpack_root, self.settings.quixel_enabled)
        self._reload_current_folder()

    def _sync_tag_registry(self, materials: list, assets: list):
        if not self._backpack_root:
            return
        changed = False
        for mat in materials:
            for t in mat.meta.tags:
                if t not in self._tag_registry:
                    head_path = mat.preview_path if mat.preview_path else None
                    get_or_create_tag(self._backpack_root, self._tag_registry, t,
                                      self.settings.accent_color, head_path)
                    changed = True
        for asset in assets:
            for t in asset.meta.tags:
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

    def _on_tags_changed(self, tags, filter_key: str):
        _, res = self._filter_state.get(filter_key, ([], []))
        self._filter_state[filter_key] = (tags, res)
        for exp_key in self._explorers_wired_from(filter_key, "filter"):
            self._apply_explorer(exp_key)

    def _on_resolutions_changed(self, resolutions, filter_key: str):
        tags, _ = self._filter_state.get(filter_key, ([], []))
        self._filter_state[filter_key] = (tags, resolutions)
        for exp_key in self._explorers_wired_from(filter_key, "filter"):
            self._apply_explorer(exp_key)

    def _on_search_changed(self, text: str, exp_key: str):
        self._explorers[exp_key]["search"] = text.lower()
        self._apply_explorer(exp_key)

    def _on_sort_changed(self, mode: str, exp_key: str) -> None:
        self._explorers[exp_key]["sort"] = mode
        self._apply_explorer(exp_key)

    def _on_latest_changed(self, on: bool, exp_key: str) -> None:
        self._explorers[exp_key]["latest"] = on
        self._apply_explorer(exp_key)   # re-filter cached items; no rescan

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

    def _on_recursive_changed(self, on: bool, exp_key: str) -> None:
        st = self._explorers[exp_key]
        st["incl_sub"] = on
        # Only project-sourced Explorers re-scan; the asset library is recursive.
        if st["node"] and st["scan_root"] == self._project_root:
            self._scan_explorer(exp_key, st["node"], st["scan_root"], animate=True)

    def _file_size(self, path) -> int:
        """Cached file size (bytes) for the 'size' sort. Cache is cleared on
        every scan, so it stays correct across re-filter/re-search re-applies."""
        key = str(path)
        size = self._size_cache.get(key)
        if size is None:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            self._size_cache[key] = size
        return size

    def _apply_explorer(self, exp_key: str):
        st = self._explorers.get(exp_key)
        if st is None:
            return
        q = st["search"]
        sort = st["sort"]
        fkey = self._filter_source_for(exp_key)
        tags, res = self._filter_state.get(fkey, ([], [])) if fkey else ([], [])

        filtered_mats = [
            m for m in st["mats"]
            if self._match_tags(m.meta.tags, m.meta.favorite, tags)
            and self._match_resolution_mat(m, res)
            and self._match_search(q, m.name, m.meta.tags, m.meta.notes, m.meta.surface_type)
        ]
        filtered_assets = [
            a for a in st["assets"]
            if self._match_tags(a.meta.tags, a.meta.favorite, tags)
            and self._match_resolution_asset(a, res)
            and self._match_search(q, a.filename, a.meta.tags, a.meta.notes, a.sub_type)
        ]
        if st["latest"]:
            filtered_assets = self._collapse_latest(filtered_assets)

        if sort == "size":
            filtered_mats   = sorted(filtered_mats,
                                     key=lambda m: sum(self._file_size(mp.path) for mp in m.maps),
                                     reverse=True)
            filtered_assets = sorted(filtered_assets,
                                     key=lambda a: self._file_size(a.path), reverse=True)
        else:
            filtered_mats   = sorted(filtered_mats,   key=lambda m: _natural_key(m.name))
            filtered_assets = sorted(filtered_assets, key=lambda a: _natural_key(a.filename))

        br = self._browser_by_key[exp_key]
        tb = self._toolbar_by_key[exp_key]
        br.set_list_root(st["node"].disk_path if st["node"] else None)
        br.display_items(filtered_mats, filtered_assets, animate=st.pop("animate", False))
        st["animate"] = False
        count = len(filtered_mats) + len(filtered_assets)
        tb.set_count(count)
        path_str = st["node"].breadcrumb_display() if st["node"] else ""
        self.status.showMessage(f"{path_str}  —  {count} item(s)")

    @staticmethod
    def _match_search(query: str, name: str, tags: list,
                      notes: str, sub_type: str = "") -> bool:
        if not query:
            return True
        return (query in name.lower()
                or query in notes.lower()
                or query in sub_type.lower()
                or any(query in t.lower() for t in tags))

    @staticmethod
    def _match_resolution_mat(mat: ScannedMaterial, active_res: list) -> bool:
        if not active_res:
            return True
        for a in mat.maps:
            tag = detect_resolution_tag(a.filename)
            if tag and tag in active_res:
                return True
        return False

    @staticmethod
    def _match_resolution_asset(asset: ScannedAsset, active_res: list) -> bool:
        if not active_res:
            return True
        tag = detect_resolution_tag(asset.filename)
        return tag in active_res if tag else False

    @staticmethod
    def _match_tags(item_tags: list[str], is_fav: bool, active_tags: list) -> bool:
        if not active_tags:
            return True
        for t in active_tags:
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

    def _on_selection_changed(self, count: int, items: list, exp_key: str):
        if exp_key in self._explorers:
            self._explorers[exp_key]["sel"] = list(items)   # per-Explorer selection
        if count > 0:
            self._last_sel_items = list(items)   # global fallback for Collections
        if count == 0:
            return
        targets = [self._detail_by_key[d] for (s, d, p) in self._edges
                   if s == exp_key and p == "select" and d in self._detail_by_key]
        for dt in targets:
            if count == 1 and items:
                kind, obj = items[0]
                if kind == "material":
                    dt.show_material(obj)
                else:
                    dt.show_asset(obj)
            else:
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

        seen: set = set()
        for st in self._explorers.values():
            for mat in st["mats"]:
                if mat.path in seen:
                    continue
                seen.add(mat.path)
                if tag_name in mat.meta.tags:
                    mat.meta.tags.remove(tag_name)
                    write_material_meta(mat.path, mat.meta)
            for asset in st["assets"]:
                if asset.path in seen:
                    continue
                seen.add(asset.path)
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
