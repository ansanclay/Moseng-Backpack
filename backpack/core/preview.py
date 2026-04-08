"""Preview cache system - generates 512x512 thumbnails for fast browsing.

Each folder that contains images gets a `.preview/` subfolder with resized copies.
"""

from pathlib import Path
from PIL import Image

PREVIEW_SIZE = (512, 512)
PREVIEW_DIR_NAME = ".preview"
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".bmp", ".exr"}


def preview_dir_for(folder: Path) -> Path:
    """Return the .preview directory path for a given folder."""
    return folder / PREVIEW_DIR_NAME


def preview_path_for(filepath: Path) -> Path:
    """Return the cached preview path for an image file.

    Preview is always saved as .jpg for consistency and small size.
    """
    pdir = preview_dir_for(filepath.parent)
    return pdir / f"{filepath.stem}_preview.jpg"


def ensure_preview(filepath: Path, force: bool = False) -> Path | None:
    """Generate a 512x512 preview for an image file if it doesn't exist.

    Returns the preview path, or None if the file can't be previewed.
    """
    if filepath.suffix.lower() not in _IMAGE_EXTS:
        return None

    ppath = preview_path_for(filepath)

    if not force and ppath.exists():
        # Check if source is newer
        if ppath.stat().st_mtime >= filepath.stat().st_mtime:
            return ppath

    try:
        pdir = ppath.parent
        pdir.mkdir(exist_ok=True)

        img = Image.open(str(filepath))
        img.thumbnail(PREVIEW_SIZE, Image.LANCZOS)

        # Convert to RGB for JPEG
        if img.mode in ("RGBA", "P", "LA", "I", "F"):
            img = img.convert("RGB")

        img.save(str(ppath), "JPEG", quality=85)
        return ppath
    except Exception:
        return None


def generate_previews_for_folder(folder: Path) -> int:
    """Generate previews for all images in a folder. Returns count generated."""
    count = 0
    for f in folder.iterdir():
        if f.is_file() and f.suffix.lower() in _IMAGE_EXTS:
            if ensure_preview(f):
                count += 1
    return count


def sync_previews(backpack_root: Path) -> int:
    """Generate preview caches for the entire BACKPACK tree.

    Returns total number of previews generated/updated.
    """
    total = 0

    if not backpack_root.exists():
        return total

    # Materials - each material folder gets previews
    mat_root = backpack_root / "Materials"
    if mat_root.exists():
        for source_dir in mat_root.iterdir():
            if not source_dir.is_dir():
                continue
            for mat_dir in source_dir.iterdir():
                if not mat_dir.is_dir() or mat_dir.name.startswith("."):
                    continue
                total += generate_previews_for_folder(mat_dir)

    # Textures, Gobo, Other - flat folders
    for folder_name in ("Textures", "Gobo", "Other"):
        folder = backpack_root / folder_name
        if folder.exists():
            total += generate_previews_for_folder(folder)

    return total


def clean_orphaned_previews(backpack_root: Path) -> int:
    """Remove preview files whose source no longer exists."""
    removed = 0

    for preview_dir in backpack_root.rglob(PREVIEW_DIR_NAME):
        if not preview_dir.is_dir():
            continue
        parent = preview_dir.parent
        for pf in preview_dir.iterdir():
            if not pf.is_file():
                continue
            # Preview name: {stem}_preview.jpg -> original stem
            orig_stem = pf.stem.replace("_preview", "")
            # Check if any source file with this stem exists
            matches = [f for f in parent.iterdir()
                       if f.is_file() and f.stem == orig_stem
                       and f.suffix.lower() in _IMAGE_EXTS]
            if not matches:
                pf.unlink()
                removed += 1

        # Remove empty preview dirs
        if preview_dir.exists() and not any(preview_dir.iterdir()):
            preview_dir.rmdir()

    return removed
