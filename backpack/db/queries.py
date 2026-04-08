"""Database query functions."""

from backpack.db.connection import DatabaseManager
from backpack.models.asset import Asset
from backpack.models.tag import Tag
from backpack.models.material import Material


# ── Asset queries ──────────────────────────────────────────────

def insert_asset(db: DatabaseManager, asset: Asset) -> int:
    cursor = db.execute(
        """INSERT INTO assets
           (uuid, filename, rel_path, asset_type, sub_type, file_ext,
            file_size, file_hash, width, height, color_space, bit_depth,
            thumb_path, source_path, notes, rating, material_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            asset.uuid, asset.filename, asset.rel_path, asset.asset_type,
            asset.sub_type, asset.file_ext, asset.file_size, asset.file_hash,
            asset.width, asset.height, asset.color_space, asset.bit_depth,
            asset.thumb_path, asset.source_path, asset.notes, asset.rating,
            asset.material_id,
        ),
    )
    db.commit()
    return cursor.lastrowid


def get_all_assets(db: DatabaseManager, asset_type: str | None = None) -> list[Asset]:
    if asset_type:
        rows = db.fetchall(
            "SELECT * FROM assets WHERE asset_type = ? ORDER BY modified_at DESC",
            (asset_type,),
        )
    else:
        rows = db.fetchall("SELECT * FROM assets ORDER BY modified_at DESC")

    assets = []
    for row in rows:
        a = Asset.from_row(dict(row))
        a.tags = get_asset_tag_names(db, a.id)
        assets.append(a)
    return assets


def get_asset_by_id(db: DatabaseManager, asset_id: int) -> Asset | None:
    row = db.fetchone("SELECT * FROM assets WHERE id = ?", (asset_id,))
    if not row:
        return None
    a = Asset.from_row(dict(row))
    a.tags = get_asset_tag_names(db, a.id)
    return a


def search_assets(
    db: DatabaseManager,
    query: str = "",
    asset_type: str | None = None,
    tag_ids: list[int] | None = None,
    surface_type: str | None = None,
) -> list[Asset]:
    conditions = []
    params = []

    if query:
        conditions.append("(a.filename LIKE ? OR a.notes LIKE ?)")
        q = f"%{query}%"
        params.extend([q, q])

    if asset_type:
        conditions.append("a.asset_type = ?")
        params.append(asset_type)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    if tag_ids:
        placeholders = ",".join("?" * len(tag_ids))
        sql = f"""
            SELECT a.* FROM assets a
            JOIN asset_tags at ON a.id = at.asset_id
            {where}
            {"AND" if conditions else "WHERE"} at.tag_id IN ({placeholders})
            GROUP BY a.id
            HAVING COUNT(DISTINCT at.tag_id) = ?
            ORDER BY a.modified_at DESC
        """
        params.extend(tag_ids)
        params.append(len(tag_ids))
    else:
        sql = f"SELECT a.* FROM assets a {where} ORDER BY a.modified_at DESC"

    rows = db.fetchall(sql, tuple(params))
    assets = []
    for row in rows:
        a = Asset.from_row(dict(row))
        a.tags = get_asset_tag_names(db, a.id)
        assets.append(a)
    return assets


def update_asset_rating(db: DatabaseManager, asset_id: int, rating: int):
    db.execute(
        "UPDATE assets SET rating = ?, modified_at = datetime('now') WHERE id = ?",
        (rating, asset_id),
    )
    db.commit()


def update_asset_notes(db: DatabaseManager, asset_id: int, notes: str):
    db.execute(
        "UPDATE assets SET notes = ?, modified_at = datetime('now') WHERE id = ?",
        (notes, asset_id),
    )
    db.commit()


def delete_asset(db: DatabaseManager, asset_id: int):
    db.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    db.commit()


def asset_hash_exists(db: DatabaseManager, file_hash: str) -> bool:
    row = db.fetchone("SELECT id FROM assets WHERE file_hash = ?", (file_hash,))
    return row is not None


# ── Material queries ───────────────────────────────────────────

def insert_material(db: DatabaseManager, mat: Material) -> int:
    cursor = db.execute(
        """INSERT INTO materials
           (uuid, name, category, surface_type, preview_path, thumb_path, asset_count)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (mat.uuid, mat.name, mat.category, mat.surface_type,
         mat.preview_path, mat.thumb_path, mat.asset_count),
    )
    db.commit()
    return cursor.lastrowid


def get_all_materials(db: DatabaseManager, category: str | None = None,
                      surface_type: str | None = None) -> list[Material]:
    conditions = []
    params = []
    if category:
        conditions.append("m.category = ?")
        params.append(category)
    if surface_type:
        conditions.append("m.surface_type = ?")
        params.append(surface_type)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = db.fetchall(
        f"SELECT * FROM materials m {where} ORDER BY m.name", tuple(params)
    )
    return [Material.from_row(dict(row)) for row in rows]


def get_material_by_id(db: DatabaseManager, mat_id: int) -> Material | None:
    row = db.fetchone("SELECT * FROM materials WHERE id = ?", (mat_id,))
    if not row:
        return None
    m = Material.from_row(dict(row))
    m.tags = get_material_tag_names(db, m.id)
    return m


