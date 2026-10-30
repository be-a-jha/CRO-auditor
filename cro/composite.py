"""Screenshot mode mockup: the screenshot itself, cut into sections at blank rows, with brand-styled components
inserted between them. Every original pixel stays exactly as the brand designed it, and no model tokens are spent
re-writing the page as HTML. Desktop two-column areas (gallery | buy box) are split into columns so a block meant
for the buy box lands under the add-to-cart, not across the gallery."""
from __future__ import annotations

import base64
import html as htmllib
import re
from pathlib import Path

import numpy as np
from PIL import Image

from . import components as comp
from .guard import GuardContext, guard_css, guard_html
from .patcher import BASE_CSS, REVIEW_UI


def css_width(view: str, w: int) -> int:
    if view == "mobile":
        return 390 if w >= 700 else w
    return w if w <= 2000 else w // 2


class View:
    def __init__(self, view: str, path: Path, sections: list[dict], scale: float):
        self.view, self.path = view, path
        self.img = Image.open(path).convert("RGB")
        self.W, self.H = self.img.size
        g = np.asarray(self.img.convert("L"), dtype=np.int16)
        self.gray = g
        self.secs = []
        for s in sections:
            if s.get("view") != view:
                continue
            y0, y1 = sorted((int(s.get("y_top", 0) / scale), int(s.get("y_bottom", 0) / scale)))
            x0, x1 = sorted((int(s.get("x_left", 0) / scale), int(s.get("x_right", 0) / scale)))
            y0, y1 = max(0, y0), min(self.H, y1)
            if y1 - y0 < 8:
                continue
            x1 = x1 if x1 - x0 > 20 else self.W
            self.secs.append({**s, "y0": y0, "y1": y1, "x0": max(0, x0), "x1": min(self.W, x1)})
        self.secs.sort(key=lambda s: s["y0"])
        self.win = max(30, int(self.H * 0.012))

    def blank_rows(self, x0=0, x1=None):
        g = self.gray[:, x0:x1 or self.W]
        return (g.max(axis=1) - g.min(axis=1)) <= 12

    def snap_y(self, y: int, x0=0, x1=None) -> int:
        y = max(0, min(self.H, y))
        b = self.blank_rows(x0, x1)
        lo, hi = max(0, y - self.win), min(self.H - 1, y + self.win)
        cand = np.nonzero(b[lo:hi + 1])[0]
        return int(lo + cand[np.argmin(np.abs(cand + lo - y))]) if len(cand) else y

    def snap_x(self, x: int, y0: int, y1: int) -> int:
        g = self.gray[y0:y1, :]
        b = (g.max(axis=0) - g.min(axis=0)) <= 12
        lo, hi = max(0, x - 80), min(self.W - 1, x + 80)
        cand = np.nonzero(b[lo:hi + 1])[0]
        return int(lo + cand[np.argmin(np.abs(cand + lo - x))]) if len(cand) else x

    def band_of(self, sec):
        """The two-column area a left/right section sits in: the contiguous run of non-full sections around it."""
        i = self.secs.index(sec)
        j = i
        while j > 0 and self.secs[j - 1].get("layout") in ("left", "right"):
            j -= 1
        k = i
        while k < len(self.secs) - 1 and self.secs[k + 1].get("layout") in ("left", "right"):
            k += 1
        run = self.secs[j:k + 1]
        top, bot = min(s["y0"] for s in run), max(s["y1"] for s in run)
        rights = [s["x0"] for s in run if s.get("layout") == "right"]
        lefts = [s["x1"] for s in run if s.get("layout") == "left"]
        split = min(rights) if rights else max(lefts) if lefts else self.W // 2
        top, bot = self.snap_y(top), self.snap_y(bot)
        return top, bot, self.snap_x(split, top, max(top + 1, bot))


