"""Browser-side scripts. Each is a JS function expression passed to page.evaluate()."""

# Installed before any page script runs: collects LCP / CLS / long tasks.
PERF_INIT = r"""
(() => {
  window.__croPerf = {lcp: 0, lcpEl: null, cls: 0, tbt: 0, fcp: 0};
  const P = window.__croPerf;
  try {
    new PerformanceObserver(l => {
      for (const e of l.getEntries()) {
        P.lcp = e.renderTime || e.loadTime || e.startTime;
        const el = e.element;
        if (el) P.lcpEl = {tag: el.tagName.toLowerCase(), src: (el.currentSrc || el.src || '').slice(0, 300),
                           loading: el.getAttribute && el.getAttribute('loading'),
                           fetchpriority: el.getAttribute && el.getAttribute('fetchpriority')};
      }
    }).observe({type: 'largest-contentful-paint', buffered: true});
  } catch (e) {}
  try {
    new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) P.cls += e.value; })
      .observe({type: 'layout-shift', buffered: true});
  } catch (e) {}
  try {
    new PerformanceObserver(l => { for (const e of l.getEntries()) P.tbt += Math.max(0, e.duration - 50); })
      .observe({type: 'longtask', buffered: true});
  } catch (e) {}
})();
"""

READ_PERF = r"""
() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const fcp = (performance.getEntriesByName('first-contentful-paint')[0] || {}).startTime || 0;
  const res = performance.getEntriesByType('resource').map(r => ({
    url: r.name.slice(0, 400), type: r.initiatorType,
    bytes: Math.max(r.transferSize || 0, r.encodedBodySize || 0)
  }));
  const head = document.head;
  const blockingCss = head ? head.querySelectorAll('link[rel="stylesheet"]:not([media="print"])').length : 0;
  const blockingJs = head ? [...head.querySelectorAll('script[src]')].filter(s => !s.async && !s.defer && s.type !== 'module').length : 0;
  return {
    ttfb: nav.responseStart || 0, dcl: nav.domContentLoadedEventEnd || 0, load: nav.loadEventEnd || 0,
    navBytes: Math.max(nav.transferSize || 0, nav.encodedBodySize || 0), fcp,
    lcp: (window.__croPerf || {}).lcp || 0, lcpEl: (window.__croPerf || {}).lcpEl || null,
    cls: (window.__croPerf || {}).cls || 0, tbt: (window.__croPerf || {}).tbt || 0,
    resources: res, blockingCss, blockingJs,
    domNodes: document.getElementsByTagName('*').length
  };
}
"""

# Removes entry popups / cookie walls / overlays that block the first view.
REMOVE_POPUPS = r"""
() => {
  const vw = innerWidth, vh = innerHeight, removed = [];
  const words = /(subscribe|newsletter|sign ?up|discount|% ?off|offer|welcome|join|unlock|spin|win|email|whatsapp|first order|exclusive)/i;
  const cookie = /(cookie|consent|gdpr|privacy preferences)/i;
  const visibleArea = (r) => {
    const w = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
    const h = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
    return w * h;
  };
  for (const el of [...document.querySelectorAll('body *')]) {
    if (!el.isConnected) continue;
    if (el.closest('header, [data-cro-keep]')) continue;
    const s = getComputedStyle(el);
    // Overlays are position:fixed. Sticky elements are in-page layout (a sticky desktop buy box with "70% off" in it
    // is the product, not a popup), and nothing that holds the product title is ever an entry popup.
    if (s.position !== 'fixed') continue;
    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) < 0.05) continue;
    if (el.querySelector('h1')) continue;
    const r = el.getBoundingClientRect();
    const area = visibleArea(r);
    const t = (el.innerText || '').slice(0, 2000);
    const z = parseInt(s.zIndex) || 0;
    const isDialog = el.matches('[role=dialog],[aria-modal=true],dialog') || !!el.querySelector('[role=dialog],[aria-modal=true]');
    const big = area >= vw * vh * 0.25;
    const cookieBar = cookie.test(t) && area > 0 && t.length < 1500;
    const ctaRe = /(add\s*to\s*(cart|bag|basket)|buy\s*(it\s*)?now)/i;
    if (ctaRe.test(t) && !big) continue;  // keep the brand's own sticky ATC bar
    if ((big && (isDialog || (z >= 10 && words.test(t)) || (z >= 100 && area >= vw * vh * 0.8))) || cookieBar) {
      removed.push({tag: el.tagName.toLowerCase(), cls: String(el.className || '').slice(0, 80),
                    text: t.replace(/\s+/g, ' ').slice(0, 140), kind: cookieBar ? 'cookie' : 'popup'});
      el.remove();
    }
  }
  for (const n of [document.documentElement, document.body]) {
    n.style.overflow = ''; n.style.position = '';
    for (const c of ['overflow-hidden', 'no-scroll', 'modal-open', 'overflow-hidden-mobile', 'overflow-hidden-tablet', 'lock-scroll'])
      n.classList.remove(c);
  }
  return removed;
}
"""

