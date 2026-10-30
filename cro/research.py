"""CRO research library. Every rubric check and every recommendation cites one of these, so the report says why
a change matters and who found it. Findings are stated as the sources state them; no numbers are added here that
the source does not publish. URLs are given only where the page address is stable."""
from __future__ import annotations

RESEARCH = [
    {"id": "nng-fold", "source": "Nielsen Norman Group, Scrolling and Attention (2018)",
     "finding": "57% of page-viewing time is spent above the fold and 74% within the first two screenfuls, so what the "
                "first screen shows gets most of the attention.",
     "url": "https://www.nngroup.com/articles/scrolling-and-attention/"},
    {"id": "nng-scan", "source": "Nielsen Norman Group, Concise, Scannable and Objective writing study",
     "finding": "Users scan web pages rather than read them; a concise, scannable, objective version of a page measured "
                "124% better usability than the original.",
     "url": "https://www.nngroup.com/articles/concise-scannable-and-objective-how-to-write-for-the-web/"},
    {"id": "baymard-abandon", "source": "Baymard Institute, Cart Abandonment Rate statistics",
     "finding": "The documented average cart abandonment rate is about 70%. Extra costs (shipping, taxes, fees) revealed "
                "late are the #1 reason shoppers abandon; slow delivery, not trusting the site with card details and an "
                "unsatisfactory returns policy are also among the top reasons.",
     "url": "https://baymard.com/lists/cart-abandonment-rate"},
    {"id": "baymard-pdp", "source": "Baymard Institute, Product Page UX research",
     "finding": "Shoppers judge products mainly through images and product information; when key details (scale, "
                "variations, specs, delivery) are missing or hard to find, they leave the page or look elsewhere. "
                "Variation options should be visible (buttons or swatches) rather than hidden in drop-downs.",
     "url": "https://baymard.com/research/product-page"},
    {"id": "spiegel-reviews", "source": "Spiegel Research Center, Northwestern University, How Online Reviews Influence Sales (2017)",
     "finding": "A product with five reviews is 270% more likely to be purchased than one with none. Purchase likelihood "
                "peaks for average ratings of 4.0-4.7 and falls as ratings approach 5.0, and reviews matter more for "
                "higher-priced products.",
     "url": "https://spiegel.medill.northwestern.edu/how-online-reviews-influence-sales/"},
    {"id": "cialdini-proof", "source": "Robert Cialdini, Influence (1984)",
     "finding": "Social proof: under uncertainty people follow what others like them have done, which is why visible "
                "ratings, review counts and real customer photos reduce hesitation.", "url": ""},
    {"id": "google-speed", "source": "Google / SOASTA research, Think with Google (2017)",
     "finding": "As mobile page load time goes from 1s to 3s, the probability of a bounce increases 32%; from 1s to 5s, "
                "it increases 90%.",
     "url": "https://www.thinkwithgoogle.com/marketing-strategies/app-and-mobile/page-load-time-statistics/"},
    {"id": "deloitte-speed", "source": "Deloitte for Google, Milliseconds Make Millions (2020)",
     "finding": "A 0.1s improvement in mobile site speed increased retail conversions by 8.4% and average order value by 9.2%.",
     "url": ""},
    {"id": "google-cwv", "source": "Google web.dev, Core Web Vitals",
     "finding": "Good thresholds at the 75th percentile of visits: LCP 2.5s or less, INP 200ms or less, CLS 0.1 or less.",
     "url": "https://web.dev/articles/vitals"},
    {"id": "wcag-contrast", "source": "W3C WCAG 2.2, SC 1.4.3 Contrast (Minimum)",
     "finding": "Text needs a contrast ratio of at least 4.5:1 against its background (3:1 for large text).",
     "url": "https://www.w3.org/TR/WCAG22/#contrast-minimum"},
    {"id": "wcag-target", "source": "W3C WCAG 2.2, SC 2.5.5 and 2.5.8 Target Size",
     "finding": "Touch targets of 44x44 CSS px meet the enhanced level (24x24 is the minimum); Apple's Human Interface "
                "Guidelines also recommend 44pt.", "url": "https://www.w3.org/TR/WCAG22/#target-size-minimum"},
    {"id": "fitts", "source": "Fitts's law (Fitts, 1954)",
     "finding": "The time to hit a target grows with its distance and shrinks with its size, so a large CTA that stays "
                "within reach (sticky) is faster and easier to tap.", "url": ""},
    {"id": "choice-overload", "source": "Iyengar & Lepper, When Choice is Demotivating (2000)",
     "finding": "Shoppers shown 6 jam flavours were about ten times more likely to buy (30%) than those shown 24 (3%): "
                "too many competing options and CTAs stall decisions.", "url": ""},
    {"id": "anchoring", "source": "Tversky & Kahneman, Judgment under Uncertainty (1974)",
     "finding": "Anchoring: a reference price (MRP or compare-at price) shapes perceived value, so showing the saving "
                "next to the selling price makes the offer register instantly.", "url": ""},
    {"id": "wyzowl-video", "source": "Wyzowl, State of Video Marketing (annual survey)",
     "finding": "In every recent edition, over 80% of consumers say a brand's video has convinced them to buy a product.",
     "url": "https://www.wyzowl.com/video-marketing-statistics/"},
    {"id": "google-interstitials", "source": "Google Search Central, intrusive interstitials guidance",
     "finding": "Popups that cover the main content on mobile make it hard to access and can hurt how the page performs "
                "in Google Search.",
     "url": "https://developers.google.com/search/docs/appearance/avoid-intrusive-interstitials"},
    {"id": "google-product-schema", "source": "Google Search Central, Product structured data",
     "finding": "Product markup with offers and aggregateRating makes pages eligible for price, availability and review "
                "star rich results.",
     "url": "https://developers.google.com/search/docs/appearance/structured-data/product"},
    {"id": "lighthouse-font", "source": "Google Lighthouse, legible font sizes audit; Apple HIG",
     "finding": "Lighthouse flags text below 12px as illegible on mobile; platform guidelines set body text larger "
                "(Apple's default body style is 17pt).", "url": ""},
    {"id": "ccpa-dark", "source": "Central Consumer Protection Authority (India), Guidelines for Prevention and Regulation of Dark Patterns, 2023",
     "finding": "Lists 13 dark patterns, including false urgency, basket sneaking, confirm shaming and drip pricing; "
                "urgency, scarcity and price claims must be true and fully disclosed.", "url": ""},
    {"id": "india-lm", "source": "Legal Metrology (Packaged Commodities) Rules, 2011, as amended in 2017 (India)",
     "finding": "E-commerce listings of packaged goods must show declarations such as manufacturer or packer, net "
                "quantity, MRP inclusive of all taxes, country of origin and consumer-care details.", "url": ""},
]

