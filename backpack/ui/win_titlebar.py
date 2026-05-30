"""Windows DWM caption-bar colour helper.

Sets the OS title bar to match the app's background colour via
DwmSetWindowAttribute.  Windows 11 build 22000+ supports an exact COLORREF
(attribute 35 = DWMWA_CAPTION_COLOR).  Windows 10 1809+ only supports
dark/light mode toggling (attribute 20 = DWMWA_USE_IMMERSIVE_DARK_MODE).

The helper is a no-op on non-Windows platforms and swallows all errors so
callers never have to guard it.
"""

import sys


def apply(hwnd: int, bg_hex: str, text_hex: str | None = None) -> None:
    """Set the OS caption bar colour for *hwnd*.

    bg_hex   — '#RRGGBB'  caption background (matched to app bg_color)
    text_hex — '#RRGGBB'  caption text; if None, white/black is chosen
                           automatically based on bg luminance
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        dwmapi = ctypes.windll.dwmapi

        # COLORREF = 0x00BBGGRR (little-endian RGB)
        def _colorref(h: str) -> int:
            h = h.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return r | (g << 8) | (b << 16)

        # 1. Enable immersive dark mode — makes the default chrome (scrollbars,
        #    menus) dark; also required on Win10 to get a dark title bar.
        DWMWA_DARK = 20
        dark = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_DARK, ctypes.byref(dark), ctypes.sizeof(dark)
        )

        # 2. Exact caption background color — Win11 22000+.
        DWMWA_CAPTION_COLOR = 35
        bg_c = ctypes.c_uint(_colorref(bg_hex))
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_CAPTION_COLOR, ctypes.byref(bg_c), ctypes.sizeof(bg_c)
        )

        # 3. Caption text color — Win11 22000+.
        DWMWA_TEXT_COLOR = 36
        if text_hex is None:
            # Auto-choose white or near-white based on bg luminance
            h = bg_hex.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            text_hex = "#e8e4d2" if lum < 128 else "#1a1a1a"
        tc = ctypes.c_uint(_colorref(text_hex))
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_TEXT_COLOR, ctypes.byref(tc), ctypes.sizeof(tc)
        )

    except Exception:
        pass  # non-Win11 or DWM unavailable — fail silently
