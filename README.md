# PDP CRO Auditor (with competitor benchmarking)

Give it D2C product page URLs or full-page screenshots. For each page it:

1. **Studies the page** on mobile (390px) and desktop (1440px)
2. **Finds 2-3 top competitors** in the same category (a protein page is benchmarked against brands like The Whole Truth,
   SuperYou, MuscleBlaze), locates their most comparable product page, and scores them with the same rubric
3. **Scores CRO** with a fixed, research-backed rubric (overall, mobile and desktop) plus performance
4. **Writes a report**: what's wrong, what's working, how the page compares with each competitor, and prioritised
   changes and add-ons, each tied to published CRO research
5. **Builds the optimised page** as HTML with the changes in place, in the brand's own colours, fonts, sizes and button style

Built for batches of 10+ pages, and for low cost (about $0.07 per URL page, half that in batch mode; see [Cost](#cost)).

```
input (URL or screenshot)
  ├─ URL ─────► headless Chrome, desktop + mobile ──► DOM facts, brand tokens, lab perf (+ PageSpeed Insights)
  └─ screenshot ► pixel palette + Claude vision ──► facts, brand tokens, page sections
        │
        ▼
  competitor discovery (Haiku, or the seed list) ─► find each competitor's comparable product page (Shopify search /
  products.json / sitemap) ─► capture + score with the same rubric (cached, shared across the batch)
        │
        ▼
  rubric score (code) + gap table vs competitors (code) ─► Claude audit + patch plan (one call, cached prompt)
        │
        ▼
  BRAND GUARD + CLAIM GUARD (code) ─► optimised.html ─► report.html, batch dashboard, summary.csv
```

## Setup

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env      # add ANTHROPIC_API_KEY (and optionally PSI_API_KEY)
```

## Run with the web UI (easiest)

```bash
python app.py
```

Your browser opens `http://127.0.0.1:8787`. Paste URLs (one per line) and/or drop screenshots, optionally list competitors,
click **Run audit**. Each page moves through Loading page > Scoring > Benchmarking competitors > Claude analysing > Building
mockup. Open each report or optimised page from the table, or **Download HTML** to save the optimised page as one file;
**Download all (.zip)** saves every report and page of the run. The key is checked live when you save it. **Economy mode** sends
the audits through the Batch API at half price (results usually within minutes). A mobile and a desktop screenshot of the
same page are paired automatically when their file names differ only by "mobile" / "desktop".

## Run from the terminal

```bash
python run.py urls.txt                          # one input per line
python run.py https://brand.com/products/x      # single page
python run.py screenshots/                      # every .png/.jpg/.webp in a folder
python run.py urls.txt --batch                  # 50% cheaper via the Message Batches API
python run.py urls.txt --competitors thewholetruthfoods.com,superyou.in   # same competitors for every page
```

Line format (label and competitors optional):

```
https://brand-one.com/products/whey-protein | Brand One
https://brand-two.in/products/face-serum | Brand Two | vs: beminimalist.co, https://foxtale.in/products/x
./shots/brand-three-mobile.png + ./shots/brand-three-desktop.png | Brand Three
```

Competitors can be domains (the tool finds the most comparable product) or exact product URLs.

Flags: `-c` pages in parallel (default 2; raise it on a fast machine), `--model` audit model (default `claude-sonnet-5-5`), `--fast-model` discovery
model (default `claude-haiku-5-5`), `--effort low|medium|high` (default low), `--batch`, `-n` competitors per page (default
3, `0` = off), `--no-llm` rules-only mode (no key, URL inputs only), `--no-psi`, `--force` redo finished pages, `--dry-run`
write the Claude requests to `out/_dry_run/` without calling the API.

## Output

```
out/
  index.html            batch dashboard: CRO, mobile, desktop, vs competitors, cost per page
  summary.csv           one row per page incl. category, competitors, rank, top 3 issues (drop into an outreach sheet)
  <page>/
    report.html         the audit (open this)
    optimized.html      page with changes, one self-contained file (send it as is); "Show changes" outlines every
                        change; add ?clean to hide the toggle
    original.html       static snapshot of the live page (URL mode)
    report.json         everything, machine readable
    shots/              before/after renders, mobile full page and desktop
    competitors/        competitor screenshots used in the report
cache/                  competitor captures (7 days) and page profiles (30 days), shared by every run
```

## Competitive analysis

1. **Who.** One small, text-only call to Claude Haiku (about $0.001) names the category and 5 candidate competitors that sell
   a directly comparable product D2C in the same market, using `cro/data/competitors.json` (a seed list of leading Indian and
   US D2C brands per category) as hints. Without an API key the seed list is matched by keywords instead. You can also name
   competitors per page or for the whole batch.
2. **Which product.** For each competitor the tool finds the most comparable product page itself, with no AI: Shopify
   predictive search, then `products.json`, then the sitemap. Combos and kits lose to the single product. Domains that don't
   resolve are skipped and the next candidate is tried, until 3 are captured.
3. **Same measurement.** Each competitor page is loaded on desktop and mobile and scored with the same 35-check rubric, plus
   lab performance. Captures are cached for 7 days and shared, so ten protein pages reuse the same competitor captures.
4. **Comparison (code).** Check-by-check matrix, where you trail (a competitor passes, you don't), table stakes (every
   competitor does it), where you lead, a side-by-side fact table (price, rating, images, video, sticky CTA, trust cues,
   offers, delivery, FAQ, page weight), and your rank.
5. **Insight (Claude).** The audit call sees the competitors' first mobile screens and the gap table, and writes what each
   competitor does differently and the takeaway, without copying their copy, claims or design.

## How brand identity is protected

The model is told the rules, but the rules are **enforced in code** (`cro/guard.py`), because a prompt is a request, not a guarantee.

| What | How |
|---|---|
| Add-ons | Claude picks from a library of components (trust strip, delivery check, sticky add-to-cart, rating summary, FAQ, benefits, key facts, how-to-use, offers, guarantee...) and only fills the copy. The HTML and CSS come from code and use only the brand's CSS variables. |
| Colours | Read from computed styles (URL) or exact pixels (screenshot). Any colour in a patch is snapped to the nearest brand colour (CIELAB distance). |
| Fonts | Only the brand's own families or `--cro-font-*` variables. Anything else becomes the brand body font. `@import`, `@font-face` and the `font` shorthand are removed. |
| Font sizes | Snapped to the brand's measured type scale. |
| Button style | Radius, text-transform, letter-spacing, weight and height copied from the brand's own add-to-cart button. |
| Logo, header, nav, footer | Marked protected; any patch that targets them is rejected. |
| Existing elements | Patch CSS may only target new `.cro-*` classes. An existing element can only receive layout changes (size, spacing, display, order), never colour or type. |
| Imagery | Only images already on the page. Stock or external images are removed. Screenshot mockups reuse the original pixels. |
| Claims | Any number in new copy that is not on the original page (ratings, review counts, "10,000+ customers", "98% saw results") is shown as a dashed placeholder for the brand to confirm. Star ratings fill only to a rating the page states. Review quotes must appear verbatim on the page. Invented urgency ("only 3 left", timers) drops the patch: false urgency is a listed dark pattern under India's CCPA Guidelines for Prevention and Regulation of Dark Patterns, 2023. |

Every correction is logged in the report under "Brand and claim guard log".

## How scoring works

**CRO score**: 35 weighted checks in 8 groups, measured from the rendered page on both viewports. Same page, same score, every
run. Claude writes the narrative and the patch plan but never sets the number. Checks the page cannot answer (e.g. schema in
screenshot mode) are n/a and excluded; the report shows coverage. The **mobile score** uses mobile and shared checks, the
**desktop score** desktop and shared checks.

| Group | Weight | Examples |
|---|---|---|
| First screen (mobile) | 22 | title, price, add-to-cart and image visible without scrolling; no entry popup |
| First screen (desktop) | 10 | name, price and CTA above the fold; buy box beside the gallery; no popup |
| Add to cart | 15 | CTA present, WCAG contrast, 44px tap target, sticky CTA on mobile |
| Trust & social proof | 20 | rating near title, reviews section, trust cues near CTA, payment options |
| Purchase friction | 15 | delivery estimate / pincode check, shipping cost, returns near CTA, variant UX |
| Content & merchandising | 15 | 6+ images, video, benefit bullets, FAQ, cross-sell |
| Offer clarity | 7 | saving stated next to MRP, offers near price, India's mandatory declarations (origin, net qty, maker, MRP incl. taxes) |
| Technical & SEO | 8 | Product + rating schema, meta description, single H1, alt text, mobile body size |

**Research.** Every check and recommendation cites the research behind it (`cro/research.py`): Nielsen Norman Group (fold
attention, scannable copy), Baymard Institute (cart abandonment reasons, product page UX), Spiegel Research Center (reviews),
Google/SOASTA and Deloitte (speed vs bounce and conversion), Core Web Vitals, WCAG 2.2 (contrast, tap targets), Fitts's law,
Iyengar & Lepper (choice overload), anchoring, Wyzowl (video), Google Search Central (interstitials, product schema), and
India's CCPA dark-pattern guidelines and Legal Metrology rules. The report lists the sources used.

**Performance**: Google PageSpeed Insights (Lighthouse mobile score plus real-user CrUX data when Google has it). Set
`PSI_API_KEY` for reliable batches. If PSI is unavailable the report falls back to a lab budget score from our own mobile
load. Competitors get the lab score.

## Cost

Claude is the only paid part; capture, scoring, competitor discovery of product pages and comparison are code.

| Lever | What it saves |
|---|---|
| Model routing | Competitor discovery runs on Claude Haiku 5.5 ($0.10 / $0.50 per million tokens), text only. Only the audit uses Sonnet. |
| Low effort | Thinking tokens are billed as output (the most expensive tokens); the audit runs at `--effort low` by default. |
| Components, not HTML | Claude fills a component's copy slots instead of writing markup, so output stays short. |
| Prompt caching | One frozen system prompt (rubric rules, research library, component catalogue) is cached across every page. |
| Small images | Screenshots go to the model at 1x CSS resolution, sliced; competitors as one first-screen image each. |
| Screenshot mockups | Built from the screenshot's own pixels plus components: no second "rebuild the page" call. |
| Batch API | `--batch` / Economy mode: 50% off the audit calls. |
| Caches | Competitor captures and page profiles are reused across pages and runs; finished pages are not re-run. |

Measured on live pages (Wellbeing Nutrition, SuperYou, Foxtale, Fableroom) with Sonnet 5.5 at low effort:

| | Claude cost per page | 10 pages |
|---|---|---|
| URL page | $0.067-0.089 (competitor discovery on Haiku: under $0.001 of that) | about $0.75 |
| Screenshot page (extract + audit) | about $0.12 | about $1.20 |
| `--batch` / Economy mode | half of the above | about $0.40 (URLs) |

Most of the cost is the audit's ~3.5k output tokens (the written report and patch plan). The shared system prompt is read
from cache by every page after the first. Every report and the dashboard show the exact cost. `--model claude-haiku-5-5`
cuts the cost by roughly 15-20x more, at some loss of audit quality.

## URL vs screenshot

| | URL | Screenshot |
|---|---|---|
| CRO facts | measured from the DOM | read by Claude vision (marked, lower certainty) |
| Mobile + desktop | both, always | whichever views you upload (pair a mobile and a desktop shot for both) |
| Performance | yes | not possible from pixels, shown as n/a |
| Brand colours | exact (computed styles) | exact (pixel sampling) |
| Fonts / sizes | exact | closest Google Font + estimated sizes |
| Optimised page | the live page, frozen, with surgical changes injected | the screenshot cut into sections at blank rows, with brand-styled blocks inserted; desktop two-column areas are split so buy-box additions land in the buy box |

Use URLs whenever the page is public. Screenshots are for gated pages, bot-protected sites or design files.

## Scale

- Browser work and Claude calls have separate concurrency limits; Claude calls retry with backoff on rate limits.
- Each page is isolated. Errors are written to `<page>/error.txt` and shown in the index; the rest of the batch completes.
- A page that times out while loading is retried once with a longer timeout.
- A competitor that fails to load is skipped and the next candidate is used; it never fails the audit.
- Finished pages are cached; re-running the same input file only redoes pages that failed (use `--force` to redo all).

## Tests

```bash
cd tests/fixtures && python -m http.server 8765        # two local product pages (keep running)
python tests/test_brand_guard.py out_test              # fake model breaks every rule; guard must catch all (30 checks)
python tests/test_screenshot_mode.py out_test/<page>/shots/before_mobile.png out_test/<page>/shots/before_desktop.png
python run.py tests/urls.example.txt --no-llm --no-psi -o out_rules   # rules-only mode, live competitor discovery
```

## Known limits

- The optimised page is a **mockup**: scripts are stripped, so carousels, variant switching and add-to-cart do not work.
  Shipping the changes means theme edits (e.g. Shopify Liquid sections), which this does not do.
- Sites behind bot protection (Cloudflare challenge, captcha) fail with a clear error, for the page and for competitors. The
  tool identifies itself (`CROAudit/1.0` in the user agent) and does not try to get around them; use a screenshot.
- Competitor product matching is keyword based; for niche products name the competitor URLs yourself (`vs:`).
- Heuristic detection (ratings, trust cues, delivery info) is English-language regex plus DOM structure.
- The rubric weights are a reasoned starting point, not fitted to conversion data. Calibrate them against real A/B results.

## Layout

```
run.py                CLI
app.py                local web UI (stdlib server, no extra deps)
ui/index.html         the UI page
cro/capture.py        Playwright capture, popup removal, frozen snapshot
cro/js.py             in-page scripts: facts, brand tokens, node tagging, perf observers
cro/rules.py          CRO rubric (mobile, desktop, shared checks)
cro/research.py       research library cited by checks and recommendations
cro/competitors.py    competitor discovery, product matching, capture cache, comparison
cro/data/competitors.json   seed list of leading D2C brands per category (edit freely)
cro/components.py     brand-styled add-on components
cro/tokens.py         brand tokens -> CSS variables
cro/perf.py           PageSpeed Insights + lab budget score
cro/llm.py            Claude prompts, schemas, caching, batch queue, cost tracking
cro/guard.py          brand guard + claim guard
cro/patcher.py        patch operations on the live page + rules-based fallback plan
cro/composite.py      screenshot-mode mockup builder
cro/report.py         report, index, CSV
cro/pipeline.py       batch orchestration
```