BY_ID = {r["id"]: r for r in RESEARCH}

# rubric check id -> research ids that justify it
CHECK_RESEARCH = {
    "title_atf": ["nng-fold"], "price_atf": ["nng-fold", "baymard-abandon"], "atc_atf": ["nng-fold", "fitts"],
    "image_atf": ["nng-fold", "baymard-pdp"], "no_popup": ["google-interstitials"],
    "d_title_price_atf": ["nng-fold"], "d_atc_atf": ["nng-fold", "fitts"], "d_buybox": ["nng-fold", "baymard-pdp"],
    "d_no_popup": ["google-interstitials"],
    "atc_present": ["choice-overload"], "atc_contrast": ["wcag-contrast"], "atc_tap": ["wcag-target", "fitts"],
    "sticky_atc": ["fitts", "nng-fold"],
    "rating_title": ["spiegel-reviews", "cialdini-proof"], "reviews": ["spiegel-reviews", "cialdini-proof"],
    "trust_atc": ["baymard-abandon"], "payments": ["baymard-abandon"],
    "delivery": ["baymard-abandon", "baymard-pdp"], "shipping": ["baymard-abandon"], "returns": ["baymard-abandon"],
    "variants": ["baymard-pdp", "choice-overload"],
    "images": ["baymard-pdp"], "video": ["wyzowl-video"], "scannable": ["nng-scan"], "faq": ["baymard-pdp"],
    "cross_sell": [], "discount": ["anchoring"], "offers": ["anchoring", "baymard-abandon"],
    "mandatory_info": ["india-lm"],
    "schema": ["google-product-schema"], "rating_schema": ["google-product-schema"], "meta": [], "h1": [], "alt": [],
    "font": ["lighthouse-font"],
}


def cite(ids) -> list[dict]:
    """Known research entries for a list of ids; unknown ids (e.g. invented by a model) are dropped."""
    out, seen = [], set()
    for i in ids or []:
        i = str(i).strip().lower()
        if i in BY_ID and i not in seen:
            seen.add(i)
            out.append(BY_ID[i])
    return out


def prompt_block() -> str:
    return "\n".join(f"- {r['id']}: {r['finding']} ({r['source']})" for r in RESEARCH)
