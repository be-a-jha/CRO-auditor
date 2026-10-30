"""Colour parsing, conversion and distance helpers (no external deps)."""
from __future__ import annotations

import math
import re

NAMED = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0), "green": (0, 128, 0),
    "blue": (0, 0, 255), "yellow": (255, 255, 0), "orange": (255, 165, 0), "purple": (128, 0, 128),
    "pink": (255, 192, 203), "gray": (128, 128, 128), "grey": (128, 128, 128), "silver": (192, 192, 192),
    "navy": (0, 0, 128), "teal": (0, 128, 128), "maroon": (128, 0, 0), "olive": (128, 128, 0),
    "lime": (0, 255, 0), "aqua": (0, 255, 255), "cyan": (0, 255, 255), "fuchsia": (255, 0, 255),
    "magenta": (255, 0, 255), "gold": (255, 215, 0), "crimson": (220, 20, 60), "tomato": (255, 99, 71),
    "coral": (255, 127, 80), "salmon": (250, 128, 114), "beige": (245, 245, 220), "ivory": (255, 255, 240),
    "khaki": (240, 230, 140), "lavender": (230, 230, 250), "brown": (165, 42, 42), "tan": (210, 180, 140),
    "whitesmoke": (245, 245, 245), "gainsboro": (220, 220, 220), "lightgray": (211, 211, 211),
    "lightgrey": (211, 211, 211), "darkgray": (169, 169, 169), "darkgrey": (169, 169, 169),
    "dimgray": (105, 105, 105), "dimgrey": (105, 105, 105), "darkgreen": (0, 100, 0),
    "forestgreen": (34, 139, 34), "seagreen": (46, 139, 87), "darkred": (139, 0, 0),
    "firebrick": (178, 34, 34), "royalblue": (65, 105, 225), "dodgerblue": (30, 144, 255),
    "skyblue": (135, 206, 235), "steelblue": (70, 130, 180), "indigo": (75, 0, 130),
    "violet": (238, 130, 238), "orchid": (218, 112, 214), "hotpink": (255, 105, 180),
    "deeppink": (255, 20, 147), "chocolate": (210, 105, 30), "sienna": (160, 82, 45),
    "peru": (205, 133, 63), "wheat": (245, 222, 179), "linen": (250, 240, 230), "snow": (255, 250, 250),
    "mintcream": (245, 255, 250), "honeydew": (240, 255, 240), "aliceblue": (240, 248, 255),
    "ghostwhite": (248, 248, 255), "seashell": (255, 245, 238), "oldlace": (253, 245, 230),
    "cornsilk": (255, 248, 220), "darkorange": (255, 140, 0), "goldenrod": (218, 165, 32),
    "darkslategray": (47, 79, 79), "darkslategrey": (47, 79, 79), "slategray": (112, 128, 144),
    "midnightblue": (25, 25, 112), "darkblue": (0, 0, 139), "rebeccapurple": (102, 51, 153),
}
NEUTRAL_KEYWORDS = {"transparent", "currentcolor", "inherit", "initial", "unset", "none", "revert"}

HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
FUNC_RE = re.compile(r"(?:rgba?|hsla?)\([^)]*\)", re.I)
WORD_RE = re.compile(r"\b[a-zA-Z]+\b")


def parse_color(s: str):
    """Return (r, g, b, a) or None."""
    if not s:
        return None
    s = s.strip().lower()
    if s in NEUTRAL_KEYWORDS:
        return None
    if s in NAMED:
        return (*NAMED[s], 1.0)
    if s.startswith("#"):
        h = s[1:]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            return None
        try:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
            return (r, g, b, a)
        except ValueError:
            return None
    m = re.match(r"(rgba?|hsla?)\(([^)]*)\)", s)
    if not m:
        return None
    kind, body = m.group(1), m.group(2)
    parts = [p for p in re.split(r"[\s,/]+", body.strip()) if p]
    try:
        if kind.startswith("rgb"):
            vals = []
            for p in parts[:3]:
                vals.append(float(p[:-1]) * 2.55 if p.endswith("%") else float(p))
            a = _alpha(parts[3]) if len(parts) > 3 else 1.0
            return (round(vals[0]), round(vals[1]), round(vals[2]), a)
        h = float(parts[0].replace("deg", "")) % 360
        sat = float(parts[1].rstrip("%")) / 100
        light = float(parts[2].rstrip("%")) / 100
        a = _alpha(parts[3]) if len(parts) > 3 else 1.0
        r, g, b = _hsl_to_rgb(h, sat, light)
        return (r, g, b, a)
    except (ValueError, IndexError):
        return None


def _alpha(p: str) -> float:
    return float(p[:-1]) / 100 if p.endswith("%") else float(p)


def _hsl_to_rgb(h, s, l):
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][int(h // 60) % 6]
    return round((r + m) * 255), round((g + m) * 255), round((b + m) * 255)


def to_hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*[max(0, min(255, int(v))) for v in rgb[:3]])


def _lin(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_lab(rgb):
    r, g, b = (_lin(v) for v in rgb[:3])
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a, b) -> float:
    la, lb = rgb_to_lab(a), rgb_to_lab(b)
    return math.dist(la, lb)


def luminance(rgb) -> float:
    r, g, b = (_lin(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg) -> float:
    l1, l2 = luminance(fg), luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def saturation(rgb) -> float:
    r, g, b = (v / 255 for v in rgb[:3])
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == mn:
        return 0.0
    l = (mx + mn) / 2
    return (mx - mn) / (1 - abs(2 * l - 1))


def blend_over(rgba, bg=(255, 255, 255)):
    r, g, b, a = rgba
    return (round(r * a + bg[0] * (1 - a)), round(g * a + bg[1] * (1 - a)), round(b * a + bg[2] * (1 - a)))


def nearest(rgb, palette_hex: list[str]) -> tuple[str, float]:
    best, best_d = palette_hex[0], 1e9
    for h in palette_hex:
        p = parse_color(h)
        d = delta_e(rgb, p)
        if d < best_d:
            best, best_d = h, d
    return best, best_d


def find_color_literals(value: str) -> list[str]:
    """Colour literals inside a CSS value (hex, rgb/hsl functions, named colours)."""
    out = HEX_RE.findall(value) + FUNC_RE.findall(value)
    stripped = FUNC_RE.sub(" ", HEX_RE.sub(" ", value))
    stripped = re.sub(r"var\([^)]*\)", " ", stripped)
    stripped = re.sub(r"url\([^)]*\)", " ", stripped)
    for w in WORD_RE.findall(stripped):
        if w.lower() in NAMED:
            out.append(w)
    return out
