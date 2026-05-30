"""Image utility functions for reading metadata and generating thumbnails."""

from pathlib import Path
from typing import Optional

from PIL import Image

from backpack.constants import IMAGE_EXTENSIONS


def get_image_info(filepath: Path) -> dict:
    """Get image dimensions, bit depth, and color space info."""
    ext = filepath.suffix.lower()
    info = {"width": None, "height": None, "bit_depth": None, "color_space": None}

    if ext not in IMAGE_EXTENSIONS:
        return info

    try:
        if ext in (".exr", ".hdr"):
            return _get_hdr_info(filepath)

        with Image.open(filepath) as img:
            info["width"], info["height"] = img.size
            mode_bits = {"L": 8, "LA": 8, "RGB": 8, "RGBA": 8,
                         "I;16": 16, "I": 32, "F": 32}
            info["bit_depth"] = mode_bits.get(img.mode, 8)
            info["color_space"] = "sRGB"
    except Exception:
        pass

    return info


def _get_hdr_info(filepath: Path) -> dict:
    """Get info from HDR/EXR files using imageio."""
    info = {"width": None, "height": None, "bit_depth": 32, "color_space": "Linear"}
    try:
        import imageio.v3 as iio
        meta = iio.improps(filepath, plugin="pillow")
        info["width"] = meta.shape[1]
        info["height"] = meta.shape[0]
    except Exception:
        try:
            import numpy as np
            import imageio.v3 as iio
            img = iio.imread(filepath)
            info["height"], info["width"] = img.shape[:2]
        except Exception:
            pass
    return info


def generate_thumbnail(
    src: Path, dst: Path, size: tuple[int, int] = (256, 256)
) -> Optional[Path]:
    """Generate a JPEG thumbnail from an image file."""
    ext = src.suffix.lower()
    dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        if ext in (".exr", ".hdr"):
            return _generate_hdr_thumbnail(src, dst, size)

        with Image.open(src) as img:
            img = img.convert("RGB")
            img.thumbnail(size, Image.Resampling.LANCZOS)
            img.save(str(dst), "JPEG", quality=85)
            return dst
    except Exception:
        return None


def _generate_hdr_thumbnail(
    src: Path, dst: Path, size: tuple[int, int]
) -> Optional[Path]:
    """Generate thumbnail from HDR/EXR using tone mapping."""
    try:
        import numpy as np
        import imageio.v3 as iio

        img = iio.imread(src).astype(np.float32)
        if img.ndim == 2:
            img = np.stack([img] * 3, axis=-1)
        elif img.shape[2] == 4:
            img = img[:, :, :3]

        # Simple Reinhard tone mapping
        img = img / (1.0 + img)
        # Gamma correction
        img = np.power(np.clip(img, 0, 1), 1.0 / 2.2)
        img = (img * 255).astype(np.uint8)

        pil_img = Image.fromarray(img)
        pil_img.thumbnail(size, Image.Resampling.LANCZOS)
        pil_img.save(str(dst), "JPEG", quality=85)
        return dst
    except Exception:
        return None
