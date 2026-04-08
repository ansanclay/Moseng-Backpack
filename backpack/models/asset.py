"""Asset data model."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Asset:
    id: Optional[int] = None
    uuid: str = ""
    filename: str = ""
    rel_path: str = ""
    asset_type: str = "other"
    sub_type: Optional[str] = None
    file_ext: str = ""
    file_size: int = 0
    file_hash: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    color_space: Optional[str] = None
    bit_depth: Optional[int] = None
    thumb_path: Optional[str] = None
    source_path: Optional[str] = None
    notes: str = ""
    rating: int = 0
    material_id: Optional[int] = None
    created_at: str = ""
    modified_at: str = ""
    tags: list[str] = field(default_factory=list)

    @staticmethod
    def from_row(row: dict) -> "Asset":
        return Asset(
            id=row.get("id"),
            uuid=row.get("uuid", ""),
            filename=row.get("filename", ""),
            rel_path=row.get("rel_path", ""),
            asset_type=row.get("asset_type", "other"),
            sub_type=row.get("sub_type"),
            file_ext=row.get("file_ext", ""),
            file_size=row.get("file_size", 0),
            file_hash=row.get("file_hash"),
            width=row.get("width"),
            height=row.get("height"),
            color_space=row.get("color_space"),
            bit_depth=row.get("bit_depth"),
            thumb_path=row.get("thumb_path"),
            source_path=row.get("source_path"),
            notes=row.get("notes", ""),
            rating=row.get("rating", 0),
            material_id=row.get("material_id"),
            created_at=row.get("created_at", ""),
            modified_at=row.get("modified_at", ""),
        )
