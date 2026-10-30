#!/usr/bin/env python3
"""Local web UI for the auditor. No extra dependencies.

    python app.py            # opens http://127.0.0.1:8787
    python app.py --port 9000 --no-browser
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import sys
import mimetypes
import zipfile
import os
import re
import threading
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from cro.llm import check_key
from cro.pipeline import Options, parse_inputs
from cro.shot import is_mobile_shot
from cro.pipeline import run as run_pipeline
from run import load_env

ROOT = Path(__file__).resolve().parent
OUT_ROOT = ROOT / "out"
RUNS = OUT_ROOT / "ui-runs"
UI_FILE = ROOT / "ui" / "index.html"
MAX_BODY = 150 * 1024 * 1024
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}

ROW_KEYS = ("cro_score", "grade", "mobile_score", "desktop_score", "comp_avg", "rank", "competitors", "perf_score",
            "perf_source", "critical", "top_issues", "cost_usd", "error")
LOCK = threading.Lock()
JOBS: dict[str, dict] = {}


# ------------------------------------------------------------------ jobs
def _clean_url(line: str) -> str | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    raw, _, label = line.partition("|")
    raw = raw.strip()
    if not re.match(r"^https?://", raw, re.I):
        if re.match(r"^[\w-]+(\.[\w-]+)+(:\d+)?(/|$)", raw):
            raw = "https://" + raw
    return f"{raw} | {label.strip()}" if label.strip() else raw


def start_job(payload: dict) -> str:
    with LOCK:
        if any(j["status"] == "running" for j in JOBS.values()):
            raise ValueError("A run is already in progress. Wait for it to finish.")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = RUNS / run_id
    inputs_dir = outdir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    lines = [u for u in (_clean_url(x) for x in str(payload.get("urls", "")).splitlines()) if u]
    saved = []
    for f in payload.get("files") or []:
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(str(f.get("name", "shot.png"))).name).strip("-") or "shot.png"
        if Path(name).suffix.lower() not in IMG_EXT:
            continue
        dest = inputs_dir / name
        n = 1
        while dest.exists():
            dest = inputs_dir / f"{Path(name).stem}-{n}{Path(name).suffix}"
            n += 1
        dest.write_bytes(base64.b64decode(str(f.get("data", "")).split(",")[-1]))
        saved.append(dest)
    lines += _pair_shots(saved)
    if not lines:
        raise ValueError("Add at least one product URL or screenshot.")
    (outdir / "inputs.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    items = parse_inputs([str(outdir / "inputs.txt")])

    job = {"id": run_id, "created": datetime.now().isoformat(timespec="seconds"), "status": "running", "error": "",
           "items": {i.idx: {"idx": i.idx, "label": i.label, "kind": i.kind,
                             "input": Path(i.raw).name if i.kind == "image" else i.raw,
                             "stage": "queued", "row": None} for i in items}}
    with LOCK:
        JOBS[run_id] = job

    def progress(item, stage, row=None):
        with LOCK:
            it = job["items"].get(item.idx)
            if it:
                it["stage"] = stage
                if row:
                    it["row"] = row

    use_llm = bool(payload.get("use_llm", True)) and bool(os.getenv("ANTHROPIC_API_KEY"))
    comps = [c.strip() for c in re.split(r"[\s,]+", str(payload.get("competitors", ""))) if c.strip()]
    opts = Options(out=outdir, concurrency=max(1, min(int(payload.get("concurrency", 2)), 8)), use_llm=use_llm,
                   psi=bool(payload.get("psi", True)), model=os.getenv("CRO_MODEL", "claude-sonnet-5-5"),
                   fast_model=os.getenv("CRO_FAST_MODEL", "claude-haiku-5-5"), effort=os.getenv("CRO_EFFORT", "low"),
                   batch=bool(payload.get("batch")) and use_llm, competitors=comps,
                   n_competitors=max(0, min(int(payload.get("n_competitors", 3)), 3)), cache_dir=ROOT / "cache",
                   progress=progress)

    def worker():
        try:
            asyncio.run(run_pipeline(items, opts))
            status, err = "done", ""
        except Exception as e:
            traceback.print_exc()
            status, err = "failed", f"{type(e).__name__}: {e}"
        with LOCK:
            job["status"], job["error"] = status, err

    threading.Thread(target=worker, daemon=True).start()
    return run_id


_VIEW_WORDS = re.compile(r"(?<![a-z])(mobile|mob|phone|iphone|android|desktop|desk|web|pc|laptop)(?![a-z])", re.I)


def _pair_shots(paths: list[Path]) -> list[str]:
    """Two uploads of the same page (names equal apart from 'mobile'/'desktop', one of each view) become one input."""
    groups: dict[str, list[Path]] = {}
    for p in paths:
        key = re.sub(r"[\W_]+", "-", _VIEW_WORDS.sub("", p.stem.lower())).strip("-") or p.stem.lower()
        groups.setdefault(key, []).append(p)
    lines = []
    for key, ps in groups.items():
        views = {("m" if is_mobile_shot(p) else "d"): p for p in ps}
        if len(ps) == 2 and len(views) == 2:
            lines.append(f"{views['m']} + {views['d']} | {key}")
        else:
            lines += [f"{p} | {p.stem}" for p in ps]
    return lines


def job_view(run_id: str) -> dict | None:
    with LOCK:
        job = JOBS.get(run_id)
        if job:
            items = []
            for it in sorted(job["items"].values(), key=lambda x: x["idx"]):
                row = it["row"] or {}
                items.append({**{k: it[k] for k in ("label", "kind", "input", "stage")},
                              **{k: row.get(k) for k in ROW_KEYS},
                              "report": _link(run_id, row.get("report")), "optimized": _link(run_id, row.get("optimized"))})
            return {"id": run_id, "status": job["status"], "error": job["error"], "items": items,
                    "index": _link(run_id, "index.html") if job["status"] == "done" else None}
    summary = RUNS / run_id / "summary.json"
    if summary.exists():  # finished run from an earlier session
        rows = json.loads(summary.read_text(encoding="utf-8"))
        return {"id": run_id, "status": "done", "error": "", "index": _link(run_id, "index.html"),
                "items": [{"label": r.get("label"), "kind": r.get("kind"), "input": r.get("input"), "stage": r.get("status"),
                           **{k: r.get(k) for k in ROW_KEYS},
                           "report": _link(run_id, r.get("report")), "optimized": _link(run_id, r.get("optimized"))} for r in rows]}
    return None


def run_zip(run_id: str) -> bytes | None:
    """Every report, optimised page and dashboard of a run in one zip (raw captures and uploads left out)."""
    d = (RUNS / run_id).resolve()
    if not d.is_relative_to(RUNS.resolve()) or not (d / "index.html").exists():
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(d.rglob("*")):
            rel = f.relative_to(d)
            if f.is_file() and not ({"capture", "inputs", "_dry_run"} & set(rel.parts)) \
                    and f.name not in ("error.txt", "inputs.txt"):  # inputs.txt holds local file paths
                z.write(f, f"cro-audit-{run_id}/{rel.as_posix()}")
    return buf.getvalue()


def _link(run_id, rel):
    return f"/out/ui-runs/{run_id}/{rel}" if rel else None


def list_runs() -> list[dict]:
    out = []
    if RUNS.exists():
        for d in sorted((p for p in RUNS.iterdir() if p.is_dir()), reverse=True)[:20]:
            s = d / "summary.json"
            running = d.name in JOBS and JOBS[d.name]["status"] == "running"
            rows = json.loads(s.read_text(encoding="utf-8")) if s.exists() else []
            scores = [r["cro_score"] for r in rows if r.get("cro_score") is not None]
            out.append({"id": d.name, "pages": len(rows), "avg_cro": round(sum(scores) / len(scores)) if scores else None,
                        "status": "running" if running else ("done" if s.exists() else "incomplete"),
                        "index": _link(d.name, "index.html") if s.exists() else None})
    return out


def save_key(key: str) -> str:
    key = key.strip()
    if not key or re.search(r"\s", key) or len(key) < 20:
        raise ValueError("That doesn't look like an API key (it starts with sk-ant-api).")
    ok, msg = asyncio.run(check_key(key))
    if not ok:
        raise ValueError(msg)
    os.environ["ANTHROPIC_API_KEY"] = key
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
    env = ROOT / ".env"
    lines = env.read_text(encoding="utf-8-sig").splitlines() if env.exists() else []
    lines = [ln for ln in lines if not ln.strip().startswith("ANTHROPIC_API_KEY=")] + [f"ANTHROPIC_API_KEY={key}"]
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return msg


# ------------------------------------------------------------------ http
class Handler(BaseHTTPRequestHandler):
    server_version = "CROAudit"

    def log_message(self, fmt, *args):
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")  # blocks DNS-rebinding

    def _send(self, code, body: bytes, ctype="application/json", filename: str | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if filename:  # save instead of display
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            return self._send(200, UI_FILE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/config":
            return self._json(200, {"has_key": bool(os.getenv("ANTHROPIC_API_KEY")), "has_psi_key": bool(os.getenv("PSI_API_KEY")),
                                    "model": os.getenv("CRO_MODEL", "claude-sonnet-5-5"),
                                    "fast_model": os.getenv("CRO_FAST_MODEL", "claude-haiku-5-5"),
                                    "effort": os.getenv("CRO_EFFORT", "low")})
        if path == "/api/runs":
            return self._json(200, list_runs())
        m = re.match(r"^/api/runs/([\w-]+)\.zip$", path)
        if m:
            data = run_zip(m.group(1))
            return self._send(200, data, "application/zip", f"cro-audit-{m.group(1)}.zip") if data \
                else self._json(404, {"error": "not found"})
        m = re.match(r"^/api/runs/([\w-]+)$", path)
        if m:
            v = job_view(m.group(1))
            return self._json(200, v) if v else self._json(404, {"error": "not found"})
        if path.startswith("/out/"):
            target = (OUT_ROOT / path[len("/out/"):]).resolve()
            if not target.is_relative_to(OUT_ROOT.resolve()) or not target.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if ctype.startswith("text/"):
                ctype += "; charset=utf-8"
            download = "download" in urlparse(self.path).query.split("&")
            name = f"{target.parent.name}-{target.name}" if download else None
            return self._send(200, target.read_bytes(), ctype, name)
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        # Custom header forces a CORS preflight, so other websites can't trigger runs or overwrite the key.
        if not self._host_ok() or self.headers.get("X-CRO") != "1":
            return self._send(403, b"forbidden", "text/plain")
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self._json(413, {"error": "Upload too large (max 150 MB per run)."})
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
            path = urlparse(self.path).path
            if path == "/api/runs":
                return self._json(200, {"id": start_job(data)})
            if path == "/api/key":
                return self._json(200, {"ok": True, "message": save_key(str(data.get("key", "")))})
            return self._json(404, {"error": "not found"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles: never crash on a brand name or the rupee sign
        sys.stdout.reconfigure(errors="replace")
    os.chdir(ROOT)
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    RUNS.mkdir(parents=True, exist_ok=True)
    # importing the Claude SDK can take 10-20s on a cold Windows machine; do it now, not when the user clicks Save
    threading.Thread(target=lambda: __import__("anthropic"), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}"
    print(f"PDP CRO Auditor running at {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