# Main extraction: facts for the CRO rubric + element refs kept on window.__croEls for tagging.
FACTS = r"""
() => {
  const vw = innerWidth, vh = innerHeight;
  const isVis = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return !(s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) < 0.05);
  };
  const rect = (el) => { const r = el.getBoundingClientRect();
    return {top: Math.round(r.top + scrollY), left: Math.round(r.left + scrollX), width: Math.round(r.width), height: Math.round(r.height)}; };
  const txt = (el) => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  const inChrome = (el) => !!el.closest('header, nav, footer, [role=navigation], [class*="announcement"]');
  const els = {};
  const bodyText = txt(document.body);

  // ---- title
  let title = [...document.querySelectorAll('h1')].find(h => isVis(h) && !inChrome(h));
  if (!title) title = [...document.querySelectorAll('[class*="product"][class*="title" i], [class*="product-name" i]')].find(isVis);
  const h1Count = [...document.querySelectorAll('h1')].filter(isVis).length;
  if (title) els.title = title;

  // ---- primary CTA
  const CTA = /(add\s*to\s*(cart|bag|basket|trolley)|buy\s*(it\s*)?now|order\s*now|pre-?\s*order|add\s*to\s*order)/i;
  const ADD = /add\s*to\s*(cart|bag|basket|trolley)|pre-?\s*order/i;
  const cands = [...document.querySelectorAll('button, a, input[type=submit], input[type=button], [role=button]')]
    .filter(isVis)
    .map(el => ({el, label: String(txt(el) || el.value || el.getAttribute('aria-label') || '').slice(0, 80)}))
    .filter(c => CTA.test(c.label) && c.label.length < 60 && !inChrome(c.el));
  const fixedAncestor = (el) => { for (let n = el; n && n !== document.body; n = n.parentElement) {
      const p = getComputedStyle(n).position; if (p === 'fixed' || p === 'sticky') return true; } return false; };
  let atc = null, best = -1e9;
  for (const c of cands) {
    let s = 0;
    if (ADD.test(c.label)) s += 3;
    if (c.el.closest('form[action*="cart"], [class*="product-form" i], [class*="add-to-cart" i]')) s += 3;
    if (fixedAncestor(c.el)) s -= 4;
    s -= rect(c.el).top / 1500;
    if (s > best) { best = s; atc = c; }
  }
  const buyNow = cands.find(c => /buy\s*(it\s*)?now/i.test(c.label) && (!atc || c.el !== atc.el));
  if (atc) els.atc = atc.el;
  if (buyNow) els.buy_now = buyNow.el;

  // ---- product region
  const commonAncestor = (a, b) => { if (!a) return b; if (!b) return a;
    const set = new Set(); for (let n = a; n; n = n.parentElement) set.add(n);
    for (let n = b; n; n = n.parentElement) if (set.has(n)) return n; return document.body; };
  let region = commonAncestor(title, atc && atc.el) || document.querySelector('main') || document.body;
  if (region === document.body && atc) region = atc.el.closest('form') ? atc.el.closest('form').parentElement : region;
  els.product_info = region;
  let galleryRegion = region;
  const bigImg = (root) => [...root.querySelectorAll('img')].some(i => i.getBoundingClientRect().width >= 250);
  for (let i = 0; i < 6 && galleryRegion && galleryRegion !== document.body && !bigImg(galleryRegion); i++) galleryRegion = galleryRegion.parentElement;
  if (!galleryRegion) galleryRegion = document.body;
  const regionText = txt(region).slice(0, 6000);
  const pageMainText = txt(document.querySelector('main') || document.body).slice(0, 30000);

  // ---- price
  const CUR = /(₹|rs\.?\s?|inr\s?|\$|€|£|usd\s?|aud\s?|sgd\s?|rm\s?|aed\s?)\s?\d[\d,]*(\.\d+)?|\d[\d,]*(\.\d+)?\s?(₹|inr|usd|aud|sgd|aed)/i;
  const priceCands = [...region.querySelectorAll('*')].concat(region === document.body ? [] : [...galleryRegion.querySelectorAll('*')])
    .filter(el => el.children.length <= 3 && isVis(el) && !inChrome(el))
    .filter(el => { const t = txt(el); return t.length > 0 && t.length < 50 && CUR.test(t); });
  const struck = (el) => { for (let n = el, i = 0; n && i < 3; n = n.parentElement, i++) {
      if (/^(s|del|strike)$/i.test(n.tagName)) return true;
      if ((getComputedStyle(n).textDecorationLine || '').includes('line-through')) return true;
      if (/compare|was-price|regular|mrp|old/i.test(String(n.className))) return true; } return false; };
  let price = null, compare = null;
  const titleTop = title ? rect(title).top : 0;
  const sorted = priceCands.sort((a, b) => Math.abs(rect(a).top - titleTop) - Math.abs(rect(b).top - titleTop));
  for (const el of sorted) {
    if (struck(el)) { if (!compare) compare = el; } else if (!price) price = el;
    if (price && compare) break;
  }
  // prefer the innermost element for price
  if (price) { const inner = [...price.querySelectorAll('*')].find(c => CUR.test(txt(c)) && !struck(c)); if (inner) price = inner; els.price = price; }
  if (compare) els.compare_price = compare;

  // ---- images / video
  const norm = (u) => (u || '').split('?')[0].replace(/_(\d+x\d*|\d*x\d+|small|medium|large|grande|compact|thumb)(?=\.)/i, '').replace(/^https?:/, '');
  const imgs = [...galleryRegion.querySelectorAll('img')].filter(i => {
    const r = i.getBoundingClientRect(); return (r.width >= 60 || i.naturalWidth >= 300) && !inChrome(i); });
  const imgSet = new Set(imgs.map(i => norm(i.currentSrc || i.src || i.getAttribute('data-src'))).filter(Boolean));
  els.gallery = galleryRegion !== region ? galleryRegion : null;
  const hasVideo = !!galleryRegion.querySelector('video, iframe[src*="youtube"], iframe[src*="vimeo"], [class*="video" i]')
     || !!document.querySelector('main video');

  // ---- variants
  const variantEls = [...region.querySelectorAll('select, input[type=radio], [class*="swatch" i], [class*="variant" i] button, [class*="option" i] button')].filter(el => isVis(el) || el.type === 'radio');
  const variantSelects = variantEls.filter(el => el.tagName === 'SELECT' && !/quantity|qty/i.test(el.name || '')).length;
  const variantPicker = region.querySelector('variant-radios, variant-selects, fieldset')
    || [...region.querySelectorAll('select')].find(s => !/quantity|qty/i.test(s.name || ''))
    || [...region.querySelectorAll('[class*="variant" i], [class*="swatch" i]')].find(el => el.querySelector('input, select, button'));
  if (variantPicker && isVis(variantPicker)) els.variants = variantPicker;

  // ---- reviews
  const RATING = /(\b[0-5](\.\d)?)\s*(\/\s*5|out of 5|stars?|★)|★{3,}|\b[0-5]\.\d\s*\(\s*\d/i;
  const COUNT = /(\d[\d,]*)\s*(\+\s*)?(reviews?|ratings?|verified)/i;
  const WIDGET = '[class*="jdgm" i], [class*="yotpo" i], [class*="loox" i], [class*="okendo" i], [class*="stamped" i], [class*="rating" i], [class*="review" i], [class*="stars" i]';
  let ratingEl = null;
  if (title) {
    const near = [...region.querySelectorAll(WIDGET + ', span, div, a')].filter(el => isVis(el) && el.children.length < 12)
      .filter(el => Math.abs(rect(el).top - titleTop) < 260 && (RATING.test(txt(el)) || COUNT.test(txt(el)) || el.matches(WIDGET) && el.querySelector('svg, i, img')));
    ratingEl = near.sort((a, b) => txt(a).length - txt(b).length)[0] || null;
  }
  if (ratingEl) els.rating = ratingEl;
  const reviewsSection = /(customer reviews|ratings? (&|and) reviews|write a review|reviews? \(\d|\d+\s+reviews)/i.test(pageMainText)
     || !!document.querySelector('[class*="jdgm-widget" i], [class*="yotpo-main" i], [id*="review" i] [class*="review" i]');
  const revSec = [...document.querySelectorAll('[id*="review" i], [class*="reviews" i], section')]
     .find(el => isVis(el) && /reviews?/i.test(txt(el).slice(0, 200)) && el !== region && !region.contains(el));
  if (revSec) els.reviews = revSec;

  // ---- trust / friction
  const T = {
    secure: /secure (checkout|payments?)|100% secure|ssl|safe (checkout|payments?)/i,
    returns: /(easy|free|hassle[- ]free|\d+[- ]days?) (returns?|exchange)|return policy|returns? (within|in) \d+|no questions asked/i,
    authentic: /100% (authentic|genuine|original)|genuine products?|authenticity/i,
    warranty: /warranty|guarantee/i,
    free_shipping: /free (shipping|delivery)/i,
    cod: /cash on delivery|\bcod\b/i,
    quality: /dermatologi|clinically|certified|cruelty[- ]free|made in india|vegan|fssai|iso \d|lab[- ]tested|paraben[- ]free|non[- ]toxic|organic/i,
  };
  const trustNear = {}, trustPage = {};
  for (const [k, re] of Object.entries(T)) { trustNear[k] = re.test(regionText); trustPage[k] = re.test(bodyText.slice(0, 40000)); }
  const snipIn = (text, re) => { const m = text.match(new RegExp('[^.|·•]{0,40}(' + re.source + ')[^.|·•]{0,40}', 'i'));
    return m ? m[0].trim().slice(0, 70) : null; };
  const snip = (re) => snipIn(bodyText, re);
  const trustSnips = {free_shipping: snip(T.free_shipping), returns: snip(T.returns), cod: snip(T.cod)};
  const payRe = /\b(upi|gpay|google pay|phonepe|paytm|visa|mastercard|rupay|net ?banking|emi|cash on delivery|cod|apple pay|paypal|shop ?pay|klarna|afterpay|simpl|lazypay|snapmint)\b/i;
  const payImgs = [...region.querySelectorAll('img, svg')].some(i => payRe.test((i.getAttribute('alt') || '') + ' ' + (i.getAttribute('src') || '') + ' ' + (i.getAttribute('aria-label') || '')));
  const payments = payRe.test(regionText) || payImgs;
  const delivery = /deliver(y|ed|s)?\s*(by|in|within|on|date|estimate)|estimated delivery|dispatch(es|ed)? (in|within)|ships? (in|within|by|today)|pin\s?code|zip\s?code|check delivery/i.test(regionText)
     || !!region.querySelector('input[placeholder*="pin" i], input[name*="pin" i], input[placeholder*="zip" i]');
  const offers = /coupon|use code|bank offer|\boffers?\b|flat \d|extra \d+%|cashback|buy \d+ get/i.test(regionText);
  const discountTxt = (regionText.match(/(\d{1,2})\s?%\s?off|save\s?(₹|rs\.?)?\s?\d[\d,]*/i) || [null])[0];
  const sizeGuide = /size\s*(guide|chart)|fit guide/i.test(regionText);
  const urgency = (pageMainText.match(/only \d+ (left|remaining)|selling fast|hurry|limited stock|ends in \d|\d+ (people|others) (are )?(viewing|watching|bought)/i) || [null])[0];
  const faq = /\bfaqs?\b|frequently asked/i.test(pageMainText) || document.querySelectorAll('main details').length >= 3;
  const crossSell = /you may also like|frequently bought together|complete the (look|routine|set|ritual)|related products|recommended for you|pairs? well|customers also (bought|viewed)|goes well with|shop the look/i.test(pageMainText);
  const qty = !!region.querySelector('input[type=number], [class*="quantity" i]');

  // ---- description
  const descEl = [...document.querySelectorAll('[class*="description" i], [id*="description" i], [class*="product__info" i] .rte, .rte')]
     .find(el => isVis(el) && !inChrome(el) && txt(el).length > 40);
  if (descEl) els.description = descEl;
  let descText = descEl ? txt(descEl) : '';
  if (!descEl) {  // fallback: prose paragraphs inside the product info block
    const ps = [...region.querySelectorAll('p')].filter(p => isVis(p) && txt(p).length > 80);
    descText = ps.map(txt).join(' ');
    if (ps.length) els.description = ps[0].parentElement;
  }
  const bullets = [...region.querySelectorAll('li')].filter(li => isVis(li) && !inChrome(li) && txt(li).length > 3).length
     + (descEl && !region.contains(descEl) ? [...descEl.querySelectorAll('li')].length : 0);

  // ---- schema / meta
  const ld = [];
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try { const j = JSON.parse(s.textContent); (Array.isArray(j) ? j : (j['@graph'] || [j])).forEach(x => ld.push(x)); } catch (e) {}
  }
  const prod = ld.find(x => x && (x['@type'] === 'Product' || (Array.isArray(x['@type']) && x['@type'].includes('Product'))));
  const agg = prod && prod.aggregateRating ? {value: prod.aggregateRating.ratingValue, count: prod.aggregateRating.reviewCount || prod.aggregateRating.ratingCount} : null;
  let offer = prod && prod.offers ? (Array.isArray(prod.offers) ? prod.offers[0] : prod.offers) : null;
  const metaDesc = (document.querySelector('meta[name="description"]') || {}).content || '';

  // ---- typography / accessibility
  const visImgs = [...document.querySelectorAll('img')].filter(i => isVis(i) && i.getBoundingClientRect().width >= 60);
  const altOk = visImgs.filter(i => (i.getAttribute('alt') || '').trim().length > 0).length;
  const sizeCount = {};
  for (const el of document.querySelectorAll('main p, main li, p')) {
    if (!isVis(el) || inChrome(el)) continue;
    const t = txt(el).length; if (!t) continue;
    const fs = parseFloat(getComputedStyle(el).fontSize); sizeCount[fs] = (sizeCount[fs] || 0) + t;
  }
  const bodyFont = Number(Object.entries(sizeCount).sort((a, b) => b[1] - a[1])[0]?.[0] || 0);

  const styleOf = (el) => { if (!el) return null; const s = getComputedStyle(el);
    return {bg: s.backgroundColor, color: s.color, fontSize: s.fontSize, fontFamily: s.fontFamily, fontWeight: s.fontWeight,
            textTransform: s.textTransform, letterSpacing: s.letterSpacing, borderRadius: s.borderTopLeftRadius,
            border: s.borderTopWidth + ' ' + s.borderTopStyle + ' ' + s.borderTopColor, padding: s.padding}; };
  const firstImg = imgs.map(i => rect(i)).sort((a, b) => a.top - b.top)[0] || null;

  // ---- content summary (used for competitor comparison and India's Legal Metrology declarations)
  const priceTxt = price ? txt(price) : '';
  const currency = /₹|rs\.?\s?\d|inr/i.test(priceTxt) ? 'INR' : /\$/.test(priceTxt) ? 'USD' : /€/.test(priceTxt) ? 'EUR'
     : /£/.test(priceTxt) ? 'GBP' : /aed/i.test(priceTxt) ? 'AED' : '';
  const siteName = (document.querySelector('meta[property="og:site_name"]') || {}).content || '';
  const schemaBrand = prod && prod.brand ? (typeof prod.brand === 'string' ? prod.brand : prod.brand.name || '') : '';
  const crumbEl = document.querySelector('nav[aria-label*="breadcrumb" i], [class*="breadcrumb" i]');
  const mandatory = {
    origin: /country of origin|made in [a-z]+/i.test(bodyText),
    net_qty: /net\s*(qty|quantity|wt\.?|weight|content|vol(ume)?)\b/i.test(bodyText),
    maker: /(manufactured|marketed|packed|imported|mfd\.?|mkt\.?)\s*(by|&)/i.test(bodyText),
    tax_incl: /incl(usive|\.)?\s*(of\s*)?(all\s*)?taxes/i.test(bodyText),
  };
  const bulletTexts = [...region.querySelectorAll('li')].concat(descEl ? [...descEl.querySelectorAll('li')] : [])
     .filter(li => isVis(li) && !inChrome(li)).map(txt).filter(t => t.length > 3 && t.length < 160).slice(0, 6);
  const headings = [...document.querySelectorAll('main h2, main h3, h2, h3')].filter(h => isVis(h) && !inChrome(h))
     .map(txt).filter(t => t.length > 1 && t.length < 60);
  const countM = ((ratingEl ? txt(ratingEl) : '') + ' ' + regionText.slice(0, 600)).match(COUNT);
  const valM = (ratingEl ? txt(ratingEl) : '').match(/\b([0-5](\.\d)?)\b/);

  window.__croEls = els;
  return {
    viewport: {width: vw, height: vh},
    doc_height: document.documentElement.scrollHeight,
    page_title: document.title, meta_description_len: metaDesc.length, og_image: !!document.querySelector('meta[property="og:image"]'),
    title: title ? {text: txt(title).slice(0, 200), rect: rect(title)} : null,
    h1_count: h1Count,
    price: price ? {text: txt(price).slice(0, 40), rect: rect(price)} : null,
    compare_price: compare ? {text: txt(compare).slice(0, 40)} : null,
    discount_text: discountTxt,
    atc: atc ? {label: atc.label, rect: rect(atc.el), style: styleOf(atc.el), sticky_native: fixedAncestor(atc.el)} : null,
    buy_now: buyNow ? {label: buyNow.label, rect: rect(buyNow.el)} : null,
    cta_count: cands.length,
    first_image: firstImg,
    image_count: imgSet.size,
    has_video: hasVideo,
    variant_inputs: variantEls.length, variant_selects: variantSelects,
    size_guide: sizeGuide,
    quantity_selector: qty,
    rating_near_title: ratingEl ? txt(ratingEl).slice(0, 80) || 'stars' : null,
    reviews_section: reviewsSection,
    trust_near_atc: trustNear, trust_on_page: trustPage, trust_snippets: trustSnips,
    payments_near_atc: payments,
    delivery_info: delivery,
    offers_near_atc: offers,
    urgency_text: urgency,
    faq: faq, cross_sell: crossSell,
    description_len: descText.length, bullets: bullets,
    schema: {product: !!prod, rating: agg, offer: offer ? {price: offer.price, currency: offer.priceCurrency, availability: offer.availability} : null},
    alt_coverage: visImgs.length ? altOk / visImgs.length : 1,
    body_font_px: bodyFont,
    region_text: regionText.slice(0, 2500),
    page_text_sample: pageMainText.slice(0, 6000),
    currency, brand_name: (schemaBrand || siteName).slice(0, 60),
    breadcrumbs: crumbEl ? txt(crumbEl).slice(0, 160) : '',
    mandatory_info: mandatory,
    bullet_texts: bulletTexts,
    headings: [...new Set(headings)].slice(0, 16),
    rating_value: valM ? parseFloat(valM[1]) : (agg ? parseFloat(agg.value) || null : null),
    review_count: countM ? parseInt(countM[1].replace(/,/g, '')) : (agg ? parseInt(agg.count) || null : null),
    // snippets from the product block only, so nav / footer / announcement-bar text never leaks in
    offer_snippet: snipIn(regionText, /coupon|use code|bank offer|flat \d+|extra \d+%|cashback|buy \d+ get \d+/i),
    delivery_snippet: snipIn(regionText, /deliver(y|ed|s)?\s*(by|in|within|on)|dispatch(es|ed)? (in|within)|ships? (in|within|by|today)/i),
  };
}
"""

