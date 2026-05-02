"""PBR map sub-type detection and material grouping.

Public API
──────────
detect_sub_type(filename_or_stem: str) -> str
    Returns the PBR sub-type ("albedo", "normal", …) or "" if unknown.

extract_base_name(stem: str) -> str
    Strips the sub-type keyword and resolution/variant suffixes from a
    filename stem, returning the material base name.
    e.g. "Rock_Mossy_Albedo_4K" → "Rock_Mossy"

group_into_materials(files) -> dict[str, dict[str, Path]]
    Groups a flat list of image Paths into material sets:
    { "Rock_Mossy": {"albedo": Path, "normal": Path, …}, … }

SUB_TYPE_LABEL[sub_type]  → human-readable label
IS_RAW[sub_type]          → True if the map should be loaded as linear/raw data
"""

import re
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Keyword → sub_type mapping
# Covers: Quixel/Megascans, Poliigon, AmbientCG, textures.com, Unreal/Unity
# game-asset single-letter codes (T_Rock_D / _N / _R / _M / _H)
# ─────────────────────────────────────────────────────────────────────────────

_KW: dict[str, str] = {
    # ── Albedo / Base Color ──────────────────────────────────────────────────
    "albedo":       "albedo",
    "diffuse":      "albedo",
    "diff":         "albedo",
    "basecolor":    "albedo",
    "base_color":   "albedo",
    "base":         "albedo",
    "color":        "albedo",
    "colour":       "albedo",
    "col":          "albedo",
    "col1":         "albedo",
    "bc":           "albedo",
    "d":            "albedo",   # T_Rock_D  (game-asset shorthand)

    # ── Normal ───────────────────────────────────────────────────────────────
    "normal":       "normal",
    "nrm":          "normal",
    "nor":          "normal",
    "nml":          "normal",
    "norm":         "normal",
    "normalgl":     "normal",   # AmbientCG OpenGL-space normal
    "normaldx":     "normal",   # AmbientCG DirectX-space normal
    "normalmap":    "normal",
    "n":            "normal",   # T_Rock_N

    # ── Roughness ────────────────────────────────────────────────────────────
    "roughness":    "roughness",
    "rough":        "roughness",
    "rgh":          "roughness",
    "r":            "roughness",  # T_Rock_R

    # ── Metallic / Metalness ─────────────────────────────────────────────────
    "metallic":     "metallic",
    "metalness":    "metallic",
    "metal":        "metallic",
    "met":          "metallic",
    "mtl":          "metallic",
    "m":            "metallic",   # T_Rock_M

    # ── Specular ─────────────────────────────────────────────────────────────
    "specular":     "specular",
    "spec":         "specular",
    "spc":          "specular",

    # ── Reflection (inverted specular) ───────────────────────────────────────
    # REFL / reflection maps encode the inverse of specular — bright where
    # the surface reflects, used in older / Specular-Gloss workflows.
    "refl":         "reflection",
    "reflection":   "reflection",
    "reflect":      "reflection",
    "reflectivity": "reflection",

    # ── Displacement / Height ────────────────────────────────────────────────
    "displacement": "displacement",
    "displace":     "displacement",
    "disp":         "displacement",
    "dis":          "displacement",
    "height":       "displacement",
    "hgt":          "displacement",
    "h":            "displacement",  # T_Rock_H

    # ── Bump ─────────────────────────────────────────────────────────────────
    "bump":         "bump",
    "bmp":          "bump",

    # ── Ambient Occlusion ────────────────────────────────────────────────────
    "ao":                   "ao",
    "ambientocclusion":     "ao",
    "ambient_occlusion":    "ao",
    "occlusion":            "ao",
    "occ":                  "ao",

    # ── Emissive ─────────────────────────────────────────────────────────────
    "emissive":     "emissive",
    "emission":     "emissive",
    "emit":         "emissive",
    "glow":         "emissive",
    "emm":          "emissive",

    # ── Opacity / Alpha ──────────────────────────────────────────────────────
    "opacity":      "opacity",
    "alpha":        "opacity",
    "transparency": "opacity",
    "transparent":  "opacity",
    "trans":        "opacity",
    "mask":         "opacity",

    # ── Gloss ────────────────────────────────────────────────────────────────
    "gloss":        "gloss",
    "glossiness":   "gloss",
    "gls":          "gloss",

    # ── Translucency / SSS ───────────────────────────────────────────────────
    "translucency": "translucency",
    "translucent":  "translucency",
    "sss":          "translucency",
    "subsurface":   "translucency",

    # ── Cavity ───────────────────────────────────────────────────────────────
    "cavity":       "cavity",

    # ── Curvature ────────────────────────────────────────────────────────────
    "curvature":    "curvature",
    "curv":         "curvature",

    # ── Fuzz ─────────────────────────────────────────────────────────────────
    "fuzz":         "fuzz",
    "fuzziness":    "fuzz",
}

