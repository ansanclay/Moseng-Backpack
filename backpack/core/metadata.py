"""JSON sidecar metadata system.

Every asset file gets a companion `filename_backpack.json`.
Every material folder gets a `foldername_backpack.json` inside it.
"""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional


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


def json_path_for_file(filepath: Path) -> Path:
    """Get the _backpack.json path for an asset file."""
    return filepath.parent / f"{filepath.stem}_backpack.json"


def json_path_for_material(folder: Path) -> Path:
    """Get the _backpack.json path for a material folder."""
    return folder / f"{folder.name}_backpack.json"


def read_asset_meta(filepath: Path) -> AssetMeta:
    """Read metadata for an asset file. Returns defaults if JSON doesn't exist."""
    jp = json_path_for_file(filepath)
    if jp.exists():
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
            return AssetMeta(**{k: v for k, v in data.items() if k in AssetMeta.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError):
            pass
    return AssetMeta()


def write_asset_meta(filepath: Path, meta: AssetMeta):
    """Write metadata for an asset file."""
    jp = json_path_for_file(filepath)
    jp.write_text(json.dumps(asdict(meta), indent=2, ensure_ascii=False), encoding="utf-8")


def read_material_meta(folder: Path) -> MaterialMeta:
    """Read metadata for a material folder."""
    jp = json_path_for_material(folder)
    if jp.exists():
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
            return MaterialMeta(**{k: v for k, v in data.items() if k in MaterialMeta.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError):
            pass
    return MaterialMeta()


def write_material_meta(folder: Path, meta: MaterialMeta):
    """Write metadata for a material folder."""
    jp = json_path_for_material(folder)
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
