"""Settings dialog - drive, accent color, font."""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QGroupBox, QColorDialog, QCheckBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap, QIcon

from backpack.core.settings import AppSettings
from backpack.utils.platform_utils import get_available_drives


class SettingsDialog(QDialog):
    settings_changed = Signal()

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._color = settings.accent_color
        self.setWindowTitle("Settings")
        self.setMinimumSize(420, 380)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        title = QLabel("Settings")
        title.setObjectName("heading")
        layout.addWidget(title)

        # ── Drive ──
        drive_box = QGroupBox("Storage Drive")
        drive_layout = QHBoxLayout(drive_box)

        drive_layout.addWidget(QLabel("BACKPACK drive:"))
        self.drive_combo = QComboBox()
        drives = get_available_drives()
        current_idx = 0
        for i, d in enumerate(drives):
            self.drive_combo.addItem(d.display_name, d.letter)
            if d.letter == self.settings.drive_letter:
                current_idx = i
        self.drive_combo.setCurrentIndex(current_idx)
        drive_layout.addWidget(self.drive_combo, stretch=1)
        layout.addWidget(drive_box)

        # ── Appearance ──
        appear_box = QGroupBox("Appearance")
        appear_layout = QVBoxLayout(appear_box)

        # Primary color
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Primary color:"))
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(36, 28)
        self.color_btn.clicked.connect(self._pick_color)
        self._update_color_btn()
        color_row.addWidget(self.color_btn)
        color_row.addStretch()
        appear_layout.addLayout(color_row)

        # Font
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Font:"))
        self.font_combo = QComboBox()
        self.font_combo.addItems(["Segoe UI", "Inter", "Noto Sans", "Consolas", "Roboto", "Arial"])
        idx = self.font_combo.findText(self.settings.font_family)
        if idx >= 0:
            self.font_combo.setCurrentIndex(idx)
        font_row.addWidget(self.font_combo, stretch=1)
        appear_layout.addLayout(font_row)

        # Font size
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Font size:"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(8, 16)
        self.size_spin.setValue(self.settings.font_size)
        size_row.addWidget(self.size_spin)
        size_row.addStretch()
        appear_layout.addLayout(size_row)

        layout.addWidget(appear_box)

        # ── Integrations ──
        integ_box = QGroupBox("Integrations")
        integ_layout = QVBoxLayout(integ_box)

        self.quixel_check = QCheckBox("Enable Quixel / Megascans folder")
        self.quixel_check.setChecked(self.settings.quixel_enabled)
        self.quixel_check.setToolTip(
            "Creates BACKPACK/Quixel/Downloaded/ on disk and shows it in the folder tree."
        )
        integ_layout.addWidget(self.quixel_check)

        layout.addWidget(integ_box)

        # ── Buttons ──
        layout.addStretch()
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("Save")
        btn_save.setObjectName("primaryBtn")
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)

        layout.addLayout(btn_row)

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(self._color), self, "Select Primary Color")
        if color.isValid():
            self._color = color.name()
            self._update_color_btn()

    def _update_color_btn(self):
        pix = QPixmap(28, 20)
        pix.fill(QColor(self._color))
        self.color_btn.setIcon(QIcon(pix))
        self.color_btn.setStyleSheet(
            f"border: 2px solid {self._color}; border-radius: 4px; "
            f"background-color: {self._color};"
        )

    def _save(self):
        self.settings.drive_letter = self.drive_combo.currentData()
        self.settings.accent_color = self._color
        self.settings.font_family = self.font_combo.currentText()
        self.settings.font_size = self.size_spin.value()
        self.settings.quixel_enabled = self.quixel_check.isChecked()
        self.settings_changed.emit()
        self.accept()
