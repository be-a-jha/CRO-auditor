"""Applies guarded patch operations to the frozen page, plus rule-based fallback patches (no API key needed).

Patch format (same for the model and the rules engine):
  {rec_id, op, component, target, position, viewport, heading, label, items, source, css, html}
  op        component | move | layout | replace_text | custom
  position  before | after | prepend | append   (where the new block goes relative to target; for move: before/after)
  viewport  all | mobile | desktop              (new blocks can be shown on one viewport only)
"""
from __future__ import annotations

import re

import tinycss2
from bs4 import BeautifulSoup

from . import components as comp
from .guard import GuardContext, guard_css, guard_html

OPS = ("component", "move", "layout", "replace_text", "custom")
POSITIONS = ("before", "after", "prepend", "append")
VIEWPORTS = ("all", "mobile", "desktop")

BASE_CSS = """
.cro-change{display:contents}
.cro-change.cro-c{display:block;width:100%;max-width:100%;min-width:0;flex:0 0 100%;grid-column:1/-1;float:none;clear:both;box-sizing:border-box}
.cro-c *,.cro-c *::before,.cro-c *::after{box-sizing:border-box}
.cro-c ul,.cro-c ol{list-style:none;margin:0;padding:0}.cro-c li::before,.cro-c li::marker{content:none}
.cro-c input,.cro-c button{margin:0;width:auto;max-width:100%}.cro-c label{float:none;width:auto}
.cro-placeholder{outline:1px dashed currentColor;outline-offset:2px;border-radius:2px}
.cro-visually-hidden{position:absolute!important;width:1px!important;height:1px!important;overflow:hidden!important;clip:rect(0 0 0 0)!important}
@media (min-width:750px){.cro-vp-mobile{display:none!important}}
@media (max-width:749px){.cro-vp-desktop{display:none!important}}
"""

REVIEW_UI = """
<style id="cro-review-ui">
html.cro-hl .cro-change>*,html.cro-hl .cro-changed,html.cro-hl .cro-moved{outline:2px dashed #ff00a8!important;outline-offset:3px}
#cro-review{position:fixed;left:12px;bottom:12px;z-index:2147483647;font:12px/1.3 -apple-system,Segoe UI,Roboto,Arial,sans-serif;
 background:#111;color:#fff;border-radius:999px;padding:8px 14px;box-shadow:0 4px 18px rgba(0,0,0,.25);cursor:pointer;user-select:none}
#cro-review b{color:#ff7ad1}
@media (max-width:749px){#cro-review{bottom:auto;top:12px}}
</style>
<div id="cro-review" title="Reviewer overlay, not part of the page. Add ?clean to the URL to hide.">
 <b>&#9679;</b> <span id="cro-review-label">Show changes (__N__)</span></div>
<script>
(function(){var b=document.getElementById('cro-review');if(!b)return;
 if(location.search.indexOf('clean')>-1){b.remove();return;}
 if(location.search.indexOf('highlight')>-1){document.documentElement.classList.add('cro-hl');}
 var l=document.getElementById('cro-review-label');
 function sync(){l.textContent=(document.documentElement.classList.contains('cro-hl')?'Hide':'Show')+' changes (__N__)';}
 sync();b.addEventListener('click',function(){document.documentElement.classList.toggle('cro-hl');sync();
  var f=document.querySelector('.cro-change>*,.cro-changed,.cro-moved');
  if(f&&document.documentElement.classList.contains('cro-hl'))f.scrollIntoView({behavior:'smooth',block:'center'});});
})();
</script>
"""


def _find(soup, ref: str):
    ref = (ref or "").strip()
    m = re.search(r"\bn\d+\b", ref)
    if m:
        return soup.find(attrs={"data-cro-node": m.group(0)})
    key = re.sub(r"[^a-z_]", "", ref.lower())
    return soup.find(attrs={"data-cro-key": key}) if key else None


def _protected(el) -> bool:
    for n in [el, *el.parents]:
        if getattr(n, "attrs", None) and n.attrs.get("data-cro-protected"):
            return True
    return False


def page_context(soup, F: dict, page_text: str, ctx: GuardContext) -> dict:
    """Facts the component renderers may use (all taken from the page itself)."""
    opts = []
    var = soup.find(attrs={"data-cro-key": "variants"})
    sel = (var if var is not None and var.name == "select" else var.find("select")) if var is not None else None
    if sel is not None:
        opts = [o.get_text(strip=True) for o in sel.find_all("option") if o.get_text(strip=True)]
    return {**{k: F.get(k) for k in ("title_text", "price_text", "compare_text", "atc_label", "trust_snippets",
                                     "schema_rating_obj")},
            "variant_options": opts, "page_text": page_text, "digits": ctx.digits}


