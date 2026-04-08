"""Database schema definitions."""

SCHEMA_SQL = """
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
);

CREATE TABLE IF NOT EXISTS assets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid          TEXT    NOT NULL UNIQUE,
    filename      TEXT    NOT NULL,
    rel_path      TEXT    NOT NULL UNIQUE,
    asset_type    TEXT    NOT NULL DEFAULT 'other',
    sub_type      TEXT,
    file_ext      TEXT    NOT NULL,
    file_size     INTEGER NOT NULL DEFAULT 0,
    file_hash     TEXT,
    width         INTEGER,
    height        INTEGER,
    color_space   TEXT,
    bit_depth     INTEGER,
    thumb_path    TEXT,
    source_path   TEXT,
    notes         TEXT    DEFAULT '',
    rating        INTEGER DEFAULT 0 CHECK(rating BETWEEN 0 AND 5),
    material_id   INTEGER REFERENCES materials(id) ON DELETE SET NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    modified_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    color TEXT    DEFAULT '#888888'
);

CREATE TABLE IF NOT EXISTS asset_tags (
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
    PRIMARY KEY (asset_id, tag_id)
);

CREATE TABLE IF NOT EXISTS material_tags (
    material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id)      ON DELETE CASCADE,
    PRIMARY KEY (material_id, tag_id)
);

CREATE TABLE IF NOT EXISTS collections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS collection_assets (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    asset_id      INTEGER NOT NULL REFERENCES assets(id)      ON DELETE CASCADE,
    sort_order    INTEGER DEFAULT 0,
    PRIMARY KEY (collection_id, asset_id)
);

CREATE INDEX IF NOT EXISTS idx_assets_type        ON assets(asset_type);
CREATE INDEX IF NOT EXISTS idx_assets_sub_type    ON assets(sub_type);
CREATE INDEX IF NOT EXISTS idx_assets_ext         ON assets(file_ext);
CREATE INDEX IF NOT EXISTS idx_assets_hash        ON assets(file_hash);
CREATE INDEX IF NOT EXISTS idx_assets_material    ON assets(material_id);
CREATE INDEX IF NOT EXISTS idx_materials_category ON materials(category);
CREATE INDEX IF NOT EXISTS idx_materials_surface  ON materials(surface_type);
"""
