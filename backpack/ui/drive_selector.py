"""Drive selector dialog shown at startup or from the toolbar."""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QMessageBox,
)
from PySide6.QtCore import Qt

from backpack.utils.platform_utils import get_available_drives
from backpack.models.drive import DriveInfo


class DriveSelector(QDialog):
    """Dialog for selecting the database drive."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Database Drive")
        self.setMinimumSize(420, 340)
        self.selected_drive: DriveInfo | None = None
        self._setup_ui()
        self._load_drives()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel("Select a drive for your asset database")
        title.setObjectName("sectionTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        desc = QLabel(
            "A 'DATABASE' folder will be created on the selected drive.\n"
            "All imported assets will be organized inside it."
        )
        desc.setObjectName("metaLabel")
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.drive_list = QListWidget()
        self.drive_list.setMinimumHeight(160)
        self.drive_list.itemDoubleClicked.connect(self._on_select)
        layout.addWidget(self.drive_list)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self._load_drives)
        btn_layout.addWidget(self.btn_refresh)

        self.btn_select = QPushButton("Select")
        self.btn_select.setObjectName("primaryButton")
        self.btn_select.clicked.connect(self._on_select)
        btn_layout.addWidget(self.btn_select)

        layout.addLayout(btn_layout)

    def _load_drives(self):
        self.drive_list.clear()
        drives = get_available_drives()
        for drive in drives:
            item = QListWidgetItem(drive.display_name)
            item.setData(Qt.UserRole, drive)
            self.drive_list.addItem(item)

        if drives:
            # Auto-select first drive with existing DB, or first drive
            for i, drive in enumerate(drives):
                if drive.has_database:
                    self.drive_list.setCurrentRow(i)
                    return
            self.drive_list.setCurrentRow(0)

    def _on_select(self):
        current = self.drive_list.currentItem()
        if not current:
            QMessageBox.warning(self, "No Drive", "Please select a drive.")
            return
        self.selected_drive = current.data(Qt.UserRole)
        self.accept()
