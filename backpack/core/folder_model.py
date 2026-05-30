"""Folder tree model for the BACKPACK filesystem.

Default structure:
  BACKPACK/
    Materials/
      PBR_Materials/
    Images/
      Textures/     (default_tags: Surface_Imperfections, Displacements, Decals)
      Photos/
      Gobos/
      HDRI/
    Models/
      3D_Assets/
      Foliages/
    Quixel/         (when quixel_enabled, maps to Quixel/Downloaded/)
      Materials     → Downloaded/surface/
      Models        → Downloaded/3d/
      Foliages      → Downloaded/3dplant/
      Decals        → Downloaded/atlas/
      Brushes       → Downloaded/brush/
      Smart Materials → Downloaded/smartmaterial/
      (UAssets hidden)
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def disk_to_display(name: str) -> str:
    """Convert disk folder name to display name: underscores → spaces."""
    return name.replace("_", " ")


@dataclass
class FolderNode:
    disk_name: str          # actual name on disk (e.g. "PBR_Materials")
    display_name: str       # display name (e.g. "PBR Materials")
    disk_path: Path         # absolute path on disk
    scan_mode: str          # "materials" | "texture" | "gobo" | "model" | "hdri" | "none"
    default_tags: list[str] = field(default_factory=list)
    children: list["FolderNode"] = field(default_factory=list)
    user_added: bool = False
    is_category: bool = False   # category header, not directly scannable
    is_quixel: bool = False
    parent: Optional["FolderNode"] = None  # set after construction

    def breadcrumb(self) -> list["FolderNode"]:
        """Return path from root to this node (inclusive)."""
        parts: list[FolderNode] = []
        node: FolderNode | None = self
        while node is not None:
            parts.append(node)
            node = node.parent
        parts.reverse()
        return parts

    def breadcrumb_display(self) -> str:
        return "  ›  ".join(n.display_name for n in self.breadcrumb())


# ── Quixel subdir mapping ──────────────────────────────────────────────────

_QUIXEL_SUBDIRS = [
    # (disk_name, display_name, scan_mode)
    ("surface",       "Materials",      "materials"),
    ("3d",            "Models",         "model_folder"),
    ("3dplant",       "Foliages",       "model_folder"),
    ("atlas",         "Decals",         "texture"),
    ("brush",         "Brushes",        "texture"),
    ("smartmaterial", "Smart Materials","materials"),
    # "UAssets" → hidden
]


# ── Default tree definition ────────────────────────────────────────────────

_DEFAULT_TREE = [
    # (category_disk_name, display_name, children)
    # children: (disk_name, scan_mode, default_tags)
    ("Materials", "Materials", [
        ("PBR_Materials", "materials", []),
    ]),
    ("Images", "Images", [
        ("Textures", "texture", ["Surface_Imperfections", "Displacements", "Decals"]),
        ("Photos",   "texture", []),
        ("Gobos",    "gobo",    []),
        ("HDRI",     "hdri",    []),
    ]),
    ("Models", "Models", [
        ("3D_Assets", "model_folder", []),
        ("Foliages",  "model_folder", []),
    ]),
]

_USER_FOLDERS_FILE = ".user_folders.json"


def ensure_default_folders(backpack_root: Path) -> None:
    """Create default folder structure on disk if missing."""
    for cat_name, _, children in _DEFAULT_TREE:
        cat_path = backpack_root / cat_name
        cat_path.mkdir(parents=True, exist_ok=True)
        for child_name, _, _ in children:
            (cat_path / child_name).mkdir(parents=True, exist_ok=True)


def ensure_quixel_folders(backpack_root: Path) -> None:
    """Create Quixel Downloaded folder structure on disk."""
    dl_root = backpack_root / "Quixel" / "Downloaded"
    dl_root.mkdir(parents=True, exist_ok=True)
    for disk_name, _, _ in _QUIXEL_SUBDIRS:
        (dl_root / disk_name).mkdir(parents=True, exist_ok=True)


def _load_user_folders(backpack_root: Path) -> dict[str, list[str]]:
    """Load user-added subfolders. Returns {category_disk_name: [subfolder_name, ...]}."""
    p = backpack_root / _USER_FOLDERS_FILE
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _save_user_folders(backpack_root: Path, data: dict[str, list[str]]) -> None:
    p = backpack_root / _USER_FOLDERS_FILE
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def build_folder_tree(
    backpack_root: Path,
    quixel_enabled: bool = False,
) -> FolderNode:
    """Build and return the full folder tree rooted at BACKPACK.

    Reads user_folders from disk. Also creates any missing default dirs.
    """
    ensure_default_folders(backpack_root)
    user_folders = _load_user_folders(backpack_root)

    root = FolderNode(
        disk_name="BACKPACK",
        display_name="BACKPACK",
        disk_path=backpack_root,
        scan_mode="none",
        is_category=True,
    )

    for cat_disk, cat_display, defaults in _DEFAULT_TREE:
        cat_path = backpack_root / cat_disk
        cat_node = FolderNode(
            disk_name=cat_disk,
            display_name=cat_display,
            disk_path=cat_path,
            scan_mode="none",
            is_category=True,
            parent=root,
        )

        # Default subfolders
        for child_disk, scan_mode, def_tags in defaults:
            child_path = cat_path / child_disk
            child_node = FolderNode(
                disk_name=child_disk,
                display_name=disk_to_display(child_disk),
                disk_path=child_path,
                scan_mode=scan_mode,
                default_tags=list(def_tags),
                parent=cat_node,
            )
            cat_node.children.append(child_node)

        # User-added subfolders for this category
        for user_name in user_folders.get(cat_disk, []):
            user_path = cat_path / user_name
            user_path.mkdir(parents=True, exist_ok=True)
            # Infer scan_mode from category
            scan_mode = _infer_scan_mode(cat_disk)
            user_node = FolderNode(
                disk_name=user_name,
                display_name=disk_to_display(user_name),
                disk_path=user_path,
                scan_mode=scan_mode,
                user_added=True,
                parent=cat_node,
            )
            cat_node.children.append(user_node)

        root.children.append(cat_node)

    # Quixel node
    if quixel_enabled:
        ensure_quixel_folders(backpack_root)
        dl_root = backpack_root / "Quixel" / "Downloaded"
        q_node = FolderNode(
            disk_name="Quixel",
            display_name="Quixel",
            disk_path=dl_root,
            scan_mode="none",
            is_category=True,
            is_quixel=True,
            parent=root,
        )
        for disk_name, display_name, scan_mode in _QUIXEL_SUBDIRS:
            child_path = dl_root / disk_name
            child_node = FolderNode(
                disk_name=disk_name,
                display_name=display_name,
                disk_path=child_path,
                scan_mode=scan_mode,
                is_quixel=True,
                parent=q_node,
            )
            q_node.children.append(child_node)
        root.children.append(q_node)

    return root


def _infer_scan_mode(category_disk_name: str) -> str:
    """Best-guess scan mode for a user-added folder based on its category."""
    m = {
        "Materials": "materials",
        "Images":    "texture",
        "Models":    "model_folder",
    }
    return m.get(category_disk_name, "texture")


# ── User folder management ─────────────────────────────────────────────────

def add_user_folder(
    backpack_root: Path,
    category_disk_name: str,
    folder_name: str,
) -> None:
    """Add a user folder under a category and persist."""
    data = _load_user_folders(backpack_root)
    names = data.setdefault(category_disk_name, [])
    if folder_name not in names:
        names.append(folder_name)
    (backpack_root / category_disk_name / folder_name).mkdir(parents=True, exist_ok=True)
    _save_user_folders(backpack_root, data)


def remove_user_folder(
    backpack_root: Path,
    category_disk_name: str,
    folder_name: str,
) -> None:
    """Remove a user folder from persistence (does NOT delete from disk)."""
    data = _load_user_folders(backpack_root)
    if category_disk_name in data:
        data[category_disk_name] = [n for n in data[category_disk_name] if n != folder_name]
    _save_user_folders(backpack_root, data)
