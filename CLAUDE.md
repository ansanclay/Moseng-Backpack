# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
pip install -r requirements.txt
python main.py
```

Settings are stored in `~/.moseng_backpack/settings.json` (drive letter, accent color, window size, last folder).

## Architecture

Moseng Backpack is a PySide6 desktop app for browsing a structured asset library on a designated drive (`DRIVE:/BACKPACK/`).

### Data flow

1. **`FolderNode` tree** (`core/folder_model.py`) — describes the BACKPACK folder structure. Each node has a `scan_mode` that controls how its directory is interpreted. Modes: `"materials"`, `"model_folder"`, `"texture"`, `"gobo"`, `"hdri"`, `"model"` (loose files), `"none"` (category container).

2. **Scanner** (`core/scanner.py`) — reads the filesystem and returns `ScannedMaterial` and `ScannedAsset` objects. The main entry point per-navigation is `scan_folder_recursive(node, backpack_root)` → `scan_folder_node()`. `scan_backpack()` handles the full-tree case.
   - `"materials"` / `"model_folder"` modes: use `_collect_material_dirs()` / `_collect_model_asset_dirs()` which recursively walk any depth, skipping SOURCE_* containers, and return only leaf asset folders.
   - `_is_material_dir()` / `_is_model_asset_dir()` — the heuristics that decide whether a folder is a leaf or container. Key rule for models: a direct `.fbx`/`.obj`/etc. file is always definitive regardless of subdirectories.

3. **`MainWindow`** (`ui/main_window.py`) — runs a `_ScanWorker` background thread on folder navigation, receives `(materials, assets)`, applies filters, passes to `AssetBrowser.display_items()`.

4. **`AssetBrowser`** (`ui/asset_browser.py`) — `QListView` in IconMode. Calls `_rebuild_model()` to populate a flat `QStandardItemModel`. Materials can expand in-place to show child map items (spacer items pad children to row boundaries). Immediately pre-fetches thumbnails for the first visible page via `delegate.prefetch()`.

5. **`ThumbnailDelegate`** (`ui/delegates/thumbnail_delegate.py`) — custom paint-only delegate. `paintEvent` never does I/O. All decoding is on a `QThreadPool` (up to `min(8, idealThreadCount())` threads). Jobs use an ever-increasing priority so the most recently visible item always runs first. Results come back as `QImage` → converted to `QPixmap` on the main thread → stored in `QPixmapCache`.

### Metadata system

Every asset file has a sidecar at `<parent>/.json/<stem>_backpack.json` (`AssetMeta`). Every material folder has `<folder>/.json/<folder_name>_backpack.json` (`MaterialMeta`). Old sibling-JSON paths are auto-migrated on first read. Write/read via `core/metadata.py`.

### Preview cache

`core/preview.py` generates 512×512 JPEG thumbnails at `<source_folder>/.preview/<stem>_preview.jpg` using Pillow (EXR/HDR via imageio + Reinhard tone-mapping). `sync_previews()` / `clean_orphaned_previews()` are triggered by the Refresh button. Thumbnail loading is fast only when preview files exist; falling back to full-size originals is slow.

### PBR map detection

`core/map_detector.py` — the authoritative PBR keyword detector. `detect_sub_type(stem)` splits on `[_\-\s]+`, checks parts last→first, single-letter codes (`D`, `N`, `R`, `M`, `H`) only match as the final part. Resolution/variant/LOD suffixes (`_4K`, `_VAR1`, `_LOD0`) are stripped first. `group_into_materials()` groups flat file lists into `{base_name: {sub_type: Path}}` dicts.

`constants.py` has a legacy `SUB_TYPE_PATTERNS` list (regex-based) used as fallback in `scanner._detect_sub_type()`.

### Folder structure on disk

```
DRIVE:/BACKPACK/
  Materials/
    <SOURCE>/          ← container, any depth
      <MaterialName>/  ← leaf: direct images → ScannedMaterial
  Models/
    3D_Assets/
      <SOURCE>/
        <AssetName>/   ← leaf: direct .fbx/.obj/etc. → ScannedMaterial (model_folder)
    Foliages/
  Images/
    Textures/  Photos/  Gobos/  HDRI/   ← loose file scans
  Quixel/Downloaded/{surface,3d,3dplant,atlas,brush,smartmaterial}/
```

Hidden dirs: `.preview/` (thumbnail cache), `.json/` (metadata sidecars), `.thumbs/`, `__MACOSX` — all skipped during scanning (`_SCAN_SKIP_DIRS`).

### Key design constraints

- `paintEvent` must never do disk I/O — decode only via `QThreadPool`.
- `ScannedMaterial` is reused for both PBR texture sets and 3D asset folders (`model_folder` mode). Maps list contains `ScannedAsset` entries with `asset_type="model"` (for mesh files) or `asset_type="texture"` (for bundled textures).
- The `sync_json_files()` / `sync_previews()` functions mirror the folder-walking logic of the scanner exactly — when adding a new scan mode, update all three.
- Tag colors are deterministic from the tag name (`constants.tag_color_for_name()`), not random.
