"""Constants and configuration for Moseng Backpack."""

import re
import random
import colorsys

APP_NAME = "Moseng Backpack"
APP_VERSION = "0.2.0"
DATABASE_FOLDER = "DATABASE"
DB_FILENAME = "backpack.db"
THUMB_DIR = ".thumbs"
THUMB_SIZE = (256, 256)

# ── Asset type categories ──
ASSET_TYPES = ["texture", "hdri", "gobo", "model", "scene", "other"]

# ── Blue palette for tags ──
BLUE_PALETTE = [
    "#2563eb",  # bright blue
    "#3b82f6",  # blue
    "#1d4ed8",  # darker blue
    "#4f86f7",  # medium blue
    "#6495ed",  # cornflower
    "#5b8def",  # sky blue
    "#3a6fff",  # vivid blue
    "#2d7aed",  # azure
    "#4169e1",  # royal blue
    "#1e90ff",  # dodger blue
    "#3f6fd4",  # steel blue
    "#5a7fc4",  # muted blue
    "#2979ff",  # accent blue
    "#448aff",  # light accent
    "#536dfe",  # indigo accent
    "#304ffe",  # deep indigo
    "#1a73e8",  # google blue
    "#0d6efd",  # bootstrap blue
    "#002aff",  # app accent
    "#5c9ce6",  # soft blue
]


def random_blue() -> str:
    """Return a random blue from the palette."""
    return random.choice(BLUE_PALETTE)


# ── Deterministic muted tag colors (v2 design) ──────────────────────────────

def tag_hue(name: str) -> int:
    """Deterministic hue 0–359 from tag name (matches v2 design hash function)."""
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) % 360
    return h


def tag_color_for_name(name: str) -> str:
    """Muted, sophisticated tag color derived from name.

    Approximates v2 design oklch(68% 0.12 hue):
    HLS(hue, 0.68 lightness, 0.25 saturation) — readable on dark bg, not garish.
    """
    r, g, b = colorsys.hls_to_rgb(tag_hue(name) / 360.0, 0.68, 0.25)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def random_tag_color(accent_hex: str = "#002aff") -> str:
    """Generate a tag color. Delegates to deterministic tag_color_for_name.

    accent_hex kept for API compatibility; the color is now name-independent
    so callers should prefer tag_color_for_name(tag_name) directly.
    """
    # Return a deterministic blue-family color as a neutral fallback
    r, g, b = colorsys.hls_to_rgb(230 / 360.0, 0.68, 0.25)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


