# CLAUDE.md — Daily News Digest

This file gives Claude Code the context it needs to work effectively in this codebase. Read it before writing, editing, or debugging any file in this project.

---

## Project Purpose

This is a single-recipient, zero-cost, fully automated daily news digest. It runs on GitHub Actions every morning, pulls RSS feeds across 9 categories, ranks and deduplicates stories, detects trending topics, generates an HTML email, and delivers it via Resend.

There is no web server, no database, no UI, and no user input at runtime. The pipeline runs once per day and exits.

---

## Skill Bank
Path: /Users/khuushaliraja/Desktop/Nammn AI Practice/skill-bank-refactored/CATALOG.md

Before starting any significant implementation task, read CATALOG.md, detect the project type from files in this directory, load the relevant bundle, and identify applicable skills.
Then you just say: "check the skill bank for this task" — Claude auto-reads CATALOG.md without you specifying the path each time.

---

## Architecture Overview

The pipeline runs in this exact sequence. Do not reorder steps.

```
1. validate_feeds.py   →  Preflight check: confirm feeds return content, activate backups
2. main.py             →  Fetch → Deduplicate → Score → Rank → Detect Trends → Generate Email → Send
3. archive.py          →  Save digest to /digests/YYYY-MM-DD.html → Prune files > 90 days
```

`main.py` is the core. It calls internal modules in sequence. Each stage is a discrete function — not a class, not a framework. Keep it readable.

---

## File Responsibilities

| File | Responsibility |
|---|---|
| `main.py` | Orchestrates the full pipeline. Entry point. |
| `validate_feeds.py` | Pings all feeds, logs failures, activates backup feeds. Exits with code 1 if all feeds for a category fail. |
| `archive.py` | Saves the generated HTML digest to `/digests/`, deletes files older than `ARCHIVE_RETENTION_DAYS`. |
| `config.py` | All tunable parameters. Single source of truth for weights, thresholds, and constants. |
| `rss_feeds.json` | Feed registry. All feed metadata lives here. Never hardcode a feed URL in Python. |
| `requirements.txt` | Pinned dependencies. Do not add libraries without a clear reason. |
| `.github/workflows/daily-news.yml` | GitHub Actions cron workflow. |
| `digests/` | Rolling archive of sent HTML digests. Auto-managed by `archive.py`. Do not manually edit. |

---

## config.py — Parameters Reference

All tunable values live in `config.py`. When adjusting behavior, change config — not logic.

```python
# Scoring
RECENCY_WEIGHT = 0.6          # Weight for recency in composite score (0.0–1.0)
CREDIBILITY_WEIGHT = 0.4      # Weight for source credibility (must sum to 1.0 with RECENCY_WEIGHT)
RECENCY_DECAY_HOURS = 48      # Hours over which recency score decays from 1.0 to 0.0

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

# Timezone
CRON_UTC_OFFSET = 15          # Hour (UTC) the cron fires. 15 = 7 AM PST. 14 = 7 AM PDT.
```

---

## rss_feeds.json — Schema

Each feed entry must follow this schema exactly. Do not add undocumented fields.

```json
{
  "category": "Technology",
  "primary_url": "https://techcrunch.com/feed/",
  "backup_url": "https://www.theverge.com/rss/index.xml",
  "credibility_score": 4,
  "active": true
}
```

**Field rules:**
- `category` must match exactly one of the 9 defined categories. Case-sensitive.
- `credibility_score` is an integer 1–5. Assigned manually. See README for scoring guide.
- `active: false` disables the entry without deleting it. Use this to temporarily pause a feed.
- Both `primary_url` and `backup_url` are required. Never leave `backup_url` null.

**Valid categories (exact strings):**
```
Technology, Finance, Economy, Business, Politics, World, Health, Sports, Entertainment
```

---

## Scoring Logic

Every story receives a composite score used for ranking within its category and for cross-category Top 5 selection.

```python
# Recency score: linear decay from 1.0 (published now) to 0.0 (published 48+ hours ago)
hours_old = (now - published_date).total_seconds() / 3600
recency_score = max(0.0, 1.0 - (hours_old / RECENCY_DECAY_HOURS))

# Credibility score: normalize feed's credibility_score (1–5) to 0.0–1.0
credibility_score = (feed_credibility - 1) / 4

# Composite score
composite_score = (RECENCY_WEIGHT * recency_score) + (CREDIBILITY_WEIGHT * credibility_score)
```

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

Do not add semantic similarity (sentence-transformers) to v1. It is documented as a v2 upgrade in the README. Do not implement it unless explicitly requested.

---

## Trend Detection Logic

Run after deduplication and before email generation.

