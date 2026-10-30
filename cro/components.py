"""Brand-styled add-on components. The model picks a component and fills short copy slots; the HTML and CSS come
from here. That keeps output tokens low (the model never writes markup for common add-ons) and every component
uses only the brand's CSS variables, so it looks like the brand's own designer built it.

Each renderer returns (html, css, allowed_numbers) or None when it has nothing valid to show. All copy still goes
through the claim guard afterwards (numbers not on the page become placeholders)."""
from __future__ import annotations

import html as htmllib
import re

ICON = {
    "returns": '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/>',
    "secure": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "truck": '<path d="M3 6h11v10H3zM14 10h4l3 3v3h-7"/><circle cx="7" cy="18" r="1.8"/><circle cx="17" cy="18" r="1.8"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
    "cod": '<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 9v.01M18 15v.01"/>',
    "leaf": '<path d="M5 19c0-8 6-14 15-14 0 9-6 15-14 15"/><path d="M5 19l7-7"/>',
    "star": '<path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5L16 9.5"/>',
    "gift": '<rect x="3" y="9" width="18" height="12" rx="1"/><path d="M3 13h18M12 9v12M12 9c-2-4-6-4-6-1s6 1 6 1 6 2 6-1-4-3-6 1"/>',
    "award": '<circle cx="12" cy="9" r="5"/><path d="M8.5 13l-1.5 8 5-3 5 3-1.5-8"/>',
    "heart": '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
    "drop": '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
    "bolt": '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
}
ICON_NAMES = list(ICON)

COMPONENTS = {
    "sticky_atc": "Mobile sticky bar with product name, price and the brand's add-to-cart label. Target: atc. No copy needed.",
    "trust_strip": "Row of 2-4 short trust cues with icons, under the CTA. items: {text, icon}; label: optional payment line.",
    "delivery_check": "Pincode / delivery-date checker next to the CTA. heading: field label; label: hint line.",
    "rating_summary": "Star rating + review count under the title, linking to reviews. label: e.g. '4.6 (1,203 reviews)'.",
    "variant_pills": "Shows the variant dropdown's options as tap-able buttons. Target: variants. No copy needed.",
    "savings_badge": "'% off' badge computed from price and MRP. Target: compare_price or price. No copy needed.",
    "offer_box": "Box listing current offers or coupons. heading + items: {text}.",
    "benefits": "Icon grid of 3-4 benefits. heading + items: {title, text, icon}.",
    "key_facts": "Stat grid of 3-4 product facts (e.g. '25g' / 'protein per scoop'). items: {title=value, text=label}.",
    "how_to_use": "Numbered steps. heading + items: {text}.",
    "faq": "Accordion FAQ. heading + items: {title=question, text=answer}.",
    "review_highlights": "2-3 short review quotes copied verbatim from the page. items: {text=quote, title=reviewer}.",
    "guarantee": "Guarantee badge. label: headline; heading: optional short line under it.",
}

BTN = ("display:inline-flex;align-items:center;justify-content:center;min-height:var(--cro-btn-height);padding:var(--cro-btn-padding);"
       "background:var(--cro-primary);color:var(--cro-on-primary);border:0;border-radius:var(--cro-radius);"
       "font-family:var(--cro-font-button);font-size:var(--cro-fs-button);text-transform:var(--cro-btn-transform);"
       "letter-spacing:var(--cro-btn-spacing);font-weight:var(--cro-btn-weight);text-decoration:none;white-space:nowrap")
HEAD = "font-family:var(--cro-font-heading);font-size:var(--cro-fs-lg);color:var(--cro-heading);margin:0 0 10px"


def esc(s) -> str:
    return htmllib.escape(str(s or "").strip(), quote=False)


def icon(name: str, size: int = 20) -> str:
    body = ICON.get((name or "").lower().strip(), ICON["check"])
    return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{body}</svg>')


def _items(spec, n_max, need_title=False, need_text=True):
    out = []
    for it in spec.get("items") or []:
        t, x = str(it.get("title") or "").strip(), str(it.get("text") or "").strip()
        if (need_title and not t) or (need_text and not x):
            continue
        out.append({"title": t[:90], "text": x[:300], "icon": str(it.get("icon") or "check")})
    return out[:n_max]


def _num(s):
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s or "")
    return float(m.group(0).replace(",", "")) if m else None


