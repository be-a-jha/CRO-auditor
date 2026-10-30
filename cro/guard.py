"""Brand guard + claim guard. Every patch passes through here before it touches the page.

Enforced in code, not in the prompt:
  * colours      -> snapped to the extracted brand palette (exact hex), alpha preserved
  * font-family  -> must be a brand font or a --cro-font-* var, else replaced with the body font
  * font-size    -> snapped to the brand's own type scale
  * border-radius-> snapped to the brand's own radii
  * scope        -> CSS may only target .cro-* classes (no restyling of existing brand elements)
  * add_class    -> classes added to existing elements may carry layout properties only
  * no new fonts / @import / scripts / iframes / external images
  * claims       -> numbers not present on the original page become visible [placeholders];
                    invented urgency (timers, "only 3 left") drops the whole patch
"""
from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass, field

import tinycss2
from bs4 import BeautifulSoup, NavigableString, Comment

from .colors import find_color_literals, nearest, parse_color
from .tokens import BrandTokens, family_names

COLOR_PROP = re.compile(r"(color|^background|^border|^outline|shadow$|^fill$|^stroke$|^text-decoration|^column-rule)", re.I)
LAYOUT_PROPS = {
    "display", "position", "top", "right", "bottom", "left", "z-index", "order", "flex", "flex-grow", "flex-shrink",
    "flex-basis", "flex-direction", "flex-wrap", "align-items", "align-self", "justify-content", "gap", "row-gap",
    "column-gap", "grid-template-columns", "grid-template-rows", "grid-column", "grid-row", "width", "min-width",
    "max-width", "height", "min-height", "max-height", "margin", "margin-top", "margin-bottom", "margin-left",
    "margin-right", "padding", "padding-top", "padding-bottom", "padding-left", "padding-right", "overflow",
    "visibility", "white-space", "text-align", "vertical-align", "box-sizing", "aspect-ratio",
}
URGENCY = re.compile(r"only \d+ (left|remaining)|selling fast|hurry|limited (stock|time)|ends in|offer ends|"
                     r"\d+ (people|others|shoppers) (are )?(viewing|watching|bought|looking)|last chance|almost gone|"
                     r"countdown|few left|going fast", re.I)
NUM = re.compile(r"(?:₹|\brs\.?\s?|\binr\s?|\$|€|£)?\s?\d[\d,]*(?:\.\d+)?\s?(?:%|\+|k\b|lakh|cr\b|x\b)?", re.I)
BRACKET = re.compile(r"\[[^\[\]]{1,80}\]")
BAD_TAGS = ("script", "iframe", "object", "embed", "link", "meta", "base", "frame", "frameset", "applet", "portal")