# Single-letter codes (only valid as the LAST part of a filename)
_SINGLE_LETTER = {k for k, v in _KW.items() if len(k) == 1}

# Resolution / variant suffixes to strip before processing
# Matches: _4K  _2K  _1K  _8K  _16K  _4k  _2k   (with leading sep)
_RESOLUTION_RE = re.compile(r"(?:[_\-\s]\d+[KkMm])+$")
# Variant suffix: _VAR1  _var2  _V1  _v3
_VARIANT_RE    = re.compile(r"[_\-\s][Vv][Aa][Rr]?\d+$")
# LOD suffix: _LOD0  _lod1
_LOD_RE        = re.compile(r"[_\-\s][Ll][Oo][Dd]\d+$")

# ─────────────────────────────────────────────────────────────────────────────
# Human-readable labels & color-space flag
# ─────────────────────────────────────────────────────────────────────────────

SUB_TYPE_LABEL: dict[str, str] = {
    "albedo":       "Albedo / Base Color",
    "normal":       "Normal Map",
    "roughness":    "Roughness",
    "metallic":     "Metallic",
    "specular":     "Specular",
    "reflection":   "Reflection (inv. Specular)",
    "displacement": "Displacement / Height",
    "bump":         "Bump",
    "ao":           "Ambient Occlusion",
    "emissive":     "Emissive",
    "opacity":      "Opacity / Alpha",
    "gloss":        "Gloss",
    "translucency": "Translucency / SSS",
    "cavity":       "Cavity",
    "curvature":    "Curvature",
    "fuzz":         "Fuzz",
}

# Maps that carry linear (non-colour) data → load as RAW in the renderer
IS_RAW: dict[str, bool] = {
    "albedo":       False,
    "normal":       True,
    "roughness":    True,
    "metallic":     True,
    "specular":     False,
    "reflection":   True,    # linear reflectance values
    "displacement": True,
    "bump":         True,
    "ao":           True,
    "emissive":     False,
    "opacity":      True,
    "gloss":        True,
    "translucency": True,
    "cavity":       True,
    "curvature":    True,
    "fuzz":         True,
}

# Preferred display order in UI
PREFERRED_ORDER = [
    "albedo", "normal", "roughness", "metallic", "specular", "reflection",
    "ao", "displacement", "bump", "emissive", "opacity",
    "translucency", "gloss", "cavity", "curvature", "fuzz",
]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clean_stem(stem: str) -> str:
    """Strip resolution, variant, and LOD suffixes from a filename stem."""
    s = stem
    s = _LOD_RE.sub("", s)
    s = _VARIANT_RE.sub("", s)
    s = _RESOLUTION_RE.sub("", s)
    return s


def _split_parts(stem: str) -> list[str]:
    """Split a stem into its component words by common separators."""
    return [p for p in re.split(r"[_\-\s]+", stem) if p]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def detect_sub_type(filename_or_stem: str) -> str:
    """Detect PBR map sub-type from a filename or stem.

    Strategy (in order):
    1. Strip resolution / variant / LOD suffixes.
    2. Split into parts and check each part (last → first) against the
       keyword map.  Single-letter codes only match as the very last part.
    3. If no keyword part found, try a compound-keyword regex scan on the
       full cleaned stem (handles "basecolor", "ambientocclusion", etc.).

    Returns "" when the type cannot be determined.
    """
    # Accept full filename → strip extension
    stem = Path(filename_or_stem).stem if "." in filename_or_stem else filename_or_stem

    clean = _clean_stem(stem)
    parts = _split_parts(clean)

    if not parts:
        return ""

    last_idx = len(parts) - 1

    # Check parts last→first; single-letter codes only at final position
    for i in range(last_idx, -1, -1):
        word = parts[i].lower()
        if word in _KW:
            if word in _SINGLE_LETTER and i != last_idx:
                continue   # single-letter code must be the trailing part
            return _KW[word]

    # Compound-keyword fallback (e.g. "basecolor", "normalgl", "ambientocclusion")
    lower_clean = clean.lower()
    for kw, sub in sorted(_KW.items(), key=lambda x: -len(x[0])):
        if len(kw) <= 1:
            continue
        if kw in lower_clean:
            return sub

    return ""


