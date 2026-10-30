"""Performance: Google PageSpeed Insights (real Lighthouse + CrUX field data) when reachable,
otherwise a transparent lab budget score from our own mobile load."""
from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

PSI = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def _site(host: str) -> str:
    parts = (host or "").split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "net", "org", "gov", "ac", "edu"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


async def run_psi(url: str, key: str | None = None, timeout: float = 120) -> dict:
    params = {"url": url, "strategy": "mobile", "category": "performance"}
    key = key or os.getenv("PSI_API_KEY")
    if key:
        params["key"] = key
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(PSI, params=params)
        r.raise_for_status()
        data = r.json()
    lh = data["lighthouseResult"]
    audits = lh["audits"]

    def num(k):
        return (audits.get(k) or {}).get("numericValue")

    opps = []
    for a in audits.values():
        d = a.get("details") or {}
        if d.get("type") == "opportunity" and (d.get("overallSavingsMs") or 0) >= 300:
            opps.append({"title": a.get("title"), "savings_ms": round(d["overallSavingsMs"])})
    opps.sort(key=lambda o: -o["savings_ms"])
    field = {}
    le = (data.get("loadingExperience") or {}).get("metrics") or {}
    for k, label in (("LARGEST_CONTENTFUL_PAINT_MS", "lcp_ms"), ("INTERACTION_TO_NEXT_PAINT", "inp_ms"),
                     ("CUMULATIVE_LAYOUT_SHIFT_SCORE", "cls")):
        if k in le:
            v = le[k].get("percentile")
            field[label] = {"p75": v / 100 if label == "cls" else v, "category": le[k].get("category")}
    return {
        "source": "psi",
        "score": round((lh["categories"]["performance"]["score"] or 0) * 100),
        "metrics": {"lcp_ms": num("largest-contentful-paint"), "fcp_ms": num("first-contentful-paint"),
                    "tbt_ms": num("total-blocking-time"), "cls": num("cumulative-layout-shift"),
                    "si_ms": num("speed-index")},
        "field": field,
        "opportunities": opps[:6],
    }


def lab_metrics(page_url: str, perf: dict, responses: dict) -> dict:
    host = urlparse(page_url).hostname or ""
    site = _site(host)
    by_url = {}
    for r in perf.get("resources") or []:
        by_url[r["url"]] = {"type": r["type"], "bytes": r["bytes"]}
    for u, (rtype, size, ctype) in responses.items():
        e = by_url.setdefault(u[:400], {"type": rtype, "bytes": 0})
        if not e["bytes"]:
            e["bytes"] = size
        e["rtype"], e["ctype"] = rtype, ctype
    total = sum(e["bytes"] for e in by_url.values()) + (perf.get("navBytes") or 0)
    js = sum(e["bytes"] for e in by_url.values() if e.get("rtype") == "script" or e["type"] == "script")
    img_entries = [e for e in by_url.values() if (e.get("ctype") or "").startswith("image/") or e.get("rtype") == "image"]
    img = sum(e["bytes"] for e in img_entries)
    typed = [e for e in img_entries if e.get("ctype")]
    modern = [e for e in typed if any(t in e["ctype"] for t in ("webp", "avif", "svg"))]
    third = {_site(urlparse(u).hostname or "") for u in by_url} - {site, ""}
    lcp_el = perf.get("lcpEl") or {}
    return {
        "source": "lab",
        "ttfb_ms": round(perf.get("ttfb") or 0), "fcp_ms": round(perf.get("fcp") or 0),
        "lcp_ms": round(perf.get("lcp") or 0), "cls": round(perf.get("cls") or 0, 3), "tbt_ms": round(perf.get("tbt") or 0),
        "load_ms": round(perf.get("load") or 0),
        "requests": len(by_url) + 1, "total_kb": round(total / 1024), "js_kb": round(js / 1024), "img_kb": round(img / 1024),
        "modern_img_share": round(len(modern) / len(typed), 2) if typed else None,
        "third_party_domains": len(third), "third_party_sample": sorted(third)[:12],
        "lcp_element": lcp_el, "lcp_lazy": (lcp_el.get("loading") == "lazy"),
        "blocking_css": perf.get("blockingCss", 0), "blocking_js": perf.get("blockingJs", 0),
        "dom_nodes": perf.get("domNodes", 0),
    }


def _band(v, good, ok, lower_is_better=True):
    if v is None:
        return None
    if lower_is_better:
        return 1.0 if v <= good else 0.5 if v <= ok else 0.0
    return 1.0 if v >= good else 0.5 if v >= ok else 0.0


def lab_score(m: dict) -> tuple[int, list[dict]]:
    """Budget-style score. Weights favour metrics that do not depend on our (unthrottled) network."""
    checks = [
        ("Page weight", m["total_kb"], f"{m['total_kb']} KB", _band(m["total_kb"], 2000, 4000), 15),
        ("JavaScript weight", m["js_kb"], f"{m['js_kb']} KB", _band(m["js_kb"], 400, 1000), 15),
        ("Layout shift (CLS)", m["cls"], f"{m['cls']}", _band(m["cls"], 0.1, 0.25), 15),
        ("Requests", m["requests"], f"{m['requests']}", _band(m["requests"], 80, 150), 10),
        ("Third-party domains", m["third_party_domains"], f"{m['third_party_domains']}", _band(m["third_party_domains"], 10, 20), 10),
        ("Modern image formats", m["modern_img_share"], f"{int((m['modern_img_share'] or 0) * 100)}% WebP/AVIF/SVG",
         _band(m["modern_img_share"], 0.7, 0.4, lower_is_better=False), 10),
        ("LCP (unthrottled lab)", m["lcp_ms"], f"{m['lcp_ms']} ms", _band(m["lcp_ms"] or None, 1500, 3000), 10),
        ("Main-thread blocking", m["tbt_ms"], f"{m['tbt_ms']} ms", _band(m["tbt_ms"], 200, 600), 5),
        ("Render-blocking resources", m["blocking_css"] + m["blocking_js"], f"{m['blocking_css']} CSS, {m['blocking_js']} JS",
         _band(m["blocking_css"] + m["blocking_js"], 3, 8), 5),
        ("LCP image not lazy-loaded", m["lcp_lazy"], "lazy-loaded (delays LCP)" if m["lcp_lazy"] else "eager",
         0.0 if m["lcp_lazy"] else 1.0, 5),
    ]
    got = tot = 0
    rows = []
    for name, _, shown, credit, w in checks:
        if credit is None:
            rows.append({"name": name, "value": "n/a", "status": "na"})
            continue
        got += credit * w
        tot += w
        rows.append({"name": name, "value": shown, "status": "pass" if credit == 1 else "partial" if credit else "fail"})
    return (round(100 * got / tot) if tot else 0), rows
