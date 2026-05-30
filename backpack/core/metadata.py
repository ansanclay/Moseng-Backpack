"""JSON sidecar metadata system.

Every asset file gets a companion `.json/<stem>_backpack.json` in the same folder.
Every material folder gets `.json/<folder_name>_backpack.json` inside it.
Old-style sibling JSONs (next to the file) are migrated automatically on first read.
"""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

JSON_DIR_NAME = ".json"


@dataclass
class AssetMeta:
    """Metadata for a single asset file."""
    tags: list[str] = field(default_factory=list)
    rating: int = 0
    notes: str = ""
    favorite: bool = False
    asset_type: str = "texture"   # texture, hdri, gobo, model, other
    sub_type: str = ""            # albedo, normal, roughness, etc.
    source: str = "other"         # quixel, poliigon, textures_com, other


@dataclass
class MaterialMeta:
    """Metadata for a material folder."""
    tags: list[str] = field(default_factory=list)
    rating: int = 0
    notes: str = ""
    favorite: bool = False
    source: str = "other"
    surface_type: str = ""        # Bark, Plaster, Concrete, etc.
    preview_file: str = ""        # relative filename of preview image


# ── Path helpers ──────────────────────────────────────────────────────────────

def json_path_for_file(filepath: Path) -> Path:
    """Get the _backpack.json path for an asset file (in .json/ subfolder)."""
    return filepath.parent / JSON_DIR_NAME / f"{filepath.stem}_backpack.json"


def json_path_for_material(folder: Path) -> Path:
    """Get the _backpack.json path for a material folder (in .json/ subfolder)."""
    return folder / JSON_DIR_NAME / f"{folder.name}_backpack.json"


def _old_json_path_for_file(filepath: Path) -> Path:
    """Legacy path: sibling JSON next to the file."""
    return filepath.parent / f"{filepath.stem}_backpack.json"


def _old_json_path_for_material(folder: Path) -> Path:
    """Legacy path: JSON inside the material folder directly."""
    return folder / f"{folder.name}_backpack.json"


# ── Read / Write ──────────────────────────────────────────────────────────────

def read_asset_meta(filepath: Path) -> AssetMeta:
    """Read metadata for an asset file. Migrates from old sibling path if needed."""
    jp = json_path_for_file(filepath)
    if not jp.exists():
        # Migrate from old location if present
        old_jp = _old_json_path_for_file(filepath)
        if old_jp.exists():
            try:
                data = json.loads(old_jp.read_text(encoding="utf-8"))
                meta = AssetMeta(**{k: v for k, v in data.items()
                                    if k in AssetMeta.__dataclass_fields__})
                write_asset_meta(filepath, meta)
                old_jp.unlink()
                return meta
            except (json.JSONDecodeError, TypeError):
                pass
        return AssetMeta()

    try:
        data = json.loads(jp.read_text(encoding="utf-8"))
        return AssetMeta(**{k: v for k, v in data.items()
                            if k in AssetMeta.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError):
        return AssetMeta()


def write_asset_meta(filepath: Path, meta: AssetMeta):
    """Write metadata for an asset file into .json/ subfolder."""
    jp = json_path_for_file(filepath)
    jp.parent.mkdir(exist_ok=True)
    jp.write_text(json.dumps(asdict(meta), indent=2, ensure_ascii=False), encoding="utf-8")


def read_material_meta(folder: Path) -> MaterialMeta:
    """Read metadata for a material folder. Migrates from old path if needed."""
    jp = json_path_for_material(folder)
    if not jp.exists():
        # Migrate from old location if present
        old_jp = _old_json_path_for_material(folder)
        if old_jp.exists():
            try:
                data = json.loads(old_jp.read_text(encoding="utf-8"))
                meta = MaterialMeta(**{k: v for k, v in data.items()
                                       if k in MaterialMeta.__dataclass_fields__})
                write_material_meta(folder, meta)
                old_jp.unlink()
                return meta
            except (json.JSONDecodeError, TypeError):
                pass
        return MaterialMeta()

    try:
        data = json.loads(jp.read_text(encoding="utf-8"))
        return MaterialMeta(**{k: v for k, v in data.items()
                                if k in MaterialMeta.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError):
        return MaterialMeta()


def write_material_meta(folder: Path, meta: MaterialMeta):
    """Write metadata for a material folder into .json/ subfolder."""
    jp = json_path_for_material(folder)
    jp.parent.mkdir(exist_ok=True)
    jp.write_text(json.dumps(asdict(meta), indent=2, ensure_ascii=False), encoding="utf-8")


def delete_asset_meta(filepath: Path):
    """Delete the _backpack.json for an asset file."""
    jp = json_path_for_file(filepath)
    if jp.exists():
        jp.unlink()


def delete_material_meta(folder: Path):
    """Delete the _backpack.json for a material folder."""
    jp = json_path_for_material(folder)
    if jp.exists():
        jp.unlink()