# For each key element: how many levels up its horizontal row goes (button rows, price + badge rows, flex/grid
# cells), and whether it sits in a floating bar (fixed overlay, or a short sticky bar; a sticky column is normal
# layout). A new block placed "after the add-to-cart" must go after the whole row, and never into a floating bar.
LAYOUT = r"""
() => {
  const els = window.__croEls || {};
  const vis = (c) => { const r = c.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const isRow = (p) => {
    const s = getComputedStyle(p);
    if (s.display.includes('grid')) return (s.gridTemplateColumns || '').trim().split(/\s+/).length > 1;
    if (!s.display.includes('flex') || s.flexDirection.startsWith('column')) return false;
    const kids = [...p.children].filter(vis);
    if (kids.length < 2) return false;
    const r0 = kids[0].getBoundingClientRect();
    return kids.some(c => { const r = c.getBoundingClientRect(); return r.left >= r0.right - 1 && r.top < r0.bottom; });
  };
  const floats = (el) => { for (let n = el; n && n !== document.body; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.position === 'fixed') return true;
      if (s.position === 'sticky' && n.getBoundingClientRect().height < innerHeight * 0.25) return true; }
    return false; };
  const out = {}, floating = [];
  for (const [k, el] of Object.entries(els)) {
    if (!el) continue;
    if (floats(el)) floating.push(k);
    if (k === 'product_info' || k === 'gallery') continue;
    let n = el, d = 0;
    while (d < 4 && n.parentElement && n.parentElement !== document.body && n.parentElement !== els.product_info
           && isRow(n.parentElement)) { n = n.parentElement; d++; }
    out[k] = d;
  }
  return {row_depth: out, floating};
}
"""

