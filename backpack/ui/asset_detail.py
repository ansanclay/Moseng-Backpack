"""Asset detail panel - right side, shows selected asset or material info.

Supports: X to close, multi-select count, tag editing via JSON.
"""

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QScrollArea, QFrame, QInputDialog, QComboBox, QMessageBox,
    QLayout,
)
from PySide6.QtCore import Qt, Signal, QRect, QSize
from PySide6.QtGui import QPixmap

from backpack.core.scanner import ScannedAsset, ScannedMaterial
from backpack.core.metadata import (
    read_asset_meta, write_asset_meta, read_material_meta, write_material_meta,
)
from backpack.core.downscale import get_available_resolutions, downscale_material
from backpack.constants import random_blue, random_tag_color


class FlowLayout(QLayout):
    """Flow layout that wraps widgets horizontally."""
    def __init__(self, parent=None, margin=0, spacing=4):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect)

    def sizeHint(self):
        return QSize(200, 30)

    def minimumSize(self):
        return QSize(0, 0)

    def _do_layout(self, rect, test_only=False):
        from PySide6.QtCore import QRect as QR
        x = rect.x()
        y = rect.y()
        line_h = 0

        for item in self._items:
            w = item.widget()
            if not w:
                continue
            sz = w.sizeHint()
            next_x = x + sz.width() + self._spacing
            if next_x - self._spacing > rect.right() and line_h > 0:
                x = rect.x()
                y += line_h + self._spacing
                next_x = x + sz.width() + self._spacing
                line_h = 0
            if not test_only:
                item.setGeometry(QR(x, y, sz.width(), sz.height()))
            x = next_x
            line_h = max(line_h, sz.height())

        return y + line_h - rect.y()


