"""Competitive analysis: find 2-3 leading competitors in the same category, locate their most comparable product
page, capture it (desktop + mobile) and score it with the same rubric, then compare check by check.

Cost: discovery is one small text-only call to the fast model (or a keyword match against the seed list when no
API key is set). Finding the competitor's product page, capturing and scoring it is code only, no tokens. Captures
are cached on disk and shared across the batch, so ten protein pages reuse the same three competitor captures."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image

from . import rules
from .capture import MOBILE_UA, CaptureError, capture_url
from .perf import lab_metrics, lab_score

SEED = json.loads((Path(__file__).parent / "data" / "competitors.json").read_text(encoding="utf-8"))["categories"]
STOP = {"the", "and", "for", "with", "from", "pack", "of", "a", "an", "in", "to", "by", "buy", "online", "best", "new",
        "men", "women", "g", "gm", "kg", "ml", "l", "x", "pcs", "flavour", "flavor", "combo", "size", "variant"}
SKIP_PRODUCT = re.compile(r"gift ?card|sample|trial ?pack|e-?gift|subscription box|mystery", re.I)
TRUST_LABELS = {"secure": "secure checkout", "returns": "returns", "authentic": "authenticity", "warranty": "warranty",
                "free_shipping": "free shipping", "cod": "COD", "quality": "quality claims"}


def host_of(url: str) -> str:
    h = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    return h.removeprefix("www.")


SEED_MARKET = {}
for _c in SEED:
    for _b, _d in _c["brands"]:
        SEED_MARKET.setdefault(host_of(_d), set()).add(_c["market"])
TLD_MARKET = {".co.in": "IN", ".in": "IN", ".co.uk": "GB", ".uk": "GB", ".com.au": "AU", ".ae": "AE", ".sg": "SG"}


def _market_mismatch(domain: str, market: str) -> bool:
    """A competitor known (seed list) or visibly (country domain) to sell in another market."""
    if not market:
        return False
    h = host_of(domain)
    if h in SEED_MARKET:
        return market not in SEED_MARKET[h]
    tld = next((m for t, m in TLD_MARKET.items() if h.endswith(t)), None)
    return tld is not None and tld != market


def _norm_market(m) -> str:
    m = (m or "").upper()
    return "" if m in ("", "UNKNOWN", "NONE") else {"UK": "GB"}.get(m[:2], m[:2])


def _brand_from_host(host: str) -> str:
    for c in SEED:
        for b, d in c["brands"]:
            if host_of(d) == host:
                return b
    return host


def tokens(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 1 and w not in STOP]


def seed_prompt() -> str:
    return "\n".join(f"- {c['name']} [{c['market']}]: " + ", ".join(f"{b} ({d})" for b, d in c["brands"]) for c in SEED)


def match_category(text: str, market: str = "") -> dict | None:
    t = " " + (text or "").lower() + " "
    best, score = None, 0
    for c in SEED:
        if market and c["market"] != market:
            continue
        s = sum(len(k) for k in c["keywords"] if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", t))
        if s > score:
            best, score = c, s
    return best


def _seed_fill(profile: dict, me: str, have: set) -> list[dict]:
    """Seed-list brands of the same category and market, as back-ups when the model's picks can't be resolved."""
    market = _norm_market(profile.get("market"))
    if market and market not in {c["market"] for c in SEED}:
        return []  # no seed brands for this market: fewer competitors beats wrong-market ones
    name = (profile.get("category") or "").lower()
    cat = next((c for c in SEED if c["name"].lower() == name and (not market or c["market"] == market)), None) \
        or match_category(f"{profile.get('product_type', '')} {profile.get('category', '')}", market)
    if not cat:
        return []
    q = profile.get("product_type") or ""
    own = (profile.get("brand") or "").strip().lower()
    return [{"brand": b, "domain": d, "search_query": q, "why": f"Leading {cat['name'].lower()} brand (seed list)."}
            for b, d in cat["brands"] if host_of(d) not in have and host_of(d) != me and b.lower() != own]


def heuristic_profile(info: dict) -> dict:
    """No-API-key fallback: keyword match on the page's own text against the seed list."""
    market = {"INR": "IN", "USD": "US"}.get(info.get("currency") or "", "")
    text = " ".join([info.get("title", ""), info.get("breadcrumbs", ""), " ".join(info.get("headings", [])),
                     info.get("region_text", "")[:600]])
    cat = match_category(text, market) or match_category(text)
    me = host_of(info.get("url", ""))
    title = (info.get("title") or "").lower()
    brand_toks = set(tokens(info.get("brand_name", "")) + tokens(me.split(".")[0]))
    in_title = [k for k in (cat or {}).get("keywords", []) if k in title]
    title_toks = [w for w in tokens(title) if w not in brand_toks and not w.isdigit()]
    # the product type as the title names it ("face oil", "whey protein"), else the last words of the title
    query = (" ".join(sorted(in_title, key=len, reverse=True)[:2]) if in_title else " ".join(title_toks[-3:])) or "bestseller"
    comps = [{"brand": b, "domain": d, "search_query": query, "why": f"Leading {cat['name'].lower()} brand (seed list)."}
             for b, d in (cat or {}).get("brands", []) if host_of(d) != me]
    return {"brand": info.get("brand_name") or me, "category": cat["name"] if cat else "Unknown",
            "product_type": query, "market": market or "unknown", "price_positioning": "unknown", "audience": "",
            "competitors": comps[:6], "source": "seed list" if cat else "none"}


