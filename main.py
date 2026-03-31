#!/usr/bin/env python3
"""
main.py — Core pipeline for the Daily News Digest.

Pipeline sequence:
  1. validate feeds   — load state, try URL pool, autodiscover on failure, save state
  2. fetch_stories    — Pull entries from all active feeds
  3. deduplicate      — Remove exact and fuzzy duplicate titles
  4. score_and_rank   — Composite score per story; rank per category + global top 5
  5. detect_trends    — Keyword frequency across full corpus
  6. generate_html    — Produce inline-CSS HTML email
  7. send_email       — Deliver via Resend API
"""

import hashlib
import json
import logging
import os
import re
import string
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher

import feedparser
import resend
from dateutil import parser as dateutil_parser

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Minimal English stopwords — no NLTK needed at this scale.
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "that", "this", "it", "its", "as", "up",
    "over", "after", "before", "about", "into", "than", "not", "no",
    "says", "said", "new", "more", "also", "their", "they", "which",
    "who", "what", "how", "when", "where", "why", "s", "us", "we",
}

# Category display order for the email body.
CATEGORY_ORDER = [
    "Technology", "Finance", "Economy", "Business",
    "Politics", "World", "Health", "Sports", "Entertainment",
]


def load_active_feeds(path: str = "rss_feeds.json") -> list[dict]:
    """Return feeds where active=True."""
    with open(path) as f:
        return [feed for feed in json.load(f) if feed.get("active", True)]


def parse_published_date(entry: object) -> datetime:
    """
    Parse publish date from a feedparser entry.
    Returns UTC-aware datetime. Falls back to datetime.now(UTC) if unparseable.
    """
    # feedparser populates published_parsed (struct_time) when possible
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            import calendar
            ts = calendar.timegm(entry.published_parsed)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass

    if hasattr(entry, "published") and entry.published:
        try:
            dt = dateutil_parser.parse(entry.published)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass

    logger.warning(
        "Could not parse date for entry '%s', defaulting to now",
        getattr(entry, "title", "unknown"),
    )
    return datetime.now(tz=timezone.utc)


def fetch_stories(feeds: list[dict]) -> list[dict]:
    """
    Fetch and parse RSS entries from all active feeds.

    Args:
        feeds: Feed dicts with active_url set by validate_all_feeds().

    Returns:
        Flat list of story dicts with keys: title, link, summary, published,
        source, category, credibility_score, composite_score.
    """
    stories: list[dict] = []

    for feed in feeds:
        url = feed["active_url"]  # set by validate_feeds.py — always present for validated feeds
        category = feed["category"]
        credibility = feed["credibility_score"]

        try:
            parsed = feedparser.parse(url)
            entries = parsed.entries
        except Exception as exc:
            logger.warning("Failed to fetch feed %s (%s): %s", category, url, exc)
            continue

        if not entries:
            logger.warning("Feed returned 0 entries: %s (%s)", category, url)
            continue

        for entry in entries:
            title = getattr(entry, "title", "").strip()
            if not title:
                continue

            link = getattr(entry, "link", "")
            summary_raw = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
            # Strip HTML tags from summary
            summary = re.sub(r"<[^>]+>", "", summary_raw).strip()

            published = parse_published_date(entry)

            stories.append({
                "title": title,
                "link": link,
                "summary": summary,
                "published": published,
                "source": category,
                "category": category,
                "credibility_score": credibility,
                "composite_score": 0.0,
            })

        logger.info("Fetched %d stories from %s", len(entries), category)

    logger.info("Total stories fetched: %d", len(stories))
    return stories


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    title = title.lower()
    title = title.translate(str.maketrans("", "", string.punctuation))
    title = re.sub(r"\s+", " ", title).strip()
    return title


def deduplicate_exact(stories: list[dict]) -> list[dict]:
    """
    Pass 1: MD5 hash of normalized title.
    When two stories share a hash, keep the one with the higher composite_score.
    (At this point scores are 0.0 — ties broken by keeping the first occurrence.)
    """
    seen: dict[str, dict] = {}
    for story in stories:
        key = hashlib.md5(normalize_title(story["title"]).encode()).hexdigest()
        if key not in seen:
            seen[key] = story
        elif story["composite_score"] > seen[key]["composite_score"]:
            seen[key] = story
    result = list(seen.values())
    logger.info("After exact dedup: %d stories (removed %d)", len(result), len(stories) - len(result))
    return result


