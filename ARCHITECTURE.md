# ARCHITECTURE.md — Daily News Digest

Engineering reference for this codebase. Covers architecture, file responsibilities, configuration, schemas, pipeline logic, and operational conventions.

---

## Project Purpose

A single-recipient, zero-cost, fully automated daily news digest. Runs on GitHub Actions every morning, pulls RSS feeds across 10 categories (BC/West Coast first, then Canadian, then US, then World), ranks and deduplicates stories with geographic prioritization, detects trending topics, generates an HTML email, and delivers it via Resend.

No web server. No database. No UI. No user input at runtime. The pipeline runs once per day and exits.

---

## Pipeline Sequence

Do not reorder steps.

```
1. validate_feeds.py   →  Load feed state cache → try URL pool per category → autodiscover on failure → save updated state
2. main.py             →  Fetch → Deduplicate → Score → Rank → Detect Trends → Generate Email → Send
3. archive.py          →  Save digest to /digests/YYYY-MM-DD.html → Prune files > 90 days
```

`main.py` is the core. It calls internal modules in sequence. Each stage is a discrete function — not a class, not a framework.

---

## File Responsibilities

| File | Responsibility |
|---|---|
| `main.py` | Orchestrates the full pipeline. Entry point. |
| `validate_feeds.py` | Loads feed state, tries URL pool per category, runs autodiscovery on failure, saves updated state. Exits with code 1 only if no URL works for any category. |
| `feed_discovery.py` | Auto-discovers a working feed URL when all pool URLs for a category fail. Scrapes `site_root` for `<link rel="alternate">` tags, then tries common patterns. Stdlib only (`urllib`, `html.parser`). |
| `audit_feeds.py` | Weekly audit. Ignores cached state — retests all URLs fresh. Runs discovery for dead ones. Rebuilds `feed_state.json`. Outputs GitHub Actions step summary. |
| `archive.py` | Saves the generated HTML digest to `/digests/`, deletes files older than `ARCHIVE_RETENTION_DAYS`. |
| `config.py` | All tunable parameters. Single source of truth for weights, thresholds, and constants. |
| `rss_feeds.json` | Feed registry. All feed metadata lives here. Never hardcode a feed URL in Python. |
| `feed_state.json` | Runtime feed health state. Tracks URL status and discovered replacements. Never committed — persisted between runs via GitHub Actions cache. |
| `requirements.txt` | Pinned dependencies. Do not add libraries without a clear reason. |
| `.github/workflows/daily-news.yml` | Daily cron workflow. Includes cache restore/save steps around `validate_feeds.py`. |
| `.github/workflows/weekly-audit.yml` | Weekly audit workflow. Runs `audit_feeds.py` every Sunday to retest all feeds and refresh the state cache. |
| `digests/` | Rolling archive of sent HTML digests. Auto-managed by `archive.py`. Do not manually edit. |
| `tests/` | Unit tests for pure pipeline functions. Run with `python -m pytest tests/` or `python -m unittest discover tests`. |

---

## config.py — Parameters Reference

All tunable values live in `config.py`. When adjusting behavior, change config — not logic.

```python
# Scoring
RECENCY_WEIGHT = 0.6          # Weight for recency in composite score (0.0–1.0)
CREDIBILITY_WEIGHT = 0.4      # Weight for source credibility (must sum to 1.0 with RECENCY_WEIGHT)
RECENCY_DECAY_HOURS = 24      # Hours over which recency score decays from 1.0 to 0.0

# Deduplication
FUZZY_MATCH_THRESHOLD = 0.85  # difflib SequenceMatcher ratio. Range: 0.0–1.0.
                               # Lower = more aggressive dedup. 0.80–0.90 is the practical range.

# Ranking
MAX_STORIES_PER_CATEGORY = 5  # Hard cap on stories per category in the email

# Trend Detection
TREND_MIN_APPEARANCES = 3     # Minimum cross-story appearances to flag a keyword
TREND_TOP_N = 5               # Number of trending keywords to surface

# Archive
ARCHIVE_RETENTION_DAYS = 90   # Files older than this are deleted by archive.py

# Geographic priority multiplier applied to composite score after recency+credibility.
# 1.25 means a BC story scoring 0.80 base becomes 1.00 — ranks above a US story at 0.90.
REGION_PRIORITY = {
    "canada_west": 1.25,
    "canada":      1.15,
    "usa":         1.00,
    "world":       0.85,
}

# Timezone
CRON_UTC_OFFSET = 15          # Hour (UTC) the cron fires. 15 = 7 AM PST. 14 = 7 AM PDT.
```