1. Collect all story titles and summaries into a single corpus string.
2. Tokenize. Convert to lowercase. Remove English stopwords.
3. Count keyword frequency.
4. Flag any keyword appearing `TREND_MIN_APPEARANCES` or more times across distinct stories.
5. Sort by frequency descending. Take top `TREND_TOP_N`.
6. Pass keyword list to the HTML generator for the Emerging Signals section.

Use Python's built-in `collections.Counter` for frequency counting. Do not add NLTK or spaCy for v1 — they are unnecessary.

---

## Email Generation Rules

- Use **inline CSS only**. No `<style>` blocks, no external stylesheets. Email clients strip non-inline styles.
- Max email width: **600px**. Mobile-first.
- No external images. No tracking pixels.
- Every story block must include: headline (linked), source name, RSS summary (truncated to 200 characters if longer), and a "Read more" link pointing to `story.link`.
- Section order in email: Emerging Signals → Top 5 Overall → Categories (in priority order from `rss_feeds.json`).
- Include a timestamp in the footer: `Delivered [HH:MM UTC] · [Date] · GitHub Actions`.

---

## Error Handling Conventions

- **Feed failures:** Log a warning. Activate backup. If backup also fails, log the category as unavailable and include a notice in the email. Never raise an unhandled exception for a single feed failure.
- **Scoring errors:** If `published_date` is missing or unparseable, default `recency_score` to 0.0. Log a warning. Do not skip the story.
- **Resend failures:** Raise the exception. Let GitHub Actions catch it and mark the run as failed. This triggers GitHub's built-in failure notification.
- **Archive failures:** Log a warning. Do not raise. A failed archive is not worth blocking the pipeline over.

Use Python's `logging` module throughout. Set log level to `INFO` by default. Do not use `print()` in production code paths.

---

## Secrets

Three environment variables are required at runtime. They are injected by GitHub Actions from repository secrets. When running locally, export them manually.

```bash
export RESEND_API_KEY=your_key
export RECIPIENT_EMAIL=your_email@example.com
export SENDER_EMAIL=sender@yourdomain.com
```

**Never:**
- Hardcode credentials anywhere in the codebase.
- Log credential values even partially.
- Add `.env` files to the repository. Add `.env` to `.gitignore` immediately if you create one locally.

---

## Dependency Rules

Current dependencies are intentionally minimal:

```
feedparser       # RSS parsing
resend           # Email delivery
python-dateutil  # Robust date parsing
```

`difflib` and `collections` are Python standard library — no install needed.

**Before adding any new dependency, ask:**
1. Does Python's standard library already solve this?
2. Is this for v1 or a v2 feature? (If v2, document it in the README roadmap — do not add it now.)
3. Does it run cleanly inside a GitHub-hosted runner without system dependencies?

Do not add `numpy`, `pandas`, `nltk`, `spacy`, `transformers`, or any ML library to v1.

---

## Testing

There is no test suite in v1. The `workflow_dispatch` trigger in the GitHub Actions workflow serves as the integration test. Before making changes:

1. Run `python validate_feeds.py` locally to confirm feeds are live.
2. Run `python main.py` locally with environment variables exported.
3. Confirm email arrives in inbox before pushing.

If you add unit tests in the future, use Python's built-in `unittest` module. Do not add `pytest` unless the test suite grows to a size that justifies it.

---

## What Not to Do

- Do not use classes where functions are sufficient. The pipeline is linear — keep it that way.
- Do not add a database. Feed state is not persisted between runs by design.
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

1. Update the `primary_url` or `backup_url` in `rss_feeds.json`.
2. Run `validate_feeds.py` to confirm the URL returns parseable content.
3. Assign a `credibility_score` using the guide in README.md.
4. Test locally before pushing.

---

## v2 Features (Do Not Implement in v1)

Document only. Implement only when explicitly requested.

| Feature | Notes |
|---|---|
| Semantic deduplication | `sentence-transformers` all-MiniLM-L6-v2. Local inference. No API cost. |
| AI-generated summaries | Anthropic API. Requires API key in GitHub Secrets. |
| Region filtering | Add `region` field to `rss_feeds.json`. Filter at collection stage. |
| Historical trend analytics | Parse `/digests/` HTML files. Export keyword frequency to CSV. |
| Multi-recipient support | Resend broadcast or dynamic templates. Requires unsubscribe logic before use. |

---

## Commit Message Convention

```
feat: add fuzzy deduplication pass
fix: handle missing published_date in scorer
config: lower fuzzy threshold to 0.80
feeds: replace Reuters backup with AP News
docs: update CLAUDE.md with new config param
```

Use lowercase. Be specific. Reference the file or module affected.

---

*Keep this file current. If you change the architecture, scoring logic, schema, or pipeline order — update CLAUDE.md before closing the PR.*
