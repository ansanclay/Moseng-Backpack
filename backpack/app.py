"""Application setup and launch."""

import logging
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

from backpack.core.settings import AppSettings, load_settings, save_settings, SETTINGS_DIR
from backpack.ui.main_window import MainWindow
from backpack.ui.drive_selector import DriveSelector


def configure_logging(debug: bool) -> None:
    """Configure root logger.  Call at startup and whenever debug_mode changes."""
    root = logging.getLogger()
    root.handlers.clear()

    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
                            datefmt="%H:%M:%S")

    if debug:
        root.setLevel(logging.DEBUG)

        # Console handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.DEBUG)
        ch.setFormatter(fmt)
        root.addHandler(ch)

        # File handler — always written to settings dir so it's easy to find
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(SETTINGS_DIR / "debug.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    else:
        root.setLevel(logging.WARNING)
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.WARNING)
        ch.setFormatter(fmt)
        root.addHandler(ch)


def load_stylesheet(
    app: QApplication,
    accent: str | None = None,
    secondary_color: str | None = None,
    background: str | None = None,
):
    """Load and apply style.qss, substituting colour tokens."""
    from string import Template
    from backpack.ui import theme
    style_path = Path(__file__).parent / "ui" / "resources" / "style.qss"
    if style_path.exists():
        raw = style_path.read_text(encoding="utf-8")
        styled = Template(raw).safe_substitute(
            theme.as_dict(accent, secondary_color, background)
        )
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
    configure_logging(settings.debug_mode)

    # Apply font
    font = QFont(settings.font_family, settings.font_size)
    app.setFont(font)

    load_stylesheet(app, settings.accent_color, settings.secondary_color, settings.bg_color)

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
