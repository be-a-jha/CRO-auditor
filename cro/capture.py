"""URL mode: load the product page in a real browser (desktop + mobile) and capture everything downstream needs."""
from __future__ import annotations

import asyncio
import re
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import js

DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0.0.0 Safari/537.36 CROAudit/1.0")
MOBILE_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/141.0.0.0 Mobile Safari/537.36 CROAudit/1.0")
DESKTOP = dict(viewport={"width": 1440, "height": 900}, user_agent=DESKTOP_UA, device_scale_factor=1)
MOBILE = dict(viewport={"width": 390, "height": 844}, user_agent=MOBILE_UA, device_scale_factor=2,
              is_mobile=True, has_touch=True)
BLOCK_RE = re.compile(r"just a moment|attention required|access denied|verify you are human|captcha|are you a robot", re.I)
CAPTURE_OVERHEAD_SECONDS = 45  # settling, scrolling, extraction and screenshots after navigation
CONTEXT_CLOSE_SECONDS = 5


class CaptureError(Exception):
    pass


class ViewCrash(CaptureError):
    pass


@dataclass
class ViewCapture:
    facts: dict
    screenshot_fold: Path
    screenshot_full: Path
    popups: list
    sticky_cta: bool
    perf: dict | None = None
    tokens_raw: dict | None = None
    outline: list | None = None
    html: str | None = None


@dataclass
class Capture:
    url: str
    final_url: str
    desktop: ViewCapture
    mobile: ViewCapture
    responses: dict = field(default_factory=dict)  # url -> (resource_type, bytes, content_type)
    mode: str = "full"


async def _settle(page, extra_ms=1500):
    try:
        await page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass
    await page.wait_for_timeout(extra_ms)


async def _scroll_through(page, depth: int):
    height = await page.evaluate("document.documentElement.scrollHeight")
    step = await page.evaluate("innerHeight") * 0.8
    y, n = 0, 0
    while y < min(height, depth) and n < 30:
        await page.evaluate(f"window.scrollTo(0, {int(y)})")
        await page.wait_for_timeout(180)
        y += step
        n += 1
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(600)


