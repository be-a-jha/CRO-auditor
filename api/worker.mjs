import { timingSafeEqual } from "node:crypto";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { getWorker, reserveAuditTime } from "../lib/vercel-worker.mjs";

function getTarget(query, method) {
  if (query.route === "config" && method === "GET") return "/api/config";
  if (query.route === "runs" && (method === "GET" || method === "POST")) return "/api/runs";
  if (method !== "GET") return null;
  if (query.route === "run" && typeof query.id === "string" && /^[\w-]+(?:\.zip)?$/.test(query.id)) {
    return `/api/runs/${query.id}`;
  }
  if (query.route === "asset" && typeof query.file === "string") {
    const segments = query.file.split("/");
    if (segments.length > 1 && segments.every((part) => part && part !== "." && part !== ".." && !part.includes("\\"))) {
      return `/out/ui-runs/${segments.map(encodeURIComponent).join("/")}`;
    }
  }
  return null;
}

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  const target = getTarget(req.query, req.method);
  if (!target) return res.status(404).json({ error: "Not found." });

  const token = process.env.CRO_WORKER_TOKEN?.trim();
  const key = process.env.ANTHROPIC_API_KEY?.trim();
  const password = process.env.APP_PASSWORD;
  if (target === "/api/config") {
    if (!token) return res.status(503).json({ error: "The browser worker is not configured." });
    return res.json({
      has_key: Boolean(key), has_psi_key: false,
      model: "claude-sonnet-5-5", fast_model: "claude-haiku-5-5", effort: "low",
      public: true, needs_password: Boolean(password), max_pages: 3, has_samples: true,
      supports_batch: false, max_request_bytes: 4_400_000,
    });
  }

  const headers = { "X-CRO-Worker-Token": token };
  let body;
  if (req.method === "POST") {
    if (req.headers["x-cro"] !== "1") return res.status(403).json({ error: "Forbidden." });
    const payload = req.body;
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return res.status(400).json({ error: "Send a JSON audit request." });
    }
    if (password) {
      const supplied = Buffer.from(String(payload.password ?? ""));
      const expected = Buffer.from(password);
      if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
        return res.status(401).json({ error: "Wrong access password." });
      }
    }
    if (!String(payload.urls ?? "").trim() && !payload.files?.length) {
      return res.status(400).json({ error: "Add at least one product URL or screenshot." });
    }
    const { password: _, ...audit } = payload;
    body = JSON.stringify({ ...audit, batch: false });
    headers["Content-Type"] = "application/json";
    headers["X-CRO"] = "1";
    if (key && audit.use_llm !== false) headers["X-CRO-Anthropic-Key"] = key;
  }

  if (!token) return res.status(503).json({ error: "The browser worker is not configured." });
  try {
    const { sandbox, origin } = await getWorker(token);
    if (req.method === "POST") await reserveAuditTime(sandbox);
    const url = new URL(target, origin);
    if (req.query.download !== undefined) url.search = "download";
    const upstream = await fetch(url, {
      method: req.method, headers, body, redirect: "error", signal: AbortSignal.timeout(30_000),
    });
    res.statusCode = upstream.status;
    for (const name of ["content-type", "content-disposition"]) {
      const value = upstream.headers.get(name);
      if (value) res.setHeader(name, value);
    }
    if (upstream.body) await pipeline(Readable.fromWeb(upstream.body), res);
    else res.end();
  } catch (error) {
    console.error("CRO worker unavailable", error.name, error.message);
    if (!res.headersSent) {
      return res.status(503).json({ error: "The Vercel browser worker is starting or temporarily unavailable. Retry shortly." });
    }
    res.destroy();
  }
}
