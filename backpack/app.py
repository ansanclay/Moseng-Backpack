"""Application setup and launch."""

import sys
from pathlib import Path

from PySide6.QtGui import QSurfaceFormat  # must be set before QApplication
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QIcon

# OpenGL 3.3 Core Profile — must be set before QApplication is created
_fmt = QSurfaceFormat()
_fmt.setVersion(3, 3)
_fmt.setProfile(QSurfaceFormat.CoreProfile)
_fmt.setSamples(4)
QSurfaceFormat.setDefaultFormat(_fmt)

from backpack.core.settings import AppSettings, load_settings, save_settings
from backpack.ui.main_window import MainWindow
from backpack.ui.drive_selector import DriveSelector


def load_stylesheet(app: QApplication, accent: str | None = None):
    """Load and apply style.qss, substituting colour tokens.

    accent: the user's chosen accent color (settings.accent_color).
    Overrides $primary and its derived tokens so the QSS stays in sync
    with whatever the user has picked in Settings.
    """
    from string import Template
    from backpack.ui import theme
    style_path = Path(__file__).parent / "ui" / "resources" / "style.qss"
    if style_path.exists():
        raw = style_path.read_text(encoding="utf-8")
        styled = Template(raw).safe_substitute(theme.as_dict(accent))
        app.setStyleSheet(styled)


def run():
    import traceback
    try:
        _run()
    except Exception:
        # Write crash log next to main.py so it's visible even when console closes
        log_path = Path(__file__).parent.parent / "crash.log"
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
        raise


def _run():
    app = QApplication(sys.argv)
    app.setApplicationName("Moseng Backpack")
    app.setOrganizationName("Moseng")

    # Window icon (title bar + taskbar)
    _ico = Path(__file__).parent / "ui" / "resources" / "icon.ico"
    if _ico.exists():
        app.setWindowIcon(QIcon(str(_ico)))

    settings = load_settings()

    # Apply font
    font = QFont(settings.font_family, settings.font_size)
    app.setFont(font)

    load_stylesheet(app, settings.accent_color)

    # Check for drive
    drive_letter = settings.drive_letter
    if drive_letter:
        root = Path(f"{drive_letter}:/")
        if not root.exists():
            drive_letter = ""

    if not drive_letter:
        selector = DriveSelector()
        if selector.exec() and selector.selected_drive:
            drive_letter = selector.selected_drive.letter
        else:
            sys.exit(0)

    settings.drive_letter = drive_letter
    save_settings(settings)

    window = MainWindow(settings)
    window.init_drive(drive_letter)
    window.show()

    sys.exit(app.exec())