def _insert(soup, target, position, node):
    if position == "before":
        target.insert_before(node)
    elif position == "prepend":
        target.insert(0, node)
    elif position == "append":
        target.append(node)
    else:
        target.insert_after(node)


INLINE = {"savings_badge"}  # sits inside a price row; every other new block takes a full row of its own


def scope_css(css: str, scope: str = ".cro-c") -> str:
    """Prefix every selector with the block wrapper (two classes of specificity), so the brand theme's generic rules
    for form, label, input, li, flex rows... can't reshape a component."""
    out = []
    for r in tinycss2.parse_stylesheet(css or "", skip_comments=True, skip_whitespace=True):
        if r.type == "qualified-rule":
            sels = [s.strip() for s in tinycss2.serialize(r.prelude).split(",") if s.strip()]
            sels = [s if re.match(re.escape(scope) + r"(?![\w-])", s) else f"{scope} {s}" for s in sels]
            out.append(f"{', '.join(sels)}{{{tinycss2.serialize(r.content)}}}")
        elif r.type == "at-rule" and r.lower_at_keyword in ("media", "supports") and r.content is not None:
            out.append(f"@{r.lower_at_keyword} {tinycss2.serialize(r.prelude).strip()}{{{scope_css(tinycss2.serialize(r.content), scope)}}}")
        else:
            out.append(tinycss2.serialize([r]))
    return "\n".join(out)


def _anchor(soup, key: str, anchor_id: str):
    el = soup.find(attrs={"data-cro-key": key})
    if el is not None and not soup.find(id=anchor_id):
        el.insert_before(soup.new_tag("span", attrs={"id": anchor_id}))