# ------------------------------------------------------------------ renderers
def sticky_atc(spec, page):
    title, price, label = esc(page.get("title_text")), esc(page.get("price_text")), esc(page.get("atc_label") or "Add to cart")
    html = (f'<div class="cro-sticky-atc" role="region" aria-label="Quick add to cart"><div class="cro-sticky-atc__info">'
            f'<div class="cro-sticky-atc__title">{title}</div><div class="cro-sticky-atc__price">{price}</div></div>'
            f'<a class="cro-sticky-atc__btn" href="#cro-atc">{label}</a></div>')
    css = (".cro-sticky-atc{display:none}"
           "@media (max-width:749px){.cro-sticky-atc{display:flex;align-items:center;gap:12px;position:fixed;left:0;right:0;bottom:0;"
           "z-index:60;padding:10px 16px;background:var(--cro-bg);border-top:1px solid var(--cro-border);font-family:var(--cro-font-body)}}"
           ".cro-sticky-atc__info{flex:1;min-width:0}"
           ".cro-sticky-atc__title{font-size:var(--cro-fs-sm);color:var(--cro-text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
           ".cro-sticky-atc__price{font-size:var(--cro-fs-base);color:var(--cro-heading);font-weight:600}"
           f".cro-sticky-atc__btn{{{BTN}}}")
    return html, css, set(re.findall(r"\d[\d,.]*", page.get("price_text") or ""))


def trust_strip(spec, page):
    items = _items(spec, 4)
    if len(items) < 2:
        sn = page.get("trust_snippets") or {}
        items = [{"text": sn.get("returns") or "[N]-day easy returns", "icon": "returns"},
                 {"text": "Secure checkout", "icon": "secure"},
                 {"text": sn.get("free_shipping") or "Free shipping above [amount]", "icon": "truck"},
                 {"text": "Genuine product, direct from the brand", "icon": "shield"}]
    cells = "".join(f'<li class="cro-trust__item"><span class="cro-trust__icon">{icon(i["icon"])}</span><span>{esc(i["text"])}</span></li>'
                    for i in items)
    pay = f'<div class="cro-trust__pay">{esc(spec.get("label"))}</div>' if spec.get("label") else ""
    html = f'<div class="cro-trust"><ul class="cro-trust__list">{cells}</ul>{pay}</div>'
    css = (".cro-trust{margin:16px 0;padding:12px 0;border-top:1px solid var(--cro-border);border-bottom:1px solid var(--cro-border);"
           "font-family:var(--cro-font-body);color:var(--cro-text)}"
           ".cro-trust__list{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:1fr 1fr;gap:10px 16px}"
           ".cro-trust__item{display:flex;gap:8px;align-items:center;font-size:var(--cro-fs-sm);line-height:1.3}"
           ".cro-trust__icon{color:var(--cro-primary);display:inline-flex;flex:none}"
           ".cro-trust__pay{margin-top:10px;font-size:var(--cro-fs-xs);color:var(--cro-muted)}")
    return html, css, set()


def delivery_check(spec, page):
    head = esc(spec.get("heading") or "Check delivery date")
    hint = esc(spec.get("label") or "Delivered by [date] · [COD available]")
    html = (f'<form class="cro-pincode" aria-label="Check delivery"><label class="cro-pincode__label" for="cro-pin">{head}</label>'
            '<div class="cro-pincode__row"><input id="cro-pin" class="cro-pincode__input" inputmode="numeric" placeholder="Enter pincode">'
            f'<button class="cro-pincode__btn" type="button">Check</button></div><div class="cro-pincode__hint">{hint}</div></form>')
    css = (".cro-pincode{margin:14px 0;font-family:var(--cro-font-body);color:var(--cro-text)}"
           ".cro-pincode__label{display:block;font-size:var(--cro-fs-sm);margin-bottom:6px}"
           ".cro-pincode__row{display:flex;gap:8px}"
           ".cro-pincode__input{flex:1;min-width:0;min-height:44px;padding:0 12px;border:1px solid var(--cro-border);border-radius:var(--cro-radius-input);"
           "background:var(--cro-bg);color:var(--cro-text);font-family:var(--cro-font-body);font-size:var(--cro-fs-base)}"
           ".cro-pincode__btn{min-height:44px;padding:0 16px;border:1px solid var(--cro-text);background:transparent;color:var(--cro-text);"
           "border-radius:var(--cro-radius);font-family:var(--cro-font-button);font-size:var(--cro-fs-sm);"
           "text-transform:var(--cro-btn-transform);letter-spacing:var(--cro-btn-spacing)}"
           ".cro-pincode__hint{margin-top:6px;font-size:var(--cro-fs-xs);color:var(--cro-muted)}")
    return html, css, set()


