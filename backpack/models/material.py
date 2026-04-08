"""Material set model - groups texture maps into one material."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Material:
    id: Optional[int] = None
    uuid: str = ""
    name: str = ""
    category: str = "Surface"
    surface_type: Optional[str] = None
    preview_path: Optional[str] = None
    thumb_path: Optional[str] = None
    asset_count: int = 0
    created_at: str = ""
    tags: list[str] = field(default_factory=list)
    maps: dict[str, "Asset"] = field(default_factory=dict)

    @staticmethod
    def from_row(row: dict) -> "Material":
        return Material(
            id=row.get("id"),
            uuid=row.get("uuid", ""),
            name=row.get("name", ""),
            category=row.get("category", "Surface"),
            surface_type=row.get("surface_type"),
            preview_path=row.get("preview_path"),
            thumb_path=row.get("thumb_path"),
            asset_count=row.get("asset_count", 0),
            created_at=row.get("created_at", ""),
        )

    @property
    def display_name(self) -> str:
        parts = [self.name]
        if self.surface_type:
            parts.append(f"({self.surface_type})")
        return " ".join(parts)
