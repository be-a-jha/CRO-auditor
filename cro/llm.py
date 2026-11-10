"""Claude calls. Cost controls live here:
  * model routing: a fast, cheap model (Haiku) for competitor discovery; the main model only for the audit
  * low effort by default (thinking tokens are billed as output, the most expensive tokens)
  * one frozen system prompt (research library, component catalog, rules) cached across every page in the batch
  * screenshots sent at 1x CSS resolution and sliced, so images stay small but legible
  * the model fills components instead of writing HTML, so outputs stay short
  * optional Message Batches API (50% off) for the audit calls
Structured outputs (JSON schema) so responses always parse; schemas are static so the cache prefix never changes."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from . import components as comp
from .competitors import seed_prompt
from .patcher import OPS, POSITIONS, VIEWPORTS
from .research import prompt_block

# $ per million tokens (input, output), first-party API. Cache writes 1.25x input, cache reads 0.1x (0.05x Opus 5.5).
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-sonnet-5": (2.0, 10.0),
          "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-fable-5-1": (10.0, 50.0)}
CACHE_READ = {"claude-opus-5-5": 0.05, "claude-fable-5-1": 0.025}


def cost_of(model: str, u: dict, batch: bool = False) -> float:
    pin, pout = PRICES.get(model, (2.0, 10.0))
    c = (u.get("input_tokens", 0) * pin + u.get("cache_creation_input_tokens", 0) * pin * 1.25
         + u.get("cache_read_input_tokens", 0) * pin * CACHE_READ.get(model, 0.1) + u.get("output_tokens", 0) * pout) / 1e6
    return c * (0.5 if batch else 1.0)


S, B, N, I = {"type": "string"}, {"type": "boolean"}, {"type": "number"}, {"type": "integer"}


def obj(props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def arr(x):
    return {"type": "array", "items": x}


def enum(*v):
    return {"type": "string", "enum": list(v)}


CATEGORIES = ["First screen (mobile)", "First screen (desktop)", "Add to cart", "Trust & social proof", "Purchase friction",
              "Content & merchandising", "Offer clarity", "Technical & SEO", "Copy & messaging", "Visual hierarchy"]
VIEW = enum("mobile", "desktop", "both")
COMPETITOR = obj({"brand": S, "domain": S, "search_query": S, "why": S})

PROFILE_SCHEMA = obj({"brand": S, "category": S, "product_type": S, "market": S,
                      "price_positioning": enum("budget", "mid", "premium", "unknown"), "audience": S,
                      "competitors": arr(COMPETITOR)})

AUDIT_SCHEMA = obj({
    "is_product_page": B,
    "page_summary": S,
    "competitive_position": S,
    "strengths": arr(obj({"title": S, "detail": S, "viewport": VIEW})),
    "issues": arr(obj({"id": S, "title": S, "detail": S, "severity": enum("critical", "high", "medium", "low"),
                       "category": enum(*CATEGORIES), "viewport": VIEW, "evidence": S, "research": arr(S)})),
    "competitor_insights": arr(obj({"competitor": S, "observation": S, "takeaway": S})),
    "recommendations": arr(obj({"id": S, "title": S, "why": S, "kind": enum("change", "addon"), "viewport": VIEW,
                                "impact": enum("high", "medium", "low"), "effort": enum("low", "medium", "high"),
                                "fixes_issue_ids": arr(S), "research": arr(S), "competitor_ref": S, "in_mockup": B})),
    "patches": arr(obj({"rec_id": S, "op": enum(*OPS), "component": enum("none", *comp.COMPONENTS), "target": S,
                        "position": enum(*POSITIONS), "viewport": enum(*VIEWPORTS), "heading": S, "label": S,
                        "items": arr(obj({"title": S, "text": S, "icon": S})), "source": S, "css": S, "html": S})),
})

# Screenshot facts are reported as two lists of ids (present / absent; anything else is unknown). A yes/no/unknown
# enum per fact, or colour enums per role, compile to a grammar that is too large for structured outputs.
VISION_FACTS = ["atc_present", "atc_contrast_ok", "rating_near_title", "reviews_section", "trust_secure", "trust_returns",
                "trust_authentic", "trust_free_shipping", "trust_cod", "trust_quality", "payments_near", "delivery_info",
                "compare_price", "discount_shown", "offers_near", "faq", "cross_sell", "video", "variants_dropdown_only",
                "size_guide", "decl_origin", "decl_net_qty", "decl_maker", "decl_tax_incl",
                "m_title_atf", "m_price_atf", "m_atc_atf", "m_image_atf", "m_popup", "m_sticky_atc",
                "d_title_price_atf", "d_atc_atf", "d_two_col", "d_popup"]
ROLES = ("primary", "on_primary", "text", "heading", "muted", "bg", "surface", "border", "accent", "link")
GENERIC = enum("serif", "sans-serif", "monospace", "cursive")
VISION_SCHEMA = obj({
    "is_product_page": B,
    "brand": obj({"name": S, "roles": obj({k: S for k in ROLES}),
                  "fonts": obj({"heading_font": S, "heading_generic": GENERIC, "body_font": S, "body_generic": GENERIC}),
                  "type_scale_px": obj({k: N for k in ("body", "small", "price", "section_heading", "product_title", "button")}),
                  "button_radius_px": N, "button_uppercase": B, "button_height_px": N}),
    "present": arr(enum(*VISION_FACTS)),
    "absent": arr(enum(*VISION_FACTS)),
    "counts": obj({"image_count": I, "bullets": I, "variant_options": I}),
    "text": obj({k: S for k in ("title", "price", "compare_price", "discount", "atc_label", "rating_text", "currency",
                                "visible_text")}),
    "sections": arr(obj({"id": S, "view": enum("mobile", "desktop"), "label": S, "layout": enum("full", "left", "right"),
                         "y_top": I, "y_bottom": I, "x_left": I, "x_right": I})),
    "profile": obj({"category": S, "product_type": S, "market": S,
                    "price_positioning": enum("budget", "mid", "premium", "unknown"), "competitors": arr(COMPETITOR)}),
})

VAR_LIST = ("--cro-primary, --cro-on-primary, --cro-text, --cro-heading, --cro-muted, --cro-bg, --cro-surface, --cro-border, "
            "--cro-accent, --cro-link, --cro-font-body, --cro-font-heading, --cro-font-button, --cro-fs-xs, --cro-fs-sm, "
            "--cro-fs-base, --cro-fs-lg, --cro-fs-xl, --cro-fs-h1, --cro-fs-button, --cro-radius, --cro-radius-input, "
            "--cro-btn-transform, --cro-btn-spacing, --cro-btn-weight, --cro-btn-padding, --cro-btn-height, --cro-line")

# One frozen system prompt for every audit call in every batch (URL and screenshot): it is the cached prefix.
AUDIT_SYSTEM = f"""You are a senior conversion-rate-optimisation (CRO) strategist at an AI-native website growth agency. You audit D2C product detail pages (PDPs) and benchmark them against the leading competitors in the same category. Most traffic is mobile. Many brands sell in India (UPI, COD, pincode delivery checks, MRP vs selling price, Legal Metrology declarations) but judge each page in its own market.

