"""File utility functions."""

import hashlib
import os
import shutil
from pathlib import Path


def compute_file_hash(filepath: str | Path, algorithm: str = "sha256") -> str:
    """Compute hash of a file in chunks to handle large files."""
    h = hashlib.new(algorithm)
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_copy(src: Path, dst: Path) -> Path:
    """Copy a file, creating parent directories and handling name collisions."""
    dst.parent.mkdir(parents=True, exist_ok=True)

    if dst.exists():
        stem = dst.stem
        suffix = dst.suffix
        counter = 1
        while dst.exists():
            dst = dst.parent / f"{stem}_{counter}{suffix}"
            counter += 1

    shutil.copy2(str(src), str(dst))
    return dst


def get_file_extension(filepath: str | Path) -> str:
    """Get lowercase file extension including the dot."""
    p = Path(filepath)
    # Handle double extensions like .bgeo.sc
    suffixes = p.suffixes
    if len(suffixes) >= 2:
        combined = "".join(suffixes[-2:]).lower()
        if combined in (".bgeo.sc",):
            return combined
    return p.suffix.lower()


def collect_files(path: str | Path, recursive: bool = True) -> list[Path]:
    """Collect all files from a path (file or directory)."""
    p = Path(path)
    if p.is_file():
        return [p]
    elif p.is_dir():
        files = []
        if recursive:
            for root, _, filenames in os.walk(p):
                for fname in filenames:
                    files.append(Path(root) / fname)
        else:
            files = [f for f in p.iterdir() if f.is_file()]
        return sorted(files)
    return []