def apply_patches(frozen_html: str, patches: list[dict], ctx: GuardContext, F: dict, page_text: str) -> tuple[str, list, list]:
    soup = BeautifulSoup(frozen_html, "lxml")
    page = page_context(soup, F, page_text, ctx)
    applied, rejected, css_out = [], [], []
    seen_components = set()
    for i, p in enumerate(patches, 1):
        rec, op = p.get("rec_id") or f"P{i}", p.get("op")
        vp = p.get("viewport") if p.get("viewport") in VIEWPORTS else "all"
        pos = p.get("position") if p.get("position") in POSITIONS else "after"

        def reject(reason, log=True):
            rejected.append({**_brief(p), "reason": reason})
            if log:
                ctx.note(rec, "target", f"{reason}; patch skipped")

        if op not in OPS:
            reject(f"unknown op {op}")
            continue
        target = _find(soup, p.get("target"))
        if target is None:
            reject(f"target '{p.get('target')}' not found")
            continue
        if _protected(target):
            reject("target is header/nav/logo/footer (protected)")
            continue
        floating = set(F.get("floating_keys") or []) | ({"atc"} if F.get("atc_floating") else set())
        hit = next((n.attrs["data-cro-key"] for n in [target, *target.parents]
                    if getattr(n, "attrs", {}).get("data-cro-key") in floating), None)
        if op in ("component", "custom") and p.get("component") != "sticky_atc" and hit:
            # the element sits in a fixed / sticky bar on desktop or mobile: a block next to it would float with the bar
            alt = next((a for k in ("price", "product_info") if k not in floating
                        for a in [soup.find(attrs={"data-cro-key": k})] if a is not None), None)
            if alt is not None:
                ctx.note(rec, "target", f"'{hit}' sits in a floating bar, so the block was placed after the price "
                                        "instead (next to it, it would float with the bar)")
                target, pos = alt, ("after" if alt.get("data-cro-key") == "price" else "append")

        key = target.get("data-cro-key") if hasattr(target, "get") else None
        depth = (F.get("row_depth") or {}).get(key, 0) if key else 0
        if op in ("component", "custom") and depth and not (op == "component" and p.get("component") in INLINE):
            anchor = target
            for _ in range(depth):
                if anchor.parent is None or anchor.parent.name in ("body", "html") or _protected(anchor.parent):
                    break
                anchor = anchor.parent
            if anchor is not target:
                ctx.note(rec, "target", f"'{key}' sits in a horizontal row; the block was placed after the whole row")
                target, pos = anchor, ("before" if pos in ("before", "prepend") else "after")

        if op == "component" and p.get("component") == "sticky_atc" and F.get("sticky_atc"):
            reject("the page already has a sticky add-to-cart", log=False)
            continue

        if op == "layout":
            decls = (p.get("css") or "").strip().strip("{}")
            if not decls:
                reject("layout patch without CSS", log=False)
                continue
            cls = f"cro-l{i}"
            rule = f".{cls}{{{decls}}}"
            if vp == "mobile":
                rule = f"@media (max-width:749px){{{rule}}}"
            elif vp == "desktop":
                rule = f"@media (min-width:750px){{{rule}}}"
            css = guard_css(rule, ctx, rec, scoped=True, layout_classes={cls})
            if not css:
                reject("layout CSS removed by brand guard (layout properties only on existing elements)", log=False)
                continue
            target["class"] = (target.get("class") or []) + [cls, "cro-changed"]
            target["data-cro-rec"] = rec
            css_out.append(css)
        elif op == "move":
            src = _find(soup, p.get("source"))
            if src is None or _protected(src) or src is target or src in target.parents or target in src.parents:
                reject("move source missing, protected, or nested with the target")
                continue
            src.extract()
            (target.insert_before if pos in ("before", "prepend") else target.insert_after)(src)
            src["class"] = (src.get("class") or []) + ["cro-moved"]
            src["data-cro-rec"] = rec
        elif op == "replace_text":
            new = (p.get("label") or "").strip()
            if not new or len(target.find_all(True)) > 4 or len(target.get_text(" ", strip=True)) > 120:
                reject("replace_text only allowed on short text elements (e.g. a CTA label)")
                continue
            cleaned = guard_html(comp.esc(new), ctx, rec)
            if not cleaned:
                reject("dropped by claim guard", log=False)
                continue
            target.clear()
            for child in list(BeautifulSoup(cleaned, "html.parser").contents):
                target.append(child)
            target["class"] = (target.get("class") or []) + ["cro-changed"]
            target["data-cro-rec"] = rec
        else:  # component | custom: a new block
            extra_css: list = []
            if op == "component":
                name = p.get("component") or ""
                if name in seen_components:
                    reject(f"component {name} already added", log=False)
                    continue
                out = comp.render(name, p, page)
                if out is None:
                    reject(f"component '{name}' unknown or had nothing valid to show")
                    continue
                html, css, allow = out
                if name == "sticky_atc":
                    vp = "mobile"
                    _anchor(soup, "atc", "cro-atc")
                if name == "rating_summary":
                    _anchor(soup, "reviews", "cro-reviews")
                seen_components.add(name)
            else:
                html, css, allow = p.get("html") or "", p.get("css") or "", set()
            cleaned = guard_html(html, ctx, rec, allow_numbers=allow, extra_css=extra_css)
            if cleaned is None or not cleaned.strip():
                reject("dropped by claim/brand guard", log=False)
                continue
            block = not (op == "component" and p.get("component") in INLINE)
            classes = ["cro-change"] + (["cro-c"] if block else []) + ([f"cro-vp-{vp}"] if vp != "all" else [])
            wrap = soup.new_tag("div", attrs={"class": " ".join(classes), "data-cro-rec": rec})
            for child in list(BeautifulSoup(cleaned, "html.parser").contents):
                wrap.append(child)
            _insert(soup, target, pos, wrap)
            if op == "component" and p.get("component") == "variant_pills":
                sel = target if target.name == "select" else target.find("select")
                if sel is not None:
                    sel["class"] = (sel.get("class") or []) + ["cro-hide-select", "cro-changed"]
                    css_out.append(".cro-hide-select{display:none!important}")
            css_all = "\n".join([css] + extra_css)
            if css_all.strip():
                guarded = guard_css(css_all, ctx, rec, scoped=True)
                css_out.append(scope_css(guarded) if block else guarded)
        applied.append(_brief(p))

    head = soup.head
    style = soup.new_tag("style", id="cro-tokens")
    style.string = ctx.tokens.css_vars() + BASE_CSS
    head.append(style)
    style2 = soup.new_tag("style", id="cro-patches")
    style2.string = "\n".join(c for c in dict.fromkeys(css_out) if c)
    head.append(style2)
    if soup.body:
        soup.body.append(BeautifulSoup(REVIEW_UI.replace("__N__", str(len(applied))), "html.parser"))
    return str(soup), applied, rejected