def rating_summary(spec, page):
    sr = page.get("schema_rating_obj") or {}
    label = str(spec.get("label") or "").strip()
    allow = set()
    if sr.get("value"):
        allow = {str(sr.get("value")), str(sr.get("count") or "")}
        label = label or f"{sr.get('value')} ({sr.get('count')} reviews)"
    label = label or "[Rating] ([N] reviews)"
    # stars fill to the real rating; with no known rating they stay empty (never imply a rating the page doesn't have)
    val = _num(label) if _num(label) is not None and _num(label) <= 5 else None
    if val is not None and page.get("digits") is not None and f"{val:g}" not in page["digits"] | allow:
        val = None  # a rating the page doesn't state: show empty stars; the guard marks the number as a placeholder
    fill = 0 if val is None else round(100 * val / 5)
    html = (f'<a class="cro-rating" href="#cro-reviews"><span class="cro-rating__stars" aria-hidden="true">'
            f'<span class="cro-rating__base">&#9734;&#9734;&#9734;&#9734;&#9734;</span>'
            f'<span class="cro-rating__fill" style="width:{fill}%">&#9733;&#9733;&#9733;&#9733;&#9733;</span></span> {esc(label)}</a>')
    css = (".cro-rating{display:inline-flex;gap:6px;align-items:center;margin:4px 0 8px;font-family:var(--cro-font-body);"
           "font-size:var(--cro-fs-sm);color:var(--cro-text);text-decoration:none}"
           ".cro-rating__stars{position:relative;display:inline-block;color:var(--cro-primary);letter-spacing:1px;line-height:1}"
           ".cro-rating__fill{position:absolute;left:0;top:0;overflow:hidden;white-space:nowrap}")
    return html, css, allow | {str(fill)}


def variant_pills(spec, page):
    opts = [o for o in page.get("variant_options") or [] if o][:8]
    if len(opts) < 2:
        return None
    pills = "".join(f'<span class="cro-pill{" cro-pill--on" if i == 0 else ""}">{esc(o)}</span>' for i, o in enumerate(opts))
    html = f'<div class="cro-pills" role="radiogroup">{pills}</div>'
    css = (".cro-pills{display:flex;flex-wrap:wrap;gap:8px;margin:4px 0 8px}"
           ".cro-pill{display:inline-flex;align-items:center;min-height:44px;padding:0 14px;border:1px solid var(--cro-border);"
           "border-radius:var(--cro-radius-input);font-family:var(--cro-font-body);font-size:var(--cro-fs-sm);color:var(--cro-text);background:var(--cro-bg)}"
           ".cro-pill--on{border-color:var(--cro-text);box-shadow:inset 0 0 0 1px var(--cro-text)}")
    return html, css, set(re.findall(r"\d+(?:\.\d+)?", " ".join(opts)))


def savings_badge(spec, page):
    pn, cn = _num(page.get("price_text")), _num(page.get("compare_text"))
    if not (pn and cn and cn > pn):
        return None
    pct = round(100 * (cn - pn) / cn)
    html = f'<span class="cro-save">{pct}% off</span>'
    css = (".cro-save{display:inline-block;margin-left:8px;font-family:var(--cro-font-body);font-size:var(--cro-fs-sm);"
           "color:var(--cro-accent);font-weight:600}")
    return html, css, {str(pct)}


def offer_box(spec, page):
    items = _items(spec, 4)
    if not items:
        return None
    head = f'<div class="cro-offers__title">{esc(spec.get("heading") or "Offers for you")}</div>'
    li = "".join(f'<li class="cro-offers__item">{icon("gift", 18)}<span>{esc(i["text"])}</span></li>' for i in items)
    html = f'<div class="cro-offers">{head}<ul class="cro-offers__list">{li}</ul></div>'
    css = (".cro-offers{margin:14px 0;padding:12px 14px;border:1px dashed var(--cro-border);border-radius:var(--cro-radius-input);"
           "background:var(--cro-surface);font-family:var(--cro-font-body);color:var(--cro-text)}"
           ".cro-offers__title{font-size:var(--cro-fs-sm);font-weight:600;color:var(--cro-heading);margin-bottom:6px}"
           ".cro-offers__list{list-style:none;margin:0;padding:0;display:grid;gap:6px}"
           ".cro-offers__item{display:flex;gap:8px;align-items:flex-start;font-size:var(--cro-fs-sm)}"
           ".cro-offers__item svg{color:var(--cro-primary);flex:none;margin-top:1px}")
    return html, css, set()


