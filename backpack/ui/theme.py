"""Moseng Backpack — Brand Colour Tokens.

Three user-configurable colours drive the entire palette:
  primary    — accent/highlight color  (amber  #C4A84A)
  secondary  — light/text color        (cream  #F2EEDC)
  background — base background color   (dark   #1C2420)
                sidebar = blend(background, black, 0.22)
                canvas  = background

Usage in QSS  : $primary, $secondary, $surface_low, $text, ...
Usage in Python: from backpack.ui.theme import as_dict, surface_low_for
"""

# ── Defaults ──────────────────────────────────────────────────────────────────
primary         = "#C4A84A"
primary_hover   = "#D4BB60"
primary_pressed = "#A89038"
primary_bg      = "#231E0C"

secondary       = "#F2EEDC"   # cream — light text / white elements
text_mid        = "#B8B4A0"
text_low        = "#7A7868"

surface_low     = "#141A18"   # panels — sidebar, titlebar, detail
surface         = "#1C2420"   # main canvas
surface_mid     = "#232E2A"
surface_high    = "#2A3632"
surface_focus   = "#1E3228"

border          = "#273028"
border_hover    = "#344040"
pressed_bg      = "#18221E"


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


def surface_low_for(background: str) -> str:
    """Return the panel/sidebar color for a given background (slightly darker)."""
    return _blend(background, "#000000", 0.22)


def as_dict(
    accent: str | None = None,
    secondary_color: str | None = None,
    background: str | None = None,
) -> dict[str, str]:
    """Return all tokens keyed by their QSS placeholder name.

    accent          — primary accent color (highlights, buttons)
    secondary_color — light/text color     (text, card borders, white elements)
    background      — base background      (drives both canvas and sidebar)
    """
    # Primary
    _primary         = accent if accent else primary
    _primary_hover   = _blend(_primary, "#ffffff", 0.12) if accent else primary_hover
    _primary_pressed = _blend(_primary, "#000000", 0.20) if accent else primary_pressed
    _primary_bg      = _blend(_primary, "#000000", 0.86) if accent else primary_bg

    # Secondary → text / light elements
    _text     = secondary_color if secondary_color else secondary
    _text_mid = _blend(_text, "#000000", 0.30) if secondary_color else text_mid
    _text_low = _blend(_text, "#000000", 0.52) if secondary_color else text_low

    # Background → canvas + panels both use the EXACT colour the user set, so
    # the UI background matches the picked value (panels were 22% darker before).
    _surface      = background if background else surface
    _surface_low  = _surface if background else surface_low
    _surface_mid  = _blend(_surface, "#ffffff", 0.05) if background else surface_mid
    _surface_high = _blend(_surface, "#ffffff", 0.10) if background else surface_high
    _surface_focus= _blend(_surface, _primary,  0.15) if background else surface_focus
    _pressed_bg   = _blend(_surface, "#000000", 0.12) if background else pressed_bg
    _border       = _blend(_surface, "#ffffff", 0.08) if background else border
    _border_hover = _blend(_surface, "#ffffff", 0.15) if background else border_hover

    return {
        "primary":          _primary,
        "primary_hover":    _primary_hover,
        "primary_pressed":  _primary_pressed,
        "primary_bg":       _primary_bg,
        "secondary":        _text,          # alias: $secondary = same as $text
        "secondary_hover":  _text_mid,
        "secondary_pressed":_text_low,
        "secondary_bg":     _blend(_text, "#000000", 0.88),
        "surface_low":      _surface_low,
        "surface":          _surface,
        "surface_mid":      _surface_mid,
        "surface_high":     _surface_high,
        "surface_focus":    _surface_focus,
        "text":             _text,
        "text_mid":         _text_mid,
        "text_low":         _text_low,
        "border":           _border,
        "border_hover":     _border_hover,
        "pressed_bg":       _pressed_bg,
    }