# ------------------------------------------------------------------ finding the comparable product page
MULTI = re.compile(r"\b(combo|kit|bundle|pack of|set of|duo|trio|regime|routine)\b", re.I)


def _score(query_toks: list[str], text: str) -> float:
    """Share of query words in the product title; combos/kits lose out to the single comparable product."""
    if not query_toks:
        return 0.0
    t = set(tokens(text))
    s = sum(1 for q in query_toks if q in t or any(w.startswith(q[:5]) for w in t if len(q) >= 5)) / len(query_toks)
    if MULTI.search(text or "") and not MULTI.search(" ".join(query_toks)):
        s -= 0.3
    return s


async def _get(http: httpx.AsyncClient, url: str, **kw):
    try:
        r = await http.get(url, **kw)
        return r if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


async def resolve_pdp(http: httpx.AsyncClient, domain: str, query: str) -> str | None:
    """Most comparable product URL on a competitor's site: Shopify search, then products.json, then the sitemap."""
    base = "https://" + host_of(domain)
    qt = tokens(query)
    fallback = None  # the store's own top search hit, used only if nothing matches the query better
    # 1. Shopify predictive search (ranked by relevance on the store itself)
    for q in dict.fromkeys([query, " ".join(qt[:2]), qt[-1] if qt else ""]):
        if not q:
            continue
        r = await _get(http, f"{base}/search/suggest.json",
                       params={"q": q, "resources[type]": "product", "resources[limit]": "8"})
        if r is None:
            break
        try:
            prods = r.json()["resources"]["results"]["products"]
        except (ValueError, KeyError, TypeError):
            break
        prods = [p for p in prods if p.get("url") and not SKIP_PRODUCT.search(p.get("title", ""))]
        if prods:
            best = max(prods, key=lambda p: (_score(qt, p.get("title", "")), p.get("available", True)))
            url = urljoin(str(r.url), best["url"].split("?")[0])
            if _score(qt, best.get("title", "")) > 0.3:
                return url
            fallback = fallback or url
    # 2. Shopify products.json
    r = await _get(http, f"{base}/products.json", params={"limit": "250"})
    if r is not None:
        try:
            prods = [p for p in r.json().get("products", []) if not SKIP_PRODUCT.search(p.get("title", ""))]
        except ValueError:
            prods = []
        if prods:
            best = max(prods, key=lambda p: _score(qt, f"{p.get('title', '')} {p.get('product_type', '')} {' '.join(p.get('tags') or [])}"))
            if _score(qt, best.get("title", "") + " " + best.get("product_type", "")) > 0.3:
                return urljoin(str(r.url), f"/products/{best['handle']}")
    if fallback:
        return fallback
    # 3. sitemap (most non-Shopify storefronts)
    r = await _get(http, f"{base}/sitemap.xml")
    if r is None:
        return None
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text[:5_000_000])
    if "<sitemapindex" in r.text[:2000]:
        subs = sorted(locs, key=lambda u: (0 if "product" in u.lower() else 1))[:3]
        locs = []
        for sm in subs:
            rr = await _get(http, sm)
            if rr is not None:
                locs += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", rr.text[:5_000_000])
    pdp = [u for u in locs if re.search(r"/(products?|p|dp|item)/[^/]+|-p-?\d+|/sp-\d+", u, re.I)]
    if not pdp:
        return None
    best = max(pdp, key=lambda u: _score(qt, urlparse(u).path.replace("-", " ").replace("/", " ")))
    return best if _score(qt, urlparse(best).path.replace("-", " ")) > 0 else None


