"""Settings dialog - drive, accent color, font."""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QGroupBox, QColorDialog, QCheckBox,
    QTabWidget, QWidget, QPlainTextEdit,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QColor, QPixmap, QIcon

from backpack.core.settings import AppSettings
from backpack.utils.platform_utils import get_available_drives


class SettingsDialog(QDialog):
    settings_changed = Signal()
    reset_requested  = Signal()   # "Reset Metadata" button (Advanced tab)

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings     = settings
        self._primary     = settings.accent_color
        self._secondary   = settings.secondary_color
        self._bg          = settings.bg_color
        self._debug_color = settings.debug_overlay_color
        self.setWindowTitle("Settings")
        self.setMinimumSize(420, 380)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        title = QLabel("Settings")
        title.setObjectName("heading")
        layout.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(), "General")
        tabs.addTab(self._build_project_tab(), "Project")
        tabs.addTab(self._build_appearance_tab(), "Appearance")
        tabs.addTab(self._build_advanced_tab(), "Advanced")
        layout.addWidget(tabs, stretch=1)

        # ── Buttons ──
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

    # ── Tabs ──────────────────────────────────────────────────────────────────

    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)

        drive_box = QGroupBox("Storage Drive")
        drive_layout = QHBoxLayout(drive_box)
        drive_layout.addWidget(QLabel("BACKPACK drive:"))
        self.drive_combo = QComboBox()
        for d in get_available_drives():
            self.drive_combo.addItem(d.display_name, d.letter)
            if d.letter == self.settings.drive_letter:
                self.drive_combo.setCurrentIndex(self.drive_combo.count() - 1)
        drive_layout.addWidget(self.drive_combo, stretch=1)
        v.addWidget(drive_box)

        integ_box = QGroupBox("Integrations")
        integ_layout = QVBoxLayout(integ_box)
        self.quixel_check = QCheckBox("Enable Quixel / Megascans folder")
        self.quixel_check.setChecked(self.settings.quixel_enabled)
        self.quixel_check.setToolTip(
            "Creates BACKPACK/Quixel/Downloaded/ on disk and shows it in the folder tree."
        )
        integ_layout.addWidget(self.quixel_check)
        v.addWidget(integ_box)

        v.addStretch()
        return page

    def _build_project_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)

        info_box = QGroupBox("Active Project")
        iv = QVBoxLayout(info_box)
        root = self.settings.project_root or "(none — use Open / New in the Project panel)"
        lbl = QLabel(root)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#9ea0a8; font-family:'DM Mono','Consolas',monospace; font-size:11px;")
        iv.addWidget(lbl)
        v.addWidget(info_box)

        tmpl_box = QGroupBox("New-Project Folder Template")
        tv = QVBoxLayout(tmpl_box)
        hint = QLabel("One folder per line. Use “/” to nest, e.g. 01_Assets/Textures.\n"
                      "These folders are created on disk when you make a New Project.")
        hint.setStyleSheet("color:#6f7280; font-size:11px;")
        tv.addWidget(hint)
        self.template_edit = QPlainTextEdit()
        self.template_edit.setPlainText("\n".join(self.settings.project_template))
        self.template_edit.setStyleSheet(
            "QPlainTextEdit{font-family:'DM Mono','Consolas',monospace; font-size:11px;}")
        tv.addWidget(self.template_edit, stretch=1)
        btn_reset = QPushButton("Reset to default")
        btn_reset.clicked.connect(self._reset_template)
        tv.addWidget(btn_reset, alignment=Qt.AlignLeft)
        v.addWidget(tmpl_box, stretch=1)
        return page

    def _reset_template(self):
        from backpack.core.settings import DEFAULT_PROJECT_TEMPLATE
        self.template_edit.setPlainText("\n".join(DEFAULT_PROJECT_TEMPLATE))

    def _build_appearance_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)

        color_box = QGroupBox("Theme Colors")
        color_layout = QVBoxLayout(color_box)
        self.primary_btn   = self._color_row(color_layout, "Primary:",    self._primary,   self._pick_primary)
        self.secondary_btn = self._color_row(color_layout, "Secondary:",  self._secondary, self._pick_secondary)
        self.bg_btn        = self._color_row(color_layout, "Background:", self._bg,        self._pick_bg)
        v.addWidget(color_box)

        font_box = QGroupBox("Font")
        font_v = QVBoxLayout(font_box)
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Family:"))
        self.font_combo = QComboBox()
        self.font_combo.addItems(["Segoe UI", "Inter", "Noto Sans", "Consolas", "Roboto", "Arial"])
        idx = self.font_combo.findText(self.settings.font_family)
        if idx >= 0:
            self.font_combo.setCurrentIndex(idx)
        font_row.addWidget(self.font_combo, stretch=1)
        font_v.addLayout(font_row)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Size:"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(8, 16)
        self.size_spin.setValue(self.settings.font_size)
        size_row.addWidget(self.size_spin)
        size_row.addStretch()
        font_v.addLayout(size_row)
        v.addWidget(font_box)

        v.addStretch()
        return page

    def _build_advanced_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)

        # Data
        data_box = QGroupBox("Data")
        data_v = QVBoxLayout(data_box)
        data_v.addWidget(QLabel("Delete all .json metadata (tags, ratings, notes, favorites)."))
        btn_reset = QPushButton("Reset Metadata…")
        btn_reset.setToolTip("Delete all .json metadata files in BACKPACK/JSON/")
        btn_reset.clicked.connect(self.reset_requested.emit)
        data_v.addWidget(btn_reset, alignment=Qt.AlignLeft)
        v.addWidget(data_box)

        # Developer
        dev_box = QGroupBox("Developer")
        dev_layout = QVBoxLayout(dev_box)
        self.debug_check = QCheckBox("Debug mode")
        self.debug_check.setChecked(self.settings.debug_mode)
        self.debug_check.setToolTip(
            "Enable verbose logging to console and ~/.moseng_backpack/debug.log"
        )
        dev_layout.addWidget(self.debug_check)

        self.debug_color_btn = self._color_row(dev_layout, "Overlay color:", self._debug_color, self._pick_debug_color)

        dbg_line_row = QHBoxLayout()
        dbg_line_row.addWidget(QLabel("Line thickness:"))
        self.debug_line_spin = QDoubleSpinBox()
        self.debug_line_spin.setRange(0.5, 5.0)
        self.debug_line_spin.setSingleStep(0.5)
        self.debug_line_spin.setDecimals(1)
        self.debug_line_spin.setValue(self.settings.debug_line_width)
        self.debug_line_spin.setSuffix(" px")
        dbg_line_row.addWidget(self.debug_line_spin)
        dbg_line_row.addStretch()
        dev_layout.addLayout(dbg_line_row)
        v.addWidget(dev_box)

        v.addStretch()
        return page

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _color_row(self, parent_layout, label: str, color: str, slot) -> QPushButton:
        """Add a labeled color-swatch row and return the button."""
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        btn = QPushButton()
        btn.setFixedSize(36, 28)
        btn.clicked.connect(slot)
        self._refresh_swatch(btn, color)
        row.addWidget(btn)
        row.addStretch()
        parent_layout.addLayout(row)
        return btn

    @staticmethod
    def _refresh_swatch(btn: QPushButton, color: str):
        pix = QPixmap(28, 20)
        pix.fill(QColor(color))
        btn.setIcon(QIcon(pix))
        btn.setStyleSheet(
            f"border: 2px solid {color}; border-radius: 4px; "
            f"background-color: {color};"
        )

    # ── Pickers ───────────────────────────────────────────────────────────────

    def _pick_primary(self):
        c = QColorDialog.getColor(QColor(self._primary), self, "Primary Color")
        if c.isValid():
            self._primary = c.name()
            self._refresh_swatch(self.primary_btn, self._primary)

    def _pick_secondary(self):
        c = QColorDialog.getColor(QColor(self._secondary), self, "Secondary Color")
        if c.isValid():
            self._secondary = c.name()
            self._refresh_swatch(self.secondary_btn, self._secondary)

    def _pick_bg(self):
        c = QColorDialog.getColor(QColor(self._bg), self, "Background Color")
        if c.isValid():
            self._bg = c.name()
            self._refresh_swatch(self.bg_btn, self._bg)

    def _pick_debug_color(self):
        c = QColorDialog.getColor(QColor(self._debug_color), self, "Overlay Color")
        if c.isValid():
            self._debug_color = c.name()
            self._refresh_swatch(self.debug_color_btn, self._debug_color)

    # ── Save ──────────────────────────────────────────────────────────────────

    def _save(self):
        self.settings.drive_letter        = self.drive_combo.currentData()
        self.settings.accent_color        = self._primary
        self.settings.secondary_color     = self._secondary
        self.settings.bg_color            = self._bg
        self.settings.font_family         = self.font_combo.currentText()
        self.settings.font_size           = self.size_spin.value()
        self.settings.quixel_enabled      = self.quixel_check.isChecked()
        self.settings.debug_mode          = self.debug_check.isChecked()
        self.settings.debug_overlay_color = self._debug_color
        self.settings.debug_line_width    = self.debug_line_spin.value()
        lines = [l.strip() for l in self.template_edit.toPlainText().splitlines()]
        self.settings.project_template    = [l for l in lines if l]
        self.settings_changed.emit()
        self.accept()