def _brief(p):
    return {k: p.get(k) for k in ("rec_id", "op", "component", "target", "position", "viewport", "source")}


def page_images(html: str) -> set:
    soup = BeautifulSoup(html, "lxml")
    out = set()
    for t in soup.find_all(["img", "source"]):
        for u in [t.get("src")] + [s.strip().split()[0] for s in str(t.get("srcset") or "").split(",") if s.strip()]:
            if u:
                out.add(re.sub(r"^https?:", "", u.split("?")[0]))
    for m in re.findall(r"url\(\s*['\"]?(https?:[^'\")]+)", html):
        out.add(re.sub(r"^https?:", "", m.split("?")[0]))
    return out


# ---------------------------------------------------------------- rule-based fallback
RECS_ONLY = [
    ("no_popup", "Remove the entry popup on product pages", "It covers the product on first load; move email capture to exit intent or a 2nd page view.", "high", "low"),
    ("d_no_popup", "Remove the desktop entry popup", "It hides the buy box on first load; trigger it on exit intent instead.", "medium", "low"),
    ("price_atf", "Bring price into the first mobile screen", "Shoppers decide on price first; banners above the product push it below the fold.", "high", "medium"),
    ("atc_atf", "Bring the add-to-cart into the first mobile screen", "Shorten the gallery or spacing so the CTA is visible without scrolling.", "high", "medium"),
    ("d_buybox", "Two-column desktop layout", "Put the gallery and the buy box side by side so price and CTA are on the first desktop screen.", "medium", "medium"),
    ("d_atc_atf", "Bring the desktop CTA above the fold", "Tighten the desktop buy box so the CTA sits within the first screen.", "medium", "medium"),
    ("scannable", "Lead with 3-5 benefit bullets", "Long paragraphs are skipped on mobile; bullets carry the key reasons to buy.", "medium", "low"),
    ("images", "Add more product imagery (6+)", "Texture, scale, how-to-use and before/after shots answer what copy can't.", "medium", "medium"),
    ("video", "Add a short product video", "A 15-30s demo in the gallery reduces uncertainty about texture and use.", "medium", "medium"),
    ("cross_sell", "Add a bundle or 'frequently bought together'", "Lifts AOV without extra traffic cost.", "medium", "medium"),
    ("reviews", "Add a reviews section with photos", "UGC is the strongest proof for a D2C brand without retail presence.", "high", "medium"),
    ("mandatory_info", "Add the mandatory product declarations", "Indian e-commerce listings must state origin, net quantity, maker and MRP inclusive of taxes.", "medium", "low"),
    ("alt", "Write alt text for product images", "Accessibility and image search traffic.", "low", "low"),
    ("font", "Increase mobile body text size", "Small body text hurts readability on phones.", "low", "low"),
    ("schema", "Add Product structured data", "Enables price/stock rich results in Google.", "low", "low"),
    ("rating_schema", "Add aggregateRating to schema", "Stars in search results lift CTR.", "low", "low"),
]