def _save(img: Image.Image, box, dest: Path, max_w: int) -> tuple[str, int, int]:
    part = img.crop(box)
    if part.width > max_w:
        part = part.resize((max_w, max(1, int(part.height * max_w / part.width))), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part.save(dest, "JPEG", quality=84, optimize=True)
    return dest.name, part.width, part.height


def build(views: list[View], patches: list[dict], page: dict, ctx: GuardContext, tokens, outdir: Path,
          title: str) -> tuple[str, list, list]:
    applied, rejected, css_out = [], [], []
    by_id = {s["id"]: (v, s) for v in views for s in v.secs}
    inserts = {v.view: {"full": [], "col": []} for v in views}
    moves = {v.view: [] for v in views}
    seen = set()
    for i, p in enumerate(patches, 1):
        rec, op = p.get("rec_id") or f"P{i}", p.get("op")
        hit = by_id.get(str(p.get("target") or "").strip())

        def reject(reason):
            rejected.append({**{k: p.get(k) for k in ("rec_id", "op", "component", "target")}, "reason": reason})
            ctx.note(rec, "target", f"{reason}; patch skipped")

        if op in ("layout", "replace_text"):
            reject(f"{op} is not possible on a screenshot")
            continue
        if hit is None:
            reject(f"section '{p.get('target')}' not found")
            continue
        v, sec = hit
        if op == "move":
            src = by_id.get(str(p.get("source") or "").strip())
            if not src or src[0] is not v or src[1].get("layout") != "full" or sec.get("layout") != "full":
                reject("move needs a full-width source and target section in the same view")
                continue
            moves[v.view].append((src[1], sec, p.get("position") in ("before", "prepend"), rec))
            applied.append({k: p.get(k) for k in ("rec_id", "op", "target", "source")})
            continue
        extra_css: list = []
        if op == "component":
            name = p.get("component") or ""
            if (v.view, name) in seen:
                reject(f"component {name} already added")
                continue
            out = comp.render(name, p, page)
            if out is None:
                reject(f"component '{name}' unknown or had nothing valid to show")
                continue
            html, css, allow = out
            if name == "sticky_atc":
                html = html.replace('href="#cro-atc"', 'href="#"')
            seen.add((v.view, name))
        elif op == "custom":
            html, css, allow = p.get("html") or "", p.get("css") or "", set()
        else:
            reject(f"unknown op {op}")
            continue
        cleaned = guard_html(html, ctx, rec, allow_numbers=allow, extra_css=extra_css)
        if not cleaned:
            rejected.append({"rec_id": rec, "op": op, "target": p.get("target"), "reason": "dropped by claim/brand guard"})
            continue
        css_out.append(guard_css("\n".join([css] + extra_css), ctx, rec, scoped=True))
        fixed = " cro-ins--fixed" if p.get("component") == "sticky_atc" else ""
        block = f'<div class="cro-ins{fixed}"><div class="cro-change" data-cro-rec="{htmllib.escape(rec)}">{cleaned}</div></div>'
        before = p.get("position") in ("before", "prepend")
        if sec.get("layout") in ("left", "right") and v.view == "desktop":
            top, bot, split = v.band_of(sec)
            x0, x1 = (0, split) if sec["layout"] == "left" else (split, v.W)
            y = min(max(v.snap_y(sec["y0"] if before else sec["y1"], x0, x1), top), bot)
            inserts[v.view]["col"].append({"band": (top, bot, split), "side": sec["layout"], "y": y, "html": block})
        else:
            y = v.snap_y(sec["y0"] if before else sec["y1"])
            inserts[v.view]["full"].append({"y": y, "html": block})
        applied.append({k: p.get(k) for k in ("rec_id", "op", "component", "target", "position")})

    parts = []
    for v in views:
        parts.append(_render_view(v, inserts[v.view], moves[v.view], outdir, len(views) > 1))
    n = len(applied)
    font_link = f'<link rel="stylesheet" href="{htmllib.escape(tokens.google_font_url)}">' if tokens.google_font_url else ""
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{htmllib.escape(title)} (optimised mockup)</title>
{font_link}
<style id="cro-tokens">{tokens.css_vars()}{BASE_CSS}
body{{margin:0;background:var(--cro-bg);color:var(--cro-text);font-family:var(--cro-font-body);font-size:var(--cro-fs-base);line-height:var(--cro-line)}}
.cro-shot{{margin:0 auto}}.cro-shot img{{display:block;width:100%;height:auto}}
.cro-shot--mobile{{max-width:{css_width('mobile', 390)}px}}
.cro-ins{{background:var(--cro-bg);padding:4px 16px}}
.cro-shot--desktop .cro-ins{{padding:8px 24px}}
.cro-band{{display:flex;align-items:flex-start}}.cro-col{{min-width:0}}
{_view_switch(views)}
.cro-ins--fixed{{padding:0}}
</style>
<style id="cro-patches">{chr(10).join(c for c in dict.fromkeys(css_out) if c)}</style></head>
<body>{''.join(parts)}{REVIEW_UI.replace("__N__", str(n))}</body></html>"""
    return inline_assets(doc, outdir), applied, rejected


def inline_assets(doc: str, outdir: Path) -> str:
    """Embed the screenshot sections as data URIs, so optimized.html is one file that can be downloaded and sent."""
    def sub(m):
        f = outdir / "assets" / m.group(1)
        if not f.exists():
            return m.group(0)
        return 'src="data:image/jpeg;base64,' + base64.b64encode(f.read_bytes()).decode() + '"'
    return re.sub(r'src="assets/([\w.-]+\.jpg)"', sub, doc)


def _view_switch(views) -> str:
    if len(views) < 2:
        return ""
    return ("@media (max-width:749px){.cro-shot--desktop{display:none}}"
            "@media (min-width:750px){.cro-shot--mobile{display:none}}")


def _render_view(v: View, ins: dict, moves: list, outdir: Path, both: bool) -> str:
    max_w = 780 if v.view == "mobile" else min(v.W, 2000)
    assets = outdir / "assets"
    prefix = v.view[0]
    # merge overlapping two-column bands
    bands = []
    for c in sorted(ins["col"], key=lambda c: c["band"][0]):
        t, b, s = c["band"]
        if bands and t < bands[-1]["bot"]:
            bands[-1]["bot"] = max(bands[-1]["bot"], b)
            bands[-1]["items"].append(c)
        else:
            bands.append({"top": t, "bot": b, "split": s, "items": [c]})
    cuts = {0, v.H}
    for c in ins["full"]:
        y = c["y"]
        for bd in bands:  # a full-width insert can't split a two-column band: move it to the band edge
            if bd["top"] < y < bd["bot"]:
                y = bd["top"] if y - bd["top"] < bd["bot"] - y else bd["bot"]
        c["y"] = y
        cuts.add(y)
    for bd in bands:
        cuts |= {bd["top"], bd["bot"]}
    for src, tgt, _, _ in moves:
        cuts |= {v.snap_y(src["y0"]), v.snap_y(src["y1"]), v.snap_y(tgt["y0"]), v.snap_y(tgt["y1"])}
    ys = sorted(c for c in cuts if 0 <= c <= v.H)
    items = []  # ordered: ("seg", top, bot, html) or ("ins", y, html)
    k = 0
    for a, b in zip(ys, ys[1:]):
        bd = next((x for x in bands if x["top"] == a and x["bot"] == b), None)
        if bd:
            items.append(("seg", a, b, _band_html(v, bd, assets, prefix, max_w)))
        elif b - a >= 2:
            k += 1
            name, w, h = _save(v.img, (0, a, v.W, b), assets / f"{prefix}_{k}.jpg", max_w)
            items.append(("seg", a, b, f'<img src="assets/{name}" width="{w}" height="{h}" alt="">'))
        for c in ins["full"]:
            if c["y"] == b:
                items.append(("ins", b, c["html"]))
    for c in ins["full"]:
        if c["y"] == 0:
            items.insert(0, ("ins", 0, c["html"]))
    for src, tgt, before, _ in moves:
        s0, s1 = v.snap_y(src["y0"]), v.snap_y(src["y1"])
        run = [it for it in items if it[0] == "seg" and it[1] >= s0 and it[2] <= s1]
        if not run:
            continue
        rest = [it for it in items if it not in run]
        t = v.snap_y(tgt["y0"] if before else tgt["y1"])
        idx = next((i for i, it in enumerate(rest) if it[0] == "seg" and it[1] >= t), len(rest))
        for it in run:
            it_html = it[3].replace("<img ", '<img class="cro-moved" ', 1)
            rest.insert(idx, ("seg", it[1], it[2], it_html))
            idx += 1
        items = rest
    body = "".join(it[3] if it[0] == "seg" else it[2] for it in items)
    return f'<div class="cro-shot cro-shot--{v.view}">{body}</div>'


def _band_html(v: View, bd: dict, assets: Path, prefix: str, max_w: int) -> str:
    split, top, bot = bd["split"], bd["top"], bd["bot"]
    cols = []
    for side, (x0, x1) in (("left", (0, split)), ("right", (split, v.W))):
        ys = sorted({top, bot} | {c["y"] for c in bd["items"] if c["side"] == side and top < c["y"] < bot})
        html = "".join(c["html"] for c in bd["items"] if c["side"] == side and c["y"] <= top)
        for j, (a, b) in enumerate(zip(ys, ys[1:])):
            if b - a >= 2:
                name, w, h = _save(v.img, (x0, a, x1, b), assets / f"{prefix}_b{top}_{side}{j}.jpg",
                                   max(1, int(max_w * (x1 - x0) / v.W)))
                html += f'<img src="assets/{name}" width="{w}" height="{h}" alt="">'
            html += "".join(c["html"] for c in bd["items"] if c["side"] == side and c["y"] == b)
        cols.append(f'<div class="cro-col" style="width:{100 * (x1 - x0) / v.W:.3f}%">{html}</div>')
    return f'<div class="cro-band">{"".join(cols)}</div>'
