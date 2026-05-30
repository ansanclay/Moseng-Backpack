"""App settings stored in user home directory."""

import json
from pathlib import Path
from dataclasses import dataclass, asdict, field


SETTINGS_DIR = Path.home() / ".moseng_backpack"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

# Default folder structure created for a new project (one folder path per entry;
# "/" nests). Users can customise this in Settings → Project.
DEFAULT_PROJECT_TEMPLATE = [
    "01_Assets",
    "01_Assets/Models",
    "01_Assets/Textures",
    "02_Scenes",
    "03_Caches",
    "04_Renders",
    "05_References",
    "06_Exports",
]


@dataclass
class AppSettings:
    drive_letter: str = ""
    accent_color: str = "#F2EEDC"     # primary
    secondary_color: str = "#FFFFFF"  # secondary
    bg_color: str = "#171E1B"         # background
    font_family: str = "Segoe UI"
    font_size: int = 10
    grid_card_size: int = 200     # default card width in pixels
    last_type_filter: str = ""
    window_width: int = 1400
    window_height: int = 850
    quixel_enabled: bool = False
    last_folder_path: str = ""    # disk_path of last selected FolderNode
    debug_mode: bool = False
    debug_overlay_color: str = "#FF4040"
    debug_line_width: float = 1.0
    dock_state: str = ""          # base64 of CDockManager.saveState() — panel layout
    project_root: str = ""        # active project folder (Project panel)
    project_template: list = field(default_factory=lambda: list(DEFAULT_PROJECT_TEMPLATE))


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