async def _load_view(browser, url: str, cfg: dict, outdir: Path, name: str, want_dom: bool,
                     responses: dict | None, timeout_ms: int, mode: str = "full") -> tuple[ViewCapture, str]:
    from playwright.async_api import Error as PWError, TimeoutError as PWTimeout

    ctx = await browser.new_context(locale="en-IN", timezone_id="Asia/Kolkata", ignore_https_errors=True,
                                    service_workers="allow" if mode == "full" else "block",
                                    java_script_enabled=mode != "static", **cfg)
    crashed = asyncio.Event()
    try:
        await ctx.add_init_script(js.PERF_INIT)
        page = await ctx.new_page()
        page.on("crash", lambda _: crashed.set())
        if mode != "full":
            async def trim(route):
                request = route.request
                kind = request.resource_type
                if kind == "document":
                    try:
                        if request.frame != page.main_frame:
                            return await route.abort()
                    except PWError:  # some navigations start before Playwright has a frame
                        pass
                if kind == "media":
                    return await route.abort()
                if kind in ("script", "xhr", "fetch", "ping", "eventsource"):
                    host = site_label(request.url)
                    first_party = site_label(page.url) if page.url.startswith(("http://", "https://")) else site_label(url)
                    if host not in (first_party, "cdn.shopify.com") and not host.endswith("." + first_party):
                        return await route.abort()
                await route.continue_()

            await page.route("**/*", trim)
        if responses is not None:
            def on_response(r):
                try:
                    h = r.headers
                    responses[r.url] = (r.request.resource_type, int(h.get("content-length") or 0), h.get("content-type", ""))
                except Exception:
                    pass
            page.on("response", on_response)
        resp = await page.goto(url, wait_until="commit", timeout=timeout_ms)
        try:  # heavy storefronts can hold DOMContentLoaded back behind blocking scripts; work with what rendered
            await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except PWTimeout:
            pass
        status = resp.status if resp is not None else 0
        if status in (401, 403, 429, 503):
            raise CaptureError(f"The site blocked the automated browser (HTTP {status}). Use a screenshot of the page instead.")
        await _settle(page)
        if BLOCK_RE.search(await page.title() or ""):
            raise CaptureError("Blocked by bot protection (captcha / challenge page). Use a screenshot instead.")
        if status >= 400:
            # Some React/Next storefronts send a 404 status for routes they then render client-side.
            # Only fail if no product actually rendered.
            looks_like_pdp = await page.evaluate(
                r"() => /add\s*to\s*(cart|bag|basket)|buy\s*(it\s*)?now/i.test(document.body ? document.body.innerText : '')")
            if not looks_like_pdp:
                raise CaptureError(f"Page not found (HTTP {status}). Open the URL in your browser and copy the exact "
                                   "address from the address bar after the product page loads.")
        perf = await page.evaluate(js.READ_PERF)  # read before scrolling so CLS reflects load, not our scroll
        popups = await page.evaluate(js.REMOVE_POPUPS)
        await page.keyboard.press("Escape")
        cap = 6000 if name == "mobile" else 5000
        if mode != "static":
            await _scroll_through(page, cap)
            popups += await page.evaluate(js.REMOVE_POPUPS)  # delayed popups
        facts = await page.evaluate(js.FACTS)
        facts.update(await page.evaluate(js.LAYOUT))  # row_depth, floating

        fold = outdir / f"{name}_fold.png"
        await page.screenshot(path=str(fold), timeout=60000)
        full = fold
        if mode != "static":
            full = outdir / f"{name}_full.png"
            doc_h = await page.evaluate("document.documentElement.scrollHeight")
            await page.screenshot(path=str(full), full_page=True, timeout=90000,
                                  clip={"x": 0, "y": 0, "width": cfg["viewport"]["width"], "height": min(doc_h, cap)})

        # Is an add-to-cart reachable once the user scrolls past the main one?
        sticky = False
        if facts.get("atc"):
            r = facts["atc"]["rect"]
            await page.evaluate(f"window.scrollTo(0, {r['top'] + r['height'] + cfg['viewport']['height']})")
            await page.wait_for_timeout(700)
            sticky = await page.evaluate(js.STICKY_CHECK)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(300)

        vc = ViewCapture(facts=facts, screenshot_fold=fold, screenshot_full=full, popups=popups,
                         sticky_cta=sticky, perf=perf)
        if want_dom:
            vc.tokens_raw = await page.evaluate(js.TOKENS)
            vc.outline = await page.evaluate(js.TAG_NODES)
            vc.html = await page.content()
        return vc, page.url
    except PWError as e:
        if crashed.is_set():
            raise ViewCrash(f"Chromium crashed during {name} capture.") from e
        raise
    finally:
        try:
            await asyncio.wait_for(ctx.close(), timeout=CONTEXT_CLOSE_SECONDS)
        except asyncio.TimeoutError:
            print(f"[warn] {name} browser context did not close in time")


async def capture_url(browser, url: str, outdir: Path, timeout_ms: int = 45000, light: bool = False,
                      *, slots: asyncio.Semaphore | None = None) -> Capture:
    """light=True (competitor pages): facts and screenshots only, no DOM snapshot, brand tokens or node outline.
    Heavy storefronts sometimes time out under load: one retry with a longer timeout before giving up."""
    from playwright.async_api import TimeoutError as PWTimeout
    try:
        try:
            return await _capture(browser, url, outdir, timeout_ms, light, slots)
        except PWTimeout:
            return await _capture(browser, url, outdir, int(timeout_ms * 1.5), light, slots)
    except ViewCrash as e:
        print(f"[warn] {e} Retrying with fewer storefront resources.", flush=True)
        try:
            return await _capture(browser, url, outdir, timeout_ms, light, slots, mode="reduced")
        except ViewCrash as retry_error:
            print(f"[warn] {retry_error} Retrying with storefront scripts disabled.", flush=True)
            return await _capture(browser, url, outdir, timeout_ms, light, slots, mode="static")


async def _capture(browser, url: str, outdir: Path, timeout_ms: int, light: bool,
                   slots: asyncio.Semaphore | None, mode: str = "full") -> Capture:
    outdir.mkdir(parents=True, exist_ok=True)
    responses: dict = {}

    async def load(cfg, name, want_dom, view_responses):
        async with slots if slots is not None else nullcontext():
            # Start the deadline after the viewport has a browser slot.
            try:
                return await asyncio.wait_for(
                    _load_view(browser, url, cfg, outdir, name, want_dom, view_responses, timeout_ms, mode),
                    timeout=timeout_ms / 1000 + CAPTURE_OVERHEAD_SECONDS,
                )
            except asyncio.TimeoutError as e:
                raise CaptureError(f"{name.title()} browser capture timed out. Retry the URL or upload screenshots.") from e

    views = [
        asyncio.create_task(load(DESKTOP, "desktop", not light, None)),
        asyncio.create_task(load(MOBILE, "mobile", False, responses)),
    ]
    try:
        done, _ = await asyncio.wait(views, return_when=asyncio.FIRST_EXCEPTION)
        for view in done:
            view.result()  # Propagate a viewport error before retrying.
        (desktop, final), (mobile, _) = (view.result() for view in views)
    finally:
        # If either viewport fails, stop the other one before retrying or releasing the browser slot.
        for view in views:
            if not view.done():
                view.cancel()
        await asyncio.gather(*views, return_exceptions=True)
    return Capture(url=url, final_url=final, desktop=desktop, mobile=mobile, responses=responses, mode=mode)