def benefits(spec, page):
    items = _items(spec, 4, need_title=True, need_text=False)
    if len(items) < 2:
        return None
    head = f'<div class="cro-benefits__title">{esc(spec["heading"])}</div>' if spec.get("heading") else ""
    cells = ""
    for i in items:
        sub = f"<span>{esc(i['text'])}</span>" if i["text"] else ""
        cells += (f'<li class="cro-benefits__item"><span class="cro-benefits__icon">{icon(i["icon"], 24)}</span>'
                  f'<b>{esc(i["title"])}</b>{sub}</li>')
    html = f'<div class="cro-benefits">{head}<ul class="cro-benefits__list">{cells}</ul></div>'
    css = (".cro-benefits{margin:18px 0;font-family:var(--cro-font-body);color:var(--cro-text)}"
           f".cro-benefits__title{{{HEAD}}}"
           ".cro-benefits__list{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:1fr 1fr;gap:12px}"
           ".cro-benefits__item{display:flex;flex-direction:column;gap:4px;padding:12px;background:var(--cro-surface);"
           "border-radius:var(--cro-radius-input);font-size:var(--cro-fs-sm)}"
           ".cro-benefits__item b{font-size:var(--cro-fs-base);color:var(--cro-heading)}"
           ".cro-benefits__icon{color:var(--cro-primary);display:inline-flex}"
           "@media (min-width:750px){.cro-benefits__list{grid-template-columns:repeat(4,1fr)}}")
    return html, css, set()


def key_facts(spec, page):
    items = _items(spec, 4, need_title=True)
    if len(items) < 2:
        return None
    cells = "".join(f'<li class="cro-facts__item"><b>{esc(i["title"])}</b><span>{esc(i["text"])}</span></li>' for i in items)
    head = f'<div class="cro-facts__title">{esc(spec["heading"])}</div>' if spec.get("heading") else ""
    html = f'<div class="cro-facts">{head}<ul class="cro-facts__list">{cells}</ul></div>'
    css = (".cro-facts{margin:14px 0;font-family:var(--cro-font-body);color:var(--cro-text)}"
           f".cro-facts__title{{{HEAD}}}"
           ".cro-facts__list{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(2,1fr);gap:8px}"
           ".cro-facts__item{display:flex;flex-direction:column;align-items:center;text-align:center;gap:2px;padding:10px 6px;"
           "border:1px solid var(--cro-border);border-radius:var(--cro-radius-input)}"
           ".cro-facts__item b{font-family:var(--cro-font-heading);font-size:var(--cro-fs-xl);color:var(--cro-heading);line-height:1.1}"
           ".cro-facts__item span{font-size:var(--cro-fs-xs);color:var(--cro-muted)}"
           "@media (min-width:750px){.cro-facts__list{grid-template-columns:repeat(4,1fr)}}")
    return html, css, set()


def how_to_use(spec, page):
    items = _items(spec, 5)
    if len(items) < 2:
        return None
    li = "".join(f'<li class="cro-steps__item"><span class="cro-steps__n">{n}</span><span>{esc(i["text"])}</span></li>'
                 for n, i in enumerate(items, 1))
    html = (f'<div class="cro-steps"><div class="cro-steps__title">{esc(spec.get("heading") or "How to use")}</div>'
            f'<ol class="cro-steps__list">{li}</ol></div>')
    css = (".cro-steps{margin:18px 0;font-family:var(--cro-font-body);color:var(--cro-text)}"
           f".cro-steps__title{{{HEAD}}}"
           ".cro-steps__list{list-style:none;margin:0;padding:0;display:grid;gap:10px}"
           ".cro-steps__item{display:flex;gap:10px;align-items:flex-start;font-size:var(--cro-fs-sm)}"
           ".cro-steps__n{flex:none;display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;"
           "border-radius:999px;background:var(--cro-primary);color:var(--cro-on-primary);font-size:var(--cro-fs-xs);font-weight:600}"
           "@media (min-width:750px){.cro-steps__list{grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}}")
    return html, css, {str(n) for n in range(1, 6)}