class TagLabel(QLabel):
    """Small colored tag badge for the detail panel."""
    def __init__(self, name: str, color: str, parent=None):
        super().__init__(name, parent)
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {color}; color: #ffffff;
                border-radius: 10px; padding: 3px 10px;
                font-size: 11px; font-weight: 600;
            }}
        """)
        self.setFixedHeight(22)


class StarRating(QWidget):
    rating_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rating = 0
        self._btns = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        for i in range(5):
            b = QPushButton()
            b.setFixedSize(22, 22)
            b.setFlat(True)
            b.clicked.connect(lambda _, idx=i: self._set(idx + 1))
            self._btns.append(b)
            lay.addWidget(b)
        lay.addStretch()
        self._update()

    def _set(self, r):
        self._rating = 0 if self._rating == r else r
        self._update()
        self.rating_changed.emit(self._rating)

    def set_rating(self, r):
        self._rating = r
        self._update()

    def _update(self):
        for i, b in enumerate(self._btns):
            on = i < self._rating
            b.setText("\u2605" if on else "\u2606")
            c = "#f0c050" if on else "#3a3d45"
            b.setStyleSheet(f"QPushButton {{ color: {c}; font-size: 15px; background: transparent; border: none; }}")


class MapBadge(QWidget):
    def __init__(self, sub_type: str, filename: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(6)
        colors = {
            "albedo": "#4a9eff", "diffuse": "#4a9eff", "normal": "#8080ff",
            "roughness": "#50c878", "metallic": "#d4d6db", "displacement": "#c0c0c0",
            "ao": "#ffffff", "emissive": "#f0c050", "opacity": "#e06070",
            "bump": "#a0a0ff", "preview": "#ff9040",
        }
        dot = QLabel("\u25cf")
        dot.setStyleSheet(f"color: {colors.get(sub_type, '#6b6e76')}; font-size: 8px;")
        dot.setFixedWidth(12)
        lay.addWidget(dot)
        tl = QLabel(sub_type.title() if sub_type else "Unknown")
        tl.setStyleSheet("color: #8b8e96; font-size: 11px; font-weight: 600;")
        tl.setFixedWidth(80)
        lay.addWidget(tl)
        fl = QLabel(filename)
        fl.setStyleSheet("color: #6b6e76; font-size: 11px;")
        lay.addWidget(fl, stretch=1)


class AssetDetailPanel(QWidget):
    refresh_requested = Signal()
    tag_head_changed = Signal(str, object)  # tag_name, asset/material obj

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("detailPanel")
        self.setFixedWidth(300)
        self._current_asset: ScannedAsset | None = None
        self._current_material: ScannedMaterial | None = None
        self._tag_registry: dict = {}
        self._backpack_root: Path | None = None
        self._setup_ui()
        self.hide()

    def set_tag_registry(self, registry: dict, backpack_root: Path):
        self._tag_registry = registry
        self._backpack_root = backpack_root

    def _setup_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)

        # Close button
        close_row = QHBoxLayout()
        close_row.addStretch()
        btn_close = QPushButton("\u2715")
        btn_close.setFixedSize(24, 24)
        btn_close.setStyleSheet(
            "QPushButton { color: #3a3d45; font-size: 14px; background: transparent; border: none; }"
            "QPushButton:hover { color: #d4d6db; }"
        )
        btn_close.clicked.connect(self.hide)
        close_row.addWidget(btn_close)
        layout.addLayout(close_row)

        # Preview
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(180)
        self.preview.setStyleSheet("background-color: #15171d; border-radius: 10px; padding: 4px;")
        layout.addWidget(self.preview)

        # Name
        self.name_label = QLabel()
        self.name_label.setObjectName("heading")
        self.name_label.setWordWrap(True)
        layout.addWidget(self.name_label)

        # Info labels
        self.type_label = QLabel()
        self.type_label.setObjectName("subtext")
        layout.addWidget(self.type_label)

        self.size_label = QLabel()
        self.size_label.setObjectName("subtext")
        layout.addWidget(self.size_label)

        self.dims_label = QLabel()
        self.dims_label.setObjectName("subtext")
        layout.addWidget(self.dims_label)

        # Multi-select label
        self.multi_label = QLabel()
        self.multi_label.setObjectName("heading")
        self.multi_label.setAlignment(Qt.AlignCenter)
        self.multi_label.hide()
        layout.addWidget(self.multi_label)

        # Rating
        rat_row = QHBoxLayout()
        rl = QLabel("Rating")
        rl.setStyleSheet("color: #6b6e76; font-size: 11px;")
        rat_row.addWidget(rl)
        self.stars = StarRating()
        self.stars.rating_changed.connect(self._on_rating)
        rat_row.addWidget(self.stars)
        rat_row.addStretch()
        layout.addLayout(rat_row)

        # Fav button
        self.fav_btn = QPushButton("\u2606 Favorite")
        self.fav_btn.setCheckable(True)
        self.fav_btn.setFixedHeight(28)
        self.fav_btn.clicked.connect(self._toggle_fav)
        self.fav_btn.setStyleSheet("""
            QPushButton { background: #23262e; color: #6b6e76; border: 1px solid #2a2d35;
                          border-radius: 6px; font-size: 11px; }
            QPushButton:checked { background: #f59e0b30; color: #f59e0b; border-color: #f59e0b; }
            QPushButton:hover { border-color: #f59e0b; }
        """)
        layout.addWidget(self.fav_btn)

        # Divider
        d1 = QFrame()
        d1.setFrameShape(QFrame.HLine)
        d1.setStyleSheet("color: #2a2d35;")
        layout.addWidget(d1)

        # Maps
        self.maps_title = QLabel("TEXTURE MAPS")
        self.maps_title.setStyleSheet("color: #6b6e76; font-size: 11px; font-weight: 700; letter-spacing: 1px;")
        self.maps_title.hide()
        layout.addWidget(self.maps_title)

        self.maps_container = QWidget()
        self.maps_layout = QVBoxLayout(self.maps_container)
        self.maps_layout.setContentsMargins(0, 0, 0, 0)
        self.maps_layout.setSpacing(0)
        self.maps_container.hide()
        layout.addWidget(self.maps_container)

        # Resolution section (materials only)
        self.res_container = QWidget()
        res_lay = QVBoxLayout(self.res_container)
        res_lay.setContentsMargins(0, 0, 0, 0)
        res_lay.setSpacing(6)

        res_title = QLabel("RESOLUTION")
        res_title.setStyleSheet("color: #6b6e76; font-size: 11px; font-weight: 700; letter-spacing: 1px;")
        res_lay.addWidget(res_title)

        self.res_combo = QComboBox()
        self.res_combo.setFixedHeight(28)
        self.res_combo.setStyleSheet("""
            QComboBox { background: #23262e; color: #d4d6db; border: 1px solid #2a2d35;
                        border-radius: 6px; padding: 2px 8px; font-size: 12px; }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox::down-arrow { image: none; border-left: 4px solid transparent;
                                    border-right: 4px solid transparent; border-top: 5px solid #6b6e76; }
            QComboBox QAbstractItemView { background: #23262e; color: #d4d6db;
                                          selection-background-color: #4a9eff; border: 1px solid #2a2d35; }
        """)
        res_lay.addWidget(self.res_combo)

        ds_row = QHBoxLayout()
        self.ds_2k_btn = QPushButton("Generate 2K")
        self.ds_2k_btn.setFixedHeight(28)
        self.ds_2k_btn.setStyleSheet("""
            QPushButton { background: #23262e; color: #4a9eff; border: 1px solid #2a2d35;
                          border-radius: 6px; font-size: 11px; font-weight: 600; }
            QPushButton:hover { border-color: #4a9eff; background: #262a34; }
            QPushButton:disabled { color: #3a3d45; border-color: #1e2028; }
        """)
        self.ds_2k_btn.clicked.connect(lambda: self._do_downscale("2K"))
        ds_row.addWidget(self.ds_2k_btn)

        self.ds_1k_btn = QPushButton("Generate 1K")
        self.ds_1k_btn.setFixedHeight(28)
        self.ds_1k_btn.setStyleSheet("""
            QPushButton { background: #23262e; color: #4a9eff; border: 1px solid #2a2d35;
                          border-radius: 6px; font-size: 11px; font-weight: 600; }
            QPushButton:hover { border-color: #4a9eff; background: #262a34; }
            QPushButton:disabled { color: #3a3d45; border-color: #1e2028; }
        """)
        self.ds_1k_btn.clicked.connect(lambda: self._do_downscale("1K"))
        ds_row.addWidget(self.ds_1k_btn)
        res_lay.addLayout(ds_row)

        self.res_container.hide()
        layout.addWidget(self.res_container)

        # Tags
        d2 = QFrame()
        d2.setFrameShape(QFrame.HLine)
        d2.setStyleSheet("color: #2a2d35;")
        layout.addWidget(d2)

        tag_header = QHBoxLayout()
        tl2 = QLabel("TAGS")
        tl2.setStyleSheet("color: #6b6e76; font-size: 11px; font-weight: 700; letter-spacing: 1px;")
        tag_header.addWidget(tl2)
        tag_header.addStretch()
        btn_add_tag = QPushButton("+")
        btn_add_tag.setFixedSize(22, 22)
        btn_add_tag.setStyleSheet(
            "QPushButton { background: #23262e; color: #4a9eff; border: 1px solid #2a2d35; border-radius: 11px; font-size: 14px; }"
            "QPushButton:hover { border-color: #4a9eff; }"
        )
        btn_add_tag.clicked.connect(self._add_tag)
        tag_header.addWidget(btn_add_tag)
        layout.addLayout(tag_header)

        self.tags_container = QWidget()
        self.tags_flow = FlowLayout(self.tags_container, margin=0, spacing=4)
        layout.addWidget(self.tags_container)

        self.tags_label = QLabel("No tags")
        self.tags_label.setStyleSheet("color: #8b8e96; font-size: 12px; padding: 4px 0;")
        layout.addWidget(self.tags_label)

        # Notes
        nl = QLabel("NOTES")
        nl.setStyleSheet("color: #6b6e76; font-size: 11px; font-weight: 700; letter-spacing: 1px;")
        layout.addWidget(nl)
        self.notes = QTextEdit()
        self.notes.setMaximumHeight(70)
        self.notes.setPlaceholderText("Add notes...")
        layout.addWidget(self.notes)

        btn_save = QPushButton("Save Notes")
        btn_save.setObjectName("primaryBtn")
        btn_save.setFixedHeight(28)
        btn_save.clicked.connect(self._save_notes)
        layout.addWidget(btn_save)

        # Path
        self.path_label = QLabel()
        self.path_label.setStyleSheet("color: #3a3d45; font-size: 10px; padding-top: 8px;")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        layout.addStretch()
        scroll.setWidget(content)

    def show_asset(self, asset: ScannedAsset):
        self._current_asset = asset
        self._current_material = None
        self.multi_label.hide()

        # Reload meta from JSON
        from backpack.core.metadata import read_asset_meta
        asset.meta = read_asset_meta(asset.path)

        self._set_preview_from_path(asset.path)
        self.name_label.setText(asset.filename)
        self.name_label.show()
        self.type_label.setText(f"{asset.asset_type.title()}  \u00b7  {asset.sub_type}" if asset.sub_type else asset.asset_type.title())
        self.type_label.show()

        try:
            sz = asset.path.stat().st_size / (1024 * 1024)
            self.size_label.setText(f"{sz:.1f} MB  \u00b7  {asset.path.suffix}")
        except:
            self.size_label.setText(asset.path.suffix)
        self.size_label.show()
        self.dims_label.hide()

        self.stars.set_rating(asset.meta.rating)
        self.fav_btn.setChecked(asset.meta.favorite)
        self.fav_btn.setText("\u2605 Favorite" if asset.meta.favorite else "\u2606 Favorite")
        self._update_tags_display(asset.meta.tags)
        self.notes.setPlainText(asset.meta.notes)
        self.path_label.setText(asset.rel_path)

        self.maps_title.hide()
        self.maps_container.hide()
        self.res_container.hide()
        self.show()

    def show_material(self, mat: ScannedMaterial):
        self._current_material = mat
        self._current_asset = None
        self.multi_label.hide()

        mat.meta = read_material_meta(mat.path)

        if mat.preview_path and mat.preview_path.exists():
            self._set_preview_from_path(mat.preview_path)
        else:
            self.preview.setText("MATERIAL")

        self.name_label.setText(mat.name)
        self.name_label.show()
        parts = [mat.source.title()]
        if mat.meta.surface_type:
            parts.append(mat.meta.surface_type)
        parts.append(f"{len(mat.maps)} maps")
        self.type_label.setText("  \u00b7  ".join(parts))
        self.type_label.show()

        total = sum(m.path.stat().st_size for m in mat.maps if m.path.exists())
        self.size_label.setText(f"{total / (1024*1024):.1f} MB total")
        self.size_label.show()
        self.dims_label.hide()

        self.stars.set_rating(mat.meta.rating)
        self.fav_btn.setChecked(mat.meta.favorite)
        self.fav_btn.setText("\u2605 Favorite" if mat.meta.favorite else "\u2606 Favorite")
        self._update_tags_display(mat.meta.tags)
        self.notes.setPlainText(mat.meta.notes)
        self.path_label.setText(mat.rel_path)

        # Maps
        self._clear_maps()
        self.maps_title.show()
        self.maps_container.show()
        for a in mat.maps:
            self.maps_layout.addWidget(MapBadge(a.sub_type, a.filename))

        # Resolution
        self._update_resolution(mat)

        self.show()

    def show_multi_selection(self, count: int):
        """Show multi-selection summary."""
        self._current_asset = None
        self._current_material = None

        self.preview.setText("")
        self.preview.setStyleSheet("background-color: #15171d; border-radius: 10px; padding: 4px;")
        self.name_label.hide()
        self.type_label.hide()
        self.size_label.hide()
        self.dims_label.hide()
        self.maps_title.hide()
        self.maps_container.hide()

        self.res_container.hide()
        self.multi_label.setText(f"{count} items selected")
        self.multi_label.show()
        self.show()

    def _set_preview_from_path(self, path: Path):
        if path.exists() and path.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".tga"):
            pix = QPixmap(str(path))
            if not pix.isNull():
                scaled = pix.scaled(268, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview.setPixmap(scaled)
                return
        self.preview.setText(path.suffix.upper())
        self.preview.setStyleSheet(
            "background-color: #15171d; border-radius: 10px; color: #3a3d45; font-size: 20px; font-weight: bold; padding: 4px;"
        )

    def _clear_maps(self):
        while self.maps_layout.count():
            c = self.maps_layout.takeAt(0)
            if c.widget():
                c.widget().deleteLater()

    def _on_rating(self, r):
        if self._current_asset:
            self._current_asset.meta.rating = r
            write_asset_meta(self._current_asset.path, self._current_asset.meta)
        elif self._current_material:
            self._current_material.meta.rating = r
            write_material_meta(self._current_material.path, self._current_material.meta)

    def _toggle_fav(self):
        fav = self.fav_btn.isChecked()
        self.fav_btn.setText("\u2605 Favorite" if fav else "\u2606 Favorite")
        if self._current_asset:
            self._current_asset.meta.favorite = fav
            write_asset_meta(self._current_asset.path, self._current_asset.meta)
        elif self._current_material:
            self._current_material.meta.favorite = fav
            write_material_meta(self._current_material.path, self._current_material.meta)

    def _add_tag(self):
        name, ok = QInputDialog.getText(self, "Add Tag", "Tag name:")
        if not ok or not name.strip():
            return
        tag = name.strip()
        if self._current_asset:
            if tag not in self._current_asset.meta.tags:
                self._current_asset.meta.tags.append(tag)
            write_asset_meta(self._current_asset.path, self._current_asset.meta)
            self._update_tags_display(self._current_asset.meta.tags)
        elif self._current_material:
            if tag not in self._current_material.meta.tags:
                self._current_material.meta.tags.append(tag)
            write_material_meta(self._current_material.path, self._current_material.meta)
            self._update_tags_display(self._current_material.meta.tags)
        self.refresh_requested.emit()

    def _update_tags_display(self, tags: list[str]):
        """Update the tags area with colored tag chips."""
        # Clear existing chips
        while self.tags_flow.count():
            item = self.tags_flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if tags:
            self.tags_label.hide()
            self.tags_container.show()
            for t in tags:
                info = self._tag_registry.get(t)
                color = info.color if info and info.color else random_tag_color(
                    self._backpack_root and "#4a9eff")
                chip = TagLabel(t, color)
                # Right-click to set as tag head
                chip.setContextMenuPolicy(Qt.CustomContextMenu)
                chip.customContextMenuRequested.connect(
                    lambda pos, tag=t: self._tag_chip_context(tag))
                self.tags_flow.addWidget(chip)
        else:
            self.tags_container.hide()
            self.tags_label.setText("No tags")
            self.tags_label.show()

    def _tag_chip_context(self, tag_name: str):
        """Show context menu for a tag chip."""
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QAction
        menu = QMenu(self)
        act = QAction("Set as Tag Head", self)
        act.triggered.connect(lambda: self._set_as_tag_head(tag_name))
        menu.addAction(act)
        menu.exec(self.cursor().pos())

    def _set_as_tag_head(self, tag_name: str):
        """Promote the current asset/material to be the tag head."""
        head_path = None
        if self._current_material:
            head_path = self._current_material.preview_path
        elif self._current_asset:
            head_path = self._current_asset.path

        if head_path and self._backpack_root:
            self.tag_head_changed.emit(tag_name, head_path)

    def _save_notes(self):
        text = self.notes.toPlainText()
        if self._current_asset:
            self._current_asset.meta.notes = text
            write_asset_meta(self._current_asset.path, self._current_asset.meta)
        elif self._current_material:
            self._current_material.meta.notes = text
            write_material_meta(self._current_material.path, self._current_material.meta)

    def _update_resolution(self, mat: ScannedMaterial):
        """Populate the resolution combo and enable/disable downscale buttons."""
        resolutions = get_available_resolutions(mat.path)
        self.res_combo.clear()

        if resolutions:
            for r in resolutions:
                self.res_combo.addItem(r)
            self.res_combo.setCurrentIndex(len(resolutions) - 1)  # Select highest
            self.res_container.show()
        else:
            self.res_container.hide()
            return

        has_4k = "4K" in resolutions
        has_2k = "2K" in resolutions
        has_1k = "1K" in resolutions

        # Enable Generate 2K only if 4K exists and 2K doesn't
        self.ds_2k_btn.setEnabled(has_4k and not has_2k)
        self.ds_2k_btn.setText("2K exists" if has_2k else "Generate 2K")

        # Enable Generate 1K only if 4K or 2K exists and 1K doesn't
        self.ds_1k_btn.setEnabled((has_4k or has_2k) and not has_1k)
        self.ds_1k_btn.setText("1K exists" if has_1k else "Generate 1K")

    def _do_downscale(self, target_res: str):
        """Downscale the current material to target resolution."""
        mat = self._current_material
        if not mat:
            return

        created, errors = downscale_material(mat.path, target_res)

        if errors:
            QMessageBox.warning(self, "Downscale",
                                f"Created {created} files.\nErrors:\n" + "\n".join(errors))
        elif created > 0:
            QMessageBox.information(self, "Downscale",
                                    f"Created {created} {target_res} texture(s).")
        else:
            QMessageBox.information(self, "Downscale",
                                    f"{target_res} textures already exist or source is too small.")

        # Refresh to pick up new files
        self.refresh_requested.emit()