INPUTS
Screenshots of the page on mobile and desktop, the first mobile screen of 2-3 competitor product pages in the same category, facts measured from the page, a deterministic CRO rubric, and a competitor gap table computed in code. The rubric scores are final: do not restate, recompute or argue with them. Your job is what a rubric cannot see (value proposition, copy, visual hierarchy, image quality, objection handling, layout problems) and turning everything into a prioritised plan.

OUTPUT
- page_summary: 2-3 sentences on how the page sells and its biggest conversion problem.
- competitive_position: 2-3 sentences on how the page compares with the competitors shown, naming them.
- strengths: 3-6. issues: 5-9, most severe first, each grounded in visible evidence, tagged with the viewport where it occurs (mobile, desktop or both). Analyse mobile and desktop separately: a problem only on one viewport gets that viewport.
- competitor_insights: 3-6. What a named competitor does better or differently and the takeaway for this brand. Never suggest copying a competitor's copy, claims, imagery or design; translate the idea into this brand's own identity.
- recommendations: 5-9, ordered by expected conversion impact. kind is "change" (fix something that exists) or "addon" (add a new element). research: ids from the research library that support it (only ids listed below, or none). competitor_ref: the competitor that already does this, or "". in_mockup: true only if a patch below implements it; content or engineering work (photoshoots, collecting reviews, backend logic, page speed) gets false.
- Be specific: name the element, the change, and why it moves conversion. Keep every detail, evidence, why, observation and takeaway to one or two short sentences. Write plainly, no hype or filler, no em dashes.