# ── Quixel Bridge-style surface categories ──
SURFACE_CATEGORIES = {
    "bark": ("Nature", "Bark"), "tree_bark": ("Nature", "Bark"),
    "wood_bark": ("Nature", "Bark"),
    "leaf": ("Nature", "Leaves"), "leaves": ("Nature", "Leaves"),
    "grass": ("Nature", "Grass"), "moss": ("Nature", "Moss"),
    "soil": ("Nature", "Soil"), "dirt": ("Nature", "Soil"),
    "mud": ("Nature", "Soil"),
    "sand": ("Nature", "Sand"),
    "rock": ("Nature", "Rock"), "stone": ("Nature", "Rock"),
    "cliff": ("Nature", "Rock"),
    "snow": ("Nature", "Snow"), "ice": ("Nature", "Ice"),

    "wood": ("Wood", "Wood"), "plywood": ("Wood", "Plywood"),
    "timber": ("Wood", "Timber"), "hardwood": ("Wood", "Hardwood"),
    "planks": ("Wood", "Planks"), "wood_floor": ("Wood", "Wood Floor"),
    "parquet": ("Wood", "Parquet"), "bamboo": ("Wood", "Bamboo"),

    "marble": ("Stone", "Marble"), "granite": ("Stone", "Granite"),
    "slate": ("Stone", "Slate"), "limestone": ("Stone", "Limestone"),
    "sandstone": ("Stone", "Sandstone"), "travertine": ("Stone", "Travertine"),
    "onyx": ("Stone", "Onyx"), "cobblestone": ("Stone", "Cobblestone"),

    "concrete": ("Concrete", "Concrete"), "cement": ("Concrete", "Cement"),
    "plaster": ("Concrete", "Plaster"), "stucco": ("Concrete", "Stucco"),
    "mortar": ("Concrete", "Mortar"), "grout": ("Concrete", "Grout"),

    "metal": ("Metal", "Metal"), "steel": ("Metal", "Steel"),
    "iron": ("Metal", "Iron"), "rust": ("Metal", "Rust"),
    "copper": ("Metal", "Copper"), "brass": ("Metal", "Brass"),
    "bronze": ("Metal", "Bronze"), "aluminum": ("Metal", "Aluminum"),
    "gold": ("Metal", "Gold"), "silver": ("Metal", "Silver"),
    "chrome": ("Metal", "Chrome"), "titanium": ("Metal", "Titanium"),
    "corrugated": ("Metal", "Corrugated"),

    "brick": ("Brick", "Brick"),
    "tile": ("Tile", "Tile"), "ceramic": ("Tile", "Ceramic"),
    "mosaic": ("Tile", "Mosaic"), "terracotta": ("Tile", "Terracotta"),
    "roof_tile": ("Tile", "Roof Tile"),

    "fabric": ("Fabric", "Fabric"), "cloth": ("Fabric", "Cloth"),
    "cotton": ("Fabric", "Cotton"), "linen": ("Fabric", "Linen"),
    "silk": ("Fabric", "Silk"), "wool": ("Fabric", "Wool"),
    "denim": ("Fabric", "Denim"), "canvas": ("Fabric", "Canvas"),
    "burlap": ("Fabric", "Burlap"), "leather": ("Fabric", "Leather"),
    "suede": ("Fabric", "Suede"), "carpet": ("Fabric", "Carpet"),

    "paint": ("Paint", "Paint"), "lacquer": ("Paint", "Lacquer"),
    "varnish": ("Paint", "Varnish"), "enamel": ("Paint", "Enamel"),

    "plastic": ("Plastic", "Plastic"), "rubber": ("Plastic", "Rubber"),
    "vinyl": ("Plastic", "Vinyl"), "foam": ("Plastic", "Foam"),

    "glass": ("Glass", "Glass"), "frosted": ("Glass", "Frosted Glass"),

    "paper": ("Paper", "Paper"), "cardboard": ("Paper", "Cardboard"),
    "wallpaper": ("Paper", "Wallpaper"),

    "asphalt": ("Ground", "Asphalt"), "gravel": ("Ground", "Gravel"),
    "pavement": ("Ground", "Pavement"), "road": ("Ground", "Road"),
    "pebble": ("Ground", "Pebbles"),

    "food": ("Organic", "Food"), "fruit": ("Organic", "Fruit"),
    "skin": ("Organic", "Skin"),

    "decal": ("Decal", "Decal"), "grunge": ("Overlay", "Grunge"),
    "scratch": ("Overlay", "Scratches"), "dust": ("Overlay", "Dust"),
    "water": ("Liquid", "Water"), "lava": ("Liquid", "Lava"),
}

# ── Extension to asset type mapping ──
EXTENSION_MAP = {
    ".png": "texture", ".jpg": "texture", ".jpeg": "texture",
    ".tif": "texture", ".tiff": "texture", ".tga": "texture",
    ".bmp": "texture", ".exr": "texture", ".tx": "texture",
    ".hdr": "hdri",
    ".obj": "model", ".fbx": "model", ".abc": "model",
    ".usd": "model", ".usda": "model", ".usdc": "model",
    ".usdz": "model", ".bgeo": "model",
    ".hip": "scene", ".hipnc": "scene", ".hiplc": "scene",
    ".blend": "scene", ".ma": "scene", ".mb": "scene",
    ".ies": "gobo",
}

