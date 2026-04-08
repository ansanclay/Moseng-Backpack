"""App settings stored in user home directory."""

import json
from pathlib import Path
from dataclasses import dataclass, asdict


SETTINGS_DIR = Path.home() / ".moseng_backpack"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"


@dataclass
class AppSettings:
    drive_letter: str = ""
    accent_color: str = "#4a9eff"
    font_family: str = "Segoe UI"
    font_size: int = 10
    grid_card_size: int = 200     # default card width in pixels
    last_type_filter: str = ""
    window_width: int = 1400
    window_height: int = 850


def load_settings() -> AppSettings:
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return AppSettings(**{k: v for k, v in data.items() if k in AppSettings.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError):
            pass
    return AppSettings()


def save_settings(s: AppSettings):
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
