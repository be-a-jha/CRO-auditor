"""Deterministic CRO rubric. Same page in, same score out: the LLM writes the narrative but never sets the number."""
from __future__ import annotations

from .colors import contrast_ratio, parse_color
from .research import CHECK_RESEARCH

TRUST_KEYS = ("secure", "returns", "authentic", "warranty", "free_shipping", "cod", "quality")


def normalize_dom(d: dict, m: dict, sticky_mobile: bool, popups_mobile: list, page_bg: str,
                  popups_desktop: list | None = None) -> dict:
    """Merge desktop (d) and mobile (m) facts into the rubric's input."""
    vh = m["viewport"]["height"]
    dvh, dvw = d["viewport"]["height"], d["viewport"]["width"]

    def datf(f):
        if not f:
            return None
        r = f["rect"]
        return r["top"] + min(r["height"], 30) <= dvh

    def atf(f, frac=1.0):
        if not f:
            return None
        r = f["rect"]
        return r["top"] + min(r["height"], 30) <= vh * frac

    atc = m.get("atc") or d.get("atc")
    contrast = None
    if atc and atc.get("style"):
        fg = parse_color(atc["style"].get("color"))
        bg = parse_color(atc["style"].get("bg"))
        if bg is None or bg[3] < 0.1:
            bg = parse_color(page_bg)
        if fg and bg:
            contrast = round(contrast_ratio(fg[:3], bg[:3]), 2)
    rating_near = m.get("rating_near_title") or d.get("rating_near_title") or False
    schema = d.get("schema") or {}
    blocking = [p for p in popups_mobile if p.get("kind") == "popup"]
    blocking_d = [p for p in (popups_desktop or []) if p.get("kind") == "popup"]
    d_title, d_img = d.get("title"), d.get("first_image")
    two_col = None
    if d_title and d_img:
        two_col = d_title["rect"]["left"] >= dvw * 0.4 and d_img["left"] + d_img["width"] / 2 < dvw * 0.5
    return {
        "title_atf": atf(m.get("title")), "price_atf": atf(m.get("price")),
        "atc_atf": (m["atc"]["rect"]["top"] + m["atc"]["rect"]["height"] <= vh) if m.get("atc") else None,
        "atc_within_1_5": (m["atc"]["rect"]["top"] + m["atc"]["rect"]["height"] <= vh * 1.5) if m.get("atc") else None,
        "atc_top_px": m["atc"]["rect"]["top"] if m.get("atc") else None,
        "image_atf": (m["first_image"]["top"] < vh * 0.6) if m.get("first_image") else None,
        "entry_popup": blocking[0]["text"] if blocking else "",
        "atc_present": bool(atc), "atc_label": atc["label"] if atc else "",
        "atc_contrast": contrast,
        "atc_height_mobile": m["atc"]["rect"]["height"] if m.get("atc") else None,
        "sticky_atc": sticky_mobile or bool((m.get("atc") or {}).get("sticky_native")),
        "rating_near_title": rating_near, "reviews_section": d.get("reviews_section") or m.get("reviews_section"),
        "trust_near": {k: bool(d["trust_near_atc"].get(k) or m["trust_near_atc"].get(k)) for k in TRUST_KEYS},
        "trust_page": {k: bool(d["trust_on_page"].get(k) or m["trust_on_page"].get(k)) for k in TRUST_KEYS},
        "payments_near": d.get("payments_near_atc") or m.get("payments_near_atc"),
        "trust_snippets": d.get("trust_snippets") or {},
        "delivery_info": d.get("delivery_info") or m.get("delivery_info"),
        "variants": max(d.get("variant_inputs", 0), m.get("variant_inputs", 0)),
        "variant_selects": max(d.get("variant_selects", 0), m.get("variant_selects", 0)),
        "size_guide": d.get("size_guide") or m.get("size_guide"),
        "image_count": max(d.get("image_count", 0), m.get("image_count", 0)),
        "video": d.get("has_video") or m.get("has_video"),
        "bullets": d.get("bullets", 0), "description_len": d.get("description_len", 0),
        "faq": d.get("faq") or m.get("faq"), "cross_sell": d.get("cross_sell") or m.get("cross_sell"),
        "compare_price": bool(d.get("compare_price") or m.get("compare_price")),
        "discount_text": d.get("discount_text") or m.get("discount_text"),
        "offers_near": d.get("offers_near_atc") or m.get("offers_near_atc"),
        "schema_product": schema.get("product"), "schema_offer": bool(schema.get("offer")),
        "schema_rating": bool(schema.get("rating")), "schema_rating_obj": schema.get("rating"),
        "compare_text": (d.get("compare_price") or m.get("compare_price") or {}).get("text", ""),
        "rating_on_page": bool(rating_near or d.get("reviews_section")),
        "meta_desc_len": d.get("meta_description_len"), "h1_count": d.get("h1_count"),
        "alt_coverage": d.get("alt_coverage"), "body_font_px": m.get("body_font_px"),
        "urgency_text": d.get("urgency_text") or m.get("urgency_text"),
        "title_text": (d.get("title") or {}).get("text", ""), "price_text": (d.get("price") or m.get("price") or {}).get("text", ""),
        # desktop first screen
        "d_title_atf": datf(d.get("title")), "d_price_atf": datf(d.get("price")),
        "d_atc_atf": (d["atc"]["rect"]["top"] + d["atc"]["rect"]["height"] <= dvh) if d.get("atc") else None,
        "d_atc_top_px": d["atc"]["rect"]["top"] if d.get("atc") else None,
        "d_two_col": two_col,
        "d_entry_popup": blocking_d[0]["text"] if blocking_d else ("" if popups_desktop is not None else None),
        "currency": d.get("currency") or m.get("currency") or "",
        "mandatory_info": d.get("mandatory_info"),
        # the page's add-to-cart lives in a sticky / fixed bar (on mobile): new blocks must not be put next to it
        "atc_floating": bool((m.get("atc") or {}).get("sticky_native") or (d.get("atc") or {}).get("sticky_native")),
        # same HTML on both viewports (only CSS differs), so the deeper row of the two is safe for both
        "row_depth": {k: max((d.get("row_depth") or {}).get(k, 0), (m.get("row_depth") or {}).get(k, 0))
                      for k in set(d.get("row_depth") or {}) | set(m.get("row_depth") or {})},
        "floating_keys": sorted(set(d.get("floating") or []) | set(m.get("floating") or [])),
    }


