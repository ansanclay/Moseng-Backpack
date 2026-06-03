"""Filesystem scanner - reads the BACKPACK folder tree in real-time.

Folder structure:
  DRIVE:\BACKPACK\Materials\QUIXEL\MaterialName\...
  DRIVE:\BACKPACK\Materials\Other\MaterialName\...
  DRIVE:\BACKPACK\Textures\file.png
  DRIVE:\BACKPACK\Gobo\file.ies
  DRIVE:\BACKPACK\Other\file.ext
"""

import json
import os
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
from backpack.core.preview import preview_path_for
from backpack.core.folder_model import JSON_DIR_NAME, PREVIEWS_DIR_NAME
from backpack.core.downscale import strip_resolution_suffix


# ─────────────────────────────────────────────────────────────────────────────
# Directory-scan primitives
# ─────────────────────────────────────────────────────────────────────────────
# os.scandir() yields DirEntry objects whose is_dir()/is_file() reuse the
# attributes gathered during the directory read — on Windows this avoids the
# extra stat() syscall per entry that Path.iterdir() + Path.is_*() incur.

def _scandir_entries(folder) -> list:
    """Return the os.DirEntry objects in *folder*; [] if it can't be listed."""
    try:
        with os.scandir(folder) as it:
            return list(it)
    except OSError:
        return []


def _entry_is_dir(entry) -> bool:
    """DirEntry.is_dir() tolerant of stat errors (mirrors Path.is_dir())."""
    try:
        return entry.is_dir()
    except OSError:
        return False


def _entry_is_file(entry) -> bool:
    """DirEntry.is_file() tolerant of stat errors (mirrors Path.is_file())."""
    try:
        return entry.is_file()
    except OSError:
        return False


def _entry_ext(entry) -> str:
    """Lower-cased extension of a DirEntry (equivalent to Path.suffix.lower())."""
    return os.path.splitext(entry.name)[1].lower()


def _by_normcase(entry) -> str:
    """Sort key reproducing sorted(Path.iterdir()) order on this platform."""
    return os.path.normcase(entry.name)


# ─────────────────────────────────────────────────────────────────────────────
# Fast per-folder metadata helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read_dir_names(folder: Path) -> set[str]:
    """Return the set of filenames inside *folder*, or empty set on error."""
    try:
        return {p.name for p in folder.iterdir()}
    except (PermissionError, FileNotFoundError):
        return set()


