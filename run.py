#!/usr/bin/env python3
"""PDP CRO auditor with competitor benchmarking.

  python run.py urls.txt                      # one input per line (see README for the line format)
  python run.py https://brand.com/products/x  # single page
  python run.py screenshots/                  # every .png/.jpg/.webp in a folder
  python run.py urls.txt shots/ -c 4 -o out   # mix inputs, 4 pages in parallel
  python run.py urls.txt --batch              # Claude Message Batches API: 50% cheaper, results in minutes
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from cro.pipeline import Options, parse_inputs, run


def load_env(path=".env"):
    """.env wins over variables already set on the machine: an old system-wide ANTHROPIC_API_KEY is a common
    hidden cause of 'invalid key' errors."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():  # -sig: Notepad adds a BOM
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip("'\"")
            if v:
                os.environ[k.strip()] = v
    if os.getenv("ANTHROPIC_API_KEY"):
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)  # would otherwise be sent instead of / alongside the key


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles: never crash on a brand name or the rupee sign
        sys.stdout.reconfigure(errors="replace")
    load_env()
    ap = argparse.ArgumentParser(description="Audit D2C product pages for CRO against their top competitors and build "
                                             "brand-safe optimised pages.")
    ap.add_argument("inputs", nargs="+", help="URLs, screenshot files, folders of screenshots, or .txt files with one input per line")
    ap.add_argument("-o", "--out", default="out", help="output folder (default: out)")
    ap.add_argument("-c", "--concurrency", type=int, default=2, help="browser pages in parallel (default 2; raise on a fast machine)")
    ap.add_argument("--llm-concurrency", type=int, default=3, help="parallel Claude calls (default 3)")
    ap.add_argument("--model", default=os.getenv("CRO_MODEL", "claude-sonnet-5-5"), help="model for the audit (default claude-sonnet-5-5)")
    ap.add_argument("--fast-model", default=os.getenv("CRO_FAST_MODEL", "claude-haiku-5-5"),
                    help="cheap model for competitor discovery (default claude-haiku-5-5)")
    ap.add_argument("--effort", default=os.getenv("CRO_EFFORT", "low"), choices=["low", "medium", "high"],
                    help="audit thinking effort; higher = more thorough and more expensive (default low)")
    ap.add_argument("--batch", action="store_true", help="send audit calls through the Message Batches API (50%% cheaper, slower)")
    ap.add_argument("--competitors", default="", help="comma-separated competitor URLs or domains for every page "
                                                      "(default: found automatically)")
    ap.add_argument("-n", "--num-competitors", type=int, default=3, help="competitors per page (default 3, 0 = off)")
    ap.add_argument("--no-llm", action="store_true", help="rules engine only (no API key needed; URL inputs only)")
    ap.add_argument("--no-psi", action="store_true", help="skip Google PageSpeed Insights")
    ap.add_argument("--dry-run", action="store_true", help="write the Claude request payloads instead of calling the API")
    ap.add_argument("--force", action="store_true", help="redo pages that already finished (and refresh cached profiles)")
    ap.add_argument("--timeout", type=int, default=45, help="page load timeout in seconds")
    a = ap.parse_args()

    items = parse_inputs(a.inputs)
    if not items:
        sys.exit("No inputs found.")
    print(f"{len(items)} page(s) -> {a.out}/  (capture x{a.concurrency}, "
          f"{'model ' + a.model + ' / ' + a.fast_model + ', effort ' + a.effort if not a.no_llm else 'rules only'}"
          f"{', batch API' if a.batch else ''}, {a.num_competitors} competitors)")
    opts = Options(out=Path(a.out), concurrency=a.concurrency, llm_concurrency=a.llm_concurrency, model=a.model,
                   fast_model=a.fast_model, effort=a.effort, use_llm=not a.no_llm, batch=a.batch, dry_run=a.dry_run,
                   psi=not a.no_psi, force=a.force, timeout_ms=a.timeout * 1000, n_competitors=a.num_competitors,
                   competitors=[c.strip() for c in a.competitors.split(",") if c.strip()])
    rows = asyncio.run(run(items, opts))
    ok = sum(1 for r in rows if r["status"] in ("ok", "partial"))
    print(f"\nDone: {ok}/{len(rows)} pages. Open {Path(a.out) / 'index.html'}")


if __name__ == "__main__":
    main()