# ------------------------------------------------------------------ capture + summary (cached, shared across the batch)
def _jpeg(src: Path, dest: Path, width: int, max_h: int):
    with Image.open(src) as im:
        im = im.convert("RGB")
        s = width / im.width
        im = im.resize((width, max(1, int(im.height * s))), Image.LANCZOS)
        im = im.crop((0, 0, width, min(im.height, max_h)))
        im.save(dest, "JPEG", quality=80, optimize=True)


MONEY = re.compile(r"(?:₹|rs\.?\s?|inr\s?|\$|€|£|aed\s?)\s?\d[\d,]*(?:\.\d+)?", re.I)


def clean_price(price: str, compare: str = "") -> str:
    """'Regular price Sale price ₹1,299 ₹1,499 -' -> '₹1,299 (MRP ₹1,499)'."""
    amounts = MONEY.findall(price or "")
    if not amounts:
        return (price or "").strip()[:40]
    mrp = MONEY.findall(compare or "") or amounts[1:2]
    mrp = [x for x in mrp if x != amounts[0]]
    return amounts[0].strip() + (f" (MRP {mrp[0].strip()})" if mrp else "")


def summarize(url: str, final_url: str, cap, F: dict, rubric: dict, lab: dict | None, lab_s: int | None) -> dict:
    d, m = cap.desktop.facts, cap.mobile.facts
    st = {c["id"]: c["status"] for c in rubric["checks"]}
    return {
        "url": url, "final_url": final_url, "domain": host_of(final_url),
        "brand": d.get("brand_name") or host_of(final_url), "title": F.get("title_text", ""),
        "price": clean_price(F.get("price_text", ""), F.get("compare_text", "")), "compare": F.get("compare_text", ""),
        "discount": F.get("discount_text") or "",
        "currency": F.get("currency", ""), "atc_label": F.get("atc_label", ""),
        "rating": d.get("rating_value"), "reviews": d.get("review_count"), "rating_text": F.get("rating_near_title") or "",
        "images": F.get("image_count"), "video": bool(F.get("video")), "sticky_atc": bool(F.get("sticky_atc")),
        "bullets": d.get("bullet_texts") or [], "headings": d.get("headings") or [],
        "trust": [TRUST_LABELS[k] for k, v in (F.get("trust_near") or {}).items() if v],
        "offers": d.get("offer_snippet") or m.get("offer_snippet") or "",
        "delivery": d.get("delivery_snippet") or m.get("delivery_snippet") or "",
        "faq": bool(F.get("faq")), "cross_sell": bool(F.get("cross_sell")),
        "cro_score": rubric["cro_score"], "grade": rubric["grade"],
        "mobile_score": rubric["viewport_scores"]["mobile"], "desktop_score": rubric["viewport_scores"]["desktop"],
        "categories": {k: v["score"] for k, v in rubric["categories"].items()}, "checks": st,
        "perf_score": lab_s, "lab": {k: (lab or {}).get(k) for k in ("total_kb", "js_kb", "requests", "lcp_ms", "cls")},
    }


class CompetitorBank:
    def __init__(self, cache_dir: Path, browser, bsem: asyncio.Semaphore, timeout_ms: int, ttl_days: float = 7):
        self.dir = cache_dir / "competitors"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.browser, self.bsem, self.timeout_ms, self.ttl = browser, bsem, timeout_ms, ttl_days * 86400
        self.tasks: dict[str, asyncio.Task] = {}
        self.http = httpx.AsyncClient(follow_redirects=True, timeout=15,
                                      headers={"User-Agent": MOBILE_UA, "Accept": "application/json, text/html, */*"})

    async def close(self):
        await self.http.aclose()

    def _key(self, url: str) -> str:
        u = urlparse(url)
        return hashlib.sha1(f"{host_of(url)}{u.path.rstrip('/')}".lower().encode()).hexdigest()[:12]

    async def get(self, url: str) -> dict | None:
        k = self._key(url)
        if k not in self.tasks:
            self.tasks[k] = asyncio.ensure_future(self._load(url, self.dir / k))
        return await asyncio.shield(self.tasks[k])

    async def _load(self, url: str, d: Path) -> dict | None:
        f = d / "summary.json"
        if f.exists():
            s = json.loads(f.read_text(encoding="utf-8"))
            if time.time() - s.get("captured_ts", 0) < (self.ttl if s.get("ok") else 3600):  # failures retry after 1h
                return s if s.get("ok") else None
        d.mkdir(parents=True, exist_ok=True)
        try:
            cap = await capture_url(self.browser, url, d / "raw", self.timeout_ms, light=True, slots=self.bsem)
            F = rules.normalize_dom(cap.desktop.facts, cap.mobile.facts, cap.mobile.sticky_cta, cap.mobile.popups,
                                    "rgb(255, 255, 255)", cap.desktop.popups)
            if not (F.get("atc_present") or F.get("price_text")):
                raise CaptureError("not a product page (no price or add-to-cart found)")
            rubric = rules.evaluate(F)
            lab = lab_metrics(cap.final_url, cap.mobile.perf, cap.responses)
            lab_s, _ = lab_score(lab)
            s = summarize(url, cap.final_url, cap, F, rubric, lab, lab_s)
            _jpeg(cap.mobile.screenshot_fold, d / "mobile_fold.jpg", 390, 844)
            _jpeg(cap.mobile.screenshot_full, d / "mobile_full.jpg", 390, 5000)
            _jpeg(cap.desktop.screenshot_fold, d / "desktop_fold.jpg", 960, 600)
            shutil.rmtree(d / "raw", ignore_errors=True)
            s.update(ok=True, captured_ts=time.time(), dir=str(d))
        except Exception as e:  # a competitor failing never fails the audit
            shutil.rmtree(d / "raw", ignore_errors=True)
            s = {"ok": False, "url": url, "error": f"{type(e).__name__}: {str(e)[:160]}", "captured_ts": time.time()}
        f.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
        return s if s.get("ok") else None