@dataclass
class GuardContext:
    tokens: BrandTokens
    original_text: str
    allowed_images: set = field(default_factory=set)
    log: list = field(default_factory=list)

    def __post_init__(self):
        self.digits = {re.sub(r"[,\s]", "", m) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", self.original_text)}
        self.text_lc = re.sub(r"\s+", " ", self.original_text.lower())
        self.font_lc = {f.lower() for f in self.tokens.font_names}
        self.radii = sorted({_px(v) for v in self.tokens.radius.values() if _px(v) is not None} | {0.0})

    def note(self, rec, kind, detail):
        self.log.append({"rec_id": rec, "kind": kind, "detail": detail})


def _px(v, base=16.0):
    m = re.match(r"^\s*(-?[\d.]+)\s*(px|rem|em|pt)?\s*$", str(v))
    if not m:
        return None
    n, unit = float(m.group(1)), (m.group(2) or "px")
    return n * {"px": 1, "rem": 16, "em": base, "pt": 4 / 3}[unit]


# ---------------------------------------------------------------- declarations
def guard_decl(name: str, value: str, ctx: GuardContext, rec: str, layout_only=False) -> str | None:
    n = name.lower().strip()
    v = value.strip()
    if layout_only and n not in LAYOUT_PROPS:
        ctx.note(rec, "scope", f"'{n}' not allowed on an existing brand element (layout properties only)")
        return None
    if n == "font":
        ctx.note(rec, "font", "font shorthand removed (use --cro-font-* and --cro-fs-* vars)")
        return None
    if n == "font-family":
        if "var(--cro-font" in v:
            return v
        fams = family_names(v)
        if fams and all(f.lower() in ctx.font_lc for f in fams):
            return v
        ctx.note(rec, "font", f"font-family '{v}' is not a brand font, replaced with brand body font")
        return "var(--cro-font-body)"
    if n == "font-size":
        if v.startswith("var(--cro-fs"):
            return v
        px = _px(v, base=ctx.tokens.size_roles.get("base", 16))
        sizes = ctx.tokens.sizes or [ctx.tokens.size_roles.get("base", 16)]
        if px is None:
            ctx.note(rec, "size", f"font-size '{v}' not on the brand scale, set to brand base size")
            return "var(--cro-fs-base)"
        snap = min(sizes, key=lambda s: abs(s - px))
        if abs(snap - px) > 0.5:
            ctx.note(rec, "size", f"font-size {px:g}px snapped to brand size {snap:g}px")
        return f"{snap:g}px"
    if n in ("border-radius",) or n.endswith("-radius"):
        if "var(--cro-radius" in v or "%" in v:
            return v
        out = []
        for part in v.split():
            px = _px(part)
            if px is None:
                out.append(part)
                continue
            snap = min(ctx.radii + ([9999.0] if any(r >= 100 for r in ctx.radii) else []), key=lambda r: abs(r - px))
            if abs(snap - px) > 2:
                ctx.note(rec, "radius", f"radius {px:g}px snapped to brand radius {snap:g}px")
            out.append(f"{snap:g}px")
        return " ".join(out)
    if "url(" in v.lower():
        for u in re.findall(r"url\(\s*['\"]?([^'\")]+)", v, re.I):
            if u.startswith("data:image/svg") or _norm_img(u) in ctx.allowed_images:
                continue
            ctx.note(rec, "asset", f"external image {u[:80]} removed (only the brand's own imagery is allowed)")
            return None
    if n == "content":
        return _guard_content(v, ctx, rec)
    if COLOR_PROP.search(n):
        return _snap_colors(v, ctx, rec, n)
    return v


def _snap_colors(v: str, ctx: GuardContext, rec: str, prop: str) -> str:
    pal = ctx.tokens.palette_hex
    for lit in sorted(set(find_color_literals(v)), key=len, reverse=True):
        c = parse_color(lit)
        if c is None or c[3] == 0:
            continue
        hexv, d = nearest(c[:3], pal)
        if d > 2.5:
            ctx.note(rec, "color", f"{prop}: {lit} is off-palette, snapped to brand {hexv} (dE {d:.0f})")
        if c[3] < 1:
            p = parse_color(hexv)
            rep = f"rgba({p[0]}, {p[1]}, {p[2]}, {c[3]:g})"
        else:
            rep = hexv
        v = re.sub(r"(?<![\w#-])" + re.escape(lit) + r"(?![\w-])", rep, v)
    return v


def _guard_content(v, ctx, rec):
    text = re.sub(r"^['\"]|['\"]$", "", v)
    if URGENCY.search(text):
        ctx.note(rec, "claim", f"pseudo-element urgency text removed: {text[:60]}")
        return None
    for m in NUM.finditer(text):
        tok = re.sub(r"[^\d.]", "", m.group(0))
        if tok and tok not in ctx.digits:
            ctx.note(rec, "claim", f"pseudo-element number '{m.group(0).strip()}' not on page, removed")
            return None
    return v


def _norm_img(u: str) -> str:
    return re.sub(r"^https?:", "", (u or "").split("?")[0].strip())


# ---------------------------------------------------------------- stylesheets
def guard_css(css: str, ctx: GuardContext, rec: str, scoped=True, layout_classes: set | None = None) -> str:
    if not css or not css.strip():
        return ""
    rules = tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
    return _guard_rules(rules, ctx, rec, scoped, layout_classes or set(), in_keyframes=False)


def _guard_rules(rules, ctx, rec, scoped, layout_classes, in_keyframes):
    out = []
    for r in rules:
        if r.type == "error":
            ctx.note(rec, "css", f"unparseable CSS dropped: {r.message}")
            continue
        if r.type == "at-rule":
            kw = r.lower_at_keyword
            if kw in ("media", "supports", "container"):
                inner = tinycss2.parse_rule_list(r.content or [], skip_comments=True, skip_whitespace=True)
                body = _guard_rules(inner, ctx, rec, scoped, layout_classes, False)
                if body:
                    out.append(f"@{kw} {tinycss2.serialize(r.prelude).strip()}{{{body}}}")
            elif kw in ("keyframes", "-webkit-keyframes"):
                inner = tinycss2.parse_rule_list(r.content or [], skip_comments=True, skip_whitespace=True)
                body = _guard_rules(inner, ctx, rec, False, set(), True)
                out.append(f"@{kw} {tinycss2.serialize(r.prelude).strip()}{{{body}}}")
            else:
                ctx.note(rec, "css", f"@{kw} not allowed (no new fonts, imports or global at-rules)")
            continue
        if r.type != "qualified-rule":
            continue
        sel = tinycss2.serialize(r.prelude).strip()
        parts = [p.strip() for p in sel.split(",") if p.strip()]
        layout_only = False
        if scoped and not in_keyframes:
            bad = [p for p in parts if ".cro-" not in p]
            if bad:
                ctx.note(rec, "scope", f"rule '{sel[:80]}' targets brand elements directly, dropped")
                continue
            # a rule whose subject is a class added to an existing brand element: layout only
            subjects = {m for p in parts for m in re.findall(r"\.(cro-[\w-]+)", p.split()[-1])}
            layout_only = bool(subjects & layout_classes)
        decls = tinycss2.parse_declaration_list(r.content, skip_comments=True, skip_whitespace=True)
        kept = []
        for d in decls:
            if d.type != "declaration":
                continue
            val = guard_decl(d.name, tinycss2.serialize(d.value), ctx, rec, layout_only=layout_only)
            if val is not None:
                kept.append(f"{d.name}:{val}{' !important' if d.important else ''}")
        if kept:
            out.append(f"{sel}{{{';'.join(kept)}}}")
    return "\n".join(out)


def guard_inline_style(style: str, ctx: GuardContext, rec: str) -> str:
    decls = tinycss2.parse_declaration_list(style, skip_comments=True, skip_whitespace=True)
    kept = []
    for d in decls:
        if d.type != "declaration":
            continue
        val = guard_decl(d.name, tinycss2.serialize(d.value), ctx, rec)
        if val is not None:
            kept.append(f"{d.name}:{val}{' !important' if d.important else ''}")
    return ";".join(kept)


# ---------------------------------------------------------------- html fragments
def guard_html(fragment: str, ctx: GuardContext, rec: str, allow_numbers: set | None = None,
               extra_css: list | None = None) -> str | None:
    """Return cleaned HTML, or None when the fragment must be dropped entirely."""
    soup = BeautifulSoup(fragment or "", "html.parser")
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for t in soup.find_all(BAD_TAGS):
        ctx.note(rec, "tag", f"<{t.name}> removed")
        t.decompose()
    for st in soup.find_all("style"):
        if extra_css is not None and st.string:
            extra_css.append(st.string)
        st.decompose()
    for t in soup.find_all(True):
        for a in list(t.attrs):
            al = a.lower()
            if al.startswith("on") or al in ("srcdoc", "formaction"):
                del t.attrs[a]
            elif al in ("href", "src", "action") and str(t.attrs[a]).strip().lower().startswith("javascript:"):
                del t.attrs[a]
        if t.name == "form":
            t.attrs.pop("action", None)
            t["onsubmit"] = "return false"
        if t.name in ("img", "source"):
            src = t.get("src") or ""
            if src and not src.startswith("data:image/svg") and _norm_img(src) not in ctx.allowed_images:
                ctx.note(rec, "asset", f"image {src[:80]} is not one of the brand's own images, removed")
                t.decompose()
                continue
            if t.get("srcset"):
                parts = [p.strip().split()[0] for p in t["srcset"].split(",") if p.strip()]
                if not all(_norm_img(u) in ctx.allowed_images for u in parts):
                    del t["srcset"]
        for attr in ("fill", "stroke", "stop-color", "color"):
            if t.get(attr) and parse_color(t[attr]):
                t[attr] = _snap_colors(t[attr], ctx, rec, attr)
        if t.get("style"):
            t["style"] = guard_inline_style(t["style"], ctx, rec)
    # claims
    full_text = soup.get_text(" ")
    m = URGENCY.search(full_text)
    if m and m.group(0).lower() not in ctx.text_lc:
        ctx.note(rec, "claim", f"invented urgency '{m.group(0)}' (CCPA dark-pattern risk), patch dropped")
        return None
    allow = {re.sub(r"[,\s]", "", a) for a in (allow_numbers or set())}
    for node in list(soup.find_all(string=True)):
        if not isinstance(node, NavigableString) or node.parent is None or node.parent.name in ("style", "script"):
            continue
        if node.parent.get("class") and "cro-placeholder" in node.parent.get("class"):
            continue
        new = _mark_claims(str(node), ctx, rec, allow)
        if new != htmllib.escape(str(node), quote=False):
            node.replace_with(BeautifulSoup(new, "html.parser"))
    return str(soup)


def _mark_claims(text: str, ctx: GuardContext, rec: str, allow: set) -> str:
    out, pos = [], 0
    spans = []
    for m in BRACKET.finditer(text):
        spans.append((m.start(), m.end(), "bracket"))
    for m in NUM.finditer(text):
        if any(s <= m.start() < e for s, e, _ in spans):
            continue
        raw = m.group(0).strip()
        digits = re.sub(r"[^\d.]", "", raw).rstrip(".")
        if not digits or not re.search(r"\d", digits):
            continue
        if digits in ctx.digits or digits in allow:
            continue
        spans.append((m.start() + (len(m.group(0)) - len(m.group(0).lstrip())), m.end(), "number"))
        ctx.note(rec, "claim", f"'{raw}' does not appear on the original page, marked as placeholder for the brand to confirm")
    for s, e, kind in sorted(spans):
        if s < pos:
            continue
        out.append(htmllib.escape(text[pos:s], quote=False))
        seg = htmllib.escape(text[s:e], quote=False)
        title = "Placeholder: brand to fill in" if kind == "bracket" else "Not found on the original page: brand to confirm"
        out.append(f'<span class="cro-placeholder" title="{title}">{seg}</span>')
        pos = e
    out.append(htmllib.escape(text[pos:], quote=False))
    return "".join(out)
