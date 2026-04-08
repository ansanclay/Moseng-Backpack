"""Tag data model."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Tag:
    id: Optional[int] = None
    name: str = ""
    color: str = "#888888"

    @staticmethod
    def from_row(row: dict) -> "Tag":
        return Tag(
            id=row.get("id"),
            name=row.get("name", ""),
            color=row.get("color", "#888888"),
        )
