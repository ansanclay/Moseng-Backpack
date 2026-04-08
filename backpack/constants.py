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
    "#4a9eff",  # app accent
    "#5c9ce6",  # soft blue
]


def random_blue() -> str:
    """Return a random blue from the palette."""
    return random.choice(BLUE_PALETTE)


def random_tag_color(accent_hex: str = "#4a9eff") -> str:
    """Generate a random tag color in the same hue family as the accent color.

    Saturation: 30-100%, Brightness: 20-70% (ensures readable white text).
    """
    # Parse accent hex to get hue
    accent_hex = accent_hex.lstrip("#")
    r, g, b = int(accent_hex[0:2], 16), int(accent_hex[2:4], 16), int(accent_hex[4:6], 16)
    h, _, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)

    # Slight hue variation (±0.05)
    h = (h + random.uniform(-0.05, 0.05)) % 1.0
    s = random.uniform(0.30, 1.00)
    v = random.uniform(0.20, 0.70)

    r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
    return f"#{int(r2*255):02x}{int(g2*255):02x}{int(b2*255):02x}"


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
    ".rat": "texture",
    ".hdr": "hdri",
    ".obj": "model", ".fbx": "model", ".abc": "model",
    ".usd": "model", ".usda": "model", ".usdc": "model",
    ".usdz": "model", ".bgeo": "model",
    ".hip": "scene", ".hipnc": "scene", ".hiplc": "scene",
    ".blend": "scene", ".ma": "scene", ".mb": "scene",
    ".ies": "gobo",
}

# ── PBR map detection patterns ──
SUB_TYPE_PATTERNS = [
    (re.compile(r"(diffuse|diff|albedo|base_?color|col)\b", re.I), "albedo"),
    (re.compile(r"(normal|nrm|nor|nml)\b", re.I), "normal"),
    (re.compile(r"(rough|roughness|rgh)\b", re.I), "roughness"),
    (re.compile(r"(metal|metallic|metalness|met)\b", re.I), "metallic"),
    (re.compile(r"(spec|specular)\b", re.I), "specular"),
    (re.compile(r"(disp|displacement|height)\b", re.I), "displacement"),
    (re.compile(r"(bump)\b", re.I), "bump"),
    (re.compile(r"(ao|ambient_?occ|occlusion)\b", re.I), "ao"),
    (re.compile(r"(emissive|emission|glow)\b", re.I), "emissive"),
    (re.compile(r"(opacity|alpha|mask|transparency)\b", re.I), "opacity"),
    (re.compile(r"(gloss|glossiness)\b", re.I), "gloss"),
    (re.compile(r"(cavity)\b", re.I), "cavity"),
    (re.compile(r"(curvature|curv)\b", re.I), "curvature"),
    (re.compile(r"(translucen|sss|subsurface)\b", re.I), "translucency"),
    (re.compile(r"(fuzz|fuzziness)\b", re.I), "fuzz"),
    (re.compile(r"(preview|thumb|thumbnail)\b", re.I), "preview"),
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
    "Organic": "#4a9eff",
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
