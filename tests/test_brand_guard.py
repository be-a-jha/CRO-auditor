"""Adversarial end-to-end test of the LLM path without an API key.

A fake model returns a plan that breaks every brand and honesty rule. The pipeline must still produce a page that
uses only brand colours/fonts/sizes, never touches the header, and never shows invented numbers or urgency. The
second local page is used as the competitor, so the competitor capture + comparison path runs too.

Run:  cd tests/fixtures && python -m http.server 8765      (in another terminal)
      python tests/test_brand_guard.py
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
BAD_PLAN = {
    "is_product_page": True,
    "page_summary": "Strong product story, but the page hides price and add-to-cart below a promo banner.",
    "competitive_position": "Behind Fizzlab on trust and social proof.",
    "strengths": [{"title": "Clear product naming", "detail": "Title states product and benefit.", "viewport": "both"}],
    "issues": [{"id": "I1", "title": "No social proof", "detail": "No ratings near title.", "severity": "critical",
                "category": "Trust & social proof", "viewport": "both", "evidence": "No stars visible.",
                "research": ["spiegel-reviews", "made-up-study"]}],
    "competitor_insights": [{"competitor": "Fizzlab", "observation": "Rating under the title.", "takeaway": "Surface reviews."}],
    "recommendations": [{"id": f"R{i}", "title": f"Rec {i}", "why": "test", "kind": "addon", "viewport": "both",
                         "impact": "high", "effort": "low", "fixes_issue_ids": ["I1"], "research": ["nng-fold"],
                         "competitor_ref": "", "in_mockup": True} for i in range(1, 13)],
    "patches": [
        {**E, "rec_id": "R1", "op": "custom", "target": "title",
         "html": '<div class="cro-rating" style="color:#ff0000;font-family:\'Comic Sans MS\';font-size:13px">'
                 '4.9 from 12,000+ happy customers</div>',
         "css": ".cro-rating{color:rgb(255,0,0);font-size:19px;border-radius:7px;font-family:Lobster, cursive}"},
        {**E, "rec_id": "R2", "op": "custom", "target": "atc",
         "html": '<p class="cro-urgency">Hurry! Only 3 left in stock</p>', "css": ".cro-urgency{color:var(--cro-accent)}"},
        {**E, "rec_id": "R3", "op": "custom", "target": "price", "html": '<div class="cro-x">[Bank offer]</div>',
         "css": "h1{color:#000000} .product .price{font-size:40px} .cro-x{background:linear-gradient(#123456,#abcdef);font-size:1.1rem}"},
        {**E, "rec_id": "R4", "op": "component", "component": "trust_strip", "target": "__HEADER__", "position": "prepend"},
        {**E, "rec_id": "R5", "op": "layout", "target": "atc", "viewport": "mobile",
         "css": "min-height:52px;background:#00ff00;font-size:20px;color:#fff"},
        {**E, "rec_id": "R6", "op": "custom", "target": "description", "position": "before",
         "html": '<ul class="cro-benefits"><li>Infused for 21 days</li><li>16 Ayurvedic herbs</li><li>Clinically proven: 98% saw glow</li></ul>'
                 '<img src="https://stock.example.com/model.jpg"><script>alert(1)</script>',
         "css": ".cro-benefits{font-family:var(--cro-font-body);color:var(--cro-text)}"},
        {**E, "rec_id": "R7", "op": "custom", "target": "variants", "html": '<div class="cro-font">x</div>',
         "css": "@import url(https://fonts.googleapis.com/css?family=Lobster); @font-face{font-family:Lobster;src:url(x.woff)} .cro-font{color:var(--cro-text)}"},
        {**E, "rec_id": "R8", "op": "move", "target": "title", "source": "product_info", "position": "before"},
        {**E, "rec_id": "R9", "op": "replace_text", "target": "atc", "label": "Add to cart · ₹1,299"},
        {**E, "rec_id": "R10", "op": "component", "component": "rating_summary", "target": "price", "label": "4.9 (12,000 reviews)"},
        {**E, "rec_id": "R11", "op": "component", "component": "review_highlights", "target": "description",
         "items": [{"title": "Priya", "text": "Best oil I have ever used, my hair grew 3 inches", "icon": ""}]},
        {**E, "rec_id": "R12", "op": "component", "component": "faq", "target": "description",
         "items": [{"title": "How long does a bottle last?", "text": "", "icon": ""},
                   {"title": "Is it safe for coloured hair?", "text": "", "icon": ""}]},
    ],
    "source": "llm",
}


class FakeLLM(llm_mod.LLM):
    def __init__(self, *a, **k):
        self.model, self.fast_model, self.effort = "fake", "fake-fast", "low"
        self.dry_run_dir, self.client, self.batch, self.calls = None, None, None, []


async def fake_profile(llm, info, tag):
    return {"brand": "Kosha", "category": "Haircare", "product_type": "hair oil", "market": "IN",
            "price_positioning": "mid", "audience": "", "competitors": [], "source": "fake"}


async def fake_audit(llm, cap, F, rubric, tokens, perf, profile, comp_brief, comps, tag, batch_key=None):
    assert comp_brief["competitors"], "competitor data must reach the audit prompt"
    plan = json.loads(json.dumps(BAD_PLAN))
    header = next(o["id"] for o in cap.desktop.outline if o["tag"] == "header")
    for p in plan["patches"]:
        if p["target"] == "__HEADER__":
            p["target"] = header
    llm.record(f"{tag}.audit", "claude-sonnet-5-5", {"input_tokens": 12000, "output_tokens": 2500}, False)
    return plan


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "out_test"
    llm_mod.LLM = FakeLLM
    llm_mod.profile_page = fake_profile
    llm_mod.audit_url = fake_audit
    items = pipeline.parse_inputs(["http://127.0.0.1:8765/pdp_kosha.html | Kosha | vs: http://localhost:8765/pdp_fizz.html"])
    opts = pipeline.Options(out=out, psi=False, force=True, cache_dir=out / "_cache")
    rows = asyncio.run(pipeline.run(items, opts))
    assert rows[0]["status"] == "ok", rows[0]
    d = out / rows[0]["slug"]
    rep = json.loads((d / "report.json").read_text(encoding="utf-8"))
    html = (d / "optimized.html").read_text(encoding="utf-8")
    report_html = (d / "report.html").read_text(encoding="utf-8")
    patches_css = re.search(r'<style id="cro-patches">(.*?)</style>', html, re.S).group(1)
    pal = {p["hex"] for p in rep["tokens"]["palette"]}
    kinds = {}
    for g in rep["guard_log"]:
        kinds.setdefault(g["kind"], []).append(g["detail"])
    rejected = {r["rec_id"]: r["reason"] for r in rep["rejected"]}
    C = rep["comparison"]

    checks = {
        "off-palette red removed": "#ff0000" not in html.lower() and "rgb(255,0,0)" not in patches_css.replace(" ", ""),
        "neon green removed": "#00ff00" not in html.lower(),
        "every colour in patch CSS is a brand colour": all(h.lower() in pal for h in re.findall(r"#[0-9a-fA-F]{6}", patches_css)),
        "Comic Sans / Lobster removed": "comic sans" not in html.lower() and "lobster" not in patches_css.lower(),
        "font sizes on brand scale": all(float(x) in rep["tokens"]["sizes"] for x in re.findall(r"font-size:([\d.]+)px", patches_css)),
        "global h1 rule dropped": not re.search(r"(^|\})\s*h1\s*\{", patches_css),
        "brand element restyle dropped": ".product .price" not in patches_css,
        "layout op kept layout only": "min-height:52px" in patches_css and not re.search(r"\.cro-l5\{[^}]*(background|font-size|color)", patches_css),
        "fake urgency patch dropped": "Only 3 left" not in html,
        "header untouched (protected)": "R4" in rejected and "protected" in rejected["R4"],
        "@import/@font-face dropped": "@import" not in patches_css and "@font-face" not in patches_css,
        "stock image removed": "stock.example.com" not in html,
        "injected script removed": "alert(1)" not in html,
        "nested move rejected": "R8" in rejected,
        "invented 4.9 / 12,000 marked": bool(re.search(r'cro-placeholder[^>]*>4\.9', html)) and bool(re.search(r'cro-placeholder[^>]*>12,000', html)),
        "invented rating shows empty stars": 'cro-rating__fill" style="width:0%' in html,
        "invented 98% marked": bool(re.search(r'cro-placeholder[^>]*>98%', html)),
        "real facts (21 days, 16 herbs) not flagged": not re.search(r'cro-placeholder[^>]*>(21|16)<', html),
        "real price in CTA not flagged": not re.search(r'cro-placeholder[^>]*>₹1,299', html),
        "fabricated review quote dropped": "R11" in rejected and "my hair grew" not in html,
        "FAQ component rendered in brand vars": "cro-faq" in html and "Is it safe for coloured hair?" in html,
        "unknown research id dropped from report": "made-up-study" not in report_html,
        "competitor captured and scored": len(C["competitors"]) == 1 and C["competitors"][0]["cro_score"] > 0,
        "check matrix + benchmark built": len(C["matrix"]) > 10 and C["benchmark"]["of"] == 2,
        "gaps vs competitor found": len(C["gaps"]) > 0,
        "competitor screenshot copied next to report": (d / "competitors" / "c1_mobile_fold.jpg").exists(),
        "mobile + desktop scores": rep["viewport_scores"]["mobile"] is not None and rep["viewport_scores"]["desktop"] is not None,
        "desktop after render": (d / "shots" / "after_desktop.png").exists(),
        "cost recorded per page": rep["cost"]["usd"] > 0,
        "report has competitive analysis + sources": "Competitive analysis" in report_html and 'id="src-nng-fold"' in report_html,
    }
    w = max(map(len, checks))
    for k, v in checks.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k:<{w}}")
    print(f"\n  guard log: {', '.join(f'{k}={len(v)}' for k, v in sorted(kinds.items()))}; rejected patches: {len(rep['rejected'])}")
    assert all(checks.values()), "brand guard test failed"
    print("  all brand guard checks passed")


if __name__ == "__main__":
    main()