# ── PBR map detection patterns ──
# Kept for backward-compatibility; scanner.py and new code use
# backpack.core.map_detector.detect_sub_type() instead.
SUB_TYPE_PATTERNS = [
    (re.compile(r"(diffuse|diff|albedo|base_?color|col)(?:[_\-\s]|$)", re.I), "albedo"),
    (re.compile(r"(normal|nrm|nor|nml|norm)(?:[_\-\s]|$)", re.I), "normal"),
    (re.compile(r"(rough|roughness|rgh)(?:[_\-\s]|$)", re.I), "roughness"),
    (re.compile(r"(metal|metallic|metalness|met)(?:[_\-\s]|$)", re.I), "metallic"),
    (re.compile(r"(spec|specular)(?:[_\-\s]|$)", re.I), "specular"),
    (re.compile(r"(refl|reflection|reflect|reflectivity)(?:[_\-\s]|$)", re.I), "reflection"),
    (re.compile(r"(disp|displacement|height)(?:[_\-\s]|$)", re.I), "displacement"),
    (re.compile(r"(bump)(?:[_\-\s]|$)", re.I), "bump"),
    (re.compile(r"(ao|ambient_?occ|occlusion)(?:[_\-\s]|$)", re.I), "ao"),
    (re.compile(r"(emissive|emission|glow)(?:[_\-\s]|$)", re.I), "emissive"),
    (re.compile(r"(opacity|alpha|mask|transparency)(?:[_\-\s]|$)", re.I), "opacity"),
    (re.compile(r"(gloss|glossiness)(?:[_\-\s]|$)", re.I), "gloss"),
    (re.compile(r"(cavity)(?:[_\-\s]|$)", re.I), "cavity"),
    (re.compile(r"(curvature|curv)(?:[_\-\s]|$)", re.I), "curvature"),
    (re.compile(r"(translucen|sss|subsurface)(?:[_\-\s]|$)", re.I), "translucency"),
    (re.compile(r"(fuzz|fuzziness)(?:[_\-\s]|$)", re.I), "fuzz"),
    (re.compile(r"(preview|thumb|thumbnail)(?:[_\-\s]|$)", re.I), "preview"),
]

HDRI_NAME_PATTERNS = re.compile(
    r"(hdri|hdr|env|sky|panorama|pano|dome|environment)", re.I
)

FOLDER_TYPE_OVERRIDES = {
    "hdri": "hdri", "hdris": "hdri",
    "gobo": "gobo", "gobos": "gobo",
    "texture": "texture", "textures": "texture",
    "model": "model", "models": "model",
    "scene": "scene", "scenes": "scene",
}

QUIXEL_PREVIEW_PATTERNS = [
    re.compile(r"preview", re.I),
    re.compile(r"_Preview\.", re.I),
    re.compile(r"Thumb", re.I),
]

MEGASCANS_ID_PATTERN = re.compile(r"[a-z]{1,2}[a-z0-9]{4,12}$", re.I)

# Default tag presets with blue palette
DEFAULT_TAGS = {
    "Wood": "#3b82f6",
    "Metal": "#2563eb",
    "Fabric": "#5b8def",
    "Stone": "#4169e1",
    "Concrete": "#3a6fff",
    "Brick": "#1d4ed8",
    "Nature": "#2d7aed",
    "Ground": "#4f86f7",
    "Indoor": "#448aff",
    "Outdoor": "#1e90ff",
    "4K": "#5c9ce6",
    "8K": "#536dfe",
    "Seamless": "#0d6efd",
    "PBR": "#2979ff",
    "Painted": "#6495ed",
    "Weathered": "#304ffe",
    "Clean": "#1a73e8",
    "Organic": "#002aff",
    "Favorites": "#f59e0b",
}

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff",
    ".tga", ".bmp", ".exr", ".hdr",
}

CATEGORY_FOLDERS = {
    "texture": "Textures",
    "hdri": "HDRIs",
    "gobo": "Gobos",
    "model": "Models",
    "scene": "Scenes",
    "other": "Other",
}
