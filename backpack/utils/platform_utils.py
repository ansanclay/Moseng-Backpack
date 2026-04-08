"""Platform-specific utilities (drive enumeration, paths)."""

import ctypes
import os
import string
from pathlib import Path

from backpack.constants import DATABASE_FOLDER
from backpack.models.drive import DriveInfo


def get_available_drives() -> list[DriveInfo]:
    """Enumerate available drives on Windows."""
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()

    for i, letter in enumerate(string.ascii_uppercase):
        if bitmask & (1 << i):
            root = f"{letter}:\\"
            try:
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
                # 3 = DRIVE_FIXED, 2 = DRIVE_REMOVABLE, 4 = DRIVE_REMOTE
                if drive_type not in (3, 2, 4):
                    continue

                total, free = ctypes.c_ulonglong(), ctypes.c_ulonglong()
                ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                    root, None, ctypes.byref(total), ctypes.byref(free)
                )

                # Get volume label
                vol_buf = ctypes.create_unicode_buffer(256)
                ctypes.windll.kernel32.GetVolumeInformationW(
                    root, vol_buf, 256, None, None, None, None, 0
                )

                has_db = (Path(root) / DATABASE_FOLDER).exists()

                drives.append(DriveInfo(
                    letter=letter,
                    label=vol_buf.value or "",
                    total_gb=total.value / (1024 ** 3),
                    free_gb=free.value / (1024 ** 3),
                    has_database=has_db,
                ))
            except (OSError, WindowsError):
                continue

    return drives
