"""Application setup and launch."""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

from backpack.core.settings import AppSettings, load_settings, save_settings
from backpack.ui.main_window import MainWindow
from backpack.ui.drive_selector import DriveSelector


def load_stylesheet(app: QApplication):
    style_path = Path(__file__).parent / "ui" / "resources" / "style.qss"
    if style_path.exists():
        app.setStyleSheet(style_path.read_text(encoding="utf-8"))


def run():
    app = QApplication(sys.argv)
    app.setApplicationName("Moseng Backpack")
    app.setOrganizationName("Moseng")

    settings = load_settings()

    # Apply font
    font = QFont(settings.font_family, settings.font_size)
    app.setFont(font)

    load_stylesheet(app)

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
