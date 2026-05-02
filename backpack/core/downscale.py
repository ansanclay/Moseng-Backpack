"""Resolution downscale system - create 2K/1K copies from 4K textures.

Supports Quixel naming convention: `name_4K_Albedo.jpg` -> `name_2K_Albedo.jpg`
Also handles generic resolution detection from image dimensions.
"""

import re
from pathlib import Path
from PIL import Image

# Quixel resolution pattern: _4K_, _2K_, _1K_, _512_ etc.
_RES_PATTERN = re.compile(r"_(\d+K|512)_", re.I)

# Resolution targets (tag → max pixel dimension)
RESOLUTION_MAP = {
    "8K":  (8192, 8192),
    "4K":  (4096, 4096),
    "2K":  (2048, 2048),
    "1K":  (1024, 1024),
    "512": (512,  512),
}

# "Copy as Half Resolution" chain
_HALF_STEP = {"8K": "4K", "4K": "2K", "2K": "1K", "1K": "512"}


def half_resolution(res: str) -> str | None:
    """Return the next-smaller resolution tag, or None if already at minimum."""
    return _HALF_STEP.get(res.upper())


def _res_px(tag: str) -> int:
    """Convert a resolution tag to its pixel count for numeric comparison."""
    tag = tag.upper()
    if tag.endswith("K"):
        return int(tag[:-1]) * 1024
    return int(tag)   # e.g. "512" → 512

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".bmp", ".exr"}


def detect_resolution_tag(filename: str) -> str | None:
    """Extract the resolution tag (e.g. '4K') from a filename."""
    m = _RES_PATTERN.search(filename)
    return m.group(1).upper() if m else None


def detect_resolution_from_image(filepath: Path) -> str | None:
    """Detect resolution by reading the actual image dimensions."""
    try:
        with Image.open(str(filepath)) as img:
            w, h = img.size
            larger = max(w, h)
            if larger >= 7680:
                return "8K"
            if larger >= 3840:
                return "4K"
            if larger >= 1920:
                return "2K"
            if larger >= 960:
                return "1K"
    except Exception:
        pass
    return None


def get_available_resolutions(material_folder: Path) -> list[str]:
    """Detect which resolution variants exist in a material folder.

    Returns sorted list like ['1K', '2K', '4K'].
    """
    found = set()
    for f in material_folder.iterdir():
        if not f.is_file() or f.suffix.lower() not in _IMAGE_EXTS:
            continue
        tag = detect_resolution_tag(f.name)
        if tag:
            found.add(tag)

    if not found:
        # Try detecting from actual image size (use first image)
        for f in material_folder.iterdir():
            if f.is_file() and f.suffix.lower() in _IMAGE_EXTS:
                tag = detect_resolution_from_image(f)
                if tag:
                    found.add(tag)
                break

    order = ["512", "1K", "2K", "4K", "8K"]
    return [r for r in order if r in found]


def downscale_target_name(filename: str, target_res: str) -> str:
    """Generate the target filename by replacing the resolution tag.

    'sdxkdfwa_4K_Albedo.jpg' + '2K' -> 'sdxkdfwa_2K_Albedo.jpg'
    """
    m = _RES_PATTERN.search(filename)
    if m:
        return filename[:m.start(1)] + target_res + filename[m.end(1):]
    # No resolution tag - insert before extension
    stem = Path(filename).stem
    ext = Path(filename).suffix
    return f"{stem}_{target_res}{ext}"


def downscale_material(material_folder: Path, target_res: str) -> tuple[int, list[str]]:
    """Downscale all texture maps in a material folder to the target resolution.

    Only processes files that have a resolution tag AND are higher than target.
    Skips preview images.

    Returns (count_created, errors).
    """
    target_size = RESOLUTION_MAP.get(target_res)
    if not target_size:
        return 0, [f"Unknown resolution: {target_res}"]

    created = 0
    errors = []

    for f in sorted(material_folder.iterdir()):
        if not f.is_file() or f.suffix.lower() not in _IMAGE_EXTS:
            continue

        # Skip preview images
        if re.search(r"(preview|thumb)", f.stem, re.I):
            continue

        src_tag = detect_resolution_tag(f.name)
        if not src_tag:
            continue

        # Only downscale from higher resolution
        if _res_px(src_tag) <= _res_px(target_res):
            continue

        target_name = downscale_target_name(f.name, target_res)
        target_path = material_folder / target_name

        if target_path.exists():
            continue  # Already exists

        try:
            img = Image.open(str(f))
            # Scale proportionally based on target
            w, h = img.size
            scale = target_size[0] / max(w, h)
            if scale >= 1.0:
                continue  # Already smaller
            new_w = int(w * scale)
            new_h = int(h * scale)
            resized = img.resize((new_w, new_h), Image.LANCZOS)

            # Save in same format
            ext = f.suffix.lower()
            if ext in (".jpg", ".jpeg"):
                if resized.mode in ("RGBA", "P", "LA"):
                    resized = resized.convert("RGB")
                resized.save(str(target_path), "JPEG", quality=90)
            elif ext == ".png":
                resized.save(str(target_path), "PNG")
            elif ext in (".tif", ".tiff"):
                resized.save(str(target_path), "TIFF")
            else:
                # Fallback: save as PNG
                target_path = target_path.with_suffix(".png")
                resized.save(str(target_path), "PNG")

            created += 1
        except Exception as e:
            errors.append(f"{f.name}: {e}")

    return created, errors


def downscale_single_file(filepath: Path, target_res: str) -> tuple[Path | None, str]:
    """Downscale a single texture file.

    Returns (new_path, error_msg). new_path is None on error.
    """
    target_size = RESOLUTION_MAP.get(target_res)
    if not target_size:
        return None, f"Unknown resolution: {target_res}"

    target_name = downscale_target_name(filepath.name, target_res)
    target_path = filepath.parent / target_name

    if target_path.exists():
        return target_path, ""

    try:
        img = Image.open(str(filepath))
        w, h = img.size
        scale = target_size[0] / max(w, h)
        if scale >= 1.0:
            return None, "Image is already smaller than target"
        new_w = int(w * scale)
        new_h = int(h * scale)
        resized = img.resize((new_w, new_h), Image.LANCZOS)

        ext = filepath.suffix.lower()
        if ext in (".jpg", ".jpeg"):
            if resized.mode in ("RGBA", "P", "LA"):
                resized = resized.convert("RGB")
            resized.save(str(target_path), "JPEG", quality=90)
        elif ext == ".png":
            resized.save(str(target_path), "PNG")
        elif ext in (".tif", ".tiff"):
            resized.save(str(target_path), "TIFF")
        else:
            target_path = target_path.with_suffix(".png")
            resized.save(str(target_path), "PNG")

        return target_path, ""
    except Exception as e:
        return None, str(e)
