"""Preview cache system - generates 512px JPEG thumbnails for fast browsing.

New structure (when backpack_root is configured):
  BACKPACK/PREVIEWS/<relative-path-from-ASSETS>/<stem>.jpeg

Call set_backpack_root() once at startup (main_window.init_drive).
"""

from pathlib import Path
from PIL import Image
from typing import Optional

PREVIEW_SIZE = (512, 512)
PREVIEWS_DIR_NAME = "PREVIEWS"
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".bmp", ".exr", ".hdr"}

# Module-level root — set once at startup via set_backpack_root()
_backpack_root: Optional[Path] = None


def set_backpack_root(root: Path) -> None:
    global _backpack_root
    _backpack_root = root


def preview_dir_for(folder: Path) -> Path:
    """Return the PREVIEWS directory path that mirrors a given asset folder."""
    if _backpack_root:
        assets_root = _backpack_root / "ASSETS"
        try:
            rel = folder.relative_to(assets_root)
            return _backpack_root / PREVIEWS_DIR_NAME / rel
        except ValueError:
            pass
    # Fallback: legacy in-folder .preview/ subdir
    return folder / ".preview"


def preview_path_for(filepath: Path) -> Path:
    """Return the cached preview path for an image file.

    Saved as .jpeg in BACKPACK/PREVIEWS/ mirroring the ASSETS tree.
    """
    if _backpack_root:
        assets_root = _backpack_root / "ASSETS"
        try:
            rel = filepath.relative_to(assets_root)
            return _backpack_root / PREVIEWS_DIR_NAME / rel.parent / f"{filepath.stem}.jpeg"
        except ValueError:
            pass
    # Fallback: legacy in-folder .preview/ subdir
    return filepath.parent / ".preview" / f"{filepath.stem}_preview.jpg"


def ensure_preview(filepath: Path, force: bool = False) -> Path | None:
    """Generate a 512px preview for an image file if it doesn't exist.

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
        pdir.mkdir(parents=True, exist_ok=True)

        ext = filepath.suffix.lower()
        if ext in (".exr", ".hdr"):
            from backpack.utils.image_utils import _generate_hdr_thumbnail
            result = _generate_hdr_thumbnail(filepath, ppath, PREVIEW_SIZE)
            return result
        else:
            img = Image.open(str(filepath))
            img.thumbnail(PREVIEW_SIZE, Image.LANCZOS)

            # Convert to RGB for JPEG
            if img.mode in ("RGBA", "P", "LA", "I", "F"):
                img = img.convert("RGB")

            img.save(str(ppath), "JPEG", quality=85)
            return ppath
    except Exception:
        return None


def generate_previews_for_folder(folder: Path, since: float | None = None) -> int:
    """Generate previews for all images in a folder. Returns count generated.

    If ``since`` is given (unix timestamp), skip the folder entirely when its
    mtime is older than that timestamp — meaning nothing was added/changed.
    """
    if since is not None and folder.exists() and folder.stat().st_mtime <= since:
        return 0
    count = 0
    for f in folder.iterdir():
        if f.is_file() and f.suffix.lower() in _IMAGE_EXTS:
            if ensure_preview(f):
                count += 1
    return count


def sync_previews(
    backpack_root: Path,
    since: float | None = None,
    on_progress: "callable | None" = None,
) -> int:
    """Generate preview caches for the entire BACKPACK tree.

    If ``since`` is given, only process folders modified after that timestamp.
    ``on_progress(current, total, label)`` is called before processing each file.
    Returns total number of previews generated/updated.
    """
    if not backpack_root.exists():
        return 0

    from backpack.core.folder_model import build_folder_tree

    root_node = build_folder_tree(backpack_root, quixel_enabled=True)

    # ── Pass 1: collect all image files to process ───────────────────────────
    from backpack.core.scanner import _collect_material_dirs, _collect_model_asset_dirs

    _files: list[Path] = []
    _SKIP = {"PREVIEWS", "JSON", ".preview", ".json", ".thumbs", "__MACOSX"}

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
            # Find each leaf material folder and collect its direct images
            for mat_dir in _collect_material_dirs(folder):
                _collect_leaf_images(mat_dir)
        elif node.scan_mode == "model_folder":
            # Find each leaf asset folder and collect its images (direct + one subdir)
            for asset_dir in _collect_model_asset_dirs(folder):
                _collect_leaf_images(asset_dir)
        else:
            # Flat scan (texture / hdri / gobo) — recurse through all subdirs
            _collect_recursive_images(folder)

        for child in node.children:
            _collect(child)

    def _collect_leaf_images(folder: Path):
        """Collect images from a leaf asset/material folder (direct + one subdir level)."""
        try:
            for entry in folder.iterdir():
                if entry.name.startswith(".") or entry.name in _SKIP:
                    continue
                if entry.is_file() and entry.suffix.lower() in _IMAGE_EXTS:
                    _files.append(entry)
                elif entry.is_dir():
                    try:
                        for sub in entry.iterdir():
                            if sub.is_file() and sub.suffix.lower() in _IMAGE_EXTS:
                                _files.append(sub)
                    except (PermissionError, FileNotFoundError):
                        pass
        except (PermissionError, FileNotFoundError):
            pass

    def _collect_recursive_images(folder: Path):
        """Collect images recursively (for flat scan modes: texture, hdri, gobo)."""
        if since is not None and folder.exists() and folder.stat().st_mtime <= since:
            return
        try:
            for f in folder.rglob("*"):
                if not f.is_file():
                    continue
                if f.suffix.lower() not in _IMAGE_EXTS:
                    continue
                if any(part in _SKIP for part in f.parts):
                    continue
                _files.append(f)
        except (PermissionError, FileNotFoundError):
            pass

    _collect(root_node)

    # ── Pass 2: generate previews with progress ──────────────────────────────
    total_files = len(_files)
    generated = 0
    for idx, f in enumerate(_files):
        if on_progress:
            on_progress(idx, total_files, f.name)
        if ensure_preview(f):
            generated += 1

    if on_progress:
        on_progress(total_files, total_files, "")
    return generated


def clean_orphaned_previews(backpack_root: Path) -> int:
    """Remove preview files in BACKPACK/PREVIEWS/ whose source no longer exists."""
    removed = 0
    previews_root = backpack_root / PREVIEWS_DIR_NAME
    assets_root   = backpack_root / "ASSETS"

    if not previews_root.exists():
        return removed

    for pf in previews_root.rglob("*.jpeg"):
        if not pf.is_file():
            continue
        # Mirror path: PREVIEWS/<rel> → ASSETS/<rel>
        try:
            rel = pf.relative_to(previews_root)
        except ValueError:
            continue
        # Find the original asset: same relative path under ASSETS, any image extension
        asset_dir = assets_root / rel.parent
        stem = pf.stem
        if not asset_dir.exists():
            pf.unlink()
            removed += 1
            continue
        matches = [f for f in asset_dir.iterdir()
                   if f.is_file() and f.stem == stem
                   and f.suffix.lower() in _IMAGE_EXTS]
        if not matches:
            pf.unlink()
            removed += 1

    # Remove empty preview dirs
    for d in sorted(previews_root.rglob("*"), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            try:
                d.rmdir()
            except OSError:
                pass

    return removed
