"""Filesystem scanner - reads the BACKPACK folder tree in real-time.

Folder structure:
  DRIVE:\BACKPACK\Materials\QUIXEL\MaterialName\...
  DRIVE:\BACKPACK\Materials\Other\MaterialName\...
  DRIVE:\BACKPACK\Textures\file.png
  DRIVE:\BACKPACK\Gobo\file.ies
  DRIVE:\BACKPACK\Other\file.ext
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backpack.constants import IMAGE_EXTENSIONS, SUB_TYPE_PATTERNS, QUIXEL_PREVIEW_PATTERNS
from backpack.core.map_detector import detect_sub_type as _detect_sub_type_new
from backpack.core.metadata import (
    read_asset_meta, read_material_meta, write_asset_meta, write_material_meta,
    json_path_for_file, json_path_for_material,
    AssetMeta, MaterialMeta,
)
from backpack.core.preview import preview_path_for, PREVIEW_DIR_NAME
from backpack.core.metadata import JSON_DIR_NAME


@dataclass
class ScannedAsset:
    """A single file found on disk."""
    path: Path
    filename: str
    rel_path: str              # relative to BACKPACK root
    asset_type: str            # texture, hdri, gobo, model, other
    sub_type: str = ""         # albedo, normal, etc.
    meta: AssetMeta = field(default_factory=AssetMeta)
    has_json: bool = False
    material_folder: Optional[str] = None  # parent material name if applicable
    preview_cache: Optional[Path] = None   # path to .preview/ cached thumbnail


@dataclass
class ScannedMaterial:
    """A material folder containing multiple texture maps."""
    path: Path
    name: str
    rel_path: str
    source: str = "other"      # quixel, poliigon, other
    preview_path: Optional[Path] = None
    maps: list[ScannedAsset] = field(default_factory=list)
    meta: MaterialMeta = field(default_factory=MaterialMeta)
    has_json: bool = False
    preview_cache: Optional[Path] = None   # cached thumbnail for the material preview


# File extensions by category
_TEXTURE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".bmp", ".exr", ".tx"}
_HDRI_EXTS = {".hdr", ".exr"}
_GOBO_EXTS = {".ies"}
_MODEL_EXTS = {".obj", ".fbx", ".abc", ".usd", ".usda", ".usdc", ".usdz", ".bgeo"}
_SCENE_EXTS = {".hip", ".hipnc", ".hiplc", ".blend", ".ma", ".mb"}
_SKIP_EXTS = {".json", ".db", ".db-wal", ".db-shm", ".rat"}

_SCAN_SKIP_DIRS = frozenset({PREVIEW_DIR_NAME, JSON_DIR_NAME, ".thumbs", "__MACOSX"})
_ALL_IMAGE_EXTS = _TEXTURE_EXTS | _HDRI_EXTS
_MODEL_ASSET_EXTS = _ALL_IMAGE_EXTS | _MODEL_EXTS | _SCENE_EXTS


def _is_material_dir(folder: Path) -> bool:
    """True if *folder* is a leaf material folder (not a category container).

    Rules (checked in order):
    1. If any immediate subdirectory has a PBR-keyword name (Albedo/, Normal/…)
       → structured material layout → True.
    2. If any immediate subdirectory (non-skip, non-PBR-keyword) itself contains
       image files → this folder is a category/container → False.
       (Stray catalog/preview images sitting next to sub-material folders are
        ignored so that SOURCE_Foo/ is never mistaken for a material.)
    3. If the folder contains at least one direct image file → material → True.
    4. Otherwise → container → False.
    """
    try:
        entries = list(folder.iterdir())
    except PermissionError:
        return False

    has_direct_images = False
    for entry in entries:
        if entry.name.startswith(".") or entry.name in _SCAN_SKIP_DIRS:
            continue
        if entry.is_file() and entry.suffix.lower() in _ALL_IMAGE_EXTS:
            has_direct_images = True
        elif entry.is_dir():
            if _detect_sub_type(entry.name):
                return True   # PBR-keyword subfolder → structured material
            # Non-PBR subdir: peek inside — if it has images this folder is a container
            try:
                for sub in entry.iterdir():
                    if sub.is_file() and sub.suffix.lower() in _ALL_IMAGE_EXTS:
                        return False  # sub-material found → we are a container
            except PermissionError:
                pass

    return has_direct_images


def _collect_material_dirs(search_root: Path) -> list[Path]:
    """Return all material folders inside *search_root* at any depth.

    Category/container folders (no direct images, no PBR-keyword subfolders)
    are recursed into transparently — so any nesting depth works:

        Materials/PBR_Materials/SOURCE_Texturescom/Asphalt 01 [8K]/  → found
        Materials/Quixel/Rock_Mossy/                                  → found
        Materials/MyLib/Stone/Granite/Polished/                       → found
    """
    results: list[Path] = []
    _walk_for_materials(search_root, results)
    return results


def _walk_for_materials(folder: Path, out: list) -> None:
    if _is_material_dir(folder):
        out.append(folder)
        return   # don't recurse inside a material folder itself

    try:
        entries = list(folder.iterdir())
    except PermissionError:
        return

    for entry in sorted(entries):
        if entry.is_dir() and not entry.name.startswith(".") \
                and entry.name not in _SCAN_SKIP_DIRS:
            _walk_for_materials(entry, out)


# ─────────────────────────────────────────────────────────────────────────────
# 3-D asset folder detection  (scan_mode="model_folder")
# ─────────────────────────────────────────────────────────────────────────────

def _is_model_asset_dir(folder: Path) -> bool:
    """True if *folder* is a leaf 3-D asset folder (not a category container).

    Same heuristic as _is_material_dir but extended to model file types:
    1. If a direct child is a model/scene file → asset folder.
    2. If any non-skip subdir itself contains a model or image file → container.
    3. If the folder has at least one direct image/model file and no sub-asset
       subdirs → asset folder.
    """
    try:
        entries = list(folder.iterdir())
    except PermissionError:
        return False

    has_direct_assets = False
    for entry in entries:
        if entry.name.startswith(".") or entry.name in _SCAN_SKIP_DIRS:
            continue
        if entry.is_file():
            if entry.suffix.lower() in _MODEL_ASSET_EXTS:
                has_direct_assets = True
        elif entry.is_dir():
            # Peek inside — if it has any relevant file this folder is a container
            try:
                for sub in entry.iterdir():
                    if sub.is_file() and sub.suffix.lower() in _MODEL_ASSET_EXTS:
                        return False   # sub-asset found → we are a container
            except PermissionError:
                pass

    return has_direct_assets


def _collect_model_asset_dirs(search_root: Path) -> list[Path]:
    """Return all 3-D asset folders inside *search_root* at any depth."""
    results: list[Path] = []
    _walk_for_model_assets(search_root, results)
    return results


def _walk_for_model_assets(folder: Path, out: list) -> None:
    if _is_model_asset_dir(folder):
        out.append(folder)
        return
    try:
        entries = list(folder.iterdir())
    except PermissionError:
        return
    for entry in sorted(entries):
        if entry.is_dir() and not entry.name.startswith(".") \
                and entry.name not in _SCAN_SKIP_DIRS:
            _walk_for_model_assets(entry, out)


def _scan_model_asset_folder(
    folder: Path, source: str, backpack_root: Path
) -> Optional[ScannedMaterial]:
    """Scan a 3-D asset folder.

    Model files (.fbx, .obj, …) become ScannedAsset entries with
    asset_type="model".  Bundled texture maps are included with their
    detected sub_type.  Returns a ScannedMaterial so the browser and
    detail panel need no changes.
    """
    rel = str(folder.relative_to(backpack_root)).replace("\\", "/")
    meta = read_material_meta(folder)
    has_json = json_path_for_material(folder).exists()

    mat = ScannedMaterial(
        path=folder,
        name=folder.name,
        rel_path=rel,
        source=source,
        meta=meta,
        has_json=has_json,
    )

    preview_path: Optional[Path] = None
    _SKIP_DIRS = {PREVIEW_DIR_NAME, JSON_DIR_NAME, ".thumbs", "__MACOSX"}

    # Collect (file, subfolder_hint) from direct children + one subdir level
    files_to_scan: list[tuple[Path, str]] = []
    for entry in sorted(folder.iterdir()):
        if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
            continue
        if entry.is_file():
            files_to_scan.append((entry, ""))
        elif entry.is_dir():
            hint = entry.name
            for sub_f in sorted(entry.iterdir()):
                if sub_f.is_file() and not sub_f.name.startswith("."):
                    files_to_scan.append((sub_f, hint))

    for f, subfolder_hint in files_to_scan:
        ext = f.suffix.lower()
        if ext in _SKIP_EXTS:
            continue

        # ── Preview detection ─────────────────────────────────────────────────
        is_preview = False
        for pat in QUIXEL_PREVIEW_PATTERNS:
            if pat.search(f.stem) or (subfolder_hint and pat.search(subfolder_hint)):
                is_preview = True
                break
        if not is_preview and f.stem.lower() == folder.name.lower():
            is_preview = True

        if is_preview and ext in _TEXTURE_EXTS:
            preview_path = f
            mat.meta.preview_file = f.name
            continue

        # ── Model / scene files ───────────────────────────────────────────────
        if ext in _MODEL_EXTS or ext in _SCENE_EXTS:
            asset_meta = read_asset_meta(f)
            pcache = preview_path_for(f)
            asset = ScannedAsset(
                path=f,
                filename=f.name,
                rel_path=str(f.relative_to(backpack_root)).replace("\\", "/"),
                asset_type="model",
                sub_type=ext.lstrip("."),   # "fbx", "obj", "abc", …
                meta=asset_meta,
                has_json=json_path_for_file(f).exists(),
                material_folder=folder.name,
                preview_cache=pcache if pcache.exists() else None,
            )
            mat.maps.append(asset)
            continue

        # ── Bundled textures ──────────────────────────────────────────────────
        if ext in _TEXTURE_EXTS or ext in _HDRI_EXTS:
            sub_type = _detect_sub_type(f.stem)
            if not sub_type and subfolder_hint:
                sub_type = _detect_sub_type(subfolder_hint)
            asset_meta = read_asset_meta(f)
            pcache = preview_path_for(f)
            asset = ScannedAsset(
                path=f,
                filename=f.name,
                rel_path=str(f.relative_to(backpack_root)).replace("\\", "/"),
                asset_type="texture",
                sub_type=sub_type or "",
                meta=asset_meta,
                has_json=json_path_for_file(f).exists(),
                material_folder=folder.name,
                preview_cache=pcache if pcache.exists() else None,
            )
            mat.maps.append(asset)

    mat.preview_path = preview_path

    # Fallback preview: albedo texture → any texture
    if not mat.preview_path:
        for a in mat.maps:
            if a.asset_type == "texture" and a.sub_type == "albedo":
                mat.preview_path = a.path
                break
    if not mat.preview_path:
        for a in mat.maps:
            if a.asset_type == "texture":
                mat.preview_path = a.path
                break

    if mat.preview_path:
        pcache = preview_path_for(mat.preview_path)
        mat.preview_cache = pcache if pcache.exists() else None

    return mat


def scan_backpack(backpack_root: Path) -> tuple[list[ScannedMaterial], list[ScannedAsset]]:
    """Scan the entire BACKPACK folder tree.

    Returns:
        (materials, loose_assets) - materials from Materials/, loose assets from other folders.
    """
    materials: list[ScannedMaterial] = []
    loose_assets: list[ScannedAsset] = []

    if not backpack_root.exists():
        return materials, loose_assets

    # Scan Materials folder — recurse to any depth
    mat_root = backpack_root / "Materials"
    if mat_root.exists():
        for source_dir in sorted(mat_root.iterdir()):
            if not source_dir.is_dir() or source_dir.name.startswith("."):
                continue
            source_name = source_dir.name.lower()
            for mat_dir in _collect_material_dirs(source_dir):
                mat = _scan_material_folder(mat_dir, source_name, backpack_root)
                if mat and mat.maps:
                    materials.append(mat)

    # Scan Textures folder
    tex_root = backpack_root / "Textures"
    if tex_root.exists():
        for f in sorted(tex_root.rglob("*")):
            if f.is_file() and f.suffix.lower() not in _SKIP_EXTS and PREVIEW_DIR_NAME not in f.parts:
                asset = _scan_single_file(f, "texture", backpack_root)
                if asset:
                    loose_assets.append(asset)

    # Scan Gobo folder
    gobo_root = backpack_root / "Gobo"
    if gobo_root.exists():
        for f in sorted(gobo_root.rglob("*")):
            if f.is_file() and f.suffix.lower() not in _SKIP_EXTS and PREVIEW_DIR_NAME not in f.parts:
                asset = _scan_single_file(f, "gobo", backpack_root)
                if asset:
                    loose_assets.append(asset)

    # Scan Other folder
    other_root = backpack_root / "Other"
    if other_root.exists():
        for f in sorted(other_root.rglob("*")):
            if f.is_file() and f.suffix.lower() not in _SKIP_EXTS and PREVIEW_DIR_NAME not in f.parts:
                asset = _scan_single_file(f, "other", backpack_root)
                if asset:
                    loose_assets.append(asset)

    return materials, loose_assets


def _scan_material_folder(
    folder: Path, source: str, backpack_root: Path
) -> Optional[ScannedMaterial]:
    """Scan a single material folder."""
    rel = str(folder.relative_to(backpack_root)).replace("\\", "/")

    meta = read_material_meta(folder)
    has_json = json_path_for_material(folder).exists()

    mat = ScannedMaterial(
        path=folder,
        name=folder.name,
        rel_path=rel,
        source=source,
        meta=meta,
        has_json=has_json,
    )

    preview_path = None

    # ── Collect (file, subfolder_hint) pairs ─────────────────────────────────
    # Direct children first, then one level of subfolders.
    # subfolder_hint carries the subfolder name so we can fall back to it
    # for sub-type detection when the filename has no keyword
    # (e.g.  Rock/Albedo/file.jpg  →  hint="Albedo"  →  sub_type="albedo").
    _SKIP_DIRS = {PREVIEW_DIR_NAME, JSON_DIR_NAME, ".thumbs", "__MACOSX"}

    files_to_scan: list[tuple[Path, str]] = []   # (path, subfolder_hint)

    for entry in sorted(folder.iterdir()):
        if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
            continue
        if entry.is_file():
            files_to_scan.append((entry, ""))
        elif entry.is_dir():
            # One level of subfolders — use the folder name as sub-type hint
            hint = entry.name
            for sub_f in sorted(entry.iterdir()):
                if sub_f.is_file() and not sub_f.name.startswith("."):
                    files_to_scan.append((sub_f, hint))

    for f, subfolder_hint in files_to_scan:
        ext = f.suffix.lower()
        if ext in _SKIP_EXTS:
            continue

        # Detect sub-type from filename; fall back to subfolder name
        sub_type = _detect_sub_type(f.stem)
        if not sub_type and subfolder_hint:
            sub_type = _detect_sub_type(subfolder_hint)

        # Check if preview:
        # 1. Filename matches a known preview pattern (Preview, Thumb, etc.)
        # 2. File stem equals the material folder name → preview thumbnail
        # 3. Subfolder is named "Preview", "Thumb", etc.
        is_preview = False
        for pat in QUIXEL_PREVIEW_PATTERNS:
            if pat.search(f.stem) or (subfolder_hint and pat.search(subfolder_hint)):
                is_preview = True
                break
        if not is_preview and f.stem.lower() == folder.name.lower():
            is_preview = True
            sub_type = "preview"

        if is_preview and ext in _TEXTURE_EXTS:
            preview_path = f
            mat.meta.preview_file = f.name
            continue  # preview-only file — not a map

        if ext in _TEXTURE_EXTS or ext in _HDRI_EXTS:
            asset_meta = read_asset_meta(f)
            pcache = preview_path_for(f)
            asset = ScannedAsset(
                path=f,
                filename=f.name,
                rel_path=str(f.relative_to(backpack_root)).replace("\\", "/"),
                asset_type="texture",
                sub_type=sub_type or "",
                meta=asset_meta,
                has_json=json_path_for_file(f).exists(),
                material_folder=folder.name,
                preview_cache=pcache if pcache.exists() else None,
            )
            mat.maps.append(asset)

    mat.preview_path = preview_path

    # If no preview found, try albedo/diffuse
    if not mat.preview_path:
        for a in mat.maps:
            if a.sub_type in ("albedo", "diffuse"):
                mat.preview_path = a.path
                break
        if not mat.preview_path and mat.maps:
            mat.preview_path = mat.maps[0].path

    # Set material preview_cache from the preview image
    if mat.preview_path:
        pcache = preview_path_for(mat.preview_path)
        mat.preview_cache = pcache if pcache.exists() else None

    return mat


def _scan_single_file(
    filepath: Path, default_type: str, backpack_root: Path
) -> Optional[ScannedAsset]:
    """Scan a single loose file."""
    ext = filepath.suffix.lower()

    # Skip thumbnail caches and json
    if ext in _SKIP_EXTS or filepath.name.startswith("."):
        return None

    asset_type = default_type
    if ext in _HDRI_EXTS and (ext == ".hdr" or _is_hdri_name(filepath.stem)):
        asset_type = "hdri"
    elif ext in _GOBO_EXTS:
        asset_type = "gobo"
    elif ext in _MODEL_EXTS:
        asset_type = "model"
    elif ext in _SCENE_EXTS:
        asset_type = "scene"

    sub_type = _detect_sub_type(filepath.stem)
    meta = read_asset_meta(filepath)

    # Check for cached preview thumbnail
    pcache = preview_path_for(filepath)

    return ScannedAsset(
        path=filepath,
        filename=filepath.name,
        rel_path=str(filepath.relative_to(backpack_root)).replace("\\", "/"),
        asset_type=asset_type,
        sub_type=sub_type or "",
        meta=meta,
        has_json=json_path_for_file(filepath).exists(),
        preview_cache=pcache if pcache.exists() else None,
    )


def _detect_sub_type(stem: str) -> str:
    """Detect PBR map sub-type from filename stem (uses map_detector)."""
    result = _detect_sub_type_new(stem)
    if result:
        return result
    # Legacy fallback for any edge-cases not yet covered
    for pattern, sub in SUB_TYPE_PATTERNS:
        if pattern.search(stem):
            return sub
    return ""


def _is_hdri_name(stem: str) -> bool:
    return bool(re.search(r"(hdri|hdr|env|sky|panorama|pano|dome)", stem, re.I))


def scan_folder_node(node, backpack_root: Path) -> tuple[list[ScannedMaterial], list[ScannedAsset]]:
    """Scan a FolderNode and return (materials, assets).

    scan_mode:
      "materials"     — each subfolder is a ScannedMaterial (PBR texture sets)
      "model_folder"  — each subfolder is a ScannedMaterial with model+texture files
      "texture"       — all files (recursive) are texture ScannedAssets
      "gobo"          — all files (recursive) are gobo ScannedAssets
      "model"         — all files (recursive) are model ScannedAssets (loose)
      "hdri"          — all files are HDRI ScannedAssets
      "none"          — return empty (category node)
    """
    from backpack.core.folder_model import FolderNode  # avoid circular at module level

    materials: list[ScannedMaterial] = []
    assets: list[ScannedAsset] = []

    folder: Path = node.disk_path
    mode: str = node.scan_mode

    if mode == "none" or not folder.exists():
        return materials, assets

    if mode == "materials":
        for mat_dir in _collect_material_dirs(folder):
            mat = _scan_material_folder(mat_dir, folder.name.lower(), backpack_root)
            if mat and mat.maps:
                materials.append(mat)

    elif mode == "model_folder":
        for asset_dir in _collect_model_asset_dirs(folder):
            mat = _scan_model_asset_folder(asset_dir, folder.name.lower(), backpack_root)
            if mat and mat.maps:
                materials.append(mat)

    else:
        # Loose file scan — determine asset type
        type_map = {
            "texture": "texture",
            "gobo":    "gobo",
            "model":   "model",
            "hdri":    "hdri",
        }
        default_type = type_map.get(mode, "texture")

        for f in sorted(folder.rglob("*")):
            if not f.is_file():
                continue
            if f.suffix.lower() in _SKIP_EXTS:
                continue
            if f.name.startswith("."):
                continue
            if PREVIEW_DIR_NAME in f.parts:
                continue
            asset = _scan_single_file(f, default_type, backpack_root)
            if asset:
                assets.append(asset)

    return materials, assets


def scan_folder_recursive(
    node, backpack_root: Path
) -> tuple[list[ScannedMaterial], list[ScannedAsset]]:
    """Scan a node AND all its leaf descendants (for category/parent nodes).

    Category nodes (scan_mode="none") aggregate results from all children.
    Leaf nodes are scanned directly via scan_folder_node.
    """
    materials: list[ScannedMaterial] = []
    assets: list[ScannedAsset] = []

    if node.scan_mode != "none":
        m, a = scan_folder_node(node, backpack_root)
        materials.extend(m)
        assets.extend(a)

    for child in node.children:
        m, a = scan_folder_recursive(child, backpack_root)
        materials.extend(m)
        assets.extend(a)

    return materials, assets


def sync_json_files(backpack_root: Path, since: float | None = None) -> tuple[int, int]:
    """Sync: create missing JSONs, remove orphaned JSONs under the entire BACKPACK tree.

    If ``since`` is given (unix timestamp), skip folders whose mtime is older —
    meaning no files were added or changed there.
    Returns (created_count, removed_count).
    """
    created = 0
    removed = 0

    if not backpack_root.exists():
        return created, removed

    from backpack.core.folder_model import build_folder_tree

    root_node = build_folder_tree(backpack_root, quixel_enabled=True)

    def _folder_changed(folder: Path) -> bool:
        """True if the folder itself was modified after ``since``."""
        if since is None or not folder.exists():
            return True
        return folder.stat().st_mtime > since

    def _walk(node):
        nonlocal created, removed
        if node.scan_mode == "none":
            for child in node.children:
                _walk(child)
            return

        folder: Path = node.disk_path
        if not folder.exists():
            for child in node.children:
                _walk(child)
            return

        if node.scan_mode == "materials":
            _SKIP_SYNC = {PREVIEW_DIR_NAME, JSON_DIR_NAME, ".thumbs", "__MACOSX"}
            for mat_dir in _collect_material_dirs(folder):
                # Skip material folder if unchanged
                if not _folder_changed(mat_dir):
                    continue
                jp = json_path_for_material(mat_dir)
                if not jp.exists():
                    write_material_meta(mat_dir, MaterialMeta())
                    created += 1
                # Scan direct files + one subfolder level (mirrors _scan_material_folder)
                file_hint_pairs: list[tuple[Path, str]] = []
                for entry in mat_dir.iterdir():
                    if entry.name.startswith(".") or entry.name in _SKIP_SYNC:
                        continue
                    if entry.is_file():
                        file_hint_pairs.append((entry, ""))
                    elif entry.is_dir():
                        for sf in entry.iterdir():
                            if sf.is_file() and not sf.name.startswith("."):
                                file_hint_pairs.append((sf, entry.name))

                for f, hint in file_hint_pairs:
                    if f.suffix.lower() in _SKIP_EXTS:
                        continue
                    if PREVIEW_DIR_NAME in f.parts or JSON_DIR_NAME in f.parts:
                        continue
                    sub = _detect_sub_type(f.stem) or (hint and _detect_sub_type(hint)) or ""
                    fjp = json_path_for_file(f)
                    if not fjp.exists():
                        write_asset_meta(f, AssetMeta(asset_type="texture",
                                                      sub_type=sub))
                        created += 1

        elif node.scan_mode == "model_folder":
            _SKIP_SYNC = {PREVIEW_DIR_NAME, JSON_DIR_NAME, ".thumbs", "__MACOSX"}
            for asset_dir in _collect_model_asset_dirs(folder):
                if not _folder_changed(asset_dir):
                    continue
                jp = json_path_for_material(asset_dir)
                if not jp.exists():
                    write_material_meta(asset_dir, MaterialMeta())
                    created += 1
                file_hint_pairs: list[tuple[Path, str]] = []
                for entry in asset_dir.iterdir():
                    if entry.name.startswith(".") or entry.name in _SKIP_SYNC:
                        continue
                    if entry.is_file():
                        file_hint_pairs.append((entry, ""))
                    elif entry.is_dir():
                        for sf in entry.iterdir():
                            if sf.is_file() and not sf.name.startswith("."):
                                file_hint_pairs.append((sf, entry.name))
                for f, hint in file_hint_pairs:
                    ext = f.suffix.lower()
                    if ext in _SKIP_EXTS:
                        continue
                    if PREVIEW_DIR_NAME in f.parts or JSON_DIR_NAME in f.parts:
                        continue
                    if ext in _MODEL_EXTS or ext in _SCENE_EXTS:
                        atype, sub = "model", ext.lstrip(".")
                    else:
                        atype = "texture"
                        sub = _detect_sub_type(f.stem) or (hint and _detect_sub_type(hint)) or ""
                    fjp = json_path_for_file(f)
                    if not fjp.exists():
                        write_asset_meta(f, AssetMeta(asset_type=atype, sub_type=sub))
                        created += 1

        else:
            # Skip entire folder if unchanged
            if not _folder_changed(folder):
                for child in node.children:
                    _walk(child)
                return
            dtype = node.scan_mode if node.scan_mode in ("texture", "gobo", "model", "hdri") else "texture"
            for f in folder.rglob("*"):
                if not f.is_file():
                    continue
                if f.suffix.lower() in _SKIP_EXTS or f.name.startswith("."):
                    continue
                if PREVIEW_DIR_NAME in f.parts or JSON_DIR_NAME in f.parts:
                    continue
                fjp = json_path_for_file(f)
                if not fjp.exists():
                    write_asset_meta(f, AssetMeta(asset_type=dtype,
                                                  sub_type=_detect_sub_type(f.stem)))
                    created += 1

        # Remove orphaned JSONs — only when folder was changed or full sync
        # JSONs now live in .json/ subfolders; source files are one level up
        if _folder_changed(folder):
            for json_dir in folder.rglob(JSON_DIR_NAME):
                if not json_dir.is_dir():
                    continue
                source_dir = json_dir.parent
                for jp in json_dir.glob("*_backpack.json"):
                    stem = jp.stem.replace("_backpack", "")
                    # Material-level JSON: stem matches the parent folder name
                    if stem == source_dir.name:
                        continue
                    # Asset-level JSON: source file must exist in source_dir
                    matches = [m for m in source_dir.iterdir()
                               if m.is_file() and m.stem == stem
                               and m.suffix.lower() != ".json"]
                    if not matches:
                        jp.unlink()
                        removed += 1

        for child in node.children:
            _walk(child)

    _walk(root_node)
    return created, removed
