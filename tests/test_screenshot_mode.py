"""Screenshot-mode pipeline test with a scripted model (no API key): mobile + desktop pairing, palette sampling,
colour roles snapped to sampled pixels, vision facts -> rubric (both viewports), competitor capture, the composite
mockup (sections cut at blank rows, column-aware inserts on desktop), guards and rendering.

    python tests/test_screenshot_mode.py mobile.png desktop.png [out_dir]
    (run tests/test_brand_guard.py first: it leaves usable screenshots in out_test/<page>/shots/)
"""
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cro import llm as llm_mod  # noqa: E402
from cro import pipeline  # noqa: E402

E = {"heading": "", "label": "", "items": [], "source": "", "css": "", "html": "", "component": "none",
     "position": "after", "viewport": "all"}


class ScriptedLLM(llm_mod.LLM):
    def __init__(self, *a, **k):
        self.model, self.fast_model, self.effort = "scripted", "scripted-fast", "low"
        self.dry_run_dir, self.client, self.batch, self.calls = None, None, None, []
        self.seen = []

    async def call(self, system, content, schema, tag, model=None, max_tokens=16000, effort=None, batchable=False,
                   batch_key=None):
        imgs = [b for b in content if b["type"] == "image"]
        self.seen.append((tag.split(".")[-1], len(imgs)))
        self.record(tag, "claude-sonnet-5-5", {"input_tokens": 9000, "output_tokens": 1500}, False)
        sizes = {}
        for b in content:
            m = re.search(r"Image \d+: (mobile|desktop) screenshot.*page is (\d+)x(\d+) px", b.get("text", ""))
            if m:
                sizes[m.group(1)] = (int(m.group(2)), int(m.group(3)))
        if tag.endswith(".extract"):
            mw, mh = sizes["mobile"]
            dw, dh = sizes["desktop"]
            pal = re.search(r"sampled from the pixels: (\[.*?\])", content[-1]["text"]).group(1)
            pal = json.loads(pal)
            return {
                "is_product_page": True,
                "brand": {"name": "Kosha",
                          "roles": {"primary": "#1f4d3a", "on_primary": "#ffffff", "text": "#222222", "heading": "#111111",
                                    "muted": "#666666", "bg": "#fafafa", "surface": "#f1ece4", "border": "#dddddd",
                                    "accent": "#ff3399", "link": pal[0]},
                          "fonts": {"heading_font": "Cormorant Garamond", "heading_generic": "serif",
                                    "body_font": "Inter", "body_generic": "sans-serif"},
                          "type_scale_px": {"body": 15, "small": 13, "price": 20, "section_heading": 22, "product_title": 28, "button": 15},
                          "button_radius_px": 2, "button_uppercase": True, "button_height_px": 48},
                "present": ["atc_present", "atc_contrast_ok", "m_title_atf", "m_image_atf", "d_title_price_atf", "d_atc_atf",
                            "d_two_col", "compare_price"],
                "absent": ["rating_near_title", "reviews_section", "faq", "m_sticky_atc", "m_popup", "d_popup",
                           "trust_returns", "trust_secure", "delivery_info", "decl_origin"],
                "counts": {"image_count": 4, "bullets": 0, "variant_options": 3},
                "text": {"title": "Kumkumadi Glow Face Oil", "price": "₹1,299", "compare_price": "₹1,599", "discount": "",
                         "atc_label": "Add to cart", "rating_text": "", "currency": "INR",
                         "visible_text": "KOSHA Kumkumadi Glow Face Oil ₹1,299 ₹1,599 Add to cart Infused for 21 days with 16 Ayurvedic herbs"},
                "sections": [
                    {"id": "m1", "view": "mobile", "label": "header", "layout": "full", "y_top": 0, "y_bottom": int(mh * .04), "x_left": 0, "x_right": mw},
                    {"id": "m2", "view": "mobile", "label": "gallery", "layout": "full", "y_top": int(mh * .04), "y_bottom": int(mh * .3), "x_left": 0, "x_right": mw},
                    {"id": "m3", "view": "mobile", "label": "title & price", "layout": "full", "y_top": int(mh * .3), "y_bottom": int(mh * .38), "x_left": 0, "x_right": mw},
                    {"id": "m4", "view": "mobile", "label": "add to cart", "layout": "full", "y_top": int(mh * .38), "y_bottom": int(mh * .45), "x_left": 0, "x_right": mw},
                    {"id": "m5", "view": "mobile", "label": "description", "layout": "full", "y_top": int(mh * .45), "y_bottom": int(mh * .8), "x_left": 0, "x_right": mw},
                    {"id": "m6", "view": "mobile", "label": "footer", "layout": "full", "y_top": int(mh * .8), "y_bottom": mh, "x_left": 0, "x_right": mw},
                    {"id": "d1", "view": "desktop", "label": "header", "layout": "full", "y_top": 0, "y_bottom": int(dh * .06), "x_left": 0, "x_right": dw},
                    {"id": "d2", "view": "desktop", "label": "gallery", "layout": "left", "y_top": int(dh * .08), "y_bottom": int(dh * .5), "x_left": 0, "x_right": int(dw * .5)},
                    {"id": "d3", "view": "desktop", "label": "buy box", "layout": "right", "y_top": int(dh * .08), "y_bottom": int(dh * .35), "x_left": int(dw * .52), "x_right": dw},
                    {"id": "d4", "view": "desktop", "label": "rest", "layout": "full", "y_top": int(dh * .52), "y_bottom": dh, "x_left": 0, "x_right": dw},
                ],
                "profile": {"category": "Skincare", "product_type": "face oil", "market": "IN", "price_positioning": "mid",
                            "competitors": []},
            }
        if tag.endswith(".audit"):
            assert any("Competitor:" in b.get("text", "") for b in content), "competitor screenshots must reach the audit"
            return {
                "is_product_page": True, "page_summary": "Clean page with no social proof.", "competitive_position": "Behind.",
                "strengths": [{"title": "Clear title", "detail": "Name and price on the first screen.", "viewport": "both"}],
                "issues": [{"id": "I1", "title": "No reviews", "detail": "No ratings anywhere.", "severity": "critical",
                            "category": "Trust & social proof", "viewport": "both", "evidence": "", "research": ["spiegel-reviews"]}],
                "competitor_insights": [],
                "recommendations": [{"id": f"R{i}", "title": f"Rec {i}", "why": "t", "kind": "addon", "viewport": "both",
                                     "impact": "high", "effort": "low", "fixes_issue_ids": ["I1"], "research": [],
                                     "competitor_ref": "", "in_mockup": True} for i in range(1, 8)],
                "patches": [
                    {**E, "rec_id": "R1", "op": "component", "component": "trust_strip", "target": "d3",
                     "items": [{"title": "", "text": "[7]-day easy returns", "icon": "returns"},
                               {"title": "", "text": "Secure checkout", "icon": "secure"}]},
                    {**E, "rec_id": "R2", "op": "component", "component": "trust_strip", "target": "m4",
                     "items": [{"title": "", "text": "[7]-day easy returns", "icon": "returns"},
                               {"title": "", "text": "Secure checkout", "icon": "secure"}]},
                    {**E, "rec_id": "R3", "op": "component", "component": "faq", "target": "m5",
                     "items": [{"title": "How do I use it?", "text": "", "icon": ""}, {"title": "Is it for oily skin?", "text": "", "icon": ""}]},
                    {**E, "rec_id": "R4", "op": "custom", "target": "m3",
                     "html": '<p class="cro-proof">Loved by 50,000 customers</p>', "css": ".cro-proof{color:#ff3399;font-family:Poppins;font-size:18px}"},
                    {**E, "rec_id": "R5", "op": "layout", "target": "m2", "css": "max-height:300px"},
                    {**E, "rec_id": "R6", "op": "component", "component": "sticky_atc", "target": "m4", "viewport": "mobile"},
                    {**E, "rec_id": "R7", "op": "component", "component": "faq", "target": "d4", "position": "before",
                     "items": [{"title": "How do I use it?", "text": "", "icon": ""}, {"title": "Is it for oily skin?", "text": "", "icon": ""}]},
                ],
            }
        raise AssertionError(tag)


