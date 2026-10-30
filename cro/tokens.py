"""Brand tokens: the identity we are not allowed to change. Built from computed styles (URL mode)
or from screenshot pixels + vision (screenshot mode). Exposed to patches only as CSS variables."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .colors import blend_over, contrast_ratio, delta_e, parse_color, rgb_to_lab, saturation, to_hex

GENERIC_FAMILIES = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-sans-serif",
                    "ui-serif", "ui-monospace", "-apple-system", "blinkmacsystemfont", "emoji", "math", "fangsong"}


@dataclass
class BrandTokens:
    source: str                                   # "dom" | "screenshot"
    roles: dict = field(default_factory=dict)     # role -> hex
    palette: list = field(default_factory=list)   # [{hex, weight}]
    fonts: dict = field(default_factory=dict)     # body/heading/button -> css font-family stack
    font_names: list = field(default_factory=list)
    sizes: list = field(default_factory=list)     # allowed px sizes (brand scale)
    size_roles: dict = field(default_factory=dict)
    radius: dict = field(default_factory=dict)
    button: dict = field(default_factory=dict)
    line_height: str = "1.5"
    logo: dict | None = None
    google_font_url: str | None = None            # screenshot mode only (approximation)
    notes: list = field(default_factory=list)

    @property
    def palette_hex(self) -> list[str]:
        return [p["hex"] for p in self.palette]

    def css_vars(self) -> str:
        r, s = self.roles, self.size_roles
        lines = [f"--cro-{k.replace('_', '-')}: {v};" for k, v in r.items()]
        for k, v in self.fonts.items():
            lines.append(f"--cro-font-{k}: {v};")
        for k, v in s.items():
            lines.append(f"--cro-fs-{k}: {v}px;")
        lines.append(f"--cro-radius: {self.radius.get('button', '0px')};")
        lines.append(f"--cro-radius-input: {self.radius.get('input', self.radius.get('button', '0px'))};")
        b = self.button
        lines += [f"--cro-btn-transform: {b.get('transform', 'none')};",
                  f"--cro-btn-spacing: {b.get('spacing', 'normal')};",
                  f"--cro-btn-weight: {b.get('weight', '600')};",
                  f"--cro-btn-padding: {b.get('padding', '0 20px')};",
                  f"--cro-btn-height: {b.get('height', '48px')};",
                  f"--cro-line: {self.line_height};"]
        return ":root{" + "".join(lines) + "}"

    def describe(self) -> dict:
        d = asdict(self)
        d["css_vars"] = self.css_vars()
        return d


def _rgb(s, over=(255, 255, 255)):
    c = parse_color(s)
    if c is None:
        return None
    return blend_over(c, over) if c[3] < 1 else c[:3]


def family_names(stack: str) -> list[str]:
    out = []
    for f in (stack or "").split(","):
        f = f.strip().strip("'\"")
        if f and f.lower() not in GENERIC_FAMILIES:
            out.append(f)
    return out


def _snap_size(v) -> float:
    try:
        return round(float(str(v).replace("px", "")) * 2) / 2
    except ValueError:
        return 0.0


def from_dom(raw: dict, facts: dict) -> BrandTokens:
    t = BrandTokens(source="dom")
    page_bg = _rgb(raw.get("page_bg")) or (255, 255, 255)

    # ---- weighted colour candidates
    cands: dict[tuple, float] = {}

    def add(colour, w):
        rgb = _rgb(colour, page_bg) if isinstance(colour, str) else colour
        if rgb is None:
            return
        for k in list(cands):
            if delta_e(k, rgb) < 4:
                cands[k] += w
                return
        cands[rgb] = cands.get(rgb, 0) + w

    for group in ("bg", "fg", "border"):
        entries = raw.get(group) or []
        total = sum(w for _, w in entries) or 1
        scale = {"bg": 1.0, "fg": 0.8, "border": 0.3}[group]
        for colour, w in entries:
            add(colour, scale * w / total)

    atc_style = (facts.get("atc") or {}).get("style") or {}
    role_src = {
        "bg": page_bg,
        "text": _rgb((raw.get("body") or {}).get("color"), page_bg),
        "heading": _rgb((raw.get("h1") or {}).get("color"), page_bg),
        "link": _rgb((raw.get("link") or {}).get("color"), page_bg),
        "atc_bg": _rgb(atc_style.get("bg"), page_bg) if atc_style.get("bg") and not str(atc_style.get("bg")).endswith(", 0)") else None,
        "atc_fg": _rgb(atc_style.get("color"), page_bg),
        "atc_border": _rgb((atc_style.get("border") or "").split(" ", 2)[-1], page_bg)
        if atc_style.get("border") and not str(atc_style.get("border")).startswith(("0px", "0 ")) else None,
    }
    for v in role_src.values():
        if v:
            add(v, 0.05)

    palette = sorted(cands.items(), key=lambda kv: -kv[1])
    keep = [(rgb, w) for rgb, w in palette if w >= 0.01][:12]
    for v in role_src.values():  # role colours always survive
        if v and not any(delta_e(v, k) < 4 for k, _ in keep):
            keep.append((v, 0.0))
    t.palette = [{"hex": to_hex(rgb), "weight": round(w, 4)} for rgb, w in keep]

    def snap(rgb):
        return min((to_hex(k) for k, _ in keep), key=lambda h: delta_e(parse_color(h)[:3], rgb))

    # ---- roles
    text = role_src["text"] or (34, 34, 34)
    primary = None
    if role_src["atc_bg"] and delta_e(role_src["atc_bg"], page_bg) > 5:
        primary = role_src["atc_bg"]
    elif role_src["atc_border"] and delta_e(role_src["atc_border"], page_bg) > 5:
        primary = role_src["atc_border"]
        t.notes.append("Primary CTA is an outline button; primary colour taken from its border.")
    else:
        chromatic = [k for k, _ in keep if saturation(k) > 0.25 and delta_e(k, page_bg) > 10]
        primary = chromatic[0] if chromatic else text
        t.notes.append("No filled add-to-cart found; primary colour inferred from the most used accent.")
    on_primary = role_src["atc_fg"] if role_src["atc_bg"] else None
    if not on_primary or contrast_ratio(on_primary, primary) < 2:
        on_primary = page_bg if contrast_ratio(page_bg, primary) >= contrast_ratio(text, primary) else text

    bg_lab = rgb_to_lab(page_bg)
    surface = next((k for k, _ in keep if 3 < delta_e(k, page_bg) < 25 and abs(rgb_to_lab(k)[0] - bg_lab[0]) < 15), page_bg)
    border_entries = raw.get("border") or []
    border = _rgb(border_entries[0][0], page_bg) if border_entries else surface
    muted = next((k for k, _ in keep if k != text and delta_e(k, text) > 8 and saturation(k) < 0.2
                  and contrast_ratio(k, page_bg) >= 3 and contrast_ratio(k, page_bg) < contrast_ratio(text, page_bg)), text)
    accent = next((k for k, _ in keep if saturation(k) > 0.3 and delta_e(k, primary) > 15 and delta_e(k, page_bg) > 10), primary)

    t.roles = {k: snap(v) for k, v in {
        "primary": primary, "on_primary": on_primary, "text": text, "heading": role_src["heading"] or text,
        "muted": muted, "bg": page_bg, "surface": surface, "border": border, "accent": accent,
        "link": role_src["link"] or text}.items()}

    # ---- fonts
    body_fam = (raw.get("body") or {}).get("family") or ""
    head_fam = (raw.get("h1") or {}).get("family") or body_fam
    btn_fam = atc_style.get("fontFamily") or body_fam
    t.fonts = {"body": body_fam, "heading": head_fam, "button": btn_fam}
    names = []
    for stack in [body_fam, head_fam, btn_fam] + [f for f, _ in raw.get("families") or []]:
        for n in family_names(stack):
            if n not in names:
                names.append(n)
    t.font_names = names

    # ---- type scale
    sizes = raw.get("sizes") or []
    total = sum(w for _, w in sizes) or 1
    scale = {_snap_size(s) for s, w in sizes if w / total >= 0.02}
    role_sizes = {
        "base": _snap_size((raw.get("body") or {}).get("size", "16px")),
        "h1": _snap_size((raw.get("h1") or {}).get("size", "0")) if raw.get("h1") else 0,
        "h2": _snap_size((raw.get("h2") or {}).get("size", "0")) if raw.get("h2") else 0,
        "button": _snap_size(atc_style.get("fontSize", "0")) if atc_style else 0,
    }
    scale |= {v for v in role_sizes.values() if v}
    scale = sorted(v for v in scale if 9 <= v <= 96)
    t.sizes = scale
    base = role_sizes["base"] or (scale[len(scale) // 2] if scale else 16)
    smaller = [v for v in scale if v < base]
    larger = [v for v in scale if v > base]
    t.size_roles = {
        "xs": smaller[0] if smaller else base, "sm": smaller[-1] if smaller else base, "base": base,
        "lg": larger[0] if larger else base, "xl": role_sizes["h2"] or (larger[min(1, len(larger) - 1)] if larger else base),
        "h1": role_sizes["h1"] or (larger[-1] if larger else base), "button": role_sizes["button"] or base,
    }
    t.line_height = (raw.get("body") or {}).get("lineHeight") or "1.5"
    if t.line_height == "normal":
        t.line_height = "1.5"

    # ---- shape / button style
    t.radius = {"button": atc_style.get("borderRadius") or "0px",
                "input": (raw.get("input") or {}).get("radius") or atc_style.get("borderRadius") or "0px"}
    atc_rect = (facts.get("atc") or {}).get("rect") or {}
    t.button = {"transform": atc_style.get("textTransform") or "none", "spacing": atc_style.get("letterSpacing") or "normal",
                "weight": atc_style.get("fontWeight") or "600", "padding": _clean_padding(atc_style.get("padding")),
                "height": f"{max(44, min(int(atc_rect.get('height') or 48), 64))}px"}
    t.logo = raw.get("logo")
    return t


def _clean_padding(p):
    """Horizontal padding only; vertical size comes from --cro-btn-height (min-height)."""
    vals = [float(v[:-2]) for v in re.findall(r"[\d.]+px", p or "")]
    horiz = vals[1] if len(vals) >= 2 else (vals[0] if vals else 20)
    return f"0 {max(16, min(horiz, 40)):g}px"


def from_screenshot(palette: list[dict], vision: dict) -> BrandTokens:
    """palette: [{hex, weight}] from pixels. vision: brand block returned by the vision model. Every role is snapped
    to the nearest sampled pixel colour, so the model cannot invent a colour."""
    from .colors import nearest
    t = BrandTokens(source="screenshot", palette=palette)
    roles = vision.get("roles", {})
    hexes = [p["hex"] for p in palette]

    def ok(h, fallback):
        c = parse_color(h or "")
        return nearest(c[:3], hexes)[0] if c and hexes else fallback

    bg = ok(roles.get("bg"), hexes[0])
    text = ok(roles.get("text"), hexes[-1])
    primary = ok(roles.get("primary"), text)
    t.roles = {"primary": primary, "on_primary": ok(roles.get("on_primary"), bg), "text": text,
               "heading": ok(roles.get("heading"), text), "muted": ok(roles.get("muted"), text), "bg": bg,
               "surface": ok(roles.get("surface"), bg), "border": ok(roles.get("border"), ok(roles.get("surface"), bg)),
               "accent": ok(roles.get("accent"), primary), "link": ok(roles.get("link"), text)}
    f = vision.get("fonts", {})
    head = f.get("heading_font") or "Inter"
    body = f.get("body_font") or "Inter"
    head_stack = f"'{head}', {f.get('heading_generic', 'sans-serif')}"
    body_stack = f"'{body}', {f.get('body_generic', 'sans-serif')}"
    t.fonts = {"body": body_stack, "heading": head_stack, "button": body_stack}
    t.font_names = list(dict.fromkeys([head, body]))
    fams = "&".join("family=" + n.replace(" ", "+") + ":wght@400;500;600;700" for n in t.font_names)
    t.google_font_url = f"https://fonts.googleapis.com/css2?{fams}&display=swap"
    sz = vision.get("type_scale_px", {})
    base = float(sz.get("body") or 15)
    roles_px = {"xs": float(sz.get("small") or base - 2), "sm": float(sz.get("small") or base - 1), "base": base,
                "lg": float(sz.get("price") or base + 2), "xl": float(sz.get("section_heading") or base + 6),
                "h1": float(sz.get("product_title") or base + 8), "button": float(sz.get("button") or base)}
    t.size_roles = {k: round(v * 2) / 2 for k, v in roles_px.items()}
    t.sizes = sorted(set(t.size_roles.values()))
    t.radius = {"button": f"{int(vision.get('button_radius_px') or 0)}px", "input": f"{int(vision.get('button_radius_px') or 0)}px"}
    t.button = {"transform": "uppercase" if vision.get("button_uppercase") else "none", "spacing": "normal",
                "weight": "600", "padding": "0 24px", "height": f"{int(vision.get('button_height_px') or 48)}px"}
    t.notes.append("Screenshot mode: colours are exact (sampled from pixels); fonts and sizes are visual estimates.")
    return t
