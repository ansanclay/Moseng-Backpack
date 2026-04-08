"""Asset browser - grid view reading from scanned filesystem data.

Supports Ctrl+Wheel zoom.
"""

from pathlib import Path

from PySide6.QtWidgets import QListView, QMenu, QAbstractItemView, QApplication
from PySide6.QtCore import Qt, Signal, QSize, QMimeData, QUrl, QPoint
from PySide6.QtGui import QStandardItemModel, QStandardItem, QAction, QKeyEvent, QDrag

from backpack.core.scanner import ScannedMaterial, ScannedAsset
from backpack.ui.delegates.thumbnail_delegate import ThumbnailDelegate


class AssetBrowser(QListView):
    asset_selected = Signal(object)          # ScannedAsset
    asset_double_clicked = Signal(object)    # ScannedAsset
    material_selected = Signal(object)       # ScannedMaterial
    material_double_clicked = Signal(object) # ScannedMaterial
    selection_changed = Signal(int)          # number of selected items
    delete_requested = Signal(list)          # list of (kind, item)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("assetGrid")
        self._model = QStandardItemModel(self)
        self.setModel(self._model)

        self._card_size = 200
        self.delegate = ThumbnailDelegate(self)
        self.setItemDelegate(self.delegate)

        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setSpacing(4)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setDragEnabled(False)  # We handle drag manually
        self.setMouseTracking(True)
        self._drag_start_pos: QPoint | None = None

        self._update_grid_size()

        self.clicked.connect(self._on_click)
        self.doubleClicked.connect(self._on_dblclick)
        self.customContextMenuRequested.connect(self._context_menu)

    def set_card_size(self, size: int):
        self._card_size = max(120, min(400, size))
        self.delegate.card_width = self._card_size
        self.delegate.card_height = int(self._card_size * 1.15)
        self._update_grid_size()
        self.viewport().update()

    def _update_grid_size(self):
        w = self._card_size + 4
        h = int(self._card_size * 1.15) + 4
        self.setGridSize(QSize(w, h))

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            step = 20 if delta > 0 else -20
            self.set_card_size(self._card_size + step)
            event.accept()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.LeftButton) and self._drag_start_pos:
            dist = (event.pos() - self._drag_start_pos).manhattanLength()
            if dist >= QApplication.startDragDistance():
                self._start_drag()
                self._drag_start_pos = None
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _start_drag(self):
        """Initiate a file drag with selected items for external drop targets."""
        items = self.get_selected_items()
        if not items:
            return

        urls = []
        text_paths = []
        for kind, obj in items:
            if kind == "material":
                # For materials, add the folder path
                if obj.path.exists():
                    urls.append(QUrl.fromLocalFile(str(obj.path)))
                    text_paths.append(str(obj.path))
            elif kind == "asset":
                if obj.path.exists():
                    urls.append(QUrl.fromLocalFile(str(obj.path)))
                    text_paths.append(str(obj.path))

        if not urls:
            return

        mime = QMimeData()
        mime.setUrls(urls)
        mime.setText("\n".join(text_paths))

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Delete:
            self._delete_selected()
        else:
            super().keyPressEvent(event)

    def display_items(self, materials: list[ScannedMaterial], assets: list[ScannedAsset]):
        """Populate the grid with scanned materials and loose assets."""
        self._model.clear()

        for mat in materials:
            item = QStandardItem()
            item.setData(mat.name, Qt.DisplayRole)
            item.setData(("material", mat), Qt.UserRole)

            # Use preview cache if available, fall back to full-res preview
            if mat.preview_cache and mat.preview_cache.exists():
                thumb = str(mat.preview_cache)
            elif mat.preview_path and mat.preview_path.exists():
                thumb = str(mat.preview_path)
            else:
                thumb = ""
            item.setData(thumb, Qt.UserRole + 1)
            item.setData("texture", Qt.UserRole + 2)
            item.setData(mat.meta.surface_type or mat.source, Qt.UserRole + 3)
            item.setData(len(mat.maps), Qt.UserRole + 4)
            item.setEditable(False)
            self._model.appendRow(item)

        for asset in assets:
            item = QStandardItem()
            item.setData(asset.filename, Qt.DisplayRole)
            item.setData(("asset", asset), Qt.UserRole)

            # Use preview cache if available, fall back to full-res path
            if asset.preview_cache and asset.preview_cache.exists():
                thumb = str(asset.preview_cache)
            elif asset.path.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".tga"):
                thumb = str(asset.path)
            else:
                thumb = ""
            item.setData(thumb, Qt.UserRole + 1)
            item.setData(asset.asset_type, Qt.UserRole + 2)
            item.setData(asset.sub_type, Qt.UserRole + 3)
            item.setData(0, Qt.UserRole + 4)
            item.setEditable(False)
            self._model.appendRow(item)

    def _on_click(self, index):
        # Emit selection count
        sel = self.selectionModel().selectedIndexes()
        self.selection_changed.emit(len(sel))

        data = index.data(Qt.UserRole)
        if not data:
            return
        kind, obj = data
        if kind == "material":
            self.material_selected.emit(obj)
        else:
            self.asset_selected.emit(obj)

    def _on_dblclick(self, index):
        data = index.data(Qt.UserRole)
        if not data:
            return
        kind, obj = data
        if kind == "material":
            self.material_double_clicked.emit(obj)
        else:
            self.asset_double_clicked.emit(obj)

    def _context_menu(self, pos):
        indices = self.selectionModel().selectedIndexes()
        if not indices:
            return

        menu = QMenu(self)

        if len(indices) == 1:
            data = indices[0].data(Qt.UserRole)
            if data:
                kind, obj = data
                if kind == "asset":
                    act_open = QAction("Open File Location", self)
                    act_open.triggered.connect(lambda: self._open_location(obj))
                    menu.addAction(act_open)

                    act_copy = QAction("Copy Path", self)
                    act_copy.triggered.connect(lambda: QApplication.clipboard().setText(str(obj.path)))
                    menu.addAction(act_copy)
                elif kind == "material":
                    act_open = QAction("Open Material Folder", self)
                    act_open.triggered.connect(lambda: self._open_location(obj))
                    menu.addAction(act_open)

        menu.addSeparator()
        act_del = QAction(f"Delete ({len(indices)} item(s))", self)
        act_del.triggered.connect(self._delete_selected)
        menu.addAction(act_del)

        menu.exec(self.mapToGlobal(pos))

    def _delete_selected(self):
        items = []
        for idx in self.selectionModel().selectedIndexes():
            data = idx.data(Qt.UserRole)
            if data:
                items.append(data)
        if items:
            self.delete_requested.emit(items)

    def _open_location(self, obj):
        import subprocess
        path = obj.path if hasattr(obj, "path") else None
        if path and path.exists():
            if path.is_dir():
                subprocess.Popen(["explorer", str(path)], creationflags=0x08000000)
            else:
                subprocess.Popen(["explorer", "/select,", str(path)], creationflags=0x08000000)

    def get_selected_items(self) -> list:
        """Return list of (kind, obj) for selected items."""
        items = []
        for idx in self.selectionModel().selectedIndexes():
            data = idx.data(Qt.UserRole)
            if data:
                items.append(data)
        return items