def search_materials(
    db: DatabaseManager,
    query: str = "",
    category: str | None = None,
    surface_type: str | None = None,
    tag_ids: list[int] | None = None,
) -> list[Material]:
    conditions = []
    params = []

    if query:
        conditions.append("m.name LIKE ?")
        params.append(f"%{query}%")
    if category:
        conditions.append("m.category = ?")
        params.append(category)
    if surface_type:
        conditions.append("m.surface_type = ?")
        params.append(surface_type)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    if tag_ids:
        placeholders = ",".join("?" * len(tag_ids))
        sql = f"""
            SELECT m.* FROM materials m
            JOIN material_tags mt ON m.id = mt.material_id
            {where}
            {"AND" if conditions else "WHERE"} mt.tag_id IN ({placeholders})
            GROUP BY m.id
            HAVING COUNT(DISTINCT mt.tag_id) = ?
            ORDER BY m.name
        """
        params.extend(tag_ids)
        params.append(len(tag_ids))
    else:
        sql = f"SELECT m.* FROM materials m {where} ORDER BY m.name"

    rows = db.fetchall(sql, tuple(params))
    materials = []
    for row in rows:
        m = Material.from_row(dict(row))
        m.tags = get_material_tag_names(db, m.id)
        materials.append(m)
    return materials


def get_material_assets(db: DatabaseManager, mat_id: int) -> list[Asset]:
    rows = db.fetchall(
        "SELECT * FROM assets WHERE material_id = ? ORDER BY sub_type",
        (mat_id,),
    )
    return [Asset.from_row(dict(row)) for row in rows]


def update_material_count(db: DatabaseManager, mat_id: int):
    db.execute(
        "UPDATE materials SET asset_count = "
        "(SELECT COUNT(*) FROM assets WHERE material_id = ?) WHERE id = ?",
        (mat_id, mat_id),
    )
    db.commit()


def delete_material(db: DatabaseManager, mat_id: int):
    db.execute("UPDATE assets SET material_id = NULL WHERE material_id = ?", (mat_id,))
    db.execute("DELETE FROM materials WHERE id = ?", (mat_id,))
    db.commit()


def get_surface_types(db: DatabaseManager) -> list[tuple[str, str, int]]:
    """Get list of (category, surface_type, count) for sidebar."""
    rows = db.fetchall("""
        SELECT category, surface_type, COUNT(*) as cnt
        FROM materials
        WHERE surface_type IS NOT NULL
        GROUP BY category, surface_type
        ORDER BY category, surface_type
    """)
    return [(row["category"], row["surface_type"], row["cnt"]) for row in rows]


# ── Tag queries ────────────────────────────────────────────────

def get_all_tags(db: DatabaseManager) -> list[Tag]:
    rows = db.fetchall("SELECT * FROM tags ORDER BY name")
    return [Tag.from_row(dict(row)) for row in rows]


def create_tag(db: DatabaseManager, name: str, color: str = "#888888") -> int:
    cursor = db.execute(
        "INSERT INTO tags (name, color) VALUES (?, ?)", (name, color)
    )
    db.commit()
    return cursor.lastrowid


def update_tag(db: DatabaseManager, tag_id: int, name: str, color: str):
    db.execute(
        "UPDATE tags SET name = ?, color = ? WHERE id = ?",
        (name, color, tag_id),
    )
    db.commit()


def delete_tag(db: DatabaseManager, tag_id: int):
    db.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    db.commit()


def get_tag_by_name(db: DatabaseManager, name: str) -> Tag | None:
    row = db.fetchone("SELECT * FROM tags WHERE name = ? COLLATE NOCASE", (name,))
    return Tag.from_row(dict(row)) if row else None


def add_tag_to_asset(db: DatabaseManager, asset_id: int, tag_id: int):
    db.execute(
        "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
        (asset_id, tag_id),
    )
    db.commit()


def remove_tag_from_asset(db: DatabaseManager, asset_id: int, tag_id: int):
    db.execute(
        "DELETE FROM asset_tags WHERE asset_id = ? AND tag_id = ?",
        (asset_id, tag_id),
    )
    db.commit()


def get_asset_tag_names(db: DatabaseManager, asset_id: int) -> list[str]:
    rows = db.fetchall(
        """SELECT t.name FROM tags t
           JOIN asset_tags at ON t.id = at.tag_id
           WHERE at.asset_id = ?
           ORDER BY t.name""",
        (asset_id,),
    )
    return [row["name"] for row in rows]


def add_tag_to_material(db: DatabaseManager, material_id: int, tag_id: int):
    db.execute(
        "INSERT OR IGNORE INTO material_tags (material_id, tag_id) VALUES (?, ?)",
        (material_id, tag_id),
    )
    db.commit()


def get_material_tag_names(db: DatabaseManager, material_id: int) -> list[str]:
    rows = db.fetchall(
        """SELECT t.name FROM tags t
           JOIN material_tags mt ON t.id = mt.tag_id
           WHERE mt.material_id = ?
           ORDER BY t.name""",
        (material_id,),
    )
    return [row["name"] for row in rows]


def get_tag_asset_count(db: DatabaseManager, tag_id: int) -> int:
    row = db.fetchone(
        "SELECT COUNT(*) as cnt FROM asset_tags WHERE tag_id = ?", (tag_id,)
    )
    return row["cnt"] if row else 0