def deduplicate_fuzzy(stories: list[dict]) -> list[dict]:
    """
    Pass 2: difflib SequenceMatcher on normalized titles.
    O(n2) — acceptable for ~100-300 stories.
    When ratio >= FUZZY_MATCH_THRESHOLD, keep higher-scored story.
    """
    normalized = [normalize_title(s["title"]) for s in stories]
    keep = [True] * len(stories)

    for i in range(len(stories)):
        if not keep[i]:
            continue
        for j in range(i + 1, len(stories)):
            if not keep[j]:
                continue
            ratio = SequenceMatcher(None, normalized[i], normalized[j]).ratio()
            if ratio >= config.FUZZY_MATCH_THRESHOLD:
                # Drop the lower-scored one; if tied, drop j
                if stories[i]["composite_score"] >= stories[j]["composite_score"]:
                    keep[j] = False
                else:
                    keep[i] = False
                    break  # i is now dropped; move to next i

    result = [s for s, k in zip(stories, keep) if k]
    logger.info("After fuzzy dedup: %d stories (removed %d)", len(result), len(stories) - len(result))
    return result


def deduplicate(stories: list[dict]) -> list[dict]:
    """Run both deduplication passes in order."""
    stories = deduplicate_exact(stories)
    stories = deduplicate_fuzzy(stories)
    return stories


# ---------------------------------------------------------------------------
# Scoring and ranking
# ---------------------------------------------------------------------------

def score_story(story: dict, now: datetime) -> float:
    """
    Composite score = (RECENCY_WEIGHT * recency_score) + (CREDIBILITY_WEIGHT * credibility_score)

    recency_score: linear decay 1.0 to 0.0 over RECENCY_DECAY_HOURS
    credibility_score: normalize feed credibility (1-5) to 0.0-1.0
    """
    hours_old = (now - story["published"]).total_seconds() / 3600
    recency_score = max(0.0, 1.0 - (hours_old / config.RECENCY_DECAY_HOURS))
    credibility_score = (story["credibility_score"] - 1) / 4
    return (config.RECENCY_WEIGHT * recency_score) + (config.CREDIBILITY_WEIGHT * credibility_score)