---

## rss_feeds.json — Schema

Each feed entry must follow this schema exactly. Do not add undocumented fields.

```json
{
  "name": "CBC News",
  "category": "BC / West Coast",
  "urls": [
    "https://www.cbc.ca/cmlink/rss-canada-britishcolumbia",
    "https://globalnews.ca/bc/feed/"
  ],
  "site_root": "https://www.cbc.ca",
  "credibility_score": 5,
  "region": "canada_west",
  "active": true
}
```

**Field rules:**
- `name` is the display name of the outlet shown in the email story card (e.g., `"CBC News"`, `"Globe and Mail"`). Required.
- `category` must match exactly one of the 10 defined categories. Case-sensitive.
- `urls` is an ordered array of RSS feed URLs. Tried in sequence at runtime; first working URL wins. Never leave empty.
- `site_root` is the canonical homepage URL used by `feed_discovery.py` when all `urls` fail. Must be the actual HTML page where `<link rel="alternate">` tags appear — not a redirect or CDN URL.
- `credibility_score` is an integer 1–5. Assigned manually. See README for scoring guide.
- `region` controls geographic scoring priority. Valid values: `"canada_west"`, `"canada"`, `"usa"`, `"world"`. Required. Multipliers defined in `config.py` under `REGION_PRIORITY`.
- `active: false` disables the entry without deleting it. Use this to temporarily pause a feed.

**Important:** Each entry represents one source with one region. Do not combine sources from different regions (e.g., Canadian + US) in a single entry — create separate entries so each gets the correct region multiplier.

**Valid categories (exact strings):**
```
BC / West Coast, Technology, Finance, Economy, Business, Politics, World, Health, Sports, Entertainment
```

---

## Feed Resilience System

The pipeline uses a three-layer fallback to stay running without manual intervention when feed URLs change or go dead.

**Layer 1 — URL Pool (`rss_feeds.json`):** Each category has 3–5 ordered URLs. `validate_feeds.py` tries them in sequence; first working URL wins. Human-managed. Update when you want to swap sources.

**Layer 2 — Autodiscovery (`feed_discovery.py`):** If all pool URLs for a category fail, `feed_discovery.py` attempts to locate the current feed:
1. Fetch `site_root` with `urllib.request`
2. Parse for `<link rel="alternate" type="application/rss+xml">` using `html.parser`
3. If found, validate with `feedparser` (must return ≥ 1 entry)
4. If not found, try common path patterns against `site_root`: `/feed/`, `/rss/`, `/rss.xml`, `/feed.xml`, `/atom.xml`, `/?feed=rss2`
5. Return first URL that passes validation, or `None`

Uses Python stdlib only. No new dependencies.

**Layer 3 — State Cache (`feed_state.json`):** Discovered replacements and known-dead URLs are persisted between runs via GitHub Actions cache (key: `feed-state-v1`). This means the pipeline doesn't re-test dead URLs or re-run discovery on every daily run.

`feed_state.json` schema:
```json
{
  "last_updated": "2026-03-31T15:00:00Z",
  "urls": {
    "https://techcrunch.com/feed/": {
      "status": "healthy",
      "last_checked": "2026-03-31T15:00:00Z"
    },
    "https://oldfeed.example.com/rss": {
      "status": "dead",
      "last_checked": "2026-03-30T15:00:00Z",
      "discovered_replacement": "https://newfeed.example.com/feed/"
    }
  }
}
```

Valid `status` values: `healthy`, `dead`.

**This file is never committed.** It lives only in the GitHub Actions cache. If the cache is evicted (7-day TTL), the next run pays the full discovery cost once and rebuilds it automatically — no pipeline failure, no human action required.

**Weekly audit (`audit_feeds.py`):** Runs every Sunday via `weekly-audit.yml`. Ignores cached state — retests all URLs fresh, re-runs discovery for any dead ones, writes a fresh `feed_state.json` back to cache. Outputs a GitHub Actions step summary showing which categories are healthy, which had dead URLs, and what was discovered.

---

## Scoring Logic

Every story receives a composite score used for ranking within its category and for cross-category Top 5 selection.