# Checks whether a CTA is reachable after scrolling past the main ATC (sticky / fixed add-to-cart bar).
STICKY_CHECK = r"""
() => {
  const CTA = /(add\s*to\s*(cart|bag|basket)|buy\s*(it\s*)?now)/i;
  for (const el of document.querySelectorAll('button, a, input[type=submit], [role=button]')) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > innerHeight) continue;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) < 0.05) continue;
    const label = String(el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
    if (!CTA.test(label)) continue;
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      const p = getComputedStyle(n).position;
      if (p === 'fixed' || p === 'sticky') return true;
    }
  }
  return false;
}
"""

# Brand tokens: colours weighted by painted area / text length, fonts, sizes, radii, logo.
TOKENS = r"""
() => {
  const isVis = (el) => { const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el); return !(s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) < 0.05); };
  const docW = document.documentElement.scrollWidth, docH = Math.min(document.documentElement.scrollHeight, 12000);
  const bg = {}, fg = {}, border = {}, families = {}, sizes = {};
  const all = [...document.querySelectorAll('body, body *')].slice(0, 6000);
  for (const el of all) {
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'PATH', 'IMG', 'VIDEO', 'IFRAME', 'PICTURE', 'SOURCE'].includes(el.tagName.toUpperCase())) continue;
    if (!isVis(el)) continue;
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    const top = r.top + scrollY;
    if (top > docH) continue;
    const area = Math.max(0, Math.min(r.width, docW)) * Math.max(0, Math.min(r.height, docH - top));
    if (s.backgroundColor && !s.backgroundColor.endsWith(', 0)') && s.backgroundColor !== 'transparent') bg[s.backgroundColor] = (bg[s.backgroundColor] || 0) + area;
    let own = 0; for (const n of el.childNodes) if (n.nodeType === 3) own += n.textContent.trim().length;
    if (own) {
      fg[s.color] = (fg[s.color] || 0) + own;
      const fam = s.fontFamily; families[fam] = (families[fam] || 0) + own;
      const fs = Math.round(parseFloat(s.fontSize) * 10) / 10; sizes[fs] = (sizes[fs] || 0) + own;
    }
    if (parseFloat(s.borderTopWidth) > 0 && s.borderTopStyle !== 'none' && !s.borderTopColor.endsWith(', 0)'))
      border[s.borderTopColor] = (border[s.borderTopColor] || 0) + (r.width + r.height);
  }
  const top = (o, n) => Object.entries(o).sort((a, b) => b[1] - a[1]).slice(0, n);
  const st = (el) => el ? getComputedStyle(el) : null;
  const pick = (sel) => [...document.querySelectorAll(sel)].find(isVis);
  const body = st(document.body), h1 = st(pick('h1')), h2 = st(pick('main h2, h2')), p = st(pick('main p, p'));
  const link = st([...document.querySelectorAll('main a, a')].find(a => isVis(a) && (a.innerText || '').trim().length > 2));
  const input = st(pick('input[type=text], input[type=email], input[type=number], select'));
  let pageBg = body.backgroundColor;
  if (!pageBg || pageBg.endsWith(', 0)')) pageBg = getComputedStyle(document.documentElement).backgroundColor;
  if (!pageBg || pageBg.endsWith(', 0)')) pageBg = 'rgb(255, 255, 255)';
  const logo = pick('header img[alt*="logo" i], header [class*="logo" i] img, header [class*="logo" i] svg, [class*="logo" i] img, header img, header svg');
  return {
    bg: top(bg, 12), fg: top(fg, 8), border: top(border, 6), families: top(families, 6), sizes: top(sizes, 14),
    page_bg: pageBg,
    body: p ? {color: p.color, family: p.fontFamily, size: p.fontSize, lineHeight: p.lineHeight} : {color: body.color, family: body.fontFamily, size: body.fontSize},
    h1: h1 ? {color: h1.color, family: h1.fontFamily, size: h1.fontSize, weight: h1.fontWeight, transform: h1.textTransform, spacing: h1.letterSpacing} : null,
    h2: h2 ? {color: h2.color, family: h2.fontFamily, size: h2.fontSize, weight: h2.fontWeight} : null,
    link: link ? {color: link.color} : null,
    input: input ? {radius: input.borderTopLeftRadius, border: input.borderTopColor} : null,
    logo: logo ? {tag: logo.tagName.toLowerCase(), src: (logo.currentSrc || logo.src || '').toString().slice(0, 400), alt: logo.getAttribute('alt') || ''} : null,
  };
}
"""