def faq(spec, page):
    items = _items(spec, 6, need_title=True, need_text=False)
    if len(items) < 2:
        return None
    qa = "".join(f'<details class="cro-faq__item"><summary class="cro-faq__q">{esc(i["title"])}</summary>'
                 f'<div class="cro-faq__a">{esc(i["text"] or "[Answer from the brand]")}</div></details>' for i in items)
    html = f'<div class="cro-faq"><div class="cro-faq__title">{esc(spec.get("heading") or "Frequently asked questions")}</div>{qa}</div>'
    css = (".cro-faq{margin:24px 0;font-family:var(--cro-font-body);color:var(--cro-text)}"
           f".cro-faq__title{{{HEAD}}}"
           ".cro-faq__item{border-bottom:1px solid var(--cro-border);padding:12px 0}"
           ".cro-faq__q{cursor:pointer;font-size:var(--cro-fs-base)}"
           ".cro-faq__a{margin-top:8px;font-size:var(--cro-fs-sm);color:var(--cro-muted)}")
    return html, css, set()


def review_highlights(spec, page):
    text_lc = re.sub(r"\s+", " ", (page.get("page_text") or "").lower())
    items = []
    for i in _items(spec, 3):
        q = re.sub(r"\s+", " ", i["text"].strip(" \"'“”")).lower()
        if len(q) >= 12 and q[:60] in text_lc:  # quotes must be real reviews from the page
            items.append(i)
    if not items:
        return None
    cards = ""
    for i in items:
        cap = f"<figcaption>{esc(i['title'])}</figcaption>" if i["title"] else ""
        cards += f'<figure class="cro-quotes__item"><blockquote>{esc(i["text"])}</blockquote>{cap}</figure>'
    html = (f'<div class="cro-quotes"><div class="cro-quotes__title">{esc(spec.get("heading") or "What customers say")}</div>'
            f'<div class="cro-quotes__list">{cards}</div></div>')
    css = (".cro-quotes{margin:18px 0;font-family:var(--cro-font-body);color:var(--cro-text)}"
           f".cro-quotes__title{{{HEAD}}}"
           ".cro-quotes__list{display:grid;gap:10px}"
           ".cro-quotes__item{margin:0;padding:12px 14px;background:var(--cro-surface);border-radius:var(--cro-radius-input)}"
           ".cro-quotes__item blockquote{margin:6px 0;font-size:var(--cro-fs-sm)}"
           ".cro-quotes__item figcaption{font-size:var(--cro-fs-xs);color:var(--cro-muted)}"
           "@media (min-width:750px){.cro-quotes__list{grid-template-columns:repeat(3,1fr)}}")
    return html, css, set()


def guarantee(spec, page):
    label = esc(spec.get("label") or "[N]-day money-back guarantee")
    sub = f'<span class="cro-guarantee__sub">{esc(spec.get("heading"))}</span>' if spec.get("heading") else ""
    html = f'<div class="cro-guarantee">{icon("award", 28)}<div><b>{label}</b>{sub}</div></div>'
    css = (".cro-guarantee{display:flex;gap:10px;align-items:center;margin:14px 0;padding:10px 12px;border:1px solid var(--cro-border);"
           "border-radius:var(--cro-radius-input);font-family:var(--cro-font-body);color:var(--cro-text);font-size:var(--cro-fs-sm)}"
           ".cro-guarantee svg{color:var(--cro-primary);flex:none}.cro-guarantee b{display:block;color:var(--cro-heading)}"
           ".cro-guarantee__sub{color:var(--cro-muted);font-size:var(--cro-fs-xs)}")
    return html, css, set()


RENDER = {"sticky_atc": sticky_atc, "trust_strip": trust_strip, "delivery_check": delivery_check,
          "rating_summary": rating_summary, "variant_pills": variant_pills, "savings_badge": savings_badge,
          "offer_box": offer_box, "benefits": benefits, "key_facts": key_facts, "how_to_use": how_to_use, "faq": faq,
          "review_highlights": review_highlights, "guarantee": guarantee}
assert set(RENDER) == set(COMPONENTS)


def render(name: str, spec: dict, page: dict):
    fn = RENDER.get(name)
    return fn(spec, page) if fn else None


def catalog_prompt() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in COMPONENTS.items()) + "\nIcons: " + ", ".join(ICON_NAMES)
