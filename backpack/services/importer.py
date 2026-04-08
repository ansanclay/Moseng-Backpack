"""File import pipeline service with material grouping.

Runs import synchronously on a QThread to avoid SQLite threading issues.
"""

import uuid as uuid_mod
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from backpack.constants import CATEGORY_FOLDERS
from backpack.services.classifier import ClassificationResult, classify_batch
from backpack.utils.file_utils import (
    collect_files, compute_file_hash, safe_copy, get_file_extension,
)
from backpack.utils.image_utils import get_image_info


class ImportResult:
    """Result of a single file import."""
    def __init__(self, asset_uuid: str, rel_path: str, asset_type: str,
                 sub_type: str | None, material_group: str | None,
                 material_name: str | None, surface_category: str | None,
                 surface_type: str | None, suggested_tags: list[str],
                 filename: str, file_ext: str, file_size: int,
                 file_hash: str, width: int | None, height: int | None,
                 color_space: str | None, bit_depth: int | None,
                 source_path: str, is_preview: bool):
        self.asset_uuid = asset_uuid
        self.rel_path = rel_path
        self.asset_type = asset_type
        self.sub_type = sub_type
        self.material_group = material_group
        self.material_name = material_name
        self.surface_category = surface_category
        self.surface_type = surface_type
        self.suggested_tags = suggested_tags
        self.filename = filename
        self.file_ext = file_ext
        self.file_size = file_size
        self.file_hash = file_hash
        self.width = width
        self.height = height
        self.color_space = color_space
        self.bit_depth = bit_depth
        self.source_path = source_path
        self.is_preview = is_preview


class ImportWorker(QThread):
    """Worker thread that copies files to DATABASE and returns results.

    Does NOT touch the database — only file I/O.
    The main thread handles all DB operations to avoid SQLite threading issues.
    """

    progress = Signal(int, int, str)       # current, total, filename
    file_imported = Signal(object)         # ImportResult
    finished_import = Signal(int)          # total imported count
    error = Signal(str)

    def __init__(self, base_path: Path,
                 items: list[tuple[Path, ClassificationResult]],
                 existing_hashes: set[str],
                 parent=None):
        super().__init__(parent)
        self.base_path = base_path
        self.items = items
        self.existing_hashes = existing_hashes
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = len(self.items)
        imported = 0

        for i, (src_path, classification) in enumerate(self.items):
            if self._cancelled:
                break

            self.progress.emit(i + 1, total, src_path.name)

            try:
                result = self._copy_single(src_path, classification)
                if result:
                    imported += 1
                    self.file_imported.emit(result)
            except Exception as e:
                self.error.emit(f"Error importing {src_path.name}: {e}")

        self.finished_import.emit(imported)

    def _copy_single(self, src_path: Path,
                     classification: ClassificationResult) -> ImportResult | None:
        # Compute hash for dedup
        file_hash = compute_file_hash(src_path)
        if file_hash in self.existing_hashes:
            return None

        asset_uuid = str(uuid_mod.uuid4())

        # Copy file to destination inside DATABASE/BACKPACK/...
        dest_dir = self.base_path / "BACKPACK" / classification.dest_subfolder
        dest_path = safe_copy(src_path, dest_dir / src_path.name)
        rel_path = str(dest_path.relative_to(self.base_path)).replace("\\", "/")

        ext = get_file_extension(src_path)
        img_info = get_image_info(src_path)

        return ImportResult(
            asset_uuid=asset_uuid,
            rel_path=rel_path,
            asset_type=classification.asset_type,
            sub_type=classification.sub_type,
            material_group=classification.material_group,
            material_name=classification.material_name,
            surface_category=classification.surface_category,
            surface_type=classification.surface_type,
            suggested_tags=classification.suggested_tags,
            filename=src_path.name,
            file_ext=ext,
            file_size=src_path.stat().st_size,
            file_hash=file_hash,
            width=img_info.get("width"),
            height=img_info.get("height"),
            color_space=img_info.get("color_space"),
            bit_depth=img_info.get("bit_depth"),
            source_path=str(src_path),
            is_preview=classification.is_preview,
        )


def prepare_import(paths: list[str]) -> list[tuple[Path, ClassificationResult]]:
    """Prepare import by collecting files and classifying them."""
    all_files = []
    drop_root = None

    for path_str in paths:
        p = Path(path_str)
        if p.is_dir():
            drop_root = p
            all_files.extend(collect_files(p))
        else:
            all_files.append(p)

    return classify_batch(all_files, drop_root)
