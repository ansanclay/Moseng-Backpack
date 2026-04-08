"""Drive info model."""

from dataclasses import dataclass


@dataclass
class DriveInfo:
    letter: str
    label: str
    total_gb: float
    free_gb: float
    has_database: bool

    @property
    def display_name(self) -> str:
        label_part = f" [{self.label}]" if self.label else ""
        db_part = " (DB)" if self.has_database else ""
        return f"{self.letter}:{label_part} - {self.free_gb:.1f}GB free{db_part}"
