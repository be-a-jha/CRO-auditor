"""Batch orchestration. Per page: capture (desktop + mobile) -> score -> find and capture competitors -> compare ->
Claude audit -> brand-guarded optimised page -> report. Each page is isolated (one failure never stops the batch),
browser work and LLM work have separate concurrency limits, competitor captures are shared across the batch, and
finished pages are cached so re-runs only redo what failed."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from PIL import Image
from playwright.async_api import async_playwright

from . import llm as llm_mod
from . import rules, tokens as tok
from .capture import CONTEXT_CLOSE_SECONDS, DESKTOP, MOBILE, CaptureError, capture_url, freeze_html, site_label
from .competitors import (CompetitorBank, brief_for_model, compare, copy_shots, find_competitors, heuristic_profile,
                          summarize, TRUST_LABELS)
from .composite import View, css_width
from .composite import build as build_composite
from .guard import GuardContext
from .patcher import apply_patches, fallback_plan, page_images
from .perf import lab_metrics, lab_score, run_psi
from .report import write_index, write_report
from .research import BY_ID
from .shot import extract_palette, is_mobile_shot

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Item:
    raw: str
    kind: str          # url | image | invalid
    label: str
    idx: int
    paths: list = field(default_factory=list)        # image inputs: 1 screenshot, or a mobile + desktop pair
    competitors: list = field(default_factory=list)  # optional per-page competitor URLs / domains


@dataclass
class Options:
    out: Path
    concurrency: int = 2
    llm_concurrency: int = 3
    model: str = "claude-sonnet-5-5"
    fast_model: str = "claude-haiku-5-5"
    effort: str = "low"
    use_llm: bool = True
    batch: bool = False
    dry_run: bool = False
    psi: bool = True
    force: bool = False
    timeout_ms: int = 45000
    n_competitors: int = 3
    competitors: list = field(default_factory=list)  # applied to every page without its own list
    cache_dir: Path = ROOT / "cache"
    progress: object = None    # optional callable(item, stage, row=None) for UIs
    browser_slots: int | None = None


def _p(opts: Options, item, stage: str, row: dict | None = None):
    if opts.progress:
        try:
            opts.progress(item, stage, row)
        except Exception:
            pass


def _split_line(ln: str) -> tuple[str, str, list]:
    parts = [x.strip() for x in ln.split("|")]
    if len(parts) == 1 and re.search(r",\s*[A-Za-z]", ln) and "vs:" not in ln.lower():  # CSV: url,label
        parts = [x.strip() for x in ln.split(",", 1)]
    raw, label, comps = parts[0], "", []
    for x in parts[1:]:
        if x.lower().startswith("vs:"):
            comps = [c.strip() for c in re.split(r"[,\s]+", x[3:]) if c.strip()]
        elif x:
            label = x
    return raw, label, comps


def parse_inputs(args: list[str]) -> list[Item]:
    """Inputs: URLs, screenshot files, folders of screenshots, or .txt/.csv files with one input per line:
         https://brand.com/products/x | Label | vs: competitor.com, https://other.in/products/y
         shot_mobile.png + shot_desktop.png | Label        (a mobile + desktop pair of the same page)"""
    lines: list[tuple[str, str, list]] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            lines += [(str(f), "", []) for f in sorted(p.iterdir()) if f.suffix.lower() in IMG_EXT]
        elif p.is_file() and p.suffix.lower() in (".txt", ".csv"):
            for ln in p.read_text(encoding="utf-8-sig").splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    lines.append(_split_line(ln))
        else:
            lines.append(_split_line(a))
    items, seen = [], set()
    for raw, label, comps in lines:
        if raw in seen:
            continue
        seen.add(raw)
        n = len(items) + 1
        if re.match(r"^https?://", raw, re.I):
            items.append(Item(raw, "url", label or site_label(raw), n, competitors=comps))
            continue
        paths = [x.strip().strip('"') for x in re.split(r"\s\+\s", raw)]
        if paths and all(Path(x).suffix.lower() in IMG_EXT and Path(x).exists() for x in paths) and len(paths) <= 2:
            res = [str(Path(x).resolve()) for x in paths]
            items.append(Item(" + ".join(res), "image", label or Path(paths[0]).stem, n, paths=res, competitors=comps))
        else:
            items.append(Item(raw, "invalid", label or raw[:40], n))
    return items


def _slug(item: Item) -> str:
    h = hashlib.sha1(item.raw.encode()).hexdigest()[:6]
    base = re.sub(r"[^a-z0-9]+", "-", item.label.lower()).strip("-")[:40] or "page"
    return f"{base}-{h}"


async def run(items: list[Item], opts: Options) -> list[dict]:
    opts.out.mkdir(parents=True, exist_ok=True)
    opts.cache_dir.mkdir(parents=True, exist_ok=True)
    llm = None
    llm_note = ""
    if opts.use_llm:
        try:
            llm = llm_mod.LLM(opts.model, opts.fast_model, opts.llm_concurrency, opts.effort, batch=opts.batch,
                              dry_run_dir=(opts.out / "_dry_run") if opts.dry_run else None)
        except llm_mod.LLMError as e:
            llm_note = f"{e}: using the rules engine for analysis and mockups (screenshot inputs need the API key)."
            print(f"[warn] {llm_note}")
    if llm and llm.batch:
        llm.batch.expect(sum(1 for i in items if i.kind != "invalid"))
    t_start = time.time()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])  # Docker: tiny /dev/shm
        bsem = asyncio.Semaphore(opts.browser_slots or opts.concurrency * 2)
        bank = CompetitorBank(opts.cache_dir, browser, bsem, opts.timeout_ms)

        async def one(item):
            t0 = time.time()
            try:
                row = await process(item, browser, bsem, bank, llm, llm_note, opts)
            finally:
                if llm and llm.batch:
                    llm.batch.skip(item.idx)  # no-op if this page already submitted its audit
            _p(opts, item, row["status"], row)
            print(f"[{row['status']:>7}] {item.label:<34} CRO {row.get('cro_score')}  perf {row.get('perf_score')}  "
                  f"vs {row.get('comp_avg')}  ${row.get('cost_usd') or 0:.3f}  {time.time() - t0:5.1f}s "
                  f"{('· ' + row['error'][:90]) if row.get('error') else ''}")
            return row

        for i in items:
            _p(opts, i, "queued")
        try:
            rows = list(await asyncio.gather(*(one(i) for i in items)))
        finally:
            await bank.close()
            await browser.close()
    totals = llm.totals() if llm else None
    write_index(opts.out, rows, totals, round(time.time() - t_start))
    if totals and totals["calls"]:
        print(f"[llm] {totals['calls']} calls, {totals['input_tokens']:,} input ({totals['cache_read_input_tokens']:,} "
              f"from cache) / {totals['output_tokens']:,} output tokens, about ${totals['usd']:.3f}")
    return rows


async def process(item: Item, browser, bsem, bank, llm, llm_note, opts: Options) -> dict:
    slug = _slug(item)
    outdir = opts.out / slug
    base_row = {"label": item.label, "input": item.raw, "kind": item.kind, "slug": slug, "cro_score": None, "grade": None,
                "mobile_score": None, "desktop_score": None, "perf_score": None, "perf_source": "", "critical": None,
                "category": "", "competitors": [], "comp_avg": None, "rank": None, "top_issues": [], "cost_usd": None,
                "status": "failed", "error": ""}
    cached = outdir / "row.json"
    if cached.exists() and not opts.force:
        row = json.loads(cached.read_text(encoding="utf-8"))
        if row.get("status") in ("ok", "partial"):
            return row
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        if item.kind == "url":
            r = await _process_url(item, browser, bsem, bank, llm, llm_note, opts, outdir)
        elif item.kind == "image":
            r = await _process_image(item, browser, bsem, bank, llm, llm_note, opts, outdir)
        else:
            raise CaptureError("Not a URL (http/https) or an existing .png/.jpg/.webp file")
    except Exception as e:  # isolate failures per page
        (outdir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        row = {**base_row, "error": f"{type(e).__name__}: {e}",
               "cost_usd": llm.cost_for(slug)["usd"] if llm else None}
        cached.write_text(json.dumps(row), encoding="utf-8")
        return row
    (outdir / "error.txt").unlink(missing_ok=True)  # left over from an earlier failed attempt
    bench = (r["comparison"] or {}).get("benchmark") or {}
    row = {**base_row, "cro_score": r["cro_score"], "grade": r["grade"], "mobile_score": r["viewport_scores"]["mobile"],
           "desktop_score": r["viewport_scores"]["desktop"], "perf_score": r["perf_score"],
           "perf_source": r["perf"].get("source", ""), "critical": r["sev_counts"]["critical"],
           "category": r["profile"].get("category", ""),
           "competitors": [c["brand"] for c in (r["comparison"] or {}).get("competitors", [])],
           "comp_avg": bench.get("avg"), "rank": f"{bench['rank']}/{bench['of']}" if bench else None,
           "top_issues": [i["title"] for i in r["issues"][:3]], "cost_usd": r["cost"]["usd"] if r.get("cost") else None,
           "status": "partial" if r.get("error") else "ok", "error": r.get("error") or "",
           "report": f"{slug}/report.html", "optimized": f"{slug}/optimized.html" if r.get("optimized") else ""}
    cached.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    return row


async def _render_all(jobs: list, slots: asyncio.Semaphore) -> list[str]:
    """Before/after renders only illustrate the report: one that times out must never discard a finished audit."""
    async def render(job):
        async with slots:
            await asyncio.wait_for(_render(*job), timeout=120)

    res = await asyncio.gather(*(render(j) for j in jobs), return_exceptions=True)
    return [f"{Path(j[2]).stem}: {type(r).__name__}" for j, r in zip(jobs, res) if isinstance(r, Exception)]


async def _render(browser, html_path: Path, png: Path, mobile: bool, full: bool, highlight: bool, cap_px: int = 4500):
    cfg = MOBILE if mobile else DESKTOP
    ctx = await browser.new_context(**cfg)
    try:
        page = await ctx.new_page()
        url = html_path.resolve().as_uri() + ("?highlight" if highlight else "?clean")
        try:
            await page.goto(url, wait_until="load", timeout=30000)
        except Exception:
            pass
        await page.wait_for_timeout(900)
        if highlight:
            await page.evaluate("document.getElementById('cro-review') && document.getElementById('cro-review').remove()")
        if full and highlight and mobile:
            # Fixed elements (sticky bars) would be painted mid-page in a full-page capture: shoot them in context
            # while scrolled, then hide them for the full-page image.
            n = await page.evaluate("""() => { const out = [];
              for (const w of document.querySelectorAll('.cro-change, .cro-changed')) for (const el of [w, ...w.querySelectorAll('*')]) {
                const s = getComputedStyle(el); if (s.position === 'fixed' && el.getBoundingClientRect().height > 0) out.push(el); }
              window.__croFixed = out; return out.length; }""")
            if n:
                await page.evaluate("window.scrollTo(0, Math.min(innerHeight * 1.6, document.documentElement.scrollHeight - innerHeight))")
                await page.wait_for_timeout(300)
                await page.screenshot(path=str(png.with_name(png.stem + "_sticky.png")), timeout=90000)
                await page.evaluate("window.__croFixed.forEach(e => e.style.setProperty('display', 'none', 'important')); window.scrollTo(0, 0)")
        if full:
            # A full-page capture paints bottom-fixed bars (sticky add-to-cart, chat bubbles) mid-page over the content.
            # Hide them in before and after alike; top-fixed headers stay. The fold screenshots show the real bars.
            await page.evaluate("""() => { for (const el of document.querySelectorAll('body *')) {
                const s = getComputedStyle(el); if (s.position !== 'fixed') continue;
                const r = el.getBoundingClientRect(); if (r.height > 0 && r.top > innerHeight * 0.5)
                  el.style.setProperty('display', 'none', 'important'); } }""")
            h = await page.evaluate("document.documentElement.scrollHeight")
            await page.screenshot(path=str(png), full_page=True, timeout=90000,
                                  clip={"x": 0, "y": 0, "width": cfg["viewport"]["width"], "height": min(h, cap_px)})
        else:
            await page.screenshot(path=str(png), timeout=90000)
    finally:
        await asyncio.wait_for(ctx.close(), timeout=CONTEXT_CLOSE_SECONDS)


async def _safe_psi(url: str) -> dict:
    try:
        return await run_psi(url)
    except Exception as e:  # never let a background task raise unobserved
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


async def _profile(llm, info: dict, opts: Options, tag: str) -> dict:
    """Category + competitor candidates. Cached per page for 30 days; the fast model, or the seed list without a key."""
    key = hashlib.sha1((info.get("url") or info.get("title", "")).encode()).hexdigest()[:12]
    f = opts.cache_dir / "profiles" / f"{key}.json"
    if f.exists() and not opts.force and time.time() - f.stat().st_mtime < 30 * 86400:
        return json.loads(f.read_text(encoding="utf-8"))
    prof = None
    if llm and not opts.dry_run:
        try:
            prof = await llm_mod.profile_page(llm, info, tag)
        except Exception as e:
            print(f"[warn] competitor discovery via {opts.fast_model} failed ({type(e).__name__}: {str(e)[:100]}); using seed list")
    if prof is None:
        return heuristic_profile(info)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(prof, ensure_ascii=False), encoding="utf-8")
    return prof


async def _competitors(item, bank, profile, target_url, opts, outdir, exclude_brand=""):
    overrides = item.competitors or opts.competitors
    if opts.n_competitors <= 0:
        return [], ["Competitor analysis switched off."]
    if exclude_brand:
        profile = {**profile, "competitors": [c for c in profile.get("competitors", [])
                                              if c.get("brand", "").strip().lower() != exclude_brand.strip().lower()]}
    comps, notes = await find_competitors(bank, profile, target_url, overrides, opts.n_competitors)
    copy_shots(comps, outdir)
    return comps, notes


async def _process_url(item, browser, bsem, bank, llm, llm_note, opts, outdir) -> dict:
    psi_task = asyncio.create_task(_safe_psi(item.raw)) if opts.psi else None
    _p(opts, item, "capturing")
    cap = await capture_url(browser, item.raw, outdir / "capture", opts.timeout_ms, slots=bsem)
    _p(opts, item, "scoring")
    frozen = freeze_html(cap.desktop.html, cap.final_url)
    (outdir / "original.html").write_text(frozen, encoding="utf-8")
    tokens = tok.from_dom(cap.desktop.tokens_raw, cap.desktop.facts)
    F = rules.normalize_dom(cap.desktop.facts, cap.mobile.facts, cap.mobile.sticky_cta, cap.mobile.popups,
                            cap.desktop.tokens_raw["page_bg"], cap.desktop.popups)
    reduced = cap.mode != "full"
    if reduced and not (F.get("atc_present") or F.get("price_text")):
        raise CaptureError("The fallback capture did not render a price or add-to-cart button. Upload screenshots of the page.")
    rubric = rules.evaluate(F)
    lab = {} if reduced else lab_metrics(cap.final_url, cap.mobile.perf, cap.responses)
    lab_s, lab_rows = (None, []) if reduced else lab_score(lab)
    target_summary = summarize(item.raw, cap.final_url, cap, F, rubric, lab, lab_s)

    _p(opts, item, "competitors")
    d = cap.desktop.facts
    info = {"url": cap.final_url, "brand_name": d.get("brand_name"), "page_title": d.get("page_title"),
            "title": F.get("title_text"), "price": F.get("price_text"), "currency": F.get("currency"),
            "breadcrumbs": d.get("breadcrumbs"), "headings": (d.get("headings") or [])[:10],
            "bullets": (d.get("bullet_texts") or [])[:5], "region_text": d.get("region_text", "")[:800]}
    profile = await _profile(llm, info, opts, outdir.name)
    comps, comp_notes = await _competitors(item, bank, profile, cap.final_url, opts, outdir)
    comparison = compare(rubric, target_summary, comps)

    perf = {"source": "lab", "score": lab_s, "lab": lab, "lab_rows": lab_rows, "psi": None,
            "note": "Lab budget score from our own mobile load (no CPU/network throttling). Weight, requests and CLS are "
                    "reliable; timings are optimistic. Set PSI_API_KEY for Lighthouse + real-user (CrUX) data."}
    if reduced:
        perf.update(source="unavailable", note="The full page crashed in Chromium, so no lab speed score is available.")
    if psi_task:
        try:
            psi = await asyncio.wait_for(psi_task, timeout=150)
        except asyncio.TimeoutError:
            psi = {"error": "PageSpeed Insights timed out"}
        if "error" in psi:
            perf["psi_error"] = psi["error"]
        else:
            perf.update(source="PageSpeed Insights", score=psi["score"], psi=psi,
                        note="Score is Google Lighthouse (mobile). Lab budget checks below are from our own load.")
    perf_label = "Lighthouse mobile" if perf["psi"] else ("Reduced capture" if reduced else "Lab budget estimate")

    error = ""
    plan = None
    if llm:
        _p(opts, item, "waiting for batch" if llm.batch else "analysing")
        try:
            perf_brief = {"score": perf["score"], "source": perf["source"],
                          **{k: lab[k] for k in ("total_kb", "js_kb", "requests", "cls", "third_party_domains", "lcp_lazy")
                             if k in lab}}
            plan = await llm_mod.audit_url(llm, cap, F, rubric, tokens, perf_brief, profile, brief_for_model(comparison),
                                           comps, tag=outdir.name, batch_key=item.idx)
        except llm_mod.DryRun as e:
            error = str(e)
        except Exception as e:
            error = f"LLM analysis failed ({type(e).__name__}: {str(e)[:160]}); used rules engine instead."
    elif llm_note:
        error = llm_note
    if plan is None:
        plan = fallback_plan(F, rubric, frozen, comparison)

    _p(opts, item, "building")
    page_text = BeautifulSoup(frozen, "lxml").get_text(" ")
    ctx = GuardContext(tokens=tokens, original_text=page_text, allowed_images=page_images(frozen))
    optimized, applied, rejected = apply_patches(frozen, plan.get("patches") or [], ctx, F, page_text)
    (outdir / "optimized.html").write_text(optimized, encoding="utf-8")

    shots_dir = outdir / "shots"
    shots_dir.mkdir(exist_ok=True)
    _p(opts, item, "rendering")
    failed = await _render_all([
        (browser, outdir / "original.html", shots_dir / "before_mobile.png", True, True, False),
        (browser, outdir / "optimized.html", shots_dir / "after_mobile.png", True, True, True),
        (browser, outdir / "original.html", shots_dir / "before_desktop.png", False, True, False, 3600),
        (browser, outdir / "optimized.html", shots_dir / "after_desktop.png", False, True, True, 3600),
    ], bsem)
    if failed:
        error = (error + " " if error else "") + "Preview render timed out (" + ", ".join(failed) + "); open optimized.html directly."
    shots = {"before_mobile": "shots/before_mobile.png", "after_mobile": "shots/after_mobile.png",
             "before_desktop": "shots/before_desktop.png", "after_desktop": "shots/after_desktop.png"}
    if (shots_dir / "after_mobile_sticky.png").exists():
        shots["after_sticky"] = "shots/after_mobile_sticky.png"
    r = _report_dict(item, opts, llm, outdir, plan, rubric, perf, perf_label, tokens, ctx, applied, rejected, error,
                     shots=shots, original="original.html", optimized="optimized.html", profile=profile,
                     comparison=comparison, comp_notes=comp_notes, target=target_summary)
    if reduced:
        skipped = "storefront scripts, media and embedded frames" if cap.mode == "static" else (
            "media, embedded frames and some third-party requests")
        r["capture_note"] = (f"Chromium crashed on the full storefront. The retry skipped {skipped}, "
                             "so widgets that depend on them may be missing from the audit.")
    write_report(outdir, r)
    return r


def _merge_palettes(paths: list[str]) -> list[dict]:
    seen = {}
    for p in paths:
        for c in extract_palette(Path(p)):
            seen[c["hex"]] = seen.get(c["hex"], 0) + c["weight"] / len(paths)
    return [{"hex": h, "weight": round(w, 4)} for h, w in sorted(seen.items(), key=lambda kv: -kv[1])[:14]]


def _vision_summary(vision: dict, F: dict, rubric: dict) -> dict:
    t = vision.get("text") or {}
    return {"brand": (vision.get("brand") or {}).get("name") or "", "price": t.get("price", ""), "rating": None,
            "reviews": None, "rating_text": t.get("rating_text", ""), "images": F.get("image_count"),
            "video": bool(F.get("video")), "sticky_atc": bool(F.get("sticky_atc")),
            "trust": [TRUST_LABELS[k] for k, v in (F.get("trust_near") or {}).items() if v],
            "offers": "shown" if F.get("offers_near") else "", "delivery": "shown" if F.get("delivery_info") else "",
            "faq": bool(F.get("faq")), "lab": {}, "cro_score": rubric["cro_score"]}


async def _process_image(item, browser, bsem, bank, llm, llm_note, opts, outdir) -> dict:
    if not llm:
        raise RuntimeError("Screenshot inputs need ANTHROPIC_API_KEY (vision analysis). " + (llm_note or ""))
    shots_dir = outdir / "shots"
    shots_dir.mkdir(exist_ok=True)
    given = {}
    for p in item.paths:
        given.setdefault("mobile" if is_mobile_shot(Path(p)) else "desktop", Path(p))
    perf = {"source": "n/a", "score": None, "note": "Performance needs a live URL. A screenshot has no network, script or "
                                                    "rendering data, so it cannot be measured. Re-run with the page URL."}
    palette = _merge_palettes([str(p) for p in given.values()])
    views = []
    for view, path in given.items():
        (shots_dir / f"before_{view}{path.suffix.lower()}").write_bytes(path.read_bytes())
        chunks = llm_mod.view_chunks(path, view)
        with Image.open(path) as im:
            W, H = im.size
        s = chunks[0]["scale"]
        fold_css = 844 if view == "mobile" else 900
        views.append({"view": view, "path": path, "chunks": chunks, "scale": s, "w_s": round(W * s), "h_s": round(H * s),
                      "fold_s": round(fold_css * W / css_width(view, W) * s)})
    _p(opts, item, "analysing")
    vision = await llm_mod.extract_screenshot(llm, views, palette, tag=outdir.name)
    if vision.get("is_product_page") is False:
        raise RuntimeError("The screenshot doesn't look like a product page.")
    tokens = tok.from_screenshot(palette, vision["brand"])
    F = llm_mod.normalize_vision(vision, list(given))
    rubric = rules.evaluate(F)
    profile = {"brand": vision["brand"].get("name", ""), **(vision.get("profile") or {}), "source": opts.model}
    _p(opts, item, "competitors")
    comps, comp_notes = await _competitors(item, bank, profile, "", opts, outdir, exclude_brand=profile["brand"])
    comparison = compare(rubric, _vision_summary(vision, F, rubric), comps)

    error = ""
    _p(opts, item, "waiting for batch" if llm.batch else "analysing")
    try:
        plan = await llm_mod.audit_screenshot(llm, views, vision, rubric, tokens, profile, brief_for_model(comparison),
                                              comps, tag=outdir.name, batch_key=item.idx)
    except llm_mod.DryRun:
        raise
    except Exception as e:
        error = f"LLM audit failed ({type(e).__name__}: {str(e)[:160]}); rubric results only."
        plan = {"page_summary": "", "positioning": "", "strengths": [], "issues": [], "recommendations": [], "patches": [],
                "competitor_insights": [], "source": "rules"}

    _p(opts, item, "building")
    t = vision.get("text") or {}
    visible = " ".join(str(x) for x in t.values())
    ctx = GuardContext(tokens=tokens, original_text=visible, allowed_images=set())
    page = {"title_text": t.get("title", ""), "price_text": t.get("price", ""), "compare_text": t.get("compare_price", ""),
            "atc_label": t.get("atc_label", ""), "trust_snippets": {}, "schema_rating_obj": None, "variant_options": [],
            "page_text": visible, "digits": ctx.digits}
    cviews = [View(v["view"], v["path"], vision.get("sections") or [], v["scale"]) for v in views]
    doc, applied, rejected = build_composite(cviews, plan.get("patches") or [], page, ctx, tokens, outdir,
                                             profile["brand"] or item.label)
    (outdir / "optimized.html").write_text(doc, encoding="utf-8")
    _p(opts, item, "rendering")
    shots = {f"before_{v}": f"shots/before_{v}{p.suffix.lower()}" for v, p in given.items()}
    failed = await _render_all([(browser, outdir / "optimized.html", shots_dir / f"after_{v}.png", v == "mobile", True,
                                 True, 6000 if v == "mobile" else 4500) for v in given], bsem)
    for v in given:
        if (shots_dir / f"after_{v}.png").exists():
            shots[f"after_{v}"] = f"shots/after_{v}.png"
    if failed:
        error = (error + " " if error else "") + "Preview render timed out (" + ", ".join(failed) + "); open optimized.html directly."
    if (shots_dir / "after_mobile_sticky.png").exists():
        shots["after_sticky"] = "shots/after_mobile_sticky.png"
    r = _report_dict(item, opts, llm, outdir, plan, rubric, perf, "needs live URL", tokens, ctx, applied, rejected, error,
                     shots=shots, original=None, optimized="optimized.html", profile=profile, comparison=comparison,
                     comp_notes=comp_notes, target=_vision_summary(vision, F, rubric))
    write_report(outdir, r)
    return r


def _report_dict(item, opts, llm, outdir, plan, rubric, perf, perf_label, tokens, ctx, applied, rejected, error, shots,
                 original, optimized, profile, comparison, comp_notes, target):
    recs = plan.get("recommendations") or []
    used = {rid for x in recs for rid in x.get("research") or []} | {rid for x in plan.get("issues") or []
                                                                      for rid in x.get("research") or []}
    used |= {rid for c in rubric["checks"] for rid in c.get("research") or []}
    if perf.get("lab_rows") or perf.get("psi"):
        used |= {"google-speed", "deloitte-speed", "google-cwv"}
    return {
        "label": item.label, "slug": outdir.name, "input": item.raw, "kind": item.kind,
        "generated": datetime.now().strftime("%d %b %Y, %H:%M"),
        "model": opts.model, "fast_model": opts.fast_model, "effort": opts.effort, "batch": opts.batch,
        "plan_source": plan.get("source", "rules"),
        "summary": plan.get("page_summary") or "", "positioning": plan.get("competitive_position") or "",
        "cro_score": rubric["cro_score"], "grade": rubric["grade"], "coverage": rubric["coverage"],
        "viewport_scores": rubric["viewport_scores"],
        "categories": rubric["categories"], "checks": rubric["checks"], "flags": rubric["flags"],
        "perf_score": perf.get("score"), "perf_label": perf_label, "perf": perf,
        "issues": plan.get("issues") or [], "strengths": plan.get("strengths") or [],
        "insights": plan.get("competitor_insights") or [],
        "recommendations": recs, "patches": plan.get("patches") or [],
        "applied": applied, "rejected": rejected, "guard_log": ctx.log, "tokens": tokens.describe(),
        "shots": shots, "original": original, "optimized": optimized, "error": error,
        "profile": profile, "comparison": comparison, "comp_notes": comp_notes, "target": target,
        "research": {k: BY_ID[k] for k in sorted(used) if k in BY_ID},
        "cost": llm.cost_for(outdir.name) if llm else None,
    }