async def find_competitors(bank: CompetitorBank, profile: dict, target_url: str, overrides: list[str],
                           n: int = 3) -> tuple[list[dict], list[str]]:
    """Returns (competitor summaries, notes). Overrides are URLs (used as-is) or domains (searched)."""
    me = host_of(target_url)
    notes = []
    if overrides:
        def as_url(o):  # "brand.com" is a domain to search; anything with a path is a product page
            if "/" not in o.split("://")[-1].strip("/"):
                return ""
            return o if "://" in o else "https://" + o
        cands = [{"brand": _brand_from_host(host_of(o)), "domain": host_of(o), "url": as_url(o),
                  "search_query": profile.get("product_type", ""), "why": "Chosen by you."} for o in overrides]
    else:
        market = _norm_market(profile.get("market"))
        cands = [c for c in profile.get("competitors", []) if host_of(c.get("domain", "")) and host_of(c["domain"]) != me]
        dropped = [c for c in cands if _market_mismatch(c["domain"], market)]
        if dropped:
            notes.append("Skipped (sell in another market than this page): " + ", ".join(c.get("brand") or c["domain"] for c in dropped) + ".")
        cands = [c for c in cands if c not in dropped]
        cands = (cands[:6] + _seed_fill(profile, me, {host_of(c["domain"]) for c in cands}))[:8]

    async def resolve(c):
        if c.get("url"):
            return c["url"]
        return await resolve_pdp(bank.http, c["domain"], c.get("search_query") or profile.get("product_type", ""))

    urls = await asyncio.gather(*(resolve(c) for c in cands))
    queue = []
    for c, u in zip(cands, urls):
        if u and host_of(u) != me:
            queue.append((c, u))
        else:
            notes.append(f"{c.get('brand') or c.get('domain')}: no comparable product page found on {c.get('domain')}.")
    out: list[dict] = []
    while queue and len(out) < n:
        take, queue = queue[: n - len(out)], queue[n - len(out):]
        got = await asyncio.gather(*(bank.get(u) for _, u in take))
        for (c, u), s in zip(take, got):
            if s:
                out.append({**s, "brand": c.get("brand") or s["brand"], "why": c.get("why", "")})
            else:
                notes.append(f"{c.get('brand') or host_of(u)}: page could not be captured ({u}).")
    if len(out) < 2:
        notes.append(f"Only {len(out)} competitor page(s) could be analysed; add competitor URLs to the input line "
                     "(`url | label | vs: https://competitor.com/products/x`) for a fuller comparison.")
    return out, notes


def copy_shots(comps: list[dict], outdir: Path) -> None:
    """Copy each competitor's screenshots next to the report so it stays self-contained."""
    dest = outdir / "competitors"
    dest.mkdir(exist_ok=True)
    for i, c in enumerate(comps, 1):
        c["shots"] = {}
        for name in ("mobile_fold", "mobile_full", "desktop_fold"):
            src = Path(c.get("dir", "")) / f"{name}.jpg"
            if src.exists():
                shutil.copyfile(src, dest / f"c{i}_{name}.jpg")
                c["shots"][name] = f"competitors/c{i}_{name}.jpg"