def extract_base_name(stem: str) -> str:
    """Strip the sub-type keyword + trailing suffixes; return the base name.

    Example:
        "Rock_Mossy_Albedo_4K"  → "Rock_Mossy"
        "T_Concrete_N_2K"       → "T_Concrete"
        "fabric_carpet_NRM"     → "fabric_carpet"
    """
    clean = _clean_stem(stem)
    parts = _split_parts(clean)

    if not parts:
        return stem

    last_idx = len(parts) - 1

    # Find the index of the sub-type keyword (last→first)
    sub_idx = -1
    for i in range(last_idx, -1, -1):
        word = parts[i].lower()
        if word in _KW:
            if word in _SINGLE_LETTER and i != last_idx:
                continue
            sub_idx = i
            break

    if sub_idx > 0:
        # Reconstruct the separator used in the original stem
        base = _reconstruct_prefix(stem, parts, sub_idx)
        return base or stem
    if sub_idx == 0:
        # Sub-type is the very first component — return cleaned stem
        return clean

    # No sub-type detected → return cleaned stem
    return clean


def _reconstruct_prefix(original: str, parts: list[str], up_to: int) -> str:
    """Re-join the first ``up_to`` parts using the separator from ``original``."""
    prefix_parts = parts[:up_to]
    if not prefix_parts:
        return ""
    # Find where the sub-type part begins in the original (case-insensitive)
    target = parts[up_to]
    lo = original.lower()
    tl = target.lower()
    idx = lo.find(tl)
    if idx > 0:
        # Strip trailing separators from everything before the keyword
        return original[:idx].rstrip("_-. ")
    # Fallback: join with underscore
    return "_".join(prefix_parts)


def group_into_materials(
    files: list[Path],
    image_exts: set[str] | None = None,
) -> dict[str, dict[str, Path]]:
    """Group a flat list of image Paths into material sets.

    Returns:
        {
          "Rock_Mossy": {
              "albedo":    Path(...),
              "normal":    Path(...),
              "roughness": Path(...),
          },
          ...
        }

    Files with no detectable sub-type are placed in a single-file group
    keyed by their stem so they don't collide with detected maps.
    Files with the same base name and same sub-type keep the last seen
    (allows override by higher-resolution variant).
    """
    if image_exts is None:
        image_exts = {
            ".png", ".jpg", ".jpeg", ".tif", ".tiff",
            ".tga", ".bmp", ".exr", ".hdr", ".tx",
        }

    groups: dict[str, dict[str, Path]] = {}

    for f in files:
        if f.suffix.lower() not in image_exts:
            continue

        sub   = detect_sub_type(f.stem)
        base  = extract_base_name(f.stem) if sub else _clean_stem(f.stem)

        if not base:
            base = f.stem

        if base not in groups:
            groups[base] = {}

        key = sub if sub else f.stem   # unrecognised → use full stem as key
        groups[base][key] = f

    return groups


def detect_material_name(files: list[Path]) -> str:
    """Return the best guess at the material name from a set of related files.

    Uses the most common base name across all files.
    Falls back to the parent folder name if no consensus.
    """
    from collections import Counter

    bases = [extract_base_name(f.stem) for f in files if extract_base_name(f.stem)]
    if not bases:
        if files:
            return files[0].parent.name
        return "Material"

    most_common = Counter(bases).most_common(1)[0][0]
    return most_common or (files[0].parent.name if files else "Material")


def confidence(maps: dict[str, Path]) -> float:
    """0–1 confidence that this dict represents a real PBR material set.

    Higher score when more known sub-types are present.
    """
    known = set(maps.keys()) & set(SUB_TYPE_LABEL.keys())
    if not known:
        return 0.0
    # Base score on number of recognised maps; albedo+normal counts extra
    score = len(known) / max(len(PREFERRED_ORDER), 1)
    if "albedo" in known:
        score += 0.15
    if "normal" in known:
        score += 0.10
    return min(score, 1.0)


def sort_maps(maps: dict[str, Path]) -> list[tuple[str, Path]]:
    """Return (sub_type, path) pairs sorted by PREFERRED_ORDER."""
    ordered = [(k, maps[k]) for k in PREFERRED_ORDER if k in maps]
    extras  = sorted((k, v) for k, v in maps.items() if k not in PREFERRED_ORDER)
    return ordered + extras
