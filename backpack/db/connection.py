"""SQLite database connection manager."""

import os
import sqlite3
from pathlib import Path

from backpack.constants import DATABASE_FOLDER, DB_FILENAME, DEFAULT_TAGS
from backpack.db.schema import SCHEMA_SQL


class DatabaseManager:
    """Manages the SQLite database connection for a specific drive."""

    def __init__(self):
        self._conn: sqlite3.Connection | None = None
        self._db_path: Path | None = None
        self._base_path: Path | None = None

    @property
    def base_path(self) -> Path | None:
        return self._base_path

    @property
    def is_connected(self) -> bool:
        return self._conn is not None

    def connect(self, drive_root: str) -> Path:
        """Connect to or create a database on the given drive.

        Args:
            drive_root: Drive root path, e.g. "D:\\" or "D:"

        Returns:
            Path to the DATABASE folder.
        """
        self.disconnect()

        base = Path(drive_root) / DATABASE_FOLDER
        base.mkdir(parents=True, exist_ok=True)

        db_path = base / DB_FILENAME
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._init_schema()
        self._init_default_tags()

        self._db_path = db_path
        self._base_path = base
        return base

    def disconnect(self):
        if self._conn:
            self._conn.close()
            self._conn = None
            self._db_path = None
            self._base_path = None

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        assert self._conn, "Database not connected"
        return self._conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]) -> sqlite3.Cursor:
        assert self._conn, "Database not connected"
        return self._conn.executemany(sql, params_list)

    def commit(self):
        if self._conn:
            self._conn.commit()

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        cursor = self.execute(sql, params)
        return cursor.fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        cursor = self.execute(sql, params)
        return cursor.fetchall()

    def _init_schema(self):
        # Run migrations before full schema (for existing DBs)
        self._migrate()
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    def _migrate(self):
        """Apply migrations for existing databases."""
        # Check if assets table exists
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='assets'"
        ).fetchone()
        if not row:
            return  # Fresh DB, no migration needed

        # Ensure materials table exists before assets references it
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS materials (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid          TEXT    NOT NULL UNIQUE,
                name          TEXT    NOT NULL,
                category      TEXT    NOT NULL DEFAULT 'Surface',
                surface_type  TEXT,
                preview_path  TEXT,
                thumb_path    TEXT,
                asset_count   INTEGER DEFAULT 0,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Add material_id to assets if missing
        columns = {
            r[1] for r in self._conn.execute("PRAGMA table_info(assets)").fetchall()
        }
        if "material_id" not in columns:
            self._conn.execute(
                "ALTER TABLE assets ADD COLUMN material_id INTEGER "
                "REFERENCES materials(id) ON DELETE SET NULL"
            )

        # Ensure material_tags table
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS material_tags (
                material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
                tag_id      INTEGER NOT NULL REFERENCES tags(id)      ON DELETE CASCADE,
                PRIMARY KEY (material_id, tag_id)
            )
        """)
        self._conn.commit()

    def _init_default_tags(self):
        existing = {}
        for row in self.fetchall("SELECT name, color FROM tags"):
            existing[row["name"].lower()] = row["color"]

        for name, color in DEFAULT_TAGS.items():
            lower = name.lower()
            if lower not in existing:
                self.execute(
                    "INSERT INTO tags (name, color) VALUES (?, ?)",
                    (name, color),
                )
            elif existing[lower] != color:
                # Update existing tag colors to match new palette
                self.execute(
                    "UPDATE tags SET color = ? WHERE name = ? COLLATE NOCASE",
                    (color, name),
                )
        self.commit()