# ------------------------------------------------------------------ comparison (code only)
def compare(target_rubric: dict, target_summary: dict, comps: list[dict]) -> dict:
    if not comps:
        return {"competitors": [], "matrix": [], "gaps": [], "leads": [], "table_stakes": [], "benchmark": None, "facts": []}
    rows, gaps, leads, stakes = [], [], [], []
    for c in target_rubric["checks"]:
        cs = [k["checks"].get(c["id"], "na") for k in comps]
        if c["status"] == "na" and all(x == "na" for x in cs):
            continue
        passing = [k["brand"] for k, x in zip(comps, cs) if x == "pass"]
        row = {"check_id": c["id"], "title": c["title"], "category": c["category"], "viewport": c["viewport"],
               "weight": c["weight"], "target": c["status"], "comps": cs, "passing": passing}
        rows.append(row)
        measured = [x for x in cs if x != "na"]
        if c["status"] in ("fail", "partial") and passing:
            gaps.append(row)
            if len(measured) >= 2 and all(x == "pass" for x in measured):
                stakes.append(row)
        elif c["status"] == "pass" and measured and sum(x != "pass" for x in measured) >= max(1, len(measured) / 2):
            leads.append(row)
    gaps.sort(key=lambda r: (-r["weight"] * len(r["passing"]), r["title"]))
    leads.sort(key=lambda r: -r["weight"])
    scores = [target_rubric["cro_score"]] + [k["cro_score"] for k in comps]
    bench = {"target": target_rubric["cro_score"], "avg": round(sum(scores[1:]) / len(comps)), "best": max(scores[1:]),
             "best_brand": max(comps, key=lambda k: k["cro_score"])["brand"],
             "rank": sorted(scores, reverse=True).index(target_rubric["cro_score"]) + 1, "of": len(scores),
             "mobile": {"target": target_rubric["viewport_scores"]["mobile"],
                        "avg": _avg([k["mobile_score"] for k in comps])},
             "desktop": {"target": target_rubric["viewport_scores"]["desktop"],
                         "avg": _avg([k["desktop_score"] for k in comps])}}
    T = target_summary

    def yn(v):
        return "yes" if v else "no"

    facts = [
        ("Price", T["price"], [k["price"] for k in comps]),
        ("Rating", _rating(T), [_rating(k) for k in comps]),
        ("Product images", T["images"], [k["images"] for k in comps]),
        ("Video", yn(T["video"]), [yn(k["video"]) for k in comps]),
        ("Sticky add-to-cart (mobile)", yn(T["sticky_atc"]), [yn(k["sticky_atc"]) for k in comps]),
        ("Trust cues near CTA", ", ".join(T["trust"]) or "none", [", ".join(k["trust"]) or "none" for k in comps]),
        ("Offer shown", T["offers"] or "none", [k["offers"] or "none" for k in comps]),
        ("Delivery info", T["delivery"] or "none", [k["delivery"] or "none" for k in comps]),
        ("FAQ", yn(T["faq"]), [yn(k["faq"]) for k in comps]),
        ("Mobile page weight", _kb(T), [_kb(k) for k in comps]),
    ]
    return {"competitors": comps, "matrix": rows, "gaps": gaps, "leads": leads, "table_stakes": stakes,
            "benchmark": bench, "facts": [{"label": a, "target": b, "comps": c} for a, b, c in facts]}


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs)) if xs else None


def _rating(s):
    if s.get("rating") and s.get("reviews"):
        return f"{s['rating']:g} ({s['reviews']:,} reviews)"
    if s.get("reviews"):
        return f"{s['reviews']:,} reviews"
    return s.get("rating_text") or "none shown"


def _kb(s):
    kb = (s.get("lab") or {}).get("total_kb")
    return f"{kb / 1024:.1f} MB" if kb else "n/a"


def brief_for_model(comparison: dict) -> dict:
    """Compact competitor data for the audit prompt (keeps input tokens low)."""
    return {
        "competitors": [{k: c.get(k) for k in ("brand", "url", "title", "price", "rating", "reviews", "images", "video",
                                               "sticky_atc", "trust", "offers", "delivery", "faq", "cro_score",
                                               "mobile_score", "desktop_score")}
                        | {"bullets": c.get("bullets", [])[:5], "sections": c.get("headings", [])[:10]}
                        for c in comparison.get("competitors", [])],
        "gaps": [{"check": g["check_id"], "title": g["title"], "viewport": g["viewport"], "done_by": g["passing"]}
                 for g in comparison.get("gaps", [])[:12]],
        "leads": [{"check": g["check_id"], "title": g["title"]} for g in comparison.get("leads", [])[:6]],
        "benchmark": comparison.get("benchmark"),
    }