MOBILE_ONLY = {"title_atf", "price_atf", "atc_atf", "image_atf", "no_popup", "atc_tap", "sticky_atc", "font"}
DESKTOP_ONLY = {"d_title_price_atf", "d_atc_atf", "d_buybox", "d_no_popup"}


def _c(cid, cat, title, w, status, evidence, fix=""):
    credit = {"pass": 1.0, "partial": 0.5, "fail": 0.0}.get(status)
    vp = "mobile" if cid in MOBILE_ONLY else "desktop" if cid in DESKTOP_ONLY else "both"
    return {"id": cid, "category": cat, "title": title, "weight": w, "status": status, "credit": credit,
            "evidence": evidence, "fix": fix, "viewport": vp, "research": CHECK_RESEARCH.get(cid, [])}


def _b(v, yes, no, fix="", partial_if=None):
    if v is None:
        return "na", "Could not determine from the page.", ""
    if partial_if:
        return "partial", partial_if, fix
    return ("pass", yes, "") if v else ("fail", no, fix)


def evaluate(F: dict) -> dict:
    C = []
    cat = "First screen (mobile)"
    s, e, f = _b(F["title_atf"], "Product name visible without scrolling.", "Product name is below the first mobile screen.",
                 "Pull the product title into the first screen, directly under the hero image.")
    C.append(_c("title_atf", cat, "Product name above the fold", 4, s, e, f))
    s, e, f = _b(F["price_atf"], "Price visible without scrolling.", "Price is not visible on the first mobile screen.",
                 "Show price next to the title in the first screen; price uncertainty is a top exit reason.")
    C.append(_c("price_atf", cat, "Price above the fold", 5, s, e, f))
    if F["atc_atf"] is None:
        C.append(_c("atc_atf", cat, "Add to cart above the fold", 6, "na", "No add-to-cart button detected."))
    elif F["atc_atf"]:
        C.append(_c("atc_atf", cat, "Add to cart above the fold", 6, "pass", "Add-to-cart fully visible on the first mobile screen."))
    else:
        st = "partial" if F["atc_within_1_5"] else "fail"
        C.append(_c("atc_atf", cat, "Add to cart above the fold", 6, st,
                    f"Add-to-cart starts {F['atc_top_px']}px down; mobile screen is 844px.",
                    "Tighten the hero (shorter gallery, smaller spacing) or add a sticky add-to-cart bar."))
    s, e, f = _b(F["image_atf"], "Product image leads the first screen.", "Product image is pushed down the first screen.",
                 "Lead with the product image; move banners and breadcrumbs below.")
    C.append(_c("image_atf", cat, "Product image leads", 3, s, e, f))
    if F["entry_popup"] is None:
        C.append(_c("no_popup", cat, "No entry popup", 4, "na", "Not checked."))
    elif F["entry_popup"]:
        C.append(_c("no_popup", cat, "No entry popup", 4, "fail", f"Popup on load: \"{F['entry_popup'][:90]}\"",
                    "Delay the email popup to exit intent or 2nd page view; never block a PDP on first load."))
    else:
        C.append(_c("no_popup", cat, "No entry popup", 4, "pass", "No blocking popup on first load."))

    cat = "First screen (desktop)"
    tp = [F.get("d_title_atf"), F.get("d_price_atf")]
    if all(v is None for v in tp):
        C.append(_c("d_title_price_atf", cat, "Name and price above the fold", 3, "na", "Not measured on desktop."))
    else:
        st = "pass" if all(tp) else "partial" if any(tp) else "fail"
        C.append(_c("d_title_price_atf", cat, "Name and price above the fold", 3, st,
                    {"pass": "Product name and price visible on the first desktop screen (1440x900).",
                     "partial": "Only one of name or price is on the first desktop screen.",
                     "fail": "Neither name nor price is on the first desktop screen."}[st],
                    "" if st == "pass" else "Bring the buy box (name, price, CTA) into the first desktop screen."))
    if F.get("d_atc_atf") is None:
        C.append(_c("d_atc_atf", cat, "Add to cart above the fold", 3, "na", "No desktop add-to-cart detected."))
    else:
        C.append(_c("d_atc_atf", cat, "Add to cart above the fold", 3, "pass" if F["d_atc_atf"] else "fail",
                    "Add-to-cart visible on the first desktop screen." if F["d_atc_atf"]
                    else f"Desktop add-to-cart starts {F.get('d_atc_top_px')}px down; the screen is 900px.",
                    "" if F["d_atc_atf"] else "Tighten the desktop buy box so the CTA sits within the first screen."))
    if F.get("d_two_col") is None:
        C.append(_c("d_buybox", cat, "Buy box beside the gallery", 2, "na", "Layout not measurable."))
    else:
        C.append(_c("d_buybox", cat, "Buy box beside the gallery", 2, "pass" if F["d_two_col"] else "fail",
                    "Gallery and buy box sit side by side." if F["d_two_col"]
                    else "Desktop stacks the gallery above the buy box, wasting the wide screen.",
                    "" if F["d_two_col"] else "Use a two-column desktop layout: gallery left, sticky buy box right."))
    if F.get("d_entry_popup") is None:
        C.append(_c("d_no_popup", cat, "No entry popup", 2, "na", "Not checked."))
    elif F["d_entry_popup"]:
        C.append(_c("d_no_popup", cat, "No entry popup", 2, "fail", f"Popup on load: \"{F['d_entry_popup'][:90]}\"",
                    "Delay the popup to exit intent on desktop."))
    else:
        C.append(_c("d_no_popup", cat, "No entry popup", 2, "pass", "No blocking popup on first desktop load."))

    cat = "Add to cart"
    if not F["atc_present"]:
        C.append(_c("atc_present", cat, "Clear add-to-cart button", 5, "fail", "No add-to-cart / buy-now button detected.",
                    "Add a single, high-contrast add-to-cart button next to the price."))
    else:
        C.append(_c("atc_present", cat, "Clear add-to-cart button", 5, "pass", f"Primary CTA: \"{F['atc_label']}\"."))
    cr = F["atc_contrast"]
    if cr is None:
        C.append(_c("atc_contrast", cat, "CTA text contrast", 4, "na", "Not measurable."))
    else:
        st = "pass" if cr >= 4.5 else "partial" if cr >= 3 else "fail"
        C.append(_c("atc_contrast", cat, "CTA text contrast", 4, st, f"Contrast ratio {cr}:1 (WCAG AA needs 4.5:1).",
                    "" if st == "pass" else "Raise CTA label contrast using the brand's own dark/light pair."))
    h = F["atc_height_mobile"]
    if h is None:
        C.append(_c("atc_tap", cat, "CTA tap target size", 3, "na", "No mobile CTA measured."))
    else:
        st = "pass" if h >= 44 else "partial" if h >= 40 else "fail"
        C.append(_c("atc_tap", cat, "CTA tap target size", 3, st, f"Mobile CTA height {h}px (target 44px+).",
                    "" if st == "pass" else "Make the mobile CTA at least 44px tall and full width."))
    s, e, f = _b(F["sticky_atc"], "Add-to-cart stays reachable after scrolling.",
                 "Add-to-cart disappears once the shopper scrolls into details/reviews.",
                 "Add a sticky bottom add-to-cart bar on mobile (title, price, CTA).")
    C.append(_c("sticky_atc", cat, "Sticky add-to-cart (mobile)", 3, s, e, f))

    cat = "Trust & social proof"
    if F["rating_near_title"]:
        C.append(_c("rating_title", cat, "Rating summary near title", 6, "pass", f"Found: \"{str(F['rating_near_title'])[:60]}\"."))
    elif F["rating_near_title"] is None:
        C.append(_c("rating_title", cat, "Rating summary near title", 6, "na", "Not determinable."))
    elif F["reviews_section"]:
        C.append(_c("rating_title", cat, "Rating summary near title", 6, "partial",
                    "Reviews exist further down but no star summary next to the title.",
                    "Surface the star rating + review count under the title, linked to the reviews section."))
    else:
        C.append(_c("rating_title", cat, "Rating summary near title", 6, "fail", "No ratings shown near the title.",
                    "Collect reviews (post-purchase WhatsApp/email) and show a star summary under the title."))
    s, e, f = _b(F["reviews_section"], "Reviews section present.", "No customer reviews section found.",
                 "Add a reviews section with photos; UGC reviews outperform brand copy.")
    C.append(_c("reviews", cat, "Customer reviews section", 4, s, e, f))
    if F["trust_near"] is None:
        C.append(_c("trust_atc", cat, "Trust signals near CTA", 5, "na", "Not determinable."))
    else:
        found = [k for k, v in F["trust_near"].items() if v]
        st = "pass" if len(found) >= 3 else "partial" if found else "fail"
        C.append(_c("trust_atc", cat, "Trust signals near CTA", 5, st,
                    ("Near CTA: " + ", ".join(found).replace("_", " ")) if found else "No returns / secure checkout / authenticity cues near the CTA.",
                    "" if st == "pass" else "Add a compact trust strip under the CTA (returns window, secure checkout, COD, authenticity)."))
    s, e, f = _b(F["payments_near"], "Payment options shown near CTA.", "No payment options (UPI / cards / COD / EMI) shown near CTA.",
                 "Show accepted payment methods (UPI, cards, COD, EMI) under the CTA.")
    C.append(_c("payments", cat, "Payment options visible", 5, s, e, f))

    cat = "Purchase friction"
    s, e, f = _b(F["delivery_info"], "Delivery estimate / pincode check near CTA.", "No delivery estimate or pincode check near the CTA.",
                 "Add a pincode checker with an estimated delivery date next to the CTA.")
    C.append(_c("delivery", cat, "Delivery estimate", 5, s, e, f))
    tn, tp = F["trust_near"] or {}, F["trust_page"] or {}
    if F["trust_near"] is None:
        C.append(_c("shipping", cat, "Shipping cost clarity", 3, "na", "Not determinable."))
        C.append(_c("returns", cat, "Returns policy near CTA", 4, "na", "Not determinable."))
    else:
        st = "pass" if tn.get("free_shipping") else "partial" if tp.get("free_shipping") else "fail"
        C.append(_c("shipping", cat, "Shipping cost clarity", 3, st,
                    {"pass": "Free shipping stated near CTA.", "partial": "Free shipping mentioned on page, not near CTA.",
                     "fail": "Shipping cost not stated anywhere on the page."}[st],
                    "" if st == "pass" else "State shipping cost or the free-shipping threshold next to the price."))
        st = "pass" if tn.get("returns") else "partial" if tp.get("returns") else "fail"
        C.append(_c("returns", cat, "Returns policy near CTA", 4, st,
                    {"pass": "Returns / exchange stated near CTA.", "partial": "Returns mentioned on page, not near CTA.",
                     "fail": "No returns or exchange policy on the page."}[st],
                    "" if st == "pass" else "Put the return window (e.g. [N]-day easy returns) right under the CTA."))
    if not F["variants"]:
        C.append(_c("variants", cat, "Variant selection UX", 3, "na", "Single-variant product."))
    elif F["variant_selects"] and F["variant_selects"] >= F["variants"]:
        C.append(_c("variants", cat, "Variant selection UX", 3, "partial", "Variants hidden inside a dropdown.",
                    "Replace dropdowns with visible buttons/swatches so all options are seen at once."))
    else:
        C.append(_c("variants", cat, "Variant selection UX", 3, "pass",
                    "Variants shown as visible options" + (" with size guide." if F["size_guide"] else ".")))

    cat = "Content & merchandising"
    n = F["image_count"]
    if n is None:
        C.append(_c("images", cat, "Product imagery depth", 4, "na", "Not determinable."))
    else:
        st = "pass" if n >= 6 else "partial" if n >= 4 else "fail"
        C.append(_c("images", cat, "Product imagery depth", 4, st, f"{n} distinct product images.",
                    "" if st == "pass" else "Add 6+ images: in-hand/scale, texture close-up, how-to-use, before/after, packaging."))
    s, e, f = _b(F["video"], "Product video present.", "No product video.", "Add a 15-30s how-to-use or demo video in the gallery.")
    C.append(_c("video", cat, "Product video", 2, s, e, f))
    if F["bullets"] is None:
        C.append(_c("scannable", cat, "Scannable benefits", 3, "na", "Not determinable."))
    elif F["bullets"] >= 3:
        C.append(_c("scannable", cat, "Scannable benefits", 3, "pass", f"{F['bullets']} bullet points in product info."))
    elif F["description_len"]:
        C.append(_c("scannable", cat, "Scannable benefits", 3, "partial", "Description is a text block, no benefit bullets.",
                    "Lead with 3-5 benefit bullets above the long description."))
    else:
        C.append(_c("scannable", cat, "Scannable benefits", 3, "fail", "No product description found.",
                    "Add benefit bullets and a short description."))
    s, e, f = _b(F["faq"], "FAQ present.", "No FAQ on the page.", "Add a 5-6 question FAQ accordion answering pre-purchase doubts.")
    C.append(_c("faq", cat, "FAQ / objection handling", 3, s, e, f))
    s, e, f = _b(F["cross_sell"], "Cross-sell present.", "No cross-sell / bundle module.", "Add 'Frequently bought together' or a bundle to lift AOV.")
    C.append(_c("cross_sell", cat, "Cross-sell / bundles", 3, s, e, f))

    cat = "Offer clarity"
    if not F["compare_price"]:
        C.append(_c("discount", cat, "Discount clearly stated", 3, "na", "No compare-at / MRP price shown."))
    elif F["discount_text"]:
        C.append(_c("discount", cat, "Discount clearly stated", 3, "pass", f"Shown as \"{F['discount_text']}\"."))
    else:
        C.append(_c("discount", cat, "Discount clearly stated", 3, "partial", "MRP is struck through but the saving is not stated.",
                    "Show the saving as '% off' next to the price."))
    if F["offers_near"] is None:
        C.append(_c("offers", cat, "Offers / coupons near price", 2, "na", "Not determinable."))
    else:
        C.append(_c("offers", cat, "Offers / coupons near price", 2, "pass" if F["offers_near"] else "partial",
                    "Offer messaging near price." if F["offers_near"] else "No coupon or offer messaging near price.",
                    "" if F["offers_near"] else "If an offer is running, show it under the price (coupon, bank offer, bundle)."))

    mi = F.get("mandatory_info")
    if F.get("currency") != "INR" or mi is None:
        C.append(_c("mandatory_info", cat, "Mandatory product declarations (India)", 2, "na",
                    "Applies to Indian listings (INR prices)." if mi is not None else "Not checkable."))
    else:
        names = {"origin": "country of origin", "net_qty": "net quantity", "maker": "manufacturer / marketer",
                 "tax_incl": "MRP inclusive of taxes"}
        have = [names[k] for k, v in mi.items() if v]
        miss = [names[k] for k, v in mi.items() if not v]
        st = "pass" if len(have) >= 3 else "partial" if have else "fail"
        C.append(_c("mandatory_info", cat, "Mandatory product declarations (India)", 2, st,
                    ("Found: " + ", ".join(have) + "." if have else "None found.")
                    + (" Missing: " + ", ".join(miss) + "." if miss else ""),
                    "" if st == "pass" else "Add the Legal Metrology declarations (" + ", ".join(miss)
                    + ") in a 'Product details' block."))

    cat = "Technical & SEO"
    if F["schema_product"] is None:
        C.append(_c("schema", cat, "Product structured data", 3, "na", "Not checkable from a screenshot."))
    else:
        st = "pass" if F["schema_product"] and F["schema_offer"] else "partial" if F["schema_product"] else "fail"
        C.append(_c("schema", cat, "Product structured data", 3, st,
                    {"pass": "Product JSON-LD with offer.", "partial": "Product JSON-LD without offer/price.",
                     "fail": "No Product JSON-LD (loses price/stock rich results)."}[st],
                    "" if st == "pass" else "Add Product schema with offers (price, currency, availability)."))
    if F["schema_product"] is None or not F["rating_on_page"]:
        C.append(_c("rating_schema", cat, "Review stars in search", 1, "na", "No ratings on page to mark up." if F["schema_product"] is not None else "Not checkable."))
    else:
        C.append(_c("rating_schema", cat, "Review stars in search", 1, "pass" if F["schema_rating"] else "fail",
                    "aggregateRating in schema." if F["schema_rating"] else "Ratings on page but not in schema (no stars in Google).",
                    "" if F["schema_rating"] else "Add aggregateRating to the Product schema."))
    ml = F["meta_desc_len"]
    if ml is None:
        C.append(_c("meta", cat, "Meta description", 1, "na", "Not checkable."))
    else:
        st = "pass" if 50 <= ml <= 170 else "partial" if ml else "fail"
        C.append(_c("meta", cat, "Meta description", 1, st, f"{ml} characters.", "" if st == "pass" else "Write a 120-160 character meta description."))
    hc = F["h1_count"]
    if hc is None:
        C.append(_c("h1", cat, "Single H1", 1, "na", "Not checkable."))
    else:
        C.append(_c("h1", cat, "Single H1", 1, "pass" if hc == 1 else "fail", f"{hc} visible H1 tags.",
                    "" if hc == 1 else "Use exactly one H1 (the product name)."))
    ac = F["alt_coverage"]
    if ac is None:
        C.append(_c("alt", cat, "Image alt text", 1, "na", "Not checkable."))
    else:
        st = "pass" if ac >= 0.9 else "partial" if ac >= 0.6 else "fail"
        C.append(_c("alt", cat, "Image alt text", 1, st, f"{int(ac * 100)}% of images have alt text.",
                    "" if st == "pass" else "Add descriptive alt text to product images."))
    fs = F["body_font_px"]
    if not fs:
        C.append(_c("font", cat, "Readable mobile body text", 1, "na", "Not measured."))
    else:
        st = "pass" if fs >= 14 else "partial" if fs >= 13 else "fail"
        C.append(_c("font", cat, "Readable mobile body text", 1, st, f"Body text {fs:g}px on mobile.",
                    "" if st == "pass" else "Bump mobile body copy to 14-16px."))

    flags = []
    if F.get("urgency_text"):
        flags.append(f"Urgency claim found: \"{F['urgency_text']}\". Must reflect real stock/timers; false urgency is a listed "
                     "dark pattern under India's CCPA Guidelines for Prevention and Regulation of Dark Patterns, 2023.")
    return {"checks": C, **score(C), "flags": flags}