def main():
    mob, desk = Path(sys.argv[1]), Path(sys.argv[2])
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / "out_shot_test"
    holder = {}

    def make(*a, **k):
        holder["llm"] = ScriptedLLM()
        return holder["llm"]

    llm_mod.LLM = make
    items = pipeline.parse_inputs([f"{mob} + {desk} | Kosha screenshot | vs: http://localhost:8765/pdp_fizz.html"])
    assert items[0].kind == "image" and len(items[0].paths) == 2, items[0]
    rows = asyncio.run(pipeline.run(items, pipeline.Options(out=out, psi=False, force=True, cache_dir=out / "_cache")))
    row = rows[0]
    assert row["status"] == "ok", row
    d = out / row["slug"]
    rep = json.loads((d / "report.json").read_text(encoding="utf-8"))
    html = (d / "optimized.html").read_text(encoding="utf-8")
    css = re.search(r'<style id="cro-patches">(.*?)</style>', html, re.S).group(1)
    pal = {p["hex"] for p in rep["tokens"]["palette"]}
    rejected = {r["rec_id"]: r["reason"] for r in rep["rejected"]}
    checks = {
        "mobile + desktop pair parsed": len(items[0].paths) == 2,
        "palette sampled from pixels": len(pal) >= 3,
        "every colour role snapped to a sampled pixel colour": all(v in pal for v in rep["tokens"]["roles"].values()),
        "rubric scored for both viewports": rep["viewport_scores"]["mobile"] is not None and rep["viewport_scores"]["desktop"] is not None,
        "desktop checks answered from vision": any(c["id"] == "d_buybox" and c["status"] == "pass" for c in rep["checks"]),
        "performance n/a (needs URL)": rep["perf_score"] is None,
        "competitor captured": len(rep["comparison"]["competitors"]) == 1,
        "composite cut into image sections": len(list((d / "assets").glob("m_*.jpg"))) >= 3 and len(list((d / "assets").glob("d_*.jpg"))) >= 1,
        "desktop buy-box insert placed in the right column": 'class="cro-band"' in html and "cro-col" in html,
        "mobile and desktop composites switch by viewport": "cro-shot--mobile" in html and "cro-shot--desktop" in html,
        "components inserted": html.count('class="cro-trust__list"') == 2 and html.count('class="cro-faq"') == 2,
        "component CSS not duplicated": css.count(".cro-trust__list{") == 1,
        "layout op refused on a screenshot": "R5" in rejected,
        "off-brand #ff3399 snapped": "#ff3399" not in css.lower(),
        "Poppins replaced": "poppins" not in css.lower(),
        "18px snapped to brand scale": "font-size:18px" not in css,
        "invented 50,000 marked": bool(re.search(r'cro-placeholder[^>]*>50,000', html)),
        "after renders for both views": (d / "shots" / "after_mobile.png").exists() and (d / "shots" / "after_desktop.png").exists(),
        "two model calls with images (extract, audit)": [s for s, n in holder["llm"].seen if n > 0] == ["extract", "audit"],
    }
    w = max(map(len, checks))
    for k, v in checks.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k:<{w}}")
    assert all(checks.values())
    print("  screenshot mode checks passed")


if __name__ == "__main__":
    main()