RESEARCH LIBRARY (cite by id)
{prompt_block()}

BRAND IDENTITY IS FIXED
Never change the logo, colours, fonts, font sizes, button style, or the header, navigation and footer. Changes must look like the brand's own designer made them. An automated brand guard snaps anything off-brand back to brand values.

NEVER INVENT FACTS
Ratings, review counts, prices, discounts, delivery times, return windows, stock levels, certifications, ingredient amounts and customer counts must come from this page. Otherwise write a bracketed placeholder such as [7-day returns] for the brand to fill. Unsupported numbers are flagged automatically. No dark patterns (false urgency, invented scarcity, countdown timers, pre-ticked boxes, confirm-shaming, drip pricing): they are illegal under India's CCPA 2023 guidelines and erode trust.

MOCKUP PATCHES (3-8, each with the rec_id of a recommendation that has in_mockup=true)
Prefer op "component": pick from the catalogue and fill its copy slots (heading, label, items). The HTML and CSS are generated in the brand's style. Leave unused fields "" or [] and set component "none" for other ops.
{comp.catalog_prompt()}
Other ops:
- move: move "source" before or after "target" (position before/after). Use it to reorder existing blocks (e.g. bring reviews or benefits up).
- layout: "css" holds declarations only (e.g. "max-height:420px;margin-bottom:8px"), applied to the target. Layout properties only (size, spacing, display, order, position); never colour or type.
- replace_text: "label" is the new text for a short text element such as a vague CTA label.
- custom: only when no component fits. "html" plus "css"; visual properties only through {VAR_LIST}; every selector must use a class starting with "cro-"; no scripts, iframes, external images or fonts.
position: before / after / prepend / append relative to the target. viewport: all / mobile / desktop (mobile-only blocks for mobile problems, desktop-only for desktop problems)."""

URL_TARGETS = ("Targets: an outline id from the page outline (\"n12\") or one of the keys listed in available_keys (only "
               "those exist on this page). Header, navigation and footer are not in the outline and cannot be targeted.")
SHOT_TARGETS = ("This page is a screenshot, so the mockup is the screenshot cut into sections with new blocks inserted "
                "between them. Targets are section ids from the sections list. Allowed ops: component, custom, and move "
                "(full-width sections only). layout and replace_text are not possible on a screenshot.")

PROFILE_SYSTEM = """You identify a D2C product page's category and its strongest direct competitors for a CRO benchmark.
market: the country the page sells to, as a 2-letter code; infer it from the currency and the domain (INR / ₹ / .in = IN), not from the language. category: a seed-list category name when one fits.
Competitors: 5 brands, best first, that sell a directly comparable product to the same shoppers in that market, sold on the brand's own website (D2C), not marketplaces (Amazon, Flipkart, Nykaa, Myntra) and not the page's own brand. Prefer brands from the seed list for that market; add others only if you are sure of their official domain. domain: the brand's own store domain, no path. search_query: 2-4 words that would find the most comparable product on that store (product type, not the brand name).
Seed list (category [market]: brand (domain)):
""" + seed_prompt()

VISION_SYSTEM = """You extract facts from screenshots of a D2C product page for a CRO audit. Report only what is visible; never guess. Then identify the category and the strongest D2C competitors for a benchmark: profile.market is the 2-letter country the page sells to, inferred from the currency (₹ / Rs = IN), and the 5 competitors (best first) must sell a directly comparable product in that market from their own website, not marketplaces, not this brand; prefer the seed list for that market and use its category names.
Seed list (category [market]: brand (domain)):
""" + seed_prompt()


class LLMError(Exception):
    pass


class DryRun(Exception):
    pass


def _parse(msg) -> dict:
    if msg.stop_reason == "max_tokens":
        raise LLMError("model output truncated (max_tokens)")
    if msg.stop_reason == "refusal":
        raise LLMError("model declined the request")
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"unparseable model output: {e}") from e


def _usage(msg) -> dict:
    u = msg.usage
    return {k: getattr(u, k, 0) or 0 for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                               "cache_read_input_tokens")}


class LLM:
    def __init__(self, model: str, fast_model: str = "claude-haiku-5-5", concurrency: int = 3, effort: str = "low",
                 batch: bool = False, dry_run_dir: Path | None = None, api_key: str | None = None):
        self.model, self.fast_model, self.effort = model, fast_model, effort
        self.dry_run_dir = dry_run_dir
        self.sem = asyncio.Semaphore(concurrency)
        self.calls: list[dict] = []   # one row per call: tag, model, usage, cost, batch
        self.warm: dict[str, asyncio.Future] = {}
        self.client = None
        self.batch = None
        if not dry_run_dir:
            import anthropic
            if not (api_key or os.getenv("ANTHROPIC_API_KEY")):
                raise LLMError("ANTHROPIC_API_KEY not set")
            self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=6, timeout=600)
            if batch:
                self.batch = BatchQueue(self)

    # ---------------------------------------------------------------- accounting
    def record(self, tag: str, model: str, usage: dict, batch: bool):
        u = {k: usage.get(k, 0) or 0 for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                               "cache_read_input_tokens")}
        self.calls.append({"tag": tag, "model": model, **u, "batch": batch, "cost": cost_of(model, u, batch)})

    def cost_for(self, prefix: str) -> dict:
        rows = [c for c in self.calls if c["tag"].startswith(prefix + ".")]
        return {"usd": round(sum(c["cost"] for c in rows), 4), "calls": [
            {k: c[k] for k in ("tag", "model", "input_tokens", "output_tokens", "cache_read_input_tokens",
                               "cache_creation_input_tokens", "batch")} | {"usd": round(c["cost"], 4)} for c in rows]}

    def totals(self) -> dict:
        t = {k: sum(c[k] for c in self.calls) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                        "cache_creation_input_tokens")}
        return {**t, "calls": len(self.calls), "usd": round(sum(c["cost"] for c in self.calls), 4)}

    # ---------------------------------------------------------------- calls
    def params(self, system: str, content: list, schema: dict, model: str, max_tokens: int, effort: str) -> dict:
        return dict(model=model, max_tokens=max_tokens,
                    system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": content}],
                    output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}})

    async def call(self, system: str, content: list, schema: dict, tag: str, model: str | None = None,
                   max_tokens: int = 16000, effort: str | None = None, batchable: bool = False, batch_key=None) -> dict:
        model = model or self.model
        p = self.params(system, content, schema, model, max_tokens, effort or self.effort)
        if self.dry_run_dir:
            self.dry_run_dir.mkdir(parents=True, exist_ok=True)
            slim = json.loads(json.dumps(p))
            for blk in slim["messages"][0]["content"]:
                if blk.get("type") == "image":
                    blk["source"]["data"] = f"<{len(blk['source']['data'])} b64 chars>"
            (self.dry_run_dir / f"{tag}.request.json").write_text(json.dumps(slim, indent=1, ensure_ascii=False), encoding="utf-8")
            raise DryRun(f"dry run: request written to {tag}.request.json")
        if batchable and self.batch:
            msg = await self.batch.submit(p, batch_key)
            self.record(tag, model, _usage(msg), True)
            return _parse(msg)
        msg = await self._direct(p, f"{model}:{hash(system)}")
        self.record(tag, model, _usage(msg), False)
        return _parse(msg)

    async def _direct(self, p: dict, key: str):
        """Cache-warm gate: when several pages reach the same prompt at once, the first request goes ahead and the
        rest wait until it starts streaming (its prefix is cached by then), so they read the cache at 0.1x instead of
        each writing it at 1.25x."""
        gate = self.warm.get(key)
        first = gate is None
        if first:
            gate = self.warm[key] = asyncio.get_running_loop().create_future()
        else:
            try:
                await asyncio.wait_for(asyncio.shield(gate), timeout=90)
            except asyncio.TimeoutError:
                pass

        def release():
            if first and not gate.done():
                gate.set_result(True)

        try:
            async with self.sem:
                # fallbacks="default": if the model declines on policy grounds, the API retries on its default substitute
                async with self.client.beta.messages.stream(**p, betas=["server-side-fallback-2026-07-01"],
                                                            fallbacks="default") as stream:
                    async for ev in stream:
                        if ev.type == "message_start":
                            release()
                    return await stream.get_final_message()
        finally:
            release()


class BatchQueue:
    """Collects the audit calls of a run into one Message Batches request (50% cheaper, results usually within
    minutes, at most 24h). Flushes when every page still in flight has submitted, or after a quiet period."""

    def __init__(self, llm: LLM, idle_s: float = 90, poll_s: float = 15):
        self.llm, self.idle_s, self.poll_s = llm, idle_s, poll_s
        self.remaining = 0
        self.pending: list = []
        self.done_keys: set = set()
        self.timer: asyncio.Task | None = None
        self.batches: list[str] = []

    def expect(self, n: int):
        self.remaining += n

    def skip(self, key):
        """A page that will not submit (failed early or ran rules-only)."""
        if key in self.done_keys:
            return
        self.done_keys.add(key)
        self.remaining -= 1
        self._maybe_flush()

    async def submit(self, params: dict, key):
        fut = asyncio.get_running_loop().create_future()
        self.done_keys.add(key)
        self.pending.append((f"r{len(self.done_keys)}-{int(time.time())}", params, fut))
        self._maybe_flush()
        if self.pending and (self.timer is None or self.timer.done()):
            self.timer = asyncio.ensure_future(self._idle_flush())
        return await fut

    def _maybe_flush(self):
        if self.pending and len(self.pending) >= self.remaining:
            self._flush()

    async def _idle_flush(self):
        n = len(self.pending)
        await asyncio.sleep(self.idle_s)
        if self.pending and len(self.pending) == n:
            self._flush()

    def _flush(self):
        reqs, self.pending = self.pending, []
        self.remaining -= len(reqs)
        asyncio.ensure_future(self._run(reqs))

    async def _run(self, reqs):
        client = self.llm.client
        try:
            b = await client.messages.batches.create(requests=[{"custom_id": cid, "params": p} for cid, p, _ in reqs])
            self.batches.append(b.id)
            print(f"[batch] submitted {len(reqs)} audit request(s) as {b.id}; waiting for results (50% cheaper)")
            while b.processing_status != "ended":
                await asyncio.sleep(self.poll_s)
                b = await client.messages.batches.retrieve(b.id)
            futs = {cid: fut for cid, _, fut in reqs}
            async for r in await client.messages.batches.results(b.id):
                fut = futs.pop(r.custom_id, None)
                if fut is None or fut.done():
                    continue
                if r.result.type == "succeeded":
                    fut.set_result(r.result.message)
                else:
                    err = getattr(getattr(r.result, "error", None), "error", None)
                    fut.set_exception(LLMError(f"batch request {r.result.type}: {getattr(err, 'message', '')}".strip()))
            for fut in futs.values():
                if not fut.done():
                    fut.set_exception(LLMError("batch result missing"))
        except Exception as e:
            for _, _, fut in reqs:
                if not fut.done():
                    fut.set_exception(LLMError(f"batch failed: {type(e).__name__}: {str(e)[:160]}"))


async def check_key(key: str) -> tuple[bool, str]:
    """Validate a key with a 1-token request on the cheapest model (costs a fraction of a cent)."""
    key = (key or "").strip()
    if key.startswith("sk-ant-oat"):
        return False, ("That is a Claude.ai / Claude Code login token, not an API key. Create an API key at "
                       "platform.claude.com > API Keys (it starts with sk-ant-api).")
    if not key.startswith("sk-ant-"):
        return False, "That doesn't look like a Claude API key (they start with sk-ant-api)."
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=key, max_retries=0, timeout=20)
    try:
        await client.messages.create(model="claude-haiku-5-5", max_tokens=1, messages=[{"role": "user", "content": "hi"}])
        return True, "Key accepted."
    except anthropic.APITimeoutError:
        return True, "Saved. Couldn't verify the key right now (network timeout); the first audit will confirm it."
    except anthropic.AuthenticationError:
        return False, "Claude rejected this key (invalid, revoked, or cut off when copying). Copy the whole key again."
    except anthropic.PermissionDeniedError:
        return False, "This key doesn't have permission to use the API. Check the key's workspace in platform.claude.com."
    except anthropic.BadRequestError as e:
        msg = str(getattr(e, "message", e))
        if "credit" in msg.lower() or "balance" in msg.lower():
            return False, "The key works but the account has no credit. Add credit under Billing in platform.claude.com."
        return True, "Key accepted."
    except anthropic.APIConnectionError:
        return False, "Couldn't reach the Claude API. Check your internet connection."
    except anthropic.APIStatusError as e:
        return False, f"Claude API error {e.status_code}: {str(getattr(e, 'message', e))[:160]}"
    finally:
        await client.close()


# ==================================================================== prompts
def _img(chunk: dict, label: str) -> list:
    return [{"type": "text", "text": label},
            {"type": "image", "source": {"type": "base64", "media_type": chunk["media_type"], "data": chunk["b64"]}}]


def _slim_outline(outline: list, limit: int = 90) -> list:
    out = []
    for o in outline or []:
        if o.get("protected"):
            continue
        row = {"id": o["id"]}
        for k in ("key", "tag", "cls"):
            if o.get(k):
                row[k] = o[k][:32]
        if o.get("text"):
            row["text"] = o["text"][:60]
        out.append(row)
    keyed = [o for o in out if o.get("key")]
    rest = [o for o in out if not o.get("key")][: max(0, limit - len(keyed))]
    keep = {o["id"] for o in keyed + rest}
    return [o for o in out if o["id"] in keep]


def competitor_images(comps: list[dict]) -> list:
    from .shot import region_for_model
    content = []
    for c in comps:
        p = Path(c.get("dir", "")) / "mobile_fold.jpg"
        if p.exists():
            ch = region_for_model(p, 0, 844, 390)
            if ch:
                content += _img(ch, f"Competitor: {c['brand']} ({c['domain']}), first mobile screen. CRO score {c['cro_score']}.")
    return content


async def profile_page(llm: LLM, info: dict, tag: str) -> dict:
    text = ("Identify the category and the 5 strongest direct D2C competitors for this product page.\n\n"
            + json.dumps({k: info.get(k) for k in ("url", "brand_name", "page_title", "title", "price", "currency",
                                                   "breadcrumbs", "headings", "bullets", "region_text")},
                         ensure_ascii=False))
    out = await llm.call(PROFILE_SYSTEM, [{"type": "text", "text": text}], PROFILE_SCHEMA, tag=f"{tag}.profile",
                         model=llm.fast_model, max_tokens=4000, effort="low")
    out["source"] = llm.fast_model
    return out


async def audit_url(llm: LLM, cap, F: dict, rubric: dict, tokens, perf: dict, profile: dict, comp_brief: dict,
                    comps: list[dict], tag: str, batch_key=None) -> dict:
    from .shot import chunks_for_model, region_for_model
    content = []
    content += _img(region_for_model(cap.desktop.screenshot_fold, 0, 900, 1280), "Page, desktop, first screen (1440x900).")
    below = region_for_model(cap.desktop.screenshot_full, 900, 3600, 640)
    if below:
        content += _img(below, "Page, desktop, below the first screen (900-3600px, downscaled).")
    for i, ch in enumerate(chunks_for_model(cap.mobile.screenshot_full, max_w=390, max_h=1900, max_chunks=3), 1):
        content += _img(ch, f"Page, mobile (390px wide) part {i}, from y={int(ch['y0'] / 2)} CSS px. "
                            "The first mobile screen is the top 844 CSS px.")
    content += competitor_images(comps)
    data = {
        "url": cap.final_url, "profile": {k: profile.get(k) for k in ("brand", "category", "product_type", "market",
                                                                       "price_positioning")},
        "rubric": {"cro_score": rubric["cro_score"], "viewport_scores": rubric["viewport_scores"],
                   "checks": [{k: c[k] for k in ("id", "title", "viewport", "status", "evidence")} for c in rubric["checks"]],
                   "flags": rubric["flags"]},
        "facts": {k: F.get(k) for k in ("title_text", "price_text", "compare_text", "discount_text", "atc_label",
                                        "image_count", "bullets", "variants", "trust_near", "schema_rating_obj")},
        "product_info_text": cap.desktop.facts.get("region_text", "")[:2000],
        "page_text_sample": cap.desktop.facts.get("page_text_sample", "")[:3000],
        "popups_removed_for_capture": [p["text"] for p in cap.mobile.popups + cap.desktop.popups][:3],
        "performance": perf,
        "brand_tokens": {"roles": tokens.roles, "fonts": tokens.fonts, "type_scale_px": tokens.size_roles},
        "competitor_benchmark": comp_brief,
        "available_keys": sorted({o["key"] for o in cap.desktop.outline or [] if o.get("key") and not o.get("protected")}),
        "outline": _slim_outline(cap.desktop.outline or []),
    }
    content.append({"type": "text", "text": "Audit this product page against its competitors and plan the mockup patches.\n"
                    + URL_TARGETS + "\n\nPAGE DATA (JSON):\n" + json.dumps(data, ensure_ascii=False)})
    out = await llm.call(AUDIT_SYSTEM, content, AUDIT_SCHEMA, tag=f"{tag}.audit", batchable=True, batch_key=batch_key)
    out["source"] = "llm"
    return out


def view_chunks(path: Path, view: str) -> list[dict]:
    """Slices of a screenshot sized for the model: mobile at about 1x CSS width, desktop at 1000px wide."""
    from .shot import chunks_for_model
    if view == "mobile":
        return chunks_for_model(path, max_w=450, max_h=2000, max_chunks=5)
    return chunks_for_model(path, max_w=1000, max_h=1250, max_chunks=4)


def _shot_images(views: list[dict]) -> list:
    content, k = [], 0
    for v in views:
        for ch in v["chunks"]:
            k += 1
            y0 = round(ch["y0"] * ch["scale"])
            content += _img(ch, f"Image {k}: {v['view']} screenshot, rows y={y0}-{y0 + ch['h']} of the {v['view']} page "
                                f"(the page is {v['w_s']}x{v['h_s']} px at this scale).")
    return content


async def extract_screenshot(llm: LLM, views: list[dict], palette: list[dict], tag: str) -> dict:
    content = _shot_images(views)
    folds = "; ".join(f"the first {v['view']} screen ends at y={v['fold_s']}" for v in views)
    content.append({"type": "text", "text":
        "These are screenshots of one D2C product page (no live URL). Extract:\n"
        f"- brand.roles: hex colours. Use only these values sampled from the pixels: {json.dumps([p['hex'] for p in palette])} "
        "(primary = add-to-cart fill, on_primary = its label colour).\n"
        "- brand.fonts: the closest Google Font you can see for headings and body. type_scale_px: sizes in CSS px "
        "(mobile pages are 390 CSS px wide, desktop 1440).\n"
        "- present / absent: fact ids clearly visible as present or absent; leave a fact out of both if unsure. "
        "m_* facts are for the mobile view only, d_* for the desktop view only (skip a view that wasn't given). "
        f"First screen: {folds}. Trust, payment and delivery facts mean near the add-to-cart. decl_* are the Legal "
        "Metrology declarations (country of origin, net quantity, manufacturer/marketer, MRP inclusive of taxes).\n"
        "- counts: -1 if unknown. text.visible_text: all visible text in reading order (max ~2500 characters). "
        "text.currency: INR, USD, etc.\n"
        "- sections: split each view top to bottom into its content blocks in reading order (header, gallery, title & "
        "price, add to cart, trust badges, benefits, description, reviews, FAQ, footer...). ids m1, m2... for mobile, d1, "
        "d2... for desktop. y_top/y_bottom and x_left/x_right in the page px stated for that view. On desktop, blocks "
        "inside a two-column area get layout left (gallery side) or right (buy box side); everything else is full.\n"
        "- profile: category, product type, market and 5 competitors."})
    return await llm.call(VISION_SYSTEM, content, VISION_SCHEMA, tag=f"{tag}.extract", max_tokens=16000)


async def audit_screenshot(llm: LLM, views: list[dict], vision: dict, rubric: dict, tokens, profile: dict,
                           comp_brief: dict, comps: list[dict], tag: str, batch_key=None) -> dict:
    content = _shot_images(views) + competitor_images(comps)
    data = {
        "profile": {k: profile.get(k) for k in ("brand", "category", "product_type", "market", "price_positioning")},
        "views_given": [v["view"] for v in views],
        "rubric": {"cro_score": rubric["cro_score"], "viewport_scores": rubric["viewport_scores"],
                   "checks": [{k: c[k] for k in ("id", "title", "viewport", "status", "evidence")} for c in rubric["checks"]]},
        "text": vision.get("text"), "counts": vision.get("counts"),
        "sections": [{k: s[k] for k in ("id", "view", "label", "layout")} for s in vision.get("sections") or []],
        "brand_tokens": {"roles": tokens.roles, "fonts": tokens.fonts, "type_scale_px": tokens.size_roles},
        "competitor_benchmark": comp_brief,
    }
    content.append({"type": "text", "text": "Audit this product page against its competitors and plan the mockup patches.\n"
                    + SHOT_TARGETS + "\n\nPAGE DATA (JSON):\n" + json.dumps(data, ensure_ascii=False)})
    out = await llm.call(AUDIT_SYSTEM, content, AUDIT_SCHEMA, tag=f"{tag}.audit", batchable=True, batch_key=batch_key)
    out["source"] = "llm"
    return out


def normalize_vision(v: dict, views: list[str]) -> dict:
    """Vision facts -> rubric input. Facts the screenshot can't answer stay None (n/a), never guessed."""
    present = set(v.get("present") or [])
    absent = set(v.get("absent") or []) - present
    c, t = v.get("counts") or {}, v.get("text") or {}
    has_m, has_d = "mobile" in views, "desktop" in views

    def yn(k, ok=True):
        if not ok:
            return None
        return True if k in present else False if k in absent else None

    def cnt(k):
        x = c.get(k, -1)
        return None if x is None or x < 0 else x

    trust_map = {"secure": "trust_secure", "returns": "trust_returns", "authentic": "trust_authentic",
                 "free_shipping": "trust_free_shipping", "cod": "trust_cod", "quality": "trust_quality", "warranty": None}
    known = any(yn(s) is not None for s in trust_map.values() if s)
    trust = {k: bool(yn(s)) if s else False for k, s in trust_map.items()} if known else None
    variants = cnt("variant_options")
    popup_m, popup_d = yn("m_popup", has_m), yn("d_popup", has_d)
    decl = {"origin": yn("decl_origin"), "net_qty": yn("decl_net_qty"), "maker": yn("decl_maker"), "tax_incl": yn("decl_tax_incl")}
    rating = yn("rating_near_title")
    return {
        "title_atf": yn("m_title_atf", has_m), "price_atf": yn("m_price_atf", has_m), "atc_atf": yn("m_atc_atf", has_m),
        "atc_within_1_5": None, "atc_top_px": None, "image_atf": yn("m_image_atf", has_m),
        "entry_popup": None if popup_m is None else ("popup visible in screenshot" if popup_m else ""),
        "atc_present": bool(yn("atc_present")), "atc_label": t.get("atc_label", ""),
        "atc_contrast": None if yn("atc_contrast_ok") is None else (4.5 if yn("atc_contrast_ok") else 2.5),
        "atc_height_mobile": None, "sticky_atc": yn("m_sticky_atc", has_m),
        "rating_near_title": (t.get("rating_text") or "visible in screenshot") if rating else rating,
        "reviews_section": yn("reviews_section"),
        "trust_near": trust, "trust_page": trust, "trust_snippets": {},
        "payments_near": yn("payments_near"), "delivery_info": yn("delivery_info"),
        "variants": variants or 0, "variant_selects": (variants or 0) if yn("variants_dropdown_only") else 0,
        "size_guide": yn("size_guide"), "image_count": cnt("image_count"), "video": yn("video"),
        "bullets": cnt("bullets"), "description_len": 1, "faq": yn("faq"), "cross_sell": yn("cross_sell"),
        "compare_price": bool(yn("compare_price")),
        "discount_text": t.get("discount") or ("shown" if yn("discount_shown") else None), "offers_near": yn("offers_near"),
        "schema_product": None, "schema_offer": False, "schema_rating": False, "schema_rating_obj": None,
        "rating_on_page": False, "meta_desc_len": None, "h1_count": None, "alt_coverage": None, "body_font_px": None,
        "urgency_text": None, "title_text": t.get("title", ""), "price_text": t.get("price", ""),
        "compare_text": t.get("compare_price", ""),
        "d_title_atf": yn("d_title_price_atf", has_d), "d_price_atf": yn("d_title_price_atf", has_d),
        "d_atc_atf": yn("d_atc_atf", has_d), "d_atc_top_px": None, "d_two_col": yn("d_two_col", has_d),
        "d_entry_popup": None if popup_d is None else ("popup visible in screenshot" if popup_d else ""),
        "currency": (t.get("currency") or "").upper()[:3],
        "mandatory_info": None if all(x is None for x in decl.values()) else {k: bool(x) for k, x in decl.items()},
    }
