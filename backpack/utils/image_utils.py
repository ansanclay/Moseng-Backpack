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


def read_exr_float32(path: Path) -> "np.ndarray | None":
    """Read an EXR file and return a float32 (H, W, 3) array.

    Tries three strategies in order:
      1. OpenEXR 3.x Python API (fastest, handles all channel layouts)
      2. imageio (works when float32 is returned — avoids PyAV/ffmpeg fallback)
      3. Pillow (last resort, requires system libOpenEXR)
    """
    import numpy as np

    # ── Strategy 1: OpenEXR 3.x ──────────────────────────────────────────────
    try:
        import OpenEXR

        f = OpenEXR.File(str(path))
        ch = f.channels()

        # Combined RGB/RGBA channel (some exporters pack all channels together)
        for key in ("RGB", "RGBA"):
            if key in ch:
                arr = ch[key].pixels.astype(np.float32)
                if arr.ndim == 3 and arr.shape[2] >= 3:
                    return arr[:, :, :3]
                if arr.ndim == 2:
                    return np.stack([arr] * 3, axis=-1)

        # Separate R, G, B channels
        r = ch.get("R") or ch.get("r")
        g = ch.get("G") or ch.get("g")
        b = ch.get("B") or ch.get("b")
        if r is not None and g is not None and b is not None:
            return np.stack(
                [r.pixels.astype(np.float32),
                 g.pixels.astype(np.float32),
                 b.pixels.astype(np.float32)],
                axis=-1,
            )

        # Single channel (roughness, metallic, displacement, AO …)
        # Try Y/luminance first, then any available channel
        single = (ch.get("Y") or ch.get("y")
                  or ch.get("R") or ch.get("r")
                  or ch.get("G") or ch.get("g")
                  or ch.get("B") or ch.get("b")
                  or (next(iter(ch.values())) if ch else None))
        if single is not None:
            arr = single.pixels.astype(np.float32)
            if arr.ndim == 2:
                return np.stack([arr] * 3, axis=-1)
            if arr.ndim == 3 and arr.shape[2] == 1:
                return np.repeat(arr, 3, axis=2)
    except Exception:
        pass

    # ── Strategy 2: imageio (only useful when it returns float data) ──────────
    try:
        import imageio.v3 as iio
        arr = iio.imread(str(path))
        if arr.dtype in (np.float16, np.float32, np.float64):
            arr = arr.astype(np.float32)
            if arr.ndim == 2:
                return np.stack([arr] * 3, axis=-1)
            if arr.ndim == 3:
                if arr.shape[2] == 1:
                    return np.repeat(arr, 3, axis=2)
                return arr[:, :, :3]
    except Exception:
        pass

    # ── Strategy 3: Pillow (requires system libOpenEXR, returns normalised data)
    try:
        from PIL import Image as PILImage
        with PILImage.open(str(path)) as pil_img:
            mode = pil_img.mode
            if mode == "F":                     # 32-bit float single channel
                arr = np.array(pil_img, dtype=np.float32)
                return np.stack([arr] * 3, axis=-1)
            elif mode in ("RGB", "RGBA"):
                arr = np.array(pil_img.convert("RGB"), dtype=np.float32) / 255.0
                return arr
            else:
                arr = np.array(pil_img.convert("RGB"), dtype=np.float32) / 255.0
                return arr
    except Exception:
        pass

    return None


def _generate_hdr_thumbnail(
    src: Path, dst: Path, size: tuple[int, int]
) -> Optional[Path]:
    """Generate thumbnail from HDR/EXR using tone mapping."""
    try:
        import numpy as np

        ext = src.suffix.lower()
        if ext == ".exr":
            img = read_exr_float32(src)
        else:
            # .hdr — imageio handles Radiance HDR natively
            import imageio.v3 as iio
            img = iio.imread(str(src)).astype(np.float32)

        if img is None:
            return None

        # Normalise to (H, W, 3)
        if img.ndim == 2:
            img = np.stack([img] * 3, axis=-1)
        elif img.ndim == 3:
            nc = img.shape[2]
            if nc == 1:
                img = np.repeat(img, 3, axis=2)
            elif nc == 2:
                img = np.stack([img[:, :, 0], img[:, :, 1], img[:, :, 0]], axis=-1)
            elif nc >= 4:
                img = img[:, :, :3]

        img = np.nan_to_num(img, nan=0.0, posinf=1.0, neginf=0.0)

        # Reinhard + gamma
        img = img / (1.0 + img)
        img = np.power(np.clip(img, 0, 1), 1.0 / 2.2)
        img = (img * 255).astype(np.uint8)

        pil_img = Image.fromarray(img)
        pil_img.thumbnail(size, Image.Resampling.LANCZOS)
        pil_img.save(str(dst), "JPEG", quality=85)
        return dst
    except Exception:
        return None
