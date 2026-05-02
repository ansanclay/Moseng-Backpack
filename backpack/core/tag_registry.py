"""Tag registry - stores global tag data (color, head asset) in BACKPACK/.backpack_tags.json."""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict


@dataclass
class TagInfo:
    """Per-tag persistent data."""
    color: str = ""               # hex color for this tag
    head_path: str = ""           # relative path to the tag-head asset (from BACKPACK root)
    head_preview: str = ""        # relative path to the preview cache of the head asset


@dataclass
class TagRegistry:
    """All tag data stored globally."""
    tags: dict[str, dict] = field(default_factory=dict)  # tag_name -> TagInfo as dict


def _registry_path(backpack_root: Path) -> Path:
    return backpack_root / ".backpack_tags.json"


def load_tag_registry(backpack_root: Path) -> dict[str, TagInfo]:
    """Load the tag registry. Returns dict of tag_name -> TagInfo."""
    fp = _registry_path(backpack_root)
    result: dict[str, TagInfo] = {}
    if fp.exists():
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            tags_dict = data.get("tags", {})
            for name, info in tags_dict.items():
                result[name] = TagInfo(**{k: v for k, v in info.items() if k in TagInfo.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError):
            pass
    return result


def save_tag_registry(backpack_root: Path, registry: dict[str, TagInfo]):
    """Save the tag registry to disk."""
    fp = _registry_path(backpack_root)
    data = {"tags": {name: asdict(info) for name, info in registry.items()}}
    fp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_or_create_tag(
    backpack_root: Path,
    registry: dict[str, TagInfo],
    tag_name: str,
    accent_color: str,
    head_asset_path: Path | None = None,
) -> TagInfo:
    """Get existing tag info or create a new one with a generated color.

    If head_asset_path is given and the tag has no head yet, set it.
    """
    from backpack.constants import tag_color_for_name
    from backpack.core.preview import preview_path_for

    if tag_name in registry:
        info = registry[tag_name]
        # Set head if not already set and a path is given
        if not info.head_path and head_asset_path:
            rel = str(head_asset_path.relative_to(backpack_root)).replace("\\", "/")
            info.head_path = rel
            pp = preview_path_for(head_asset_path)
            if pp.exists():
                info.head_preview = str(pp.relative_to(backpack_root)).replace("\\", "/")
        return info

    # Create new — use deterministic color derived from the tag name
    color = tag_color_for_name(tag_name)
    info = TagInfo(color=color)
    if head_asset_path:
        rel = str(head_asset_path.relative_to(backpack_root)).replace("\\", "/")
        info.head_path = rel
        pp = preview_path_for(head_asset_path)
        if pp.exists():
            info.head_preview = str(pp.relative_to(backpack_root)).replace("\\", "/")

    registry[tag_name] = info
    return info


def set_tag_head(
    backpack_root: Path,
    registry: dict[str, TagInfo],
    tag_name: str,
    head_asset_path: Path,
):
    """Promote an asset to be the tag head."""
    from backpack.core.preview import preview_path_for

    if tag_name not in registry:
        return
    info = registry[tag_name]
    rel = str(head_asset_path.relative_to(backpack_root)).replace("\\", "/")
    info.head_path = rel
    pp = preview_path_for(head_asset_path)
    if pp.exists():
        info.head_preview = str(pp.relative_to(backpack_root)).replace("\\", "/")
    else:
        info.head_preview = ""
