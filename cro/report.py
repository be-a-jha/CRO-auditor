"""Per-page report, batch index and CSV."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

TEMPLATES = Path(__file__).parent / "templates"


def col(score):
    if score is None:
        return "#9aa0aa"
    return "#1a7f4b" if score >= 70 else "#b7791f" if score >= 50 else "#c0362c"


def ring(score):
    pct = 0 if score is None else max(0, min(100, score))
    c = 2 * 3.14159 * 26
    return Markup(
        f'<svg width="64" height="64" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="26" fill="none" '
        f'stroke="#ecebe6" stroke-width="7"/><circle cx="32" cy="32" r="26" fill="none" stroke="{col(score)}" stroke-width="7" '
        f'stroke-linecap="round" stroke-dasharray="{c * pct / 100:.1f} {c:.1f}" transform="rotate(-90 32 32)"/></svg>')


def _env():
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
    env.globals.update(col=col, ring=ring)
    env.filters["fmtnum"] = lambda v: f"{float(v):g}" if isinstance(v, (int, float)) else v
    return env


SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def write_report(outdir: Path, r: dict) -> Path:
    r["issues"] = sorted(r.get("issues") or [], key=lambda i: SEV_ORDER.get(i.get("severity"), 9))
    r["sev_counts"] = {k: sum(1 for i in r["issues"] if i.get("severity") == k) for k in SEV_ORDER}
    r["applied_recs"] = {a.get("rec_id") for a in r.get("applied") or []}
    (outdir / "report.json").write_text(json.dumps(r, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    html = _env().get_template("report.html.j2").render(r=r)
    p = outdir / "report.html"
    p.write_text(html, encoding="utf-8")
    return p


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs)) if xs else None


def write_index(out: Path, rows: list[dict], totals: dict | None = None, seconds: int | None = None):
    rows = sorted(rows, key=lambda x: (x.get("cro_score") is None, x.get("cro_score") or 0))
    ok = [r for r in rows if r.get("cro_score") is not None]
    html = _env().get_template("index.html.j2").render(
        rows=rows, ok=ok, avg_cro=_avg([r["cro_score"] for r in ok]), avg_comp=_avg([r.get("comp_avg") for r in ok]),
        avg_m=_avg([r.get("mobile_score") for r in ok]), avg_d=_avg([r.get("desktop_score") for r in ok]),
        crit=sum(r.get("critical") or 0 for r in rows), totals=totals, seconds=seconds)
    (out / "index.html").write_text(html, encoding="utf-8")
    with open(out / "summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["label", "input", "status", "category", "cro_score", "grade", "mobile_score", "desktop_score",
                    "competitor_avg", "rank_vs_competitors", "competitors", "perf_score", "perf_source", "critical_issues",
                    "issue_1", "issue_2", "issue_3", "claude_cost_usd", "report", "optimized_html", "error"])
        for r in rows:
            ti = (r.get("top_issues") or []) + ["", "", ""]
            w.writerow([r.get("label"), r.get("input"), r.get("status"), r.get("category"), r.get("cro_score"),
                        r.get("grade"), r.get("mobile_score"), r.get("desktop_score"), r.get("comp_avg"), r.get("rank"),
                        "; ".join(r.get("competitors") or []), r.get("perf_score"), r.get("perf_source"),
                        r.get("critical"), ti[0], ti[1], ti[2], r.get("cost_usd"), r.get("report"), r.get("optimized"),
                        r.get("error") or ""])
    (out / "summary.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
