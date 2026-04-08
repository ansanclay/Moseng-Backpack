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
from backpack.core.metadata import (
    read_asset_meta, read_material_meta, write_asset_meta, write_material_meta,
    json_path_for_file, json_path_for_material,
    AssetMeta, MaterialMeta,
)
from backpack.core.preview import preview_path_for, PREVIEW_DIR_NAME


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
_TEXTURE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".bmp", ".exr", ".tx", ".rat"}
_HDRI_EXTS = {".hdr", ".exr"}
_GOBO_EXTS = {".ies"}
_MODEL_EXTS = {".obj", ".fbx", ".abc", ".usd", ".usda", ".usdc", ".usdz", ".bgeo"}
_SCENE_EXTS = {".hip", ".hipnc", ".hiplc", ".blend", ".ma", ".mb"}
_SKIP_EXTS = {".json", ".db", ".db-wal", ".db-shm"}


def scan_backpack(backpack_root: Path) -> tuple[list[ScannedMaterial], list[ScannedAsset]]:
    """Scan the entire BACKPACK folder tree.

    Returns:
        (materials, loose_assets) - materials from Materials/, loose assets from other folders.
    """
    materials: list[ScannedMaterial] = []
    loose_assets: list[ScannedAsset] = []

    if not backpack_root.exists():
        return materials, loose_assets

    # Scan Materials folder
    mat_root = backpack_root / "Materials"
    if mat_root.exists():
        for source_dir in sorted(mat_root.iterdir()):
            if not source_dir.is_dir():
                continue
            source_name = source_dir.name.lower()  # "QUIXEL", "Other", etc.
            for mat_dir in sorted(source_dir.iterdir()):
                if not mat_dir.is_dir():
                    continue
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

    for f in sorted(folder.iterdir()):
        if not f.is_file():
            continue
        if f.parent.name == PREVIEW_DIR_NAME:
            continue
        ext = f.suffix.lower()
        if ext in _SKIP_EXTS:
            continue

        # Detect sub-type
        sub_type = _detect_sub_type(f.stem)

        # Check if preview
        is_preview = False
        for pat in QUIXEL_PREVIEW_PATTERNS:
            if pat.search(f.stem):
                is_preview = True
                break

        if is_preview and ext in _TEXTURE_EXTS:
            preview_path = f
            mat.meta.preview_file = f.name

        if ext in _TEXTURE_EXTS or ext in _HDRI_EXTS:
            asset_meta = read_asset_meta(f)
            # Check for cached preview thumbnail
            pcache = preview_path_for(f)
            asset = ScannedAsset(
                path=f,
                filename=f.name,
                rel_path=str(f.relative_to(backpack_root)).replace("\\", "/"),
                asset_type="texture",
                sub_type=sub_type or ("preview" if is_preview else ""),
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
    """Detect PBR map sub-type from filename stem."""
    for pattern, sub in SUB_TYPE_PATTERNS:
        if pattern.search(stem):
            return sub
    return ""


def _is_hdri_name(stem: str) -> bool:
    return bool(re.search(r"(hdri|hdr|env|sky|panorama|pano|dome)", stem, re.I))


def sync_json_files(backpack_root: Path) -> tuple[int, int]:
    """Sync: create missing JSONs, remove orphaned JSONs.

    Returns (created_count, removed_count).
    """
    created = 0
    removed = 0

    if not backpack_root.exists():
        return created, removed

    # Materials
    mat_root = backpack_root / "Materials"
    if mat_root.exists():
        for source_dir in mat_root.iterdir():
            if not source_dir.is_dir():
                continue
            for mat_dir in source_dir.iterdir():
                if not mat_dir.is_dir() or mat_dir.name == PREVIEW_DIR_NAME:
                    continue
                # Ensure material json exists
                jp = json_path_for_material(mat_dir)
                if not jp.exists():
                    write_material_meta(mat_dir, MaterialMeta())
                    created += 1

                # Check files inside material
                for f in mat_dir.iterdir():
                    if f.is_file() and f.suffix.lower() not in _SKIP_EXTS and f.parent.name != PREVIEW_DIR_NAME:
                        fjp = json_path_for_file(f)
                        if not fjp.exists():
                            write_asset_meta(f, AssetMeta(asset_type="texture",
                                                          sub_type=_detect_sub_type(f.stem)))
                            created += 1

        # Remove orphaned JSONs in Materials
        for jp in mat_root.rglob("*_backpack.json"):
            # Check if the parent asset/folder still exists
            stem = jp.stem.replace("_backpack", "")
            parent = jp.parent
            # Could be a folder json or file json
            if jp.name == f"{parent.name}_backpack.json":
                continue  # folder-level json, valid if folder exists
            # File-level json: check if any file with that stem exists
            matches = list(parent.glob(f"{stem}.*"))
            matches = [m for m in matches if m.suffix.lower() != ".json"]
            if not matches:
                jp.unlink()
                removed += 1

    # Textures, Gobo, Other
    for folder_name in ("Textures", "Gobo", "Other"):
        folder = backpack_root / folder_name
        if not folder.exists():
            continue
        for f in folder.rglob("*"):
            if f.is_file() and f.suffix.lower() not in _SKIP_EXTS and not f.name.startswith(".") and PREVIEW_DIR_NAME not in f.parts:
                fjp = json_path_for_file(f)
                if not fjp.exists():
                    dtype = "gobo" if folder_name == "Gobo" else ("texture" if folder_name == "Textures" else "other")
                    write_asset_meta(f, AssetMeta(asset_type=dtype,
                                                  sub_type=_detect_sub_type(f.stem)))
                    created += 1

        # Remove orphaned JSONs
        for jp in folder.rglob("*_backpack.json"):
            stem = jp.stem.replace("_backpack", "")
            matches = list(jp.parent.glob(f"{stem}.*"))
            matches = [m for m in matches if m.suffix.lower() != ".json"]
            if not matches:
                jp.unlink()
                removed += 1

    return created, removed