```python
# Recency score: linear decay from 1.0 (published now) to 0.0 (published 24+ hours ago)
hours_old = (now - published_date).total_seconds() / 3600
recency_score = max(0.0, 1.0 - (hours_old / RECENCY_DECAY_HOURS))

# Credibility score: normalize feed's credibility_score (1–5) to 0.0–1.0
credibility_score = (feed_credibility - 1) / 4

# Composite score
composite_score = (RECENCY_WEIGHT * recency_score) + (CREDIBILITY_WEIGHT * credibility_score)
```

**Geographic multiplier** applied after composite: `final_score = composite_score * REGION_PRIORITY[region]`

**Cross-category Top 5:** Normalize composite scores across the full corpus (min-max normalization) before selecting the global top 5. This prevents high-volume categories from dominating the overall section.

---

## Deduplication Logic

Two-pass deduplication. Run in this order.

**Pass 1 — Exact match:**
- Normalize title: lowercase, strip punctuation, collapse whitespace.
- Hash the normalized title with MD5.
- Deduplicate by hash. Keep the version with the higher composite score.

**Pass 2 — Fuzzy match:**
- For all remaining stories, compare title pairs using `difflib.SequenceMatcher`.
- If ratio ≥ `FUZZY_MATCH_THRESHOLD`, treat as duplicate. Keep higher-scored version.
- This is O(n²) — acceptable at the scale of a daily RSS digest (~100–300 stories total).

Do not add semantic similarity (sentence-transformers) to v1. Documented as a v2 upgrade in README.

---

## Geographic Override Logic

After fetch, stories tagged `usa` or `world` are eligible for region re-tagging if Canadian/BC signals are detected in the title or summary. This catches cross-border stories filed under a US feed (e.g., AP News covering a BC wildfire).

Two-path design in `apply_geographic_overrides()`:

**PRIMARY — spaCy NER (when `en_core_web_sm` is installed):**
Extracts GPE tokens from the title. Only tokens classified as GPE are checked against signal sets — eliminates person-name false positives ("Victoria Beckham" → PERSON, not GPE).

**FALLBACK — signal matching (when spaCy unavailable or misses a rare city):**
Scans normalized title+summary using padded word-boundary matching. Strong signals fire directly. Weak signals require a `GEO_CONTEXT` word to confirm geographic usage (e.g., "victoria" alone is ambiguous; "victoria residents" is not).

BC is checked before CA — it is the more specific signal.

---

## Trend Detection Logic

Run after deduplication and before email generation.

1. Collect all story titles and summaries into a single corpus string.
2. Tokenize. Convert to lowercase. Remove English stopwords.
3. Count keyword frequency across distinct stories.
4. Flag any keyword appearing `TREND_MIN_APPEARANCES` or more times across distinct stories.
5. Sort by frequency descending. Take top `TREND_TOP_N`.
6. Pass keyword list to the HTML generator for the Emerging Signals section.

Uses Python's built-in `collections.Counter`. No NLTK or additional NLP dependencies.

---

## Email Generation Rules

- Use **inline CSS only**. No `<style>` blocks, no external stylesheets. Email clients strip non-inline styles.
- Max email width: **600px**. Mobile-first.
- No external images. No tracking pixels.
- Every story block must include: headline (linked), source name, RSS summary (truncated to 200 characters if longer), and a "Read more" link pointing to `story.link`.
- Section order in email: Emerging Signals → Top 5 (Canada) → Top 5 (USA) → Top 5 (World) → Canadian Categories → International Categories.
- Include a timestamp in the footer: `Delivered [HH:MM UTC] · [Date] · GitHub Actions`.

---

## Error Handling Conventions

- **Feed failures:** Log a warning. Try next URL in the pool. If all pool URLs fail, run `feed_discovery.py`. If discovery succeeds, log the discovered URL and use it for this run (persisted to state cache). If discovery also fails, log the category as unavailable and include a notice in the email. Never raise an unhandled exception for a single feed failure.
- **Scoring errors:** If `published_date` is missing or unparseable, default `recency_score` to 0.0. Log a warning. Do not skip the story.
- **Resend failures:** Raise the exception. Let GitHub Actions catch it and mark the run as failed. This triggers GitHub's built-in failure notification.
- **Archive failures:** Log a warning. Do not raise. A failed archive is not worth blocking the pipeline over.

Use Python's `logging` module throughout. Set log level to `INFO` by default. Do not use `print()` in production code paths.

---

## Secrets

Three environment variables are required at runtime. Injected by GitHub Actions from repository secrets. Export manually for local runs.

```bash
export RESEND_API_KEY=your_key
export RECIPIENT_EMAIL=your_email@example.com
export SENDER_EMAIL=sender@yourdomain.com
```