def score(checks: list[dict]) -> dict:
    cats: dict[str, list] = {}
    for c in checks:
        cats.setdefault(c["category"], []).append(c)
    by_cat = {}
    got = tot = 0.0
    for k, cs in cats.items():
        g = sum(c["credit"] * c["weight"] for c in cs if c["credit"] is not None)
        t = sum(c["weight"] for c in cs if c["credit"] is not None)
        by_cat[k] = {"score": round(100 * g / t) if t else None, "weight": sum(c["weight"] for c in cs)}
        got += g
        tot += t
    total = round(100 * got / tot) if tot else 0
    grade = grade_of(total)
    na = sum(1 for c in checks if c["status"] == "na")
    return {"cro_score": total, "grade": grade, "categories": by_cat, "coverage": round(1 - na / len(checks), 2),
            "viewport_scores": {"mobile": _sub(checks, ("mobile", "both")), "desktop": _sub(checks, ("desktop", "both"))}}


def _sub(checks, vps) -> int | None:
    cs = [c for c in checks if c["viewport"] in vps and c["credit"] is not None]
    t = sum(c["weight"] for c in cs)
    return round(100 * sum(c["credit"] * c["weight"] for c in cs) / t) if t else None


def grade_of(score) -> str:
    if score is None:
        return "-"
    return "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "F"
