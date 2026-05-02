"""Import wizard — drag-and-drop or folder import for Moseng Backpack.

Step 1  Auto-detects asset type (Material / Texture / Gobo / Other).
        Uses map_detector.group_into_materials() to discover PBR sets even
        when files are dropped as a flat list (no folder structure required).
Step 2  For materials: confirm / tweak detected groupings before copying.
"""

import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QButtonGroup, QProgressBar, QGroupBox,
    QTreeWidget, QTreeWidgetItem, QSizePolicy, QFrame,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from backpack.constants import IMAGE_EXTENSIONS, EXTENSION_MAP
from backpack.core.map_detector import (
    group_into_materials, detect_sub_type,
    SUB_TYPE_LABEL, PREFERRED_ORDER, confidence,
)
from backpack.utils.file_utils import collect_files


# ─────────────────────────────────────────────────────────────────────────────

class ImportDialog(QDialog):
    """Two-step import wizard with auto-detect material grouping."""

    def __init__(self, source_paths: list[str], backpack_root: Path, parent=None):
        super().__init__(parent)
        self.source_paths = source_paths
        self.backpack_root = backpack_root
        self.setWindowTitle("Import Assets")
        self.setMinimumSize(520, 500)

        self.chosen_type   = ""
        self.chosen_source = ""
        self.imported_count = 0
        self.imported_folders: list[Path] = []
        self.imported_dest_folder: Path | None = None

        # ── Collect files + auto-detect ───────────────────────────────────
        self._files      = self._collect_all_files()
        self._mat_groups = group_into_materials(self._files)   # base→{sub→Path}
        self._auto_type  = self._detect_type()

        self._setup_ui()

    # ── File collection ───────────────────────────────────────────────────────

    def _collect_all_files(self) -> list[Path]:
        files = []
        for p_str in self.source_paths:
            p = Path(p_str)
            files.extend(collect_files(p))
        return files

    def _detect_type(self) -> str:
        if not self._files:
            return "other"

        exts = {f.suffix.lower() for f in self._files}

        if ".ies" in exts:
            return "gobo"

        # A material group needs ≥ 2 recognised PBR sub-types
        valid_mats = {
            name: maps for name, maps in self._mat_groups.items()
            if sum(1 for k in maps if k in SUB_TYPE_LABEL) >= 2
        }
        if valid_mats:
            return "material"

        if exts & IMAGE_EXTENSIONS:
            return "texture"

        return "other"

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(18, 18, 18, 18)

        # Header
        header = QLabel("Import Assets")
        header.setObjectName("heading")
        layout.addWidget(header)

        file_info = QLabel(
            f"{len(self._files)} file(s) found  ·  "
            f"{len(self._mat_groups)} material group(s) detected"
        )
        file_info.setObjectName("subtext")
        layout.addWidget(file_info)

        # ── Step 1: Asset Type ────────────────────────────────────────────
        type_box = QGroupBox("Step 1 — Asset Type")
        type_layout = QVBoxLayout(type_box)
        self._type_group = QButtonGroup(self)

        for value, label in [
            ("material", "Material  (PBR texture set — albedo, normal, roughness…)"),
            ("texture",  "Texture  (single image files)"),
            ("gobo",     "Gobo / IES  (light cookies, IES profiles)"),
            ("other",    "Other"),
        ]:
            rb = QRadioButton(label)
            rb.setProperty("type_value", value)
            self._type_group.addButton(rb)
            type_layout.addWidget(rb)
            if value == self._auto_type:
                rb.setChecked(True)

        self._type_group.buttonClicked.connect(self._on_type_changed)
        layout.addWidget(type_box)

        # ── Step 2: Detected groups (materials only) ──────────────────────
        self._groups_box = QGroupBox("Step 2 — Detected Material Groups")
        groups_layout = QVBoxLayout(self._groups_box)

        self._groups_tree = QTreeWidget()
        self._groups_tree.setHeaderLabels(["Material / Map", "File"])
        self._groups_tree.setColumnWidth(0, 220)
        self._groups_tree.setMinimumHeight(160)
        self._groups_tree.setRootIsDecorated(True)
        self._groups_tree.setAlternatingRowColors(True)
        self._populate_groups_tree()
        groups_layout.addWidget(self._groups_tree)

        det_note = QLabel(
            "Maps are auto-detected from filenames. "
            "Each top-level group becomes one material folder."
        )
        det_note.setObjectName("subtext")
        det_note.setWordWrap(True)
        groups_layout.addWidget(det_note)

        layout.addWidget(self._groups_box)
        self._groups_box.setVisible(self._auto_type == "material")

        # ── Step 3: Source ────────────────────────────────────────────────
        self._source_box = QGroupBox("Step 3 — Source")
        source_layout = QVBoxLayout(self._source_box)
        self._source_group = QButtonGroup(self)

        for value, label in [
            ("quixel",       "Quixel Megascans"),
            ("poliigon",     "Poliigon"),
            ("textures_com", "textures.com / AmbientCG"),
            ("other",        "Other / Custom"),
        ]:
            rb = QRadioButton(label)
            rb.setProperty("source_value", value)
            self._source_group.addButton(rb)
            source_layout.addWidget(rb)
            if value == "other":
                rb.setChecked(True)

        layout.addWidget(self._source_box)
        self._source_box.setVisible(self._auto_type == "material")

        # ── Progress ──────────────────────────────────────────────────────
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("subtext")
        self.progress_label.hide()
        layout.addWidget(self.progress_label)

        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)

        # ── Buttons ───────────────────────────────────────────────────────
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

    def _populate_groups_tree(self):
        """Fill the material-groups tree with detected groupings."""
        tree = self._groups_tree
        tree.clear()

        for base_name, maps in sorted(self._mat_groups.items()):
            known_count = sum(1 for k in maps if k in SUB_TYPE_LABEL)
            conf = confidence(maps)

            # Root item — material name
            root = QTreeWidgetItem([
                base_name,
                f"{known_count} map(s)  [{int(conf * 100)}% confidence]",
            ])
            root.setExpanded(known_count >= 2)

            # Color-code by confidence
            if conf >= 0.5:
                root.setForeground(0, QColor("#6fcf97"))   # green
            elif conf >= 0.2:
                root.setForeground(0, QColor("#f2c94c"))   # yellow
            else:
                root.setForeground(0, QColor("#eb5757"))   # red / uncertain

            # Child rows — one per map
            for sub in PREFERRED_ORDER:
                if sub not in maps:
                    continue
                label = SUB_TYPE_LABEL.get(sub, sub)
                child = QTreeWidgetItem([f"  {label}", maps[sub].name])
                child.setForeground(0, QColor("#bdbdbd"))
                root.addChild(child)

            # Unrecognised maps
            for key, path in maps.items():
                if key in SUB_TYPE_LABEL or key in PREFERRED_ORDER:
                    continue
                child = QTreeWidgetItem([f"  {key}  (unrecognised)", path.name])
                child.setForeground(0, QColor("#828282"))
                root.addChild(child)

            tree.addTopLevelItem(root)

    # ── Signals ───────────────────────────────────────────────────────────────

    def _on_type_changed(self, btn):
        val = btn.property("type_value")
        is_mat = (val == "material")
        self._groups_box.setVisible(is_mat)
        self._source_box.setVisible(is_mat)

    # ── Import ────────────────────────────────────────────────────────────────

    def _do_import(self):
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
            elif self.chosen_type == "gobo":
                self._import_to_folder("Gobo")
            elif self.chosen_type == "texture":
                self._import_to_folder("Textures")
            else:
                self._import_to_folder("Other")
            self.accept()
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Import Error", str(e))
            self.btn_import.setEnabled(True)

    def _import_as_material(self):
        """Copy files into material folders, grouped by detected base name."""
        source_name = self.chosen_source or "Other"
        dest_base   = self.backpack_root / "Materials" / source_name.capitalize()

        # Build a definitive group map: prefer detected groups when dropping
        # loose files; fall back to per-source-folder grouping
        if self._mat_groups:
            all_groups = {
                name: {sub: path for sub, path in maps.items()}
                for name, maps in self._mat_groups.items()
            }
        else:
            # Fallback: each source path becomes one material
            all_groups = {}
            for p_str in self.source_paths:
                p = Path(p_str)
                if p.is_dir():
                    files = list(collect_files(p, recursive=False))
                    all_groups[p.name] = {detect_sub_type(f.stem) or f.stem: f for f in files}
                else:
                    all_groups[p.stem] = {detect_sub_type(p.stem) or p.stem: p}

        total = sum(len(maps) for maps in all_groups.values())
        self.progress.setMaximum(max(total, 1))
        done = 0

        for mat_name, maps in all_groups.items():
            dest_dir = dest_base / mat_name
            dest_dir.mkdir(parents=True, exist_ok=True)
            self.imported_folders.append(dest_dir)

            for _sub, src_path in maps.items():
                dest_file = dest_dir / src_path.name
                if not dest_file.exists():
                    shutil.copy2(str(src_path), str(dest_file))
                done += 1
                self.progress.setValue(done)
                self.progress_label.setText(f"Copying: {src_path.name}")
                from PySide6.QtWidgets import QApplication
                QApplication.processEvents()

        self.imported_count = done

    def _import_to_folder(self, folder_name: str):
        """Copy loose files into a flat category folder."""
        dest_base = self.backpack_root / folder_name
        dest_base.mkdir(parents=True, exist_ok=True)
        self.imported_dest_folder = dest_base

        total = len(self._files)
        self.progress.setMaximum(max(total, 1))

        for i, f in enumerate(self._files):
            dest_file = dest_base / f.name
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