Never hardcode credentials. Never log credential values. Never add `.env` to the repository.

---

## Dependency Rules

Current dependencies are intentionally minimal:

```
feedparser       # RSS parsing
resend           # Email delivery
python-dateutil  # Robust date parsing
spacy            # NER for geographic override (en_core_web_sm model)
```

`difflib` and `collections` are Python standard library — no install needed.

**Before adding any new dependency, ask:**
1. Does Python's standard library already solve this?
2. Is this for v1 or a v2 feature? (If v2, document it in the README roadmap — do not add it now.)
3. Does it run cleanly inside a GitHub-hosted runner without system dependencies?

Do not add `numpy`, `pandas`, `nltk`, `transformers`, or any ML library to v1.

---

## Testing

Unit tests live in `tests/test_pipeline.py`. Run with:

```bash
python -m unittest discover tests
```

Tests cover pure pipeline functions: title normalization, exact and fuzzy deduplication, story scoring, and trend detection. Functions with external I/O (feed fetching, email sending) are not unit-tested — the `workflow_dispatch` trigger in GitHub Actions serves as the integration test.

If the test suite grows to warrant it, add `pytest`. Do not add it preemptively.

---

## What Not to Do

- Do not use classes where functions are sufficient. The pipeline is linear — keep it that way.
- Do not add a database. Feed health state is persisted via GitHub Actions cache only.
- Do not add a web server, API layer, or UI. This runs headless on a cron.
- Do not add logging that outputs sensitive data (email addresses, API keys, full story content).
- Do not modify files in `/digests/` manually. They are auto-managed by `archive.py`.
- Do not change the pipeline execution order in `main.py` without updating this file.
- Do not hardcode feed URLs anywhere outside `rss_feeds.json`.
- Do not change `config.py` values without documenting the reason in a commit message.

---

## Adding a New Feed Category

1. Add a new entry to `rss_feeds.json` with a valid `category` string.
2. Run `validate_feeds.py` to confirm the new feed returns content.
3. Update the valid categories list in this file.
4. Update the email section order if the new category has a defined priority.
5. Test a full manual run before pushing.

---

## Adding a New Feed to an Existing Category

1. Add the new URL to the `urls` array in `rss_feeds.json` for that category. Position it by priority (first = most preferred).
2. Confirm `site_root` is set to the correct homepage for autodiscovery.
3. Run `validate_feeds.py` to confirm the URL returns parseable content.
4. Assign or confirm a `credibility_score` using the guide in README.md.
5. Test locally before pushing.

---

## Commit Message Convention

```
feat: add fuzzy deduplication pass
fix: handle missing published_date in scorer
config: lower fuzzy threshold to 0.80
feeds: replace Reuters backup with AP News
docs: update ARCHITECTURE.md with new config param
```

Use lowercase. Be specific. Reference the file or module affected.

---

## Engineering Notes

Decisions and observations recorded during development. Add an entry when something fails, a workaround is found, or an approach is confirmed solid.

---

### Confirmed Approaches

| Date | Area | What | Why it worked |
|------|------|------|---------------|
| 2026-03-31 | Pipeline smoke test | `python main.py` end-to-end | 201 stories fetched, 9 categories ranked, 45 KB HTML generated, digest saved — validated every stage in one run |
| 2026-03-31 | Feed resilience | 3-layer fallback (pool → autodiscovery → state cache) | Dead AP News and Reuters URLs fell through to backups automatically with zero intervention |
| 2026-03-31 | Email delivery without domain | `SENDER_EMAIL=onboarding@resend.dev` | Resend's shared test address bypasses SPF/DKIM — pipeline runs end-to-end, emails land in spam but functional for testing |

---

### Do Not Repeat

| Date | Area | What was tried | What to do instead |
|------|------|----------------|--------------------|
| 2026-03-31 | `main()` orchestration | Called `load_active_feeds()` directly | Always call `vf.validate_all_feeds()` — `load_active_feeds()` does not set `active_url`, which `fetch_stories()` requires |
| 2026-03-31 | Intermediate smoke tests | Per-function temp scripts | `python main.py` catches everything — intermediate scripts are redundant overhead |
| 2026-03-31 | Lint cadence | Running linter after every single file | Run once per logical task group — same errors caught, fewer round-trips |

---

*Keep this file current. If you change the architecture, scoring logic, schema, or pipeline order — update ARCHITECTURE.md before closing the PR.*