def fallback_plan(F: dict, rubric: dict, frozen_html: str, comparison: dict | None = None) -> dict:
    """Deterministic recommendations + component patches from failed checks. Copy uses placeholders for anything
    the page doesn't state, so nothing is invented. Competitor evidence is attached where a competitor passes."""
    checks = {c["id"]: c for c in rubric["checks"]}
    soup = BeautifulSoup(frozen_html, "lxml")
    has = lambda key: soup.find(attrs={"data-cro-key": key}) is not None
    gaps = {g["check_id"]: g for g in (comparison or {}).get("gaps", [])}
    recs, patches = [], []

    def bad(cid):
        return checks.get(cid, {}).get("status") in ("fail", "partial")

    def rec(cid, title, why, impact, effort, in_mockup, kind="addon"):
        rid = f"R{len(recs) + 1}"
        c = checks.get(cid, {})
        g = gaps.get(cid)
        recs.append({"id": rid, "title": title, "why": why, "kind": kind, "viewport": c.get("viewport", "both"),
                     "impact": impact, "effort": effort, "fixes_issue_ids": [cid], "research": c.get("research", []),
                     "competitor_ref": ("Done by " + ", ".join(g["passing"])) if g else "", "in_mockup": in_mockup})
        return rid

    def patch(rid, component, target, position="after", **kw):
        patches.append({"rec_id": rid, "op": "component", "component": component, "target": target,
                        "position": position, "viewport": kw.pop("viewport", "all"), "heading": "", "label": "",
                        "items": [], **kw})

    anchor = "atc" if has("atc") else "product_info"
    if bad("sticky_atc") and has("atc"):
        patch(rec("sticky_atc", "Sticky add-to-cart bar on mobile",
                  "Once shoppers scroll into details or reviews the CTA disappears; a sticky bar keeps purchase one tap away.",
                  "high", "low", True), "sticky_atc", "atc", viewport="mobile")
    if bad("atc_tap") and has("atc"):
        patches.append({"rec_id": rec("atc_tap", "Enlarge the mobile add-to-cart tap target",
                                      "Below 44px the button is easy to miss on a phone; size only, colour and type stay as they are.",
                                      "medium", "low", True, kind="change"),
                        "op": "layout", "target": "atc", "viewport": "mobile", "css": "min-height:48px"})
    if bad("rating_title") and has("title") and F.get("reviews_section"):
        patch(rec("rating_title", "Star rating + review count under the product title",
                  "Social proof at the decision point; reviews exist on the page but shoppers don't see them in the first screen.",
                  "high", "low", True), "rating_summary", "title")
    elif bad("rating_title") and not F.get("reviews_section"):
        rec("rating_title", "Start collecting and showing reviews",
            "No reviews anywhere on the page. Trigger a post-delivery review request (WhatsApp/email) and show a star summary under the title.",
            "high", "medium", False)
    if bad("delivery") and has(anchor):
        patch(rec("delivery", "Pincode check with estimated delivery date",
                  "Delivery time is a top pre-purchase question; answering it next to the CTA removes a reason to leave.",
                  "high", "medium", True), "delivery_check", anchor)
    if (bad("trust_atc") or bad("returns") or bad("payments") or bad("shipping")) and has(anchor):
        patch(rec("trust_atc", "Trust strip under the add-to-cart",
                  "Returns, payment safety and authenticity answered at the moment of hesitation; values are placeholders until the brand confirms its policies.",
                  "high", "low", True), "trust_strip", anchor,
              label="Pay via [UPI · Cards · Netbanking · COD]" if bad("payments") else "")
    if bad("variants") and has("variants"):
        patch(rec("variants", "Show variants as buttons instead of a dropdown",
                  "A dropdown hides the options; visible buttons show the range and let shoppers pick in one tap.",
                  "medium", "medium", True, kind="change"), "variant_pills", "variants")
    if bad("discount") and F.get("compare_price"):
        patch(rec("discount", "State the saving next to the price",
                  "A struck-through MRP without the saving makes shoppers do maths; '% off' is read instantly.",
                  "medium", "low", True, kind="change"), "savings_badge", "compare_price" if has("compare_price") else "price")
    if bad("faq") and (has("description") or has("product_info")):
        patch(rec("faq", "FAQ accordion for pre-purchase doubts",
                  "Answers the questions that otherwise go to WhatsApp support or end the session; answers to be written by the brand.",
                  "medium", "low", True), "faq", "description" if has("description") else "product_info",
              items=[{"title": q, "text": "", "icon": ""} for q in
                     ("How do I use it?", "When will my order arrive?", "Can I return or exchange it?", "Is Cash on Delivery available?")])
    for cid, title, why, impact, effort in RECS_ONLY:
        if bad(cid) and not any(cid in r["fixes_issue_ids"] for r in recs):
            rec(cid, title, why, impact, effort, False, kind="change")

    issues = [{"id": c["id"], "title": c["title"], "detail": c["evidence"], "fix": c["fix"], "severity": _sev(c),
               "category": c["category"], "viewport": c["viewport"], "evidence": "", "research": c["research"]}
              for c in rubric["checks"] if c["status"] in ("fail", "partial")]
    strengths = [{"title": c["title"], "detail": c["evidence"], "viewport": c["viewport"]}
                 for c in rubric["checks"] if c["status"] == "pass" and c["weight"] >= 3]
    return {"page_summary": "", "positioning": "", "strengths": strengths, "issues": issues, "competitor_insights": [],
            "recommendations": recs, "patches": patches, "source": "rules"}


def _sev(c):
    if c["status"] == "fail" and c["weight"] >= 5:
        return "critical"
    if c["status"] == "fail" and c["weight"] >= 3:
        return "high"
    if c["status"] == "fail" or c["weight"] >= 5:
        return "medium"
    return "low"
