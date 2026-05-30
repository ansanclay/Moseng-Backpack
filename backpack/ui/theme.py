"""Moseng Backpack — Brand Colour Tokens.

Single source of truth for the entire UI palette.
Matches the v2 design: DM Sans, near-black bg, near-transparent cards,
6% white borders, accent oklch(58% 0.22 254) ≈ #002aff brand blue.

Usage in QSS  : $primary, $surface_low, $border, ...
Usage in Python: from backpack.ui.theme import primary, surface_mid, ...
"""

# ── PRIMARY  (brand blue) ─────────────────────────────────────────────────────
primary         = "#002aff"   # Moseng blue — brand accent
primary_hover   = "#1a3fff"   # accentHi-ish hover
primary_pressed = "#0020cc"   # darker pressed
primary_bg      = "#05091e"   # accentBg — ~14% alpha on #04060f

# ── SECONDARY  (surface layers, deepest → lightest) ──────────────────────────
surface_low     = "#07080d"   # glass panels — sidebar, titlebar, detail
surface         = "#04060f"   # main background (matches design #04060f)
surface_mid     = "#0a0b14"   # card hover bg (~4% white on bg)
surface_high    = "#0f1018"   # elevated: inputs, menus, tooltips
surface_focus   = "#13142a"   # focused-input bg

# ── NEUTRAL  (text + borders) ────────────────────────────────────────────────
text            = "#cdd0df"   # oklch(85% 0.02 258) approx
text_mid        = "#6f7280"   # oklch(52% 0.03 258) approx
text_low        = "#4c4e58"   # oklch(36% 0.025 258) approx — dim
border          = "#101118"   # rgba(255,255,255,0.06) on #04060f
border_hover    = "#18192a"   # rgba(255,255,255,0.10) on #04060f
pressed_bg      = "#12132a"   # button pressed


def _blend(hex_color: str, mix: str, mix_ratio: float) -> str:
    """Blend hex_color toward mix by mix_ratio (0=original, 1=mix)."""
    def _parse(h: str):
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r1, g1, b1 = _parse(hex_color)
    r2, g2, b2 = _parse(mix)
    r = int(r1 + (r2 - r1) * mix_ratio)
    g = int(g1 + (g2 - g1) * mix_ratio)
    b = int(b1 + (b2 - b1) * mix_ratio)
    return f"#{r:02x}{g:02x}{b:02x}"


def as_dict(accent: str | None = None) -> dict[str, str]:
    """Return all tokens keyed by their QSS placeholder name."""
    _primary         = accent if accent else primary
    _primary_hover   = _blend(_primary, "#ffffff", 0.12) if accent else primary_hover
    _primary_pressed = _blend(_primary, "#000000", 0.20) if accent else primary_pressed
    _primary_bg      = _blend(_primary, "#000000", 0.86) if accent else primary_bg

    return {
        "primary":          _primary,
        "primary_hover":    _primary_hover,
        "primary_pressed":  _primary_pressed,
        "primary_bg":       _primary_bg,
        "surface_low":      surface_low,
        "surface":          surface,
        "surface_mid":      surface_mid,
        "surface_high":     surface_high,
        "surface_focus":    surface_focus,
        "text":             text,
        "text_mid":         text_mid,
        "text_low":         text_low,
        "border":           border,
        "border_hover":     border_hover,
        "pressed_bg":       pressed_bg,
    }
