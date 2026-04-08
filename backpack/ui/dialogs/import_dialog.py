"""Import wizard - 2 step dialog for importing files.

Step 1: Select asset type (Material, Texture, Gobo, Other) - auto-detected, user confirms
Step 2: Select source (Quixel, Poliigon, textures.com, Other) - only for Materials
"""

import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QButtonGroup, QProgressBar, QGroupBox,
    QFileDialog,
)
from PySide6.QtCore import Qt, Signal

from backpack.constants import IMAGE_EXTENSIONS, EXTENSION_MAP
from backpack.utils.file_utils import collect_files


class ImportDialog(QDialog):
    """Two-step import wizard."""

    def __init__(self, source_paths: list[str], backpack_root: Path, parent=None):
        super().__init__(parent)
        self.source_paths = source_paths
        self.backpack_root = backpack_root
        self.setWindowTitle("Import Assets")
        self.setMinimumSize(480, 420)

        self.chosen_type = ""    # material, texture, gobo, other
        self.chosen_source = ""  # quixel, poliigon, textures_com, other
        self.imported_count = 0
        self.imported_folders: list[Path] = []     # material folders created
        self.imported_dest_folder: Path | None = None  # flat folder used for loose assets

        self._files = self._collect_all_files()
        self._auto_type = self._detect_type()

        self._setup_ui()

    def _collect_all_files(self) -> list[Path]:
        files = []
        for p_str in self.source_paths:
            p = Path(p_str)
            files.extend(collect_files(p))
        return files

    def _detect_type(self) -> str:
        """Auto-detect if this looks like a material set, textures, gobo, etc."""
        if not self._files:
            return "other"

        exts = {f.suffix.lower() for f in self._files}
        texture_exts = exts & IMAGE_EXTENSIONS

        # Check if multiple PBR maps exist (= material)
        from backpack.constants import SUB_TYPE_PATTERNS
        found_subtypes = set()
        for f in self._files:
            for pat, sub in SUB_TYPE_PATTERNS:
                if pat.search(f.stem):
                    found_subtypes.add(sub)
                    break

        if len(found_subtypes) >= 2:
            return "material"

        if ".ies" in exts:
            return "gobo"

        if texture_exts:
            return "texture"

        model_exts = {".obj", ".fbx", ".abc", ".usd", ".usda", ".usdc"}
        if exts & model_exts:
            return "other"

        return "other"

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # Header
        header = QLabel("Import Assets")
        header.setObjectName("heading")
        layout.addWidget(header)

        file_info = QLabel(f"{len(self._files)} file(s) from {len(self.source_paths)} item(s)")
        file_info.setObjectName("subtext")
        layout.addWidget(file_info)

        # ── Step 1: Asset Type ──
        type_group_box = QGroupBox("Step 1: What type of asset is this?")
        type_layout = QVBoxLayout(type_group_box)
        self._type_group = QButtonGroup(self)

        types = [
            ("material", "Material (PBR texture set - albedo, normal, roughness, etc.)"),
            ("texture", "Texture (single texture files)"),
            ("gobo", "Gobo / IES (light cookies, IES profiles)"),
            ("other", "Other"),
        ]

        for value, label in types:
            rb = QRadioButton(label)
            rb.setProperty("type_value", value)
            self._type_group.addButton(rb)
            type_layout.addWidget(rb)
            if value == self._auto_type:
                rb.setChecked(True)

        self._type_group.buttonClicked.connect(self._on_type_changed)
        layout.addWidget(type_group_box)

        # ── Step 2: Source (only for materials) ──
        self._source_group_box = QGroupBox("Step 2: Source")
        source_layout = QVBoxLayout(self._source_group_box)
        self._source_group = QButtonGroup(self)

        sources = [
            ("quixel", "Quixel Megascans"),
            ("poliigon", "Poliigon"),
            ("textures_com", "textures.com / ambientCG"),
            ("other", "Other"),
        ]

        for value, label in sources:
            rb = QRadioButton(label)
            rb.setProperty("source_value", value)
            self._source_group.addButton(rb)
            source_layout.addWidget(rb)
            if value == "other":
                rb.setChecked(True)

        layout.addWidget(self._source_group_box)
        self._source_group_box.setVisible(self._auto_type == "material")

        # ── Progress ──
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("subtext")
        self.progress_label.hide()
        layout.addWidget(self.progress_label)

        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)

        # ── Buttons ──
        layout.addStretch()
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        self.btn_import = QPushButton("Import")
        self.btn_import.setObjectName("primaryBtn")
        self.btn_import.clicked.connect(self._do_import)
        btn_row.addWidget(self.btn_import)

        layout.addLayout(btn_row)

    def _on_type_changed(self, btn):
        val = btn.property("type_value")
        self._source_group_box.setVisible(val == "material")

    def _do_import(self):
        """Copy files to the BACKPACK folder structure."""
        # Get selections
        type_btn = self._type_group.checkedButton()
        self.chosen_type = type_btn.property("type_value") if type_btn else "other"

        source_btn = self._source_group.checkedButton()
        self.chosen_source = source_btn.property("source_value") if source_btn else "other"

        self.btn_import.setEnabled(False)
        self.progress.show()
        self.progress_label.show()

        try:
            if self.chosen_type == "material":
                self._import_as_material()
            elif self.chosen_type == "texture":
                self._import_to_folder("Textures")
            elif self.chosen_type == "gobo":
                self._import_to_folder("Gobo")
            else:
                self._import_to_folder("Other")

            self.accept()
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Import Error", str(e))
            self.btn_import.setEnabled(True)

    def _import_as_material(self):
        """Import as material - each source folder becomes a material folder."""
        source_label = self.chosen_source.upper() if self.chosen_source != "other" else "Other"
        dest_base = self.backpack_root / "Materials" / source_label

        total = len(self._files)
        self.progress.setMaximum(total)

        for i, src_path in enumerate(self.source_paths):
            p = Path(src_path)
            if p.is_dir():
                # Copy entire folder as a material
                mat_name = p.name
                dest_dir = dest_base / mat_name
                dest_dir.mkdir(parents=True, exist_ok=True)
                self.imported_folders.append(dest_dir)

                files_in_dir = collect_files(p, recursive=False)
                for j, f in enumerate(files_in_dir):
                    dest_file = dest_dir / f.name
                    if not dest_file.exists():
                        shutil.copy2(str(f), str(dest_file))
                    self.progress.setValue(self.progress.value() + 1)
                    self.progress_label.setText(f"Copying: {f.name}")
                    from PySide6.QtWidgets import QApplication
                    QApplication.processEvents()
            else:
                # Single file - create a material folder from stem
                mat_name = p.stem
                dest_dir = dest_base / mat_name
                dest_dir.mkdir(parents=True, exist_ok=True)
                self.imported_folders.append(dest_dir)
                dest_file = dest_dir / p.name
                if not dest_file.exists():
                    shutil.copy2(str(p), str(dest_file))
                self.progress.setValue(i + 1)
                self.progress_label.setText(f"Copying: {p.name}")
                from PySide6.QtWidgets import QApplication
                QApplication.processEvents()

        self.imported_count = total

    def _import_to_folder(self, folder_name: str):
        """Import loose files into a flat folder."""
        dest_base = self.backpack_root / folder_name
        dest_base.mkdir(parents=True, exist_ok=True)
        self.imported_dest_folder = dest_base

        total = len(self._files)
        self.progress.setMaximum(total)

        for i, f in enumerate(self._files):
            dest_file = dest_base / f.name
            # Handle name collision
            if dest_file.exists():
                stem, ext = dest_file.stem, dest_file.suffix
                counter = 1
                while dest_file.exists():
                    dest_file = dest_base / f"{stem}_{counter}{ext}"
                    counter += 1

            shutil.copy2(str(f), str(dest_file))
            self.progress.setValue(i + 1)
            self.progress_label.setText(f"Copying: {f.name}")
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()

        self.imported_count = total