# Tags key elements + a coarse block outline so patches can target stable selectors.
TAG_NODES = r"""
() => {
  const els = window.__croEls || {};
  const isVis = (el) => { const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el); return !(s.display === 'none' || s.visibility === 'hidden'); };
  const txt = (el) => ((el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim();
  for (const [k, el] of Object.entries(els)) if (el && el.setAttribute) el.setAttribute('data-cro-key', k);
  for (const el of document.querySelectorAll('header, nav, footer')) el.setAttribute('data-cro-protected', '1');
  for (const el of document.querySelectorAll('[class*="logo" i], [id*="logo" i]')) el.setAttribute('data-cro-protected', '1');
  const sel = 'main section, main > *, main > * > *, [class*="section" i], [id^="shopify-section" i], details, form, [data-cro-key], header, footer, ' +
              '[data-cro-key="product_info"] > *, [data-cro-key="product_info"] > * > *';
  const seen = new Set(), nodes = [];
  for (const el of document.querySelectorAll(sel)) {
    if (seen.has(el) || !isVis(el)) continue;
    seen.add(el);
    const r = el.getBoundingClientRect();
    if (r.height < 16 && !el.hasAttribute('data-cro-key')) continue;
    nodes.push(el);
  }
  const keyed = nodes.filter(n => n.hasAttribute('data-cro-key'));
  const rest = nodes.filter(n => !n.hasAttribute('data-cro-key')).slice(0, 150 - keyed.length);
  const keep = new Set([...keyed, ...rest]);
  const ordered = [...document.querySelectorAll('[data-cro-key], ' + sel)].filter(n => keep.has(n));
  const out = []; let i = 0; const done = new Set();
  for (const el of ordered) {
    if (done.has(el)) continue; done.add(el);
    const id = 'n' + (++i);
    el.setAttribute('data-cro-node', id);
    const r = el.getBoundingClientRect();
    const prot = !!el.closest('[data-cro-protected]');
    let parent = el.parentElement; while (parent && !parent.hasAttribute('data-cro-node')) parent = parent.parentElement;
    out.push({id, key: el.getAttribute('data-cro-key') || '', tag: el.tagName.toLowerCase(),
              cls: String(el.className || '').split(/\s+/).slice(0, 2).join(' ').slice(0, 60),
              text: txt(el).slice(0, 140), top: Math.round(r.top + scrollY), height: Math.round(r.height),
              parent: parent ? parent.getAttribute('data-cro-node') : '', protected: prot});
  }
  return out;
}
"""