def freeze_html(html: str, base_url: str) -> str:
    """Turn the live DOM into a static page: no scripts, absolute URLs, lazy images resolved."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script"]):
        if (tag.get("type") or "").lower() != "application/ld+json":
            tag.decompose()
    for tag in soup.find_all(["noscript"]):
        tag.decompose()
    for tag in soup.find_all("link"):
        rel = " ".join(tag.get("rel") or []).lower()
        if "modulepreload" in rel or (("preload" in rel or "prefetch" in rel) and tag.get("as") == "script"):
            tag.decompose()
    for tag in soup.find_all("meta"):
        if (tag.get("http-equiv") or "").lower() in ("content-security-policy", "refresh"):
            tag.decompose()
    for tag in soup.find_all("iframe"):
        src = tag.get("src") or ""
        if not re.search(r"youtube|vimeo", src):
            tag.decompose()
    for tag in soup.find_all("base"):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.lower().startswith("on"):
                del tag.attrs[attr]
        if tag.name in ("img", "source"):
            for lazy, real in (("data-src", "src"), ("data-srcset", "srcset")):
                if tag.get(lazy) and (not tag.get(real) or str(tag.get(real)).startswith("data:")):
                    tag[real] = tag[lazy]
        for attr in ("href", "src", "poster", "action"):
            v = tag.get(attr)
            if isinstance(v, str) and v and not v.startswith(("data:", "#", "javascript:", "mailto:", "tel:")):
                tag[attr] = urljoin(base_url, v)
        if tag.get("srcset"):
            parts = []
            for p in str(tag["srcset"]).split(","):
                bits = p.strip().split()
                if bits:
                    bits[0] = urljoin(base_url, bits[0])
                    parts.append(" ".join(bits))
            tag["srcset"] = ", ".join(parts)
        if tag.get("style"):
            tag["style"] = _abs_css_urls(tag["style"], base_url)
    for st in soup.find_all("style"):
        if st.string:
            st.string = _abs_css_urls(st.string, base_url)
    head = soup.head or soup.new_tag("head")
    if soup.head is None and soup.html is not None:
        soup.html.insert(0, head)
    # No <base> tag on purpose: it would make in-page #anchors navigate to the live site.
    meta = soup.new_tag("meta", attrs={"name": "cro-source", "content": base_url})
    head.insert(0, meta)
    # The snapshot is the desktop DOM, also rendered at phone width. Desktop navs often live in an open <details>;
    # on a phone the theme's script would close it, but scripts are stripped, so keep header menus closed on mobile.
    fix = soup.new_tag("style", id="cro-freeze")
    fix.string = ("@media (max-width:749px){header details[open]>:not(summary),[data-cro-protected] details[open]>:not(summary)"
                  "{display:none!important}}")
    head.append(fix)
    if not soup.find("meta", attrs={"charset": True}):
        head.insert(0, soup.new_tag("meta", charset="utf-8"))
    return str(soup)


_URL_RE = re.compile(r"url\(\s*(['\"]?)(?!data:|https?:|#)([^'\")]+)\1\s*\)", re.I)
_IMPORT_RE = re.compile(r"@import\s+(['\"])(?!https?:)([^'\"]+)\1", re.I)


def _abs_css_urls(css: str, base_url: str) -> str:
    css = _URL_RE.sub(lambda m: f"url({m.group(1)}{urljoin(base_url, m.group(2).strip())}{m.group(1)})", css)
    return _IMPORT_RE.sub(lambda m: f"@import {m.group(1)}{urljoin(base_url, m.group(2))}{m.group(1)}", css)


def site_label(url: str) -> str:
    host = urlparse(url).hostname or url
    return host.removeprefix("www.")
