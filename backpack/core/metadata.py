"""JSON sidecar metadata system.

New structure (when backpack_root is configured):
  BACKPACK/JSON/<relative-path-from-ASSETS>/<stem>.json

Legacy structure (in-folder, for migration):
  <asset_folder>/.json/<stem>_backpack.json

Call set_backpack_root() once at startup (main_window.init_drive).
"""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

# Top-level metadata directory (sibling of ASSETS/)
JSON_DIR_NAME = "JSON"

# Module-level root — set once at startup via set_backpack_root()
_backpack_root: Optional[Path] = None


def set_backpack_root(root: Path) -> None:
    global _backpack_root
    _backpack_root = root


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
    """Get the .json path for an asset file in BACKPACK/JSON/."""
    if _backpack_root:
        assets_root = _backpack_root / "ASSETS"
        try:
            rel = filepath.relative_to(assets_root)
            return _backpack_root / JSON_DIR_NAME / rel.parent / f"{filepath.stem}.json"
        except ValueError:
            pass
    # Fallback: legacy in-folder .json/ subdir
    return filepath.parent / ".json" / f"{filepath.stem}_backpack.json"


def json_path_for_material(folder: Path) -> Path:
    """Get the .json path for a material folder in BACKPACK/JSON/."""
    if _backpack_root:
        assets_root = _backpack_root / "ASSETS"
        try:
            rel = folder.relative_to(assets_root)
            return _backpack_root / JSON_DIR_NAME / rel / f"{folder.name}.json"
        except ValueError:
            pass
    # Fallback: legacy in-folder .json/ subdir
    return folder / ".json" / f"{folder.name}_backpack.json"


def _legacy_json_for_file(filepath: Path) -> Path:
    """Legacy in-folder path: <parent>/.json/<stem>_backpack.json"""
    return filepath.parent / ".json" / f"{filepath.stem}_backpack.json"


def _legacy_json_for_material(folder: Path) -> Path:
    """Legacy in-folder path: <folder>/.json/<folder_name>_backpack.json"""
    return folder / ".json" / f"{folder.name}_backpack.json"


# ── Read / Write ──────────────────────────────────────────────────────────────

def read_asset_meta(filepath: Path) -> AssetMeta:
    """Read metadata for an asset file."""
    jp = json_path_for_file(filepath)
    if not jp.exists():
        # Migrate from legacy in-folder .json/ location if present
        old_jp = _legacy_json_for_file(filepath)
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
    """Write metadata for an asset file into BACKPACK/JSON/."""
    jp = json_path_for_file(filepath)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(asdict(meta), indent=2, ensure_ascii=False), encoding="utf-8")


def read_material_meta(folder: Path) -> MaterialMeta:
    """Read metadata for a material folder."""
    jp = json_path_for_material(folder)
    if not jp.exists():
        # Migrate from legacy in-folder .json/ location if present
        old_jp = _legacy_json_for_material(folder)
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
    """Write metadata for a material folder into BACKPACK/JSON/."""
    jp = json_path_for_material(folder)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(asdict(meta), indent=2, ensure_ascii=False), encoding="utf-8")


def delete_asset_meta(filepath: Path):
    """Delete the .json for an asset file."""
    jp = json_path_for_file(filepath)
    if jp.exists():
        jp.unlink()


def delete_material_meta(folder: Path):
    """Delete the .json for a material folder."""
    jp = json_path_for_material(folder)
    if jp.exists():
        jp.unlink()
