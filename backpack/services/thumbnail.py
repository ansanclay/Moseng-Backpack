"""Thumbnail generation service."""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from backpack.constants import THUMB_DIR, THUMB_SIZE, IMAGE_EXTENSIONS
from backpack.utils.image_utils import generate_thumbnail


class ThumbnailSignals(QObject):
    finished = Signal(str, str)  # uuid, thumb_path
    error = Signal(str, str)     # uuid, error_message


class ThumbnailWorker(QRunnable):
    """Worker that generates a thumbnail in a thread pool."""

    def __init__(self, uuid: str, source_path: Path, base_path: Path):
        super().__init__()
        self.uuid = uuid
        self.source_path = source_path
        self.base_path = base_path
        self.signals = ThumbnailSignals()

    @Slot()
    def run(self):
        try:
            thumb_rel = _thumb_rel_path(self.uuid)
            thumb_abs = self.base_path / thumb_rel

            result = generate_thumbnail(self.source_path, thumb_abs, THUMB_SIZE)
            if result:
                self.signals.finished.emit(self.uuid, thumb_rel)
            else:
                self.signals.error.emit(self.uuid, "Failed to generate thumbnail")
        except Exception as e:
            self.signals.error.emit(self.uuid, str(e))


def _thumb_rel_path(uuid: str) -> str:
    """Get the relative thumbnail path for a given UUID."""
    prefix = uuid[:2]
    return f"{THUMB_DIR}/{prefix}/{uuid}.jpg"


def get_thumb_absolute_path(base_path: Path, uuid: str) -> Path:
    """Get the absolute path for a thumbnail."""
    return base_path / _thumb_rel_path(uuid)


def can_generate_thumbnail(filepath: Path) -> bool:
    """Check if we can generate a thumbnail for this file type."""
    return filepath.suffix.lower() in IMAGE_EXTENSIONS
