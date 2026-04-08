"""Main application window - filesystem-based architecture."""

import os
import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStatusBar, QMessageBox, QApplication, QFileDialog,
)
from PySide6.QtCore import Qt

from backpack.core.settings import AppSettings, save_settings
from backpack.core.scanner import (
    scan_backpack, sync_json_files, ScannedMaterial, ScannedAsset,
)
from backpack.core.metadata import (
    delete_asset_meta, delete_material_meta,
    read_asset_meta, write_asset_meta,
)
from backpack.core.preview import sync_previews, clean_orphaned_previews
from backpack.core.tag_registry import (
    load_tag_registry, save_tag_registry, get_or_create_tag, set_tag_head, TagInfo,
)
from backpack.ui.search_bar import SearchBar
from backpack.ui.tag_bar import SidebarPanel
from backpack.ui.asset_browser import AssetBrowser
from backpack.ui.asset_detail import AssetDetailPanel
from backpack.ui.drop_zone import DropOverlay
from backpack.ui.dialogs.import_dialog import ImportDialog
from backpack.ui.dialogs.settings_dialog import SettingsDialog


class MainWindow(QMainWindow):

    def __init__(self, settings: AppSettings):
        super().__init__()
        self.settings = settings
        self._backpack_root: Path | None = None

        # All scanned data
        self._materials: list[ScannedMaterial] = []
        self._assets: list[ScannedAsset] = []
        self._tag_registry: dict[str, TagInfo] = {}

        # Current filters
        self._search = ""
        self._type_filter = ""
        self._active_tags: list[str] = []

        self.setWindowTitle("Moseng Backpack")
        self.setMinimumSize(1100, 700)
        self.resize(settings.window_width, settings.window_height)

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Top bar
        self.search_bar = SearchBar()
        main_layout.addWidget(self.search_bar)

        # Content
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)

        # Left sidebar (tags)
        self.sidebar = SidebarPanel(self.settings.accent_color)
        content.addWidget(self.sidebar)

        # Center: browser + drop zone
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        self.browser = AssetBrowser()
        self.browser.set_card_size(self.settings.grid_card_size)
        center_layout.addWidget(self.browser, stretch=1)

        content.addWidget(center, stretch=1)

        # Right detail panel
        self.detail = AssetDetailPanel()
        content.addWidget(self.detail)

        main_layout.addLayout(content, stretch=1)

        # Drop overlay (full-screen, on top of central widget)
        self.drop_overlay = DropOverlay(central)

        # Enable drag-and-drop on the main window
        self.setAcceptDrops(True)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready")

    def _connect_signals(self):
        # Search bar
        self.search_bar.search_changed.connect(self._on_search)
        self.search_bar.type_filter_changed.connect(self._on_type_filter)
        self.search_bar.settings_requested.connect(self._open_settings)
        self.search_bar.refresh_requested.connect(self._refresh_sync)
        self.search_bar.import_requested.connect(self._on_import_btn)

        # Sidebar
        self.sidebar.tags_changed.connect(self._on_tags_changed)
        self.sidebar.add_tag_requested.connect(self._add_global_tag)

        # Browser
        self.browser.asset_selected.connect(self.detail.show_asset)
        self.browser.material_selected.connect(self.detail.show_material)
        self.browser.asset_double_clicked.connect(self._open_asset)
        self.browser.material_double_clicked.connect(self._open_material)
        self.browser.selection_changed.connect(self._on_selection_changed)
        self.browser.delete_requested.connect(self._delete_items)

        # Drop overlay
        self.drop_overlay.files_dropped.connect(self._on_drop)

        # Detail
        self.detail.refresh_requested.connect(self._full_refresh)
        self.detail.tag_head_changed.connect(self._on_tag_head_changed)

    # ── Initialization ──

    def init_drive(self, drive_letter: str):
        root = Path(f"{drive_letter}:/BACKPACK")
        root.mkdir(parents=True, exist_ok=True)
        # Ensure subfolders
        (root / "Materials").mkdir(exist_ok=True)
        (root / "Textures").mkdir(exist_ok=True)
        (root / "Gobo").mkdir(exist_ok=True)
        (root / "Other").mkdir(exist_ok=True)

        self._backpack_root = root
        self._tag_registry = load_tag_registry(root)
        self.sidebar.set_tag_registry(self._tag_registry, root)
        self.detail.set_tag_registry(self._tag_registry, root)
        self.status.showMessage(f"BACKPACK: {root}")
        self._full_refresh()

    # ── Scanning ──

    def _full_refresh(self):
        """Rescan the entire BACKPACK folder."""
        if not self._backpack_root:
            return

        self._materials, self._assets = scan_backpack(self._backpack_root)

        # Ensure all tags in scanned data have registry entries
        self._sync_tag_registry()

        self.sidebar.set_tag_registry(self._tag_registry, self._backpack_root)
        self.detail.set_tag_registry(self._tag_registry, self._backpack_root)
        self.sidebar.load_tags_from_scan(self._materials, self._assets)
        self._apply_filters()

    def _sync_tag_registry(self):
        """Ensure every tag used in scanned data has a registry entry with color."""
        if not self._backpack_root:
            return
        changed = False
        for mat in self._materials:
            for t in mat.meta.tags:
                if t not in self._tag_registry:
                    head_path = mat.preview_path if mat.preview_path else None
                    get_or_create_tag(self._backpack_root, self._tag_registry, t,
                                     self.settings.accent_color, head_path)
                    changed = True
        for asset in self._assets:
            for t in asset.meta.tags:
                if t not in self._tag_registry:
                    head_path = asset.path if asset.path.suffix.lower() in (
                        ".png", ".jpg", ".jpeg", ".bmp", ".tga") else None
                    get_or_create_tag(self._backpack_root, self._tag_registry, t,
                                     self.settings.accent_color, head_path)
                    changed = True
        if changed:
            save_tag_registry(self._backpack_root, self._tag_registry)

    def _refresh_sync(self):
        """Sync JSON files, generate preview caches, then refresh."""
        if not self._backpack_root:
            return

        self.status.showMessage("Syncing JSON metadata...")
        QApplication.processEvents()

        created, removed = sync_json_files(self._backpack_root)

        self.status.showMessage("Generating preview cache...")
        QApplication.processEvents()

        previews = sync_previews(self._backpack_root)
        orphans = clean_orphaned_previews(self._backpack_root)

        self._full_refresh()
        self.status.showMessage(
            f"Sync: {created} JSON created, {removed} orphan removed, "
            f"{previews} preview(s) cached, {orphans} stale preview(s) removed"
        )

    # ── Filtering ──

    def _on_search(self, q):
        self._search = q.lower()
        self._apply_filters()

    def _on_type_filter(self, f):
        self._type_filter = f
        self._apply_filters()

    def _on_tags_changed(self, tags):
        self._active_tags = tags
        self._apply_filters()

    def _apply_filters(self):
        """Filter scanned data and display in browser."""
        filtered_mats = []
        filtered_assets = []

        show_materials = self._type_filter in ("", "material")
        show_textures = self._type_filter in ("", "texture")
        show_gobos = self._type_filter in ("", "gobo")
        show_other = self._type_filter in ("", "other")

        # Filter materials
        if show_materials:
            for mat in self._materials:
                if not self._match_search_mat(mat):
                    continue
                if not self._match_tags(mat.meta.tags, mat.meta.favorite):
                    continue
                filtered_mats.append(mat)

        # Filter loose assets
        for asset in self._assets:
            at = asset.asset_type
            if at == "texture" and not show_textures:
                continue
            if at == "gobo" and not show_gobos:
                continue
            if at == "other" and not show_other:
                continue
            if at not in ("texture", "gobo", "other") and not show_other:
                continue

            if not self._match_search_asset(asset):
                continue
            if not self._match_tags(asset.meta.tags, asset.meta.favorite):
                continue
            filtered_assets.append(asset)

        self.browser.display_items(filtered_mats, filtered_assets)
        count = len(filtered_mats) + len(filtered_assets)
        self.status.showMessage(f"{count} item(s)")

    def _match_search_mat(self, mat: ScannedMaterial) -> bool:
        if not self._search:
            return True
        return (self._search in mat.name.lower()
                or self._search in mat.meta.surface_type.lower()
                or any(self._search in t.lower() for t in mat.meta.tags))

    def _match_search_asset(self, asset: ScannedAsset) -> bool:
        if not self._search:
            return True
        return (self._search in asset.filename.lower()
                or self._search in asset.sub_type.lower()
                or any(self._search in t.lower() for t in asset.meta.tags))

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

    # ── Selection ──

    def _on_selection_changed(self, count: int):
        if count > 1:
            self.detail.show_multi_selection(count)

    def _open_asset(self, asset: ScannedAsset):
        if asset.path.exists():
            os.startfile(str(asset.path))

    def _open_material(self, mat: ScannedMaterial):
        if mat.path.exists():
            os.startfile(str(mat.path))

    # ── Import ──

    def _on_drop(self, paths):
        self._run_import(paths)

    def _on_import_btn(self):
        """Import via file dialog."""
        paths = QFileDialog.getOpenFileNames(
            self, "Select files to import", "", "All Files (*.*)"
        )[0]
        if not paths:
            # Try folder
            folder = QFileDialog.getExistingDirectory(self, "Select folder to import")
            if folder:
                paths = [folder]
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

            # Sync JSONs for new files
            sync_json_files(self._backpack_root)

            # Generate preview caches for imported files
            from backpack.core.preview import generate_previews_for_folder, ensure_preview
            if dlg.chosen_type == "material" and dlg.imported_folders:
                # For materials: only generate preview for the thumbnail image
                for folder in dlg.imported_folders:
                    self._generate_material_preview(folder)
            elif dlg.imported_dest_folder:
                # For loose assets: generate previews for the dest folder
                generate_previews_for_folder(dlg.imported_dest_folder)

            self._full_refresh()
            self.status.showMessage(f"Imported {dlg.imported_count} file(s)")

    def _generate_material_preview(self, folder: Path):
        """Generate preview cache for a material folder's thumbnail image only."""
        import re
        from backpack.core.preview import ensure_preview
        from backpack.constants import QUIXEL_PREVIEW_PATTERNS

        preview_file = None

        # Priority 1: Quixel preview image
        for f in folder.iterdir():
            if not f.is_file():
                continue
            for pat in QUIXEL_PREVIEW_PATTERNS:
                if pat.search(f.stem) and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".tga", ".bmp"):
                    preview_file = f
                    break
            if preview_file:
                break

        # Priority 2: Albedo/diffuse map
        if not preview_file:
            for f in folder.iterdir():
                if not f.is_file():
                    continue
                if re.search(r"(diffuse|diff|albedo|base_?color|col)\b", f.stem, re.I):
                    if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".bmp", ".exr"):
                        preview_file = f
                        break

        # Priority 3: First image
        if not preview_file:
            for f in folder.iterdir():
                if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".tga", ".bmp"):
                    preview_file = f
                    break

        if preview_file:
            ensure_preview(preview_file)

    # ── Delete ──

    def _delete_items(self, items: list[tuple[str, object]]):
        """Delete selected items from disk and their JSON metadata."""
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
        self._full_refresh()

    # ── Tags ──

    def _on_tag_head_changed(self, tag_name: str, head_path):
        """Handle tag head promotion from detail panel."""
        if not self._backpack_root:
            return
        from backpack.core.preview import ensure_preview
        # Ensure preview exists for the head asset
        head_path = Path(head_path) if not isinstance(head_path, Path) else head_path
        ensure_preview(head_path)
        set_tag_head(self._backpack_root, self._tag_registry, tag_name, head_path)
        save_tag_registry(self._backpack_root, self._tag_registry)
        self._full_refresh()

    def _add_global_tag(self):
        """Placeholder - tags are per-item via JSON. This just refreshes."""
        from PySide6.QtWidgets import QInputDialog
        QInputDialog.getText(self, "Info",
                             "Tags are added per-item in the detail panel.\n"
                             "Select an item and click + in the Tags section.")

    # ── Settings ──

    def _open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        dlg.settings_changed.connect(self._apply_settings)
        if dlg.exec():
            self._apply_settings()

    def _apply_settings(self):
        save_settings(self.settings)
        self.sidebar.set_accent(self.settings.accent_color)

        # Re-init drive if changed
        if self.settings.drive_letter:
            root = Path(f"{self.settings.drive_letter}:/BACKPACK")
            if root != self._backpack_root:
                self.init_drive(self.settings.drive_letter)
            else:
                self._full_refresh()

    # ── Drag overlay ──

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.drop_overlay.show_overlay()

    def dragLeaveEvent(self, event):
        self.drop_overlay.hide_overlay()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep overlay sized to central widget
        if hasattr(self, "drop_overlay"):
            self.drop_overlay.setGeometry(self.centralWidget().rect())

    def closeEvent(self, event):
        # Save window size
        self.settings.window_width = self.width()
        self.settings.window_height = self.height()
        self.settings.grid_card_size = self.browser._card_size
        save_settings(self.settings)
        super().closeEvent(event)