def score_and_rank(stories: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    """
    1. Score every story.
    2. Group by category; sort each group descending; cap at MAX_STORIES_PER_CATEGORY.
    3. Compute global top 5 via min-max normalization across full corpus.

    Returns:
        - ranked_by_category: dict mapping category to top-N stories
        - top5_overall: list of 5 stories (globally highest normalized score)
    """
    now = datetime.now(tz=timezone.utc)

    # Score all stories
    for story in stories:
        story["composite_score"] = score_story(story, now)

    # Rank per category
    categories: dict[str, list[dict]] = {}
    for story in stories:
        cat = story["category"]
        categories.setdefault(cat, []).append(story)

    ranked_by_category: dict[str, list[dict]] = {}
    for cat, cat_stories in categories.items():
        cat_stories.sort(key=lambda s: s["composite_score"], reverse=True)
        ranked_by_category[cat] = cat_stories[: config.MAX_STORIES_PER_CATEGORY]

    # Global top 5 via min-max normalization
    all_scores = [s["composite_score"] for s in stories]
    score_min = min(all_scores) if all_scores else 0.0
    score_max = max(all_scores) if all_scores else 1.0
    score_range = score_max - score_min if score_max != score_min else 1.0

    for story in stories:
        story["normalized_score"] = (story["composite_score"] - score_min) / score_range

    sorted_all = sorted(stories, key=lambda s: s["normalized_score"], reverse=True)
    top5_overall = sorted_all[:5]

    logger.info(
        "Scored %d stories across %d categories. Top score: %.4f",
        len(stories),
        len(ranked_by_category),
        sorted_all[0]["composite_score"] if sorted_all else 0,
    )
    return ranked_by_category, top5_overall


# ---------------------------------------------------------------------------
# Trend detection
# ---------------------------------------------------------------------------

def detect_trends(stories: list[dict]) -> list[str]:
    """
    Keyword frequency across all story titles and summaries.

    Steps:
    1. Concatenate all titles + summaries.
    2. Tokenize: lowercase, split on whitespace/punctuation.
    3. Remove stopwords and tokens shorter than 3 chars.
    4. Count frequency with collections.Counter.
    5. Return top TREND_TOP_N keywords that appear in >= TREND_MIN_APPEARANCES distinct stories.
    """
    # Build per-story token sets (for counting distinct story appearances)
    story_token_sets: list[set[str]] = []
    for story in stories:
        text = (story["title"] + " " + story["summary"]).lower()
        tokens = re.findall(r"[a-z]+", text)
        token_set = {t for t in tokens if t not in STOPWORDS and len(t) >= 3}
        story_token_sets.append(token_set)

    # Count how many distinct stories each keyword appears in
    keyword_story_count: Counter = Counter()
    for token_set in story_token_sets:
        for token in token_set:
            keyword_story_count[token] += 1

    # Filter to keywords meeting TREND_MIN_APPEARANCES threshold
    trending = {
        kw: count
        for kw, count in keyword_story_count.items()
        if count >= config.TREND_MIN_APPEARANCES
    }

    # Sort by frequency descending, take top N
    top_keywords = sorted(trending, key=lambda k: trending[k], reverse=True)[: config.TREND_TOP_N]

    logger.info("Trending keywords (%d qualifying): %s", len(trending), top_keywords)
    return top_keywords


# ---------------------------------------------------------------------------
# HTML email generation
# ---------------------------------------------------------------------------

def _truncate(text: str, max_chars: int = 200) -> str:
    """Truncate text to max_chars, appending '...' if cut."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def generate_html(
    ranked_by_category: dict[str, list[dict]],
    top5_overall: list[dict],
    trends: list[str],
) -> str:
    """
    Generate the full HTML email body.
    - Inline CSS only (no <style> blocks — email clients strip them).
    - Max width 600px, mobile-first.
    - No external images.
    - Section order: Emerging Signals -> Top 5 Overall -> Categories.
    """
    now_utc = datetime.now(tz=timezone.utc)
    timestamp = now_utc.strftime("%H:%M UTC")
    date_str = now_utc.strftime("%A, %B %d %Y")

    # --- Inline style constants ---
    BODY_STYLE = (
        "margin:0;padding:0;background-color:#f4f4f4;"
        "font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#333;"
    )
    CONTAINER_STYLE = (
        "max-width:600px;margin:0 auto;background-color:#ffffff;"
        "border:1px solid #e0e0e0;"
    )
    HEADER_STYLE = (
        "background-color:#1a1a2e;color:#ffffff;padding:20px 24px;"
        "font-size:18px;font-weight:bold;letter-spacing:0.5px;"
    )
    SECTION_HEADER_STYLE = (
        "background-color:#f0f0f0;padding:10px 24px;"
        "font-size:13px;font-weight:bold;color:#555;text-transform:uppercase;"
        "letter-spacing:1px;border-top:2px solid #1a1a2e;"
    )
    STORY_STYLE = "padding:12px 24px;border-bottom:1px solid #eeeeee;"
    STORY_TITLE_STYLE = "font-size:15px;font-weight:bold;color:#1a1a2e;margin:0 0 4px 0;"
    STORY_LINK_STYLE = "color:#1a1a2e;text-decoration:none;"
    STORY_META_STYLE = "font-size:12px;color:#888;margin:0 0 6px 0;"
    STORY_SUMMARY_STYLE = "font-size:13px;color:#555;margin:0 0 6px 0;line-height:1.5;"
    READMORE_STYLE = "font-size:12px;color:#0066cc;"
    TRENDS_STYLE = "padding:12px 24px;"
    TREND_CHIP_STYLE = (
        "display:inline-block;background-color:#e8f0fe;color:#1a1a2e;"
        "border-radius:12px;padding:4px 10px;margin:3px;font-size:12px;font-weight:bold;"
    )
    FOOTER_STYLE = (
        "padding:16px 24px;font-size:11px;color:#aaa;"
        "border-top:1px solid #eeeeee;text-align:center;"
    )

    def story_block(story: dict, index: int) -> str:
        summary = _truncate(story["summary"])
        return (
            f'<div style="{STORY_STYLE}">'
            f'<p style="{STORY_TITLE_STYLE}">'
            f'{index}. <a href="{story["link"]}" style="{STORY_LINK_STYLE}">{story["title"]}</a>'
            f"</p>"
            f'<p style="{STORY_META_STYLE}">{story["source"]}</p>'
            + (f'<p style="{STORY_SUMMARY_STYLE}">{summary}</p>' if summary else "")
            + f'<a href="{story["link"]}" style="{READMORE_STYLE}">Read more -&gt;</a>'
            f"</div>"
        )

    parts: list[str] = []

    # Outer wrapper
    parts.append(f'<html><body style="{BODY_STYLE}">')
    parts.append(f'<div style="{CONTAINER_STYLE}">')

    # Header
    parts.append(
        f'<div style="{HEADER_STYLE}">'
        f"{date_str} &mdash; Your Daily Briefing"
        f"</div>"
    )

    # --- Emerging Signals ---
    parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x1F525; Emerging Signals</div>')
    parts.append(f'<div style="{TRENDS_STYLE}">')
    if trends:
        for kw in trends:
            parts.append(f'<span style="{TREND_CHIP_STYLE}">{kw.title()}</span>')
    else:
        parts.append('<span style="color:#888;font-size:13px;">No strong signals today.</span>')
    parts.append("</div>")

    # --- Top 5 Overall ---
    parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x1F4CC; Top 5 Overall Stories</div>')
    for i, story in enumerate(top5_overall, start=1):
        parts.append(story_block(story, i))

    # --- Per-Category Sections ---
    for category in CATEGORY_ORDER:
        cat_stories = ranked_by_category.get(category, [])
        if not cat_stories:
            parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x1F4C2; {category}</div>')
            parts.append(
                f'<div style="{STORY_STYLE}">'
                f'<p style="color:#888;font-size:13px;margin:0;">'
                f"No stories available for {category} today.</p></div>"
            )
            continue
        parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x1F4C2; {category}</div>')
        for i, story in enumerate(cat_stories, start=1):
            parts.append(story_block(story, i))

    # Footer
    parts.append(
        f'<div style="{FOOTER_STYLE}">'
        f"Delivered {timestamp} &middot; {date_str} &middot; GitHub Actions"
        f"</div>"
    )

    parts.append("</div></body></html>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Email delivery
# ---------------------------------------------------------------------------

def send_email(html: str) -> None:
    """
    Send the digest HTML via Resend.

    Reads credentials from environment variables:
      RESEND_API_KEY, RECIPIENT_EMAIL, SENDER_EMAIL

    Raises on failure — GitHub Actions will catch this and mark the run as failed.
    """
    api_key = os.environ["RESEND_API_KEY"]
    recipient = os.environ["RECIPIENT_EMAIL"]
    sender = os.environ["SENDER_EMAIL"]

    resend.api_key = api_key

    now_utc = datetime.now(tz=timezone.utc)
    subject = f"Your Daily Briefing — {now_utc.strftime('%A, %B %d %Y')}"

    params: resend.Emails.SendParams = {
        "from": sender,
        "to": [recipient],
        "subject": subject,
        "html": html,
    }

    response = resend.Emails.send(params)
    logger.info("Email sent. Resend response id: %s", response.get("id", "unknown"))


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Orchestrate the full pipeline in sequence.
    Pipeline: validate feeds (sets active_url) -> fetch -> dedupe -> score+rank -> trends -> HTML -> send

    Uses validate_feeds.validate_all_feeds() to get feed dicts with active_url set.
    feed["active_url"] is required by fetch_stories() — load_active_feeds() alone does
    not set it, so we must go through the validation layer even in main.py.
    """
    import validate_feeds as vf

    logger.info("=== Daily News Digest pipeline starting ===")

    raw_feeds = load_active_feeds()
    logger.info("Loaded %d active feeds from registry", len(raw_feeds))

    state = vf.load_state()
    feeds, failed = vf.validate_all_feeds(raw_feeds, state)
    vf.save_state(state)

    if failed:
        logger.warning("Categories unavailable (all URLs failed): %s", ", ".join(failed))
    if not feeds:
        logger.error("No feeds validated — aborting pipeline")
        raise RuntimeError("No feeds validated")

    logger.info("Validated %d feeds with active URLs", len(feeds))

    stories = fetch_stories(feeds)
    if not stories:
        logger.error("No stories fetched — aborting pipeline")
        raise RuntimeError("No stories fetched from any feed")

    stories = deduplicate(stories)
    ranked_by_category, top5_overall = score_and_rank(stories)
    trends = detect_trends(stories)
    html = generate_html(ranked_by_category, top5_overall, trends)

    logger.info("HTML digest generated (%d bytes)", len(html))

    send_email(html)

    # Save HTML to a file so archive.py can read it
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    digest_path = f"digests/{today}.html"
    with open(digest_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("Digest saved to %s", digest_path)

    logger.info("=== Daily News Digest pipeline complete ===")


if __name__ == "__main__":
    main()
