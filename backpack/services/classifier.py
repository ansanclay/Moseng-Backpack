"""Auto-classification engine for dropped files.

Supports Quixel Megascans, Poliigon, ambientCG, and generic texture sets.
Groups texture maps from the same folder into materials.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backpack.constants import (
    EXTENSION_MAP, SUB_TYPE_PATTERNS, HDRI_NAME_PATTERNS,
    FOLDER_TYPE_OVERRIDES, CATEGORY_FOLDERS, IMAGE_EXTENSIONS,
    SURFACE_CATEGORIES, QUIXEL_PREVIEW_PATTERNS,
)


@dataclass
class ClassificationResult:
    asset_type: str = "other"
    sub_type: Optional[str] = None
    suggested_tags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    dest_subfolder: str = "Other"
    # Material grouping
    material_group: Optional[str] = None   # Group key for bundling maps
    material_name: Optional[str] = None    # Display name
    surface_category: Optional[str] = None # e.g. "Nature", "Metal"
    surface_type: Optional[str] = None     # e.g. "Bark", "Rust"
    is_preview: bool = False               # Quixel preview image


def classify_file(
    filepath: Path,
    parent_folders: list[str] | None = None,
) -> ClassificationResult:
    """Classify a single file."""
    result = ClassificationResult()
    ext = filepath.suffix.lower()
    stem = filepath.stem

    # ── Stage 1: Extension mapping ──
    asset_type = EXTENSION_MAP.get(ext, "other")
    result.asset_type = asset_type
    result.confidence = 0.5 if asset_type != "other" else 0.1

    # ── Stage 2: PBR map type detection ──
    for pattern, sub_type in SUB_TYPE_PATTERNS:
        if pattern.search(stem):
            if sub_type == "preview":
                result.is_preview = True
                result.sub_type = "preview"
            else:
                result.sub_type = sub_type
            result.confidence = max(result.confidence, 0.7)
            break

    # Check for Quixel preview images
    if not result.is_preview:
        for pat in QUIXEL_PREVIEW_PATTERNS:
            if pat.search(stem):
                result.is_preview = True
                result.sub_type = "preview"
                break

    # HDRI disambiguation for .exr files
    if ext == ".exr" and HDRI_NAME_PATTERNS.search(stem):
        result.asset_type = "hdri"
        result.confidence = 0.8

    # Aspect ratio hint
    ratio_match = re.search(r"(\d{3,5})x(\d{3,5})", stem)
    if ratio_match and ext in (".exr", ".hdr"):
        w, h = int(ratio_match.group(1)), int(ratio_match.group(2))
        if w >= h * 1.8:
            result.asset_type = "hdri"
            result.confidence = max(result.confidence, 0.75)

    # ── Stage 3: Surface type detection from name + folders ──
    name_parts = _extract_name_parts(stem, parent_folders or [])
    for part in name_parts:
        lower = part.lower().replace(" ", "_").replace("-", "_")
        if lower in SURFACE_CATEGORIES:
            cat, surf = SURFACE_CATEGORIES[lower]
            result.surface_category = cat
            result.surface_type = surf
            if surf not in result.suggested_tags:
                result.suggested_tags.append(surf)
            if cat not in result.suggested_tags:
                result.suggested_tags.append(cat)
            result.confidence = max(result.confidence, 0.85)
            break

    # ── Stage 4: Folder-based overrides ──
    if parent_folders:
        for folder_name in parent_folders:
            lower = folder_name.lower()
            if lower in FOLDER_TYPE_OVERRIDES:
                result.asset_type = FOLDER_TYPE_OVERRIDES[lower]
                result.confidence = max(result.confidence, 0.85)

            # Extract tags from folder names
            cleaned = _clean_folder_name(folder_name)
            if cleaned and cleaned.lower() not in FOLDER_TYPE_OVERRIDES:
                if cleaned not in result.suggested_tags:
                    result.suggested_tags.append(cleaned)

    # ── Material group key: parent directory ──
    result.material_group = str(filepath.parent)

    # ── Destination ──
    category = CATEGORY_FOLDERS.get(result.asset_type, "Other")
    if result.surface_type:
        result.dest_subfolder = f"{category}/{result.surface_category}/{result.surface_type}"
    elif result.suggested_tags:
        result.dest_subfolder = f"{category}/{result.suggested_tags[0]}"
    else:
        result.dest_subfolder = category

    return result


def classify_batch(
    files: list[Path],
    drop_root: Path | None = None,
) -> list[tuple[Path, ClassificationResult]]:
    """Classify a batch and group into materials."""
    results = []
    for filepath in files:
        parent_folders = []
        if drop_root and drop_root.is_dir():
            try:
                rel = filepath.relative_to(drop_root)
                parent_folders = [p for p in rel.parent.parts if p != "."]
            except ValueError:
                pass

        result = classify_file(filepath, parent_folders)
        results.append((filepath, result))

    # Group into material sets
    _detect_material_sets(results)

    return results


def _detect_material_sets(results: list[tuple[Path, ClassificationResult]]):
    """Detect material sets from files in the same directory with PBR sub-types."""
    dir_groups: dict[str, list[tuple[Path, ClassificationResult]]] = defaultdict(list)

    for filepath, result in results:
        if result.asset_type == "texture" and result.sub_type:
            dir_groups[result.material_group or ""].append((filepath, result))

    for group_key, group in dir_groups.items():
        # Need at least 2 maps to form a material
        if len(group) < 2:
            continue

        # Count actual PBR maps (not previews)
        pbr_maps = [(f, r) for f, r in group if r.sub_type != "preview"]
        if len(pbr_maps) < 2:
            continue

        # Determine material name from common prefix or folder name
        mat_name = _detect_material_name(group)

        # Find preview image if any
        preview_files = [f for f, r in group if r.is_preview]

        # Detect surface type from material name
        surface_cat, surface_type = _detect_surface_from_name(mat_name)

        for filepath, result in group:
            result.material_name = mat_name
            if surface_cat and not result.surface_category:
                result.surface_category = surface_cat
                result.surface_type = surface_type
                if surface_type and surface_type not in result.suggested_tags:
                    result.suggested_tags.insert(0, surface_type)

            # Update destination to group under material name
            category = CATEGORY_FOLDERS.get(result.asset_type, "Other")
            if surface_cat and surface_type:
                result.dest_subfolder = f"{category}/{surface_cat}/{surface_type}/{mat_name}"
            else:
                result.dest_subfolder = f"{category}/{mat_name}"


def _detect_material_name(group: list[tuple[Path, ClassificationResult]]) -> str:
    """Extract the common material name from a group of texture maps."""
    filenames = [f.stem for f, r in group if r.sub_type != "preview"]

    if not filenames:
        filenames = [f.stem for f, _ in group]

    if len(filenames) == 1:
        return _clean_material_stem(filenames[0])

    # Find common prefix
    prefix = filenames[0]
    for name in filenames[1:]:
        while not name.startswith(prefix) and prefix:
            # Remove last character or last segment
            if "_" in prefix:
                prefix = prefix[:prefix.rfind("_")]
            elif "-" in prefix:
                prefix = prefix[:prefix.rfind("-")]
            else:
                prefix = prefix[:-1]

    # Clean up trailing separators
    prefix = prefix.rstrip("_- ")

    if prefix and len(prefix) >= 3:
        return _clean_material_stem(prefix)

    # Fallback: use parent folder name
    folder_name = group[0][0].parent.name
    return _clean_folder_name(folder_name) or folder_name


def _clean_material_stem(stem: str) -> str:
    """Clean a stem to produce a nice material name."""
    # Remove resolution suffixes
    cleaned = re.sub(r"[_\-]?\d+[kK]$", "", stem)
    # Remove Quixel/Megascans IDs
    cleaned = re.sub(r"[_\-][a-z]{1,2}[a-z0-9]{4,12}$", "", cleaned, flags=re.I)
    # Remove trailing map type indicators that might remain
    for pattern, _ in SUB_TYPE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = cleaned.rstrip("_- ")
    return cleaned or stem


def _detect_surface_from_name(name: str) -> tuple[Optional[str], Optional[str]]:
    """Try to detect surface category from a material name."""
    words = re.split(r"[_\-\s]+", name.lower())
    for word in words:
        if word in SURFACE_CATEGORIES:
            cat, surf = SURFACE_CATEGORIES[word]
            return cat, surf

    # Try compound checks
    full = "_".join(words)
    if full in SURFACE_CATEGORIES:
        cat, surf = SURFACE_CATEGORIES[full]
        return cat, surf

    return None, None


def _extract_name_parts(stem: str, parent_folders: list[str]) -> list[str]:
    """Extract meaningful name parts from filename stem and parent folders."""
    parts = []
    # Split stem by separators
    for part in re.split(r"[_\-\s]+", stem):
        if part and len(part) >= 3:
            parts.append(part)

    # Add folder names
    for folder in parent_folders:
        for part in re.split(r"[_\-\s]+", folder):
            if part and len(part) >= 3:
                parts.append(part)

    return parts


def _clean_folder_name(name: str) -> str:
    """Clean a folder name for use as a tag."""
    cleaned = re.sub(r"^(tex_?|mat_?|texture_?)", "", name, flags=re.I)
    cleaned = re.sub(r"[_\-]+", " ", cleaned).strip()
    if cleaned:
        cleaned = cleaned.title()
    return cleaned