def _load_meta_json(path: Path, cls):
    """Deserialise a dataclass from a JSON file (caller must know it exists)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items()
                      if k in cls.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError, OSError):
        return cls()


def _folder_caches(folder: Path, backpack_root: Path):
    """Read BACKPACK/JSON/ and BACKPACK/PREVIEWS/ dirs once for a given asset folder.

    Returns (json_names, preview_names, json_dir, preview_dir).
    Reduces syscall count from O(n_files) to O(1) per material folder.
    """
    assets_root = backpack_root / "ASSETS"
    try:
        rel = folder.relative_to(assets_root)
    except ValueError:
        rel = Path(folder.name)
    json_dir    = backpack_root / JSON_DIR_NAME / rel
    preview_dir = backpack_root / PREVIEWS_DIR_NAME / rel
    return (
        _read_dir_names(json_dir),
        _read_dir_names(preview_dir),
        json_dir,
        preview_dir,
    )


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

_SCAN_SKIP_DIRS = frozenset({
    PREVIEWS_DIR_NAME, JSON_DIR_NAME, ".thumbs", "__MACOSX",
    ".preview", ".json",                  # keep legacy names for safety
    "previews", "Previews",               # Quixel auxiliary preview dir
    "thumbs", "Thumbs",                   # Quixel auxiliary thumbs dir
})
_ALL_IMAGE_EXTS = _TEXTURE_EXTS | _HDRI_EXTS
_MODEL_ASSET_EXTS = _ALL_IMAGE_EXTS | _MODEL_EXTS | _SCENE_EXTS

# Quixel organises each material as <hash>/1K|2K|4K|8K|512/<files>.
# Treat a folder whose visible subdirs are ALL pure resolution names as the
# material itself — its resolution subdirs are variants, not separate assets.
_RES_DIR_PATTERN = re.compile(r"^(\d+K|512)$", re.I)


def _is_material_dir(folder: Path, entries: "list | None" = None) -> bool:
    """True if *folder* is a leaf material folder (not a category container).

    *entries* may be a pre-read list of os.DirEntry for *folder* (the walker
    passes it so the directory is read only once); otherwise it is read here.

    Rules (checked in order):
    1. If any immediate subdirectory has a PBR-keyword name (Albedo/, Normal/…)
       → structured material layout → True.
    2. If every visible subdirectory is a pure resolution name (1K, 2K, 4K, …)
       → Quixel-style material with per-resolution subdirs → True.
    3. If any immediate subdirectory (non-skip, non-PBR-keyword, non-res) itself
       contains image files → this folder is a category/container → False.
    4. If the folder contains at least one direct image file → material → True.
    5. Otherwise → container → False.
    """
    if entries is None:
        entries = _scandir_entries(folder)

    visible_subdirs = [
        e for e in entries
        if _entry_is_dir(e)
        and not e.name.startswith(".")
        and e.name not in _SCAN_SKIP_DIRS
    ]
    # Rule 2 — Quixel <hash>/{1K,2K,4K}/ layout.
    if visible_subdirs and all(_RES_DIR_PATTERN.match(d.name) for d in visible_subdirs):
        return True

    has_direct_images = False
    for entry in entries:
        if entry.name.startswith(".") or entry.name in _SCAN_SKIP_DIRS:
            continue
        if _entry_is_file(entry) and _entry_ext(entry) in _ALL_IMAGE_EXTS:
            has_direct_images = True
        elif _entry_is_dir(entry):
            if _detect_sub_type(entry.name):
                return True   # PBR-keyword subfolder → structured material
            # Non-PBR subdir: peek inside — if it has images this folder is a container
            for sub in _scandir_entries(entry.path):
                if _entry_is_file(sub) and _entry_ext(sub) in _ALL_IMAGE_EXTS:
                    return False  # sub-material found → we are a container

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
    entries = _scandir_entries(folder)
    if _is_material_dir(folder, entries):
        out.append(folder)
        return   # don't recurse inside a material folder itself

    for entry in sorted(entries, key=_by_normcase):
        if _entry_is_dir(entry) and not entry.name.startswith(".") \
                and entry.name not in _SCAN_SKIP_DIRS:
            _walk_for_materials(Path(entry.path), out)


# ─────────────────────────────────────────────────────────────────────────────
# 3-D asset folder detection  (scan_mode="model_folder")
# ─────────────────────────────────────────────────────────────────────────────

def _is_model_asset_dir(folder: Path, entries: "list | None" = None) -> bool:
    """True if *folder* is a leaf 3-D asset folder (not a category container).

    *entries* may be a pre-read list of os.DirEntry for *folder* (the walker
    passes it so the directory is read only once); otherwise it is read here.

    Rules (checked in order):
    1. If the folder has a direct model file (.fbx / .obj / .abc / …) → True.
       This is definitive: bundled Textures/ or Meshes/ subdirectories are NOT
       treated as evidence that the folder is a container.
    2. If no direct model file exists but a non-skip subdir contains model or
       image files → this folder is a container → False.
    3. If the folder has direct image files only (and no sub-asset subdirs) →
       True (e.g. foliage billboard / sprite packs).
    4. Otherwise → False.
    """
    if entries is None:
        entries = _scandir_entries(folder)

    has_direct_model = False
    has_direct_image = False

    for entry in entries:
        if entry.name.startswith(".") or entry.name in _SCAN_SKIP_DIRS:
            continue
        if _entry_is_file(entry):
            ext = _entry_ext(entry)
            if ext in _MODEL_EXTS or ext in _SCENE_EXTS:
                has_direct_model = True
                break   # definitive — no need to look further
            if ext in _ALL_IMAGE_EXTS:
                has_direct_image = True

    # Rule 1: direct model file → always an asset folder
    if has_direct_model:
        return True

    # Rules 2 / 3: no model file → check subdirs
    for entry in entries:
        if entry.name.startswith(".") or entry.name in _SCAN_SKIP_DIRS:
            continue
        if _entry_is_dir(entry):
            for sub in _scandir_entries(entry.path):
                if _entry_is_file(sub) and _entry_ext(sub) in _MODEL_ASSET_EXTS:
                    return False   # subdir has assets → we are a container

    return has_direct_image   # image-only pack (no sub-asset subdirs)


def _collect_model_asset_dirs(search_root: Path) -> list[Path]:
    """Return all 3-D asset folders inside *search_root* at any depth."""
    results: list[Path] = []
    _walk_for_model_assets(search_root, results)
    return results


def _walk_for_model_assets(folder: Path, out: list) -> None:
    entries = _scandir_entries(folder)
    if _is_model_asset_dir(folder, entries):
        out.append(folder)
        return
    for entry in sorted(entries, key=_by_normcase):
        if _entry_is_dir(entry) and not entry.name.startswith(".") \
                and entry.name not in _SCAN_SKIP_DIRS:
            _walk_for_model_assets(Path(entry.path), out)


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

    # ── Pre-read JSON/ and PREVIEWS/ dirs once ────────────────────────────────
    _json_names, _prev_names, _json_dir, _prev_dir = _folder_caches(folder, backpack_root)

    mat_json = f"{folder.name}.json"
    has_json = mat_json in _json_names
    meta = _load_meta_json(_json_dir / mat_json, MaterialMeta) if has_json else MaterialMeta()

    mat = ScannedMaterial(
        path=folder,
        name=strip_resolution_suffix(folder.name),
        rel_path=rel,
        source=source,
        meta=meta,
        has_json=has_json,
    )

    preview_path: Optional[Path] = None
    _SKIP_DIRS = _SCAN_SKIP_DIRS
    # Cache per-subdir preview sets (from PREVIEWS/<rel>/<subdir_name>/)
    _subdir_prev: dict[Path, set[str]] = {}

    files_to_scan: list[tuple[Path, str]] = []
    for entry in sorted(_scandir_entries(folder), key=_by_normcase):
        if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
            continue
        if _entry_is_file(entry):
            files_to_scan.append((Path(entry.path), ""))
        elif _entry_is_dir(entry):
            hint = entry.name
            sub_dir = Path(entry.path)
            _subdir_prev[sub_dir] = _read_dir_names(_prev_dir / entry.name)
            for sub_f in sorted(_scandir_entries(entry.path), key=_by_normcase):
                if _entry_is_file(sub_f) and not sub_f.name.startswith("."):
                    files_to_scan.append((Path(sub_f.path), hint))

    # Two-tier preview selection:
    #   quixel_preview  — file matching QUIXEL_PREVIEW_PATTERNS (highest priority)
    #   name_preview    — file whose stem == folder name  (fallback for custom mats)
    quixel_preview: Optional[Path] = None
    name_preview:   Optional[Path] = None

    for f, subfolder_hint in files_to_scan:
        ext = f.suffix.lower()
        if ext in _SKIP_EXTS:
            continue

        is_quixel_preview = any(
            pat.search(f.stem) or (subfolder_hint and pat.search(subfolder_hint))
            for pat in QUIXEL_PREVIEW_PATTERNS
        )
        is_name_preview = (not is_quixel_preview
                           and f.stem.lower() == folder.name.lower()
                           and f.parent == folder)   # must be in root, not a subdir

        if ext in _TEXTURE_EXTS and is_quixel_preview:
            if quixel_preview is None:      # keep first match (usually root-level)
                quixel_preview = f
            continue                        # don't add to maps

        if ext in _TEXTURE_EXTS and is_name_preview:
            name_preview = f
            continue                        # don't add to maps

        # Metadata / preview lookup
        file_json = f"{f.stem}.json"
        f_has_json = file_json in _json_names
        asset_meta = _load_meta_json(_json_dir / file_json, AssetMeta) if f_has_json else AssetMeta()
        prev_name = f"{f.stem}.jpeg"
        if f.parent == folder:
            pcache = (_prev_dir / prev_name) if prev_name in _prev_names else None
        else:
            sp = _subdir_prev.get(f.parent, set())
            pcache = (_prev_dir / f.parent.name / prev_name) if prev_name in sp else None

        if ext in _MODEL_EXTS or ext in _SCENE_EXTS:
            asset = ScannedAsset(
                path=f,
                filename=f.name,
                rel_path=str(f.relative_to(backpack_root)).replace("\\", "/"),
                asset_type="model",
                sub_type=ext.lstrip("."),
                meta=asset_meta,
                has_json=f_has_json,
                material_folder=folder.name,
                preview_cache=pcache,
            )
            mat.maps.append(asset)
            continue

        if ext in _TEXTURE_EXTS or ext in _HDRI_EXTS:
            sub_type = _detect_sub_type(f.stem)
            if not sub_type and subfolder_hint:
                sub_type = _detect_sub_type(subfolder_hint)
            asset = ScannedAsset(
                path=f,
                filename=f.name,
                rel_path=str(f.relative_to(backpack_root)).replace("\\", "/"),
                asset_type="texture",
                sub_type=sub_type or "",
                meta=asset_meta,
                has_json=f_has_json,
                material_folder=folder.name,
                preview_cache=pcache,
            )
            mat.maps.append(asset)

    # Resolve primary preview: quixel_preview > name_preview > albedo map > first map
    preview_path = quixel_preview or name_preview
    if preview_path:
        mat.meta.preview_file = preview_path.name

    mat.preview_path = preview_path
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
        prev_name = f"{mat.preview_path.stem}.jpeg"
        if mat.preview_path.parent == folder:
            mat.preview_cache = (_prev_dir / prev_name) if prev_name in _prev_names else None
        else:
            sp = _subdir_prev.get(mat.preview_path.parent, set())
            mat.preview_cache = (
                _prev_dir / mat.preview_path.parent.name / prev_name
            ) if prev_name in sp else None

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
        for source_dir in sorted(_scandir_entries(mat_root), key=_by_normcase):
            if not _entry_is_dir(source_dir) or source_dir.name.startswith("."):
                continue
            source_name = source_dir.name.lower()
            for mat_dir in _collect_material_dirs(Path(source_dir.path)):
                mat = _scan_material_folder(mat_dir, source_name, backpack_root)
                if mat and mat.maps:
                    materials.append(mat)

    # Scan Textures folder
    tex_root = backpack_root / "Textures"
    if tex_root.exists():
        for f in sorted(tex_root.rglob("*")):
            if f.is_file() and f.suffix.lower() not in _SKIP_EXTS and ".preview" not in f.parts:
                asset = _scan_single_file(f, "texture", backpack_root)
                if asset:
                    loose_assets.append(asset)

    # Scan Gobo folder
    gobo_root = backpack_root / "Gobo"
    if gobo_root.exists():
        for f in sorted(gobo_root.rglob("*")):
            if f.is_file() and f.suffix.lower() not in _SKIP_EXTS and ".preview" not in f.parts:
                asset = _scan_single_file(f, "gobo", backpack_root)
                if asset:
                    loose_assets.append(asset)

    # Scan Other folder
    other_root = backpack_root / "Other"
    if other_root.exists():
        for f in sorted(other_root.rglob("*")):
            if f.is_file() and f.suffix.lower() not in _SKIP_EXTS and ".preview" not in f.parts:
                asset = _scan_single_file(f, "other", backpack_root)
                if asset:
                    loose_assets.append(asset)

    return materials, loose_assets


def _scan_material_folder(
    folder: Path, source: str, backpack_root: Path
) -> Optional[ScannedMaterial]:
    """Scan a single material folder."""
    rel = str(folder.relative_to(backpack_root)).replace("\\", "/")

    # ── Pre-read JSON/ and PREVIEWS/ dirs once (O(1) vs O(n) exists() calls) ──
    _json_names, _prev_names, _json_dir, _prev_dir = _folder_caches(folder, backpack_root)

    mat_json = f"{folder.name}.json"
    has_json = mat_json in _json_names
    meta = _load_meta_json(_json_dir / mat_json, MaterialMeta) if has_json else MaterialMeta()

    mat = ScannedMaterial(
        path=folder,
        name=strip_resolution_suffix(folder.name),
        rel_path=rel,
        source=source,
        meta=meta,
        has_json=has_json,
    )

    preview_path = None
    _SKIP_DIRS = _SCAN_SKIP_DIRS

    files_to_scan: list[tuple[Path, str]] = []   # (path, subfolder_hint)
    _subdir_prev: dict[Path, set[str]] = {}

    for entry in sorted(_scandir_entries(folder), key=_by_normcase):
        if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
            continue
        if _entry_is_file(entry):
            files_to_scan.append((Path(entry.path), ""))
        elif _entry_is_dir(entry):
            hint = entry.name
            sub_dir = Path(entry.path)
            _subdir_prev[sub_dir] = _read_dir_names(_prev_dir / entry.name)
            for sub_f in sorted(_scandir_entries(entry.path), key=_by_normcase):
                if _entry_is_file(sub_f) and not sub_f.name.startswith("."):
                    files_to_scan.append((Path(sub_f.path), hint))

    # Two-tier preview selection (same logic as _scan_model_asset_folder)
    quixel_preview: Optional[Path] = None
    name_preview:   Optional[Path] = None

    for f, subfolder_hint in files_to_scan:
        ext = f.suffix.lower()
        if ext in _SKIP_EXTS:
            continue

        sub_type = _detect_sub_type(f.stem)
        if not sub_type and subfolder_hint:
            sub_type = _detect_sub_type(subfolder_hint)

        is_quixel_preview = any(
            pat.search(f.stem) or (subfolder_hint and pat.search(subfolder_hint))
            for pat in QUIXEL_PREVIEW_PATTERNS
        )
        is_name_preview = (not is_quixel_preview
                           and f.stem.lower() == folder.name.lower()
                           and f.parent == folder)

        if ext in _TEXTURE_EXTS and is_quixel_preview:
            if quixel_preview is None:
                quixel_preview = f
            continue

        if ext in _TEXTURE_EXTS and is_name_preview:
            name_preview = f
            continue

        if ext in _TEXTURE_EXTS or ext in _HDRI_EXTS:
            file_json = f"{f.stem}.json"
            f_has_json = file_json in _json_names
            asset_meta = _load_meta_json(_json_dir / file_json, AssetMeta) if f_has_json else AssetMeta()
            prev_name = f"{f.stem}.jpeg"
            if f.parent == folder:
                pcache = (_prev_dir / prev_name) if prev_name in _prev_names else None
            else:
                sp = _subdir_prev.get(f.parent, set())
                pcache = (_prev_dir / f.parent.name / prev_name) if prev_name in sp else None

            asset = ScannedAsset(
                path=f,
                filename=f.name,
                rel_path=str(f.relative_to(backpack_root)).replace("\\", "/"),
                asset_type="texture",
                sub_type=sub_type or "",
                meta=asset_meta,
                has_json=f_has_json,
                material_folder=folder.name,
                preview_cache=pcache,
            )
            mat.maps.append(asset)

    preview_path = quixel_preview or name_preview
    if preview_path:
        mat.meta.preview_file = preview_path.name

    mat.preview_path = preview_path
    if not mat.preview_path:
        for a in mat.maps:
            if a.sub_type in ("albedo", "diffuse"):
                mat.preview_path = a.path
                break
        if not mat.preview_path and mat.maps:
            mat.preview_path = mat.maps[0].path

    if mat.preview_path:
        prev_name = f"{mat.preview_path.stem}.jpeg"
        if mat.preview_path.parent == folder:
            mat.preview_cache = (_prev_dir / prev_name) if prev_name in _prev_names else None
        else:
            sp = _subdir_prev.get(mat.preview_path.parent, set())
            mat.preview_cache = (
                _prev_dir / mat.preview_path.parent.name / prev_name
            ) if prev_name in sp else None

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

    # PBR map classification only makes sense for image files. Non-image files
    # (.hip, .fbx, .obj, …) keep an empty sub_type — never "normal"/"metallic"/…
    sub_type = _detect_sub_type(filepath.stem) if ext in _ALL_IMAGE_EXTS else ""
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

    elif mode == "files":
        # Generic project folder: list DIRECT files only (no recursion), each
        # typed by extension. `backpack_root` here is the project root, used
        # only to compute rel_path.
        try:
            entries = sorted(p for p in folder.iterdir() if p.is_file())
        except (PermissionError, OSError):
            entries = []
        for f in entries:
            if f.suffix.lower() in _SKIP_EXTS or f.name.startswith("."):
                continue
            asset = _scan_single_file(f, "texture", backpack_root)
            if asset:
                assets.append(asset)

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
            # Skip legacy in-folder cache dirs (safe to keep for transition period)
            if ".preview" in f.parts or ".json" in f.parts:
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


def sync_json_files(
    backpack_root: Path,
    since: float | None = None,
    on_progress: "callable | None" = None,
) -> tuple[int, int]:
    """Sync: create missing JSONs, remove orphaned JSONs under the entire BACKPACK tree.

    If ``since`` is given (unix timestamp), skip folders whose mtime is older.
    ``on_progress(current, total, label)`` is called before processing each leaf folder.
    Returns (created_count, removed_count).
    """
    created = 0
    removed = 0

    if not backpack_root.exists():
        return created, removed

    from backpack.core.folder_model import build_folder_tree

    root_node = build_folder_tree(backpack_root, quixel_enabled=True)

    def _folder_changed(folder: Path) -> bool:
        if since is None or not folder.exists():
            return True
        return folder.stat().st_mtime > since

    _SKIP_SYNC = {PREVIEWS_DIR_NAME, JSON_DIR_NAME, ".preview", ".json",
                  ".thumbs", "__MACOSX"}

    # ── Pass 1: collect all leaf items ────────────────────────────────────────
    # Each item: ("mat" | "model" | "flat", leaf_folder_or_node_folder, scan_mode)
    _items: list[tuple[str, Path, str]] = []

    def _collect(node):
        if node.scan_mode == "none":
            for child in node.children:
                _collect(child)
            return
        folder: Path = node.disk_path
        if not folder.exists():
            for child in node.children:
                _collect(child)
            return
        if node.scan_mode == "materials":
            for mat_dir in _collect_material_dirs(folder):
                _items.append(("mat", mat_dir, "materials"))
        elif node.scan_mode == "model_folder":
            for asset_dir in _collect_model_asset_dirs(folder):
                _items.append(("model", asset_dir, "model_folder"))
        else:
            _items.append(("flat", folder, node.scan_mode))
        for child in node.children:
            _collect(child)

    _collect(root_node)
    total = len(_items)

    # ── Pass 2: process with progress ─────────────────────────────────────────
    for idx, (kind, folder, scan_mode) in enumerate(_items):
        if on_progress:
            on_progress(idx, total, folder.name)

        if kind == "mat":
            if not _folder_changed(folder):
                continue
            jp = json_path_for_material(folder)
            if not jp.exists():
                write_material_meta(folder, MaterialMeta())
                created += 1
            file_hint_pairs: list[tuple[Path, str]] = []
            for entry in _scandir_entries(folder):
                if entry.name.startswith(".") or entry.name in _SKIP_SYNC:
                    continue
                if _entry_is_file(entry):
                    file_hint_pairs.append((Path(entry.path), ""))
                elif _entry_is_dir(entry):
                    for sf in _scandir_entries(entry.path):
                        if _entry_is_file(sf) and not sf.name.startswith("."):
                            file_hint_pairs.append((Path(sf.path), entry.name))
            for f, hint in file_hint_pairs:
                if f.suffix.lower() in _SKIP_EXTS:
                    continue
                if ".preview" in f.parts or ".json" in f.parts:
                    continue
                sub = _detect_sub_type(f.stem) or (hint and _detect_sub_type(hint)) or ""
                fjp = json_path_for_file(f)
                if not fjp.exists():
                    write_asset_meta(f, AssetMeta(asset_type="texture", sub_type=sub))
                    created += 1

        elif kind == "model":
            if not _folder_changed(folder):
                continue
            jp = json_path_for_material(folder)
            if not jp.exists():
                write_material_meta(folder, MaterialMeta())
                created += 1
            file_hint_pairs = []
            for entry in _scandir_entries(folder):
                if entry.name.startswith(".") or entry.name in _SKIP_SYNC:
                    continue
                if _entry_is_file(entry):
                    file_hint_pairs.append((Path(entry.path), ""))
                elif _entry_is_dir(entry):
                    for sf in _scandir_entries(entry.path):
                        if _entry_is_file(sf) and not sf.name.startswith("."):
                            file_hint_pairs.append((Path(sf.path), entry.name))
            for f, hint in file_hint_pairs:
                ext = f.suffix.lower()
                if ext in _SKIP_EXTS:
                    continue
                if ".preview" in f.parts or ".json" in f.parts:
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

        else:  # flat
            if not _folder_changed(folder):
                continue
            dtype = scan_mode if scan_mode in ("texture", "gobo", "model", "hdri") else "texture"
            for f in folder.rglob("*"):
                if not f.is_file():
                    continue
                if f.suffix.lower() in _SKIP_EXTS or f.name.startswith("."):
                    continue
                if ".preview" in f.parts or ".json" in f.parts:
                    continue
                fjp = json_path_for_file(f)
                if not fjp.exists():
                    write_asset_meta(f, AssetMeta(asset_type=dtype,
                                                  sub_type=_detect_sub_type(f.stem)))
                    created += 1

        # Remove orphaned JSONs in BACKPACK/JSON/ for this folder
        if _folder_changed(folder):
            assets_root = backpack_root / "ASSETS"
            json_root   = backpack_root / JSON_DIR_NAME
            try:
                rel = folder.relative_to(assets_root)
            except ValueError:
                rel = Path(folder.name)
            json_dir = json_root / rel
            if json_dir.exists():
                for jp in json_dir.glob("*.json"):
                    stem = jp.stem
                    if stem == folder.name:
                        if not folder.exists():
                            jp.unlink()
                            removed += 1
                        continue
                    matches = []
                    for m in _scandir_entries(folder):
                        if not _entry_is_file(m):
                            continue
                        mp = Path(m.path)
                        if mp.stem == stem and mp.suffix.lower() != ".json":
                            matches.append(mp)
                    if not matches:
                        jp.unlink()
                        removed += 1

    if on_progress:
        on_progress(total, total, "")
    return created, removed
