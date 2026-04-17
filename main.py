#!/usr/bin/env python3
"""
main.py — Core pipeline for Autonomous News Intelligence.

Pipeline sequence:
  1. validate feeds   — load state, try URL pool, autodiscover on failure, save state
  2. fetch_stories    — Pull entries from all active feeds
  3. deduplicate      — Remove exact and fuzzy duplicate titles
  4. score_and_rank   — Composite score per story; rank per category + global top 5
  5. detect_trends    — Keyword frequency across full corpus
  6. generate_html    — Produce inline-CSS HTML email
  7. send_email       — Deliver via Resend API
"""

import calendar
import hashlib
import json
import logging
import os
import re
import socket
import string
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher

# Hard cap on socket operations — prevents feedparser.parse() from hanging
# indefinitely on a server that accepts the connection but never sends data.
socket.setdefaulttimeout(10)

import feedparser
import resend
from dateutil import parser as dateutil_parser

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REGION_BADGE: dict[str, str] = {
    "canada_west": "[BC]",
    "canada":      "[CA]",
    "usa":         "[US]",
    "world":       "[WORLD]",
}

# Geographic signal sets used by apply_geographic_overrides().
#
# Two-tier design:
#   STRONG — unambiguous in any news context; trigger override on their own.
#   WEAK   — ambiguous words that are also names, other places, or common nouns
#             (e.g. "victoria", "hamilton", "london", "ontario").
#             Only trigger override when a GEO_CONTEXT word is also in the title,
#             confirming the word is being used as a place.
#
# BC checked before CA — it is the more specific signal.
# Only stories tagged 'usa' or 'world' are eligible for override.

BC_STRONG: frozenset[str] = frozenset({
    "vancouver", "burnaby", "surrey", "abbotsford", "kamloops", "nanaimo",
    "prince george", "coquitlam", "langley", "chilliwack", "maple ridge",
    "north vancouver", "west vancouver", "saanich", "penticton", "vernon",
    "whistler", "squamish", "fort st john", "prince rupert", "terrace",
    "bc", "british columbia", "okanagan", "vancouver island",
    "lower mainland", "fraser valley", "metro vancouver",
})

BC_WEAK: frozenset[str] = frozenset({
    "victoria",   # also a personal name (Victoria Beckham, Queen Victoria)
    "richmond",   # also Richmond, Virginia
    "delta",      # also Delta Airlines, COVID variant, Greek letter
    "kelowna",    # low ambiguity but occasionally a surname
})

CA_STRONG: frozenset[str] = frozenset({
    "toronto", "ottawa", "montreal", "calgary", "edmonton", "winnipeg",
    "kitchener", "halifax", "saskatoon", "regina", "mississauga",
    "brampton", "markham", "quebec city", "st johns", "alberta",
    "saskatchewan", "manitoba", "nova scotia", "new brunswick", "quebec",
    "newfoundland", "yukon", "canada", "canadian", "parliament", "rcmp",
    "bay street", "tsx",
})

CA_WEAK: frozenset[str] = frozenset({
    "hamilton",   # also the musical, Alexander Hamilton, Hamilton Ontario vs Hamilton Scotland
    "london",     # also London, UK — by far the more commonly mentioned London in world news
    "ontario",    # also Ontario, California
})

# Words that confirm a weak signal is being used as a geographic location.
# Used by the signal-fallback path when spaCy is unavailable or misses a city.
# If any of these appear in the title+summary alongside a weak signal, the override fires.
GEO_CONTEXT: frozenset[str] = frozenset({
    # People and services
    "residents", "police", "paramedic", "firefighter", "ambulance",
    "mayor", "council", "government", "court", "hospital", "school",
    # Land use and planning
    "housing", "construction", "development", "rezoning", "zoning",
    "bylaw", "permit", "landlord", "tenant", "shelter", "homeless",
    "encampment", "neighbourhood", "neighborhood", "downtown", "suburb",
    # Infrastructure
    "transit", "highway", "bridge", "road", "ferry", "airport",
    "pipeline", "hydro", "utility", "sewer", "landfill", "sidewalk",
    "crosswalk", "pedestrian", "commute", "traffic", "pothole",
    # Events and incidents
    "fire", "flood", "wildfire", "earthquake", "storm", "evacuation",
    "emergency", "shooting", "stabbing", "crash", "overdose", "protest",
    "rally", "election", "vote",
    # Geographic descriptors
    "city", "area", "region", "province", "community", "municipal",
    "waterfront", "shoreline", "suburb",
})

# Minimal English stopwords — no NLTK needed at this scale.
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "that", "this", "it", "its", "as", "up",
    "over", "after", "before", "about", "into", "than", "not", "no",
    "says", "said", "new", "more", "also", "their", "they", "which",
    "who", "what", "how", "when", "where", "why", "s", "us", "we",
    # Common noise words not caught above
    "his", "her", "him", "she", "he", "our", "your", "my", "its",
    "can", "get", "got", "out", "one", "two", "all", "any", "some",
    "been", "being", "was", "were", "am", "are", "is", "has",
    "just", "now", "then", "here", "there", "so", "if", "while",
    "first", "last", "next", "year", "years", "day", "days",
    "time", "says", "said", "week", "weeks", "make", "made",
}

# Category display order for the two geographic email sections.
# Sports and Entertainment are rendered in a combined section at the end — not here.
# Markets & Economy is rendered in its own group between Canada and International — not here.
CA_CATEGORY_ORDER = [
    "BC / West Coast", "Politics", "Technology", "World", "Health",
]

INTL_CATEGORY_ORDER = [
    "Politics", "Technology", "Health",
]


# US state names used to suppress weak-signal overrides when a US location is explicit
# in the title (e.g. "Ontario California wildfire", "Victoria Texas flooding").
# Strong signals (e.g. "vancouver", "british columbia") are unambiguous and ignore this.
US_STATES: frozenset[str] = frozenset({
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming",
})

# --- spaCy NER loader (lazy, with graceful fallback) ---
# Loaded once at first call to apply_geographic_overrides().
# If spaCy or the model is not installed, the function falls back to signal-only matching.
_nlp = None
_spacy_load_attempted = False


def _load_spacy():
    global _nlp, _spacy_load_attempted
    if _spacy_load_attempted:
        return _nlp
    _spacy_load_attempted = True
    try:
        import spacy
        _nlp = spacy.load("en_core_web_sm")
        logger.info("spaCy NER loaded (en_core_web_sm)")
    except (ImportError, OSError):
        logger.warning("spaCy unavailable — geographic overrides fall back to signal-only matching")
        _nlp = None
    return _nlp


def _normalize_text(text: str) -> str:
    """Lowercase, strip accents (Montréal → montreal), strip punctuation."""
    nfd = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return stripped.translate(str.maketrans("", "", string.punctuation))


def apply_geographic_overrides(stories: list[dict]) -> list[dict]:
    """
    Title + summary geographic override pass.

    For stories tagged 'usa' or 'world', attempts to detect Canadian/BC geographic
    references and override the feed-level region tag.

    Two-path design:
      PRIMARY — spaCy NER (when en_core_web_sm is installed):
        Extracts GPE (geo-political entity) tokens from the title. Only tokens
        classified as GPE are checked against signal sets — eliminating person-name
        false positives (e.g. "Victoria Beckham" → PERSON, not GPE).

      FALLBACK — signal matching (when spaCy unavailable or misses a rare city):
        Scans normalized title+summary using padded word-boundary matching.
        Strong signals fire directly. Weak signals require a GEO_CONTEXT word
        to confirm geographic usage.

    US state exclusion applies to weak signals in both paths — suppresses overrides
    when a US state name appears alongside an ambiguous word (e.g. "Ontario California").
    Strong signals are unambiguous and bypass this check.

    Accent normalization applied before all matching (Montréal → montreal).
    BC checked before CA — it is the more specific signal.
    Stories already tagged 'canada' or 'canada_west' are skipped.
    """
    nlp = _load_spacy()
    overridden = 0

    for story in stories:
        if story.get("region") in ("canada", "canada_west"):
            continue

        # Normalize title + summary for signal matching
        combined = story["title"] + " " + story.get("summary", "")
        clean = _normalize_text(combined)
        padded = f" {clean} "
        words = set(clean.split())

        has_geo_context = bool(words & GEO_CONTEXT)
        has_us_state = any(f" {state} " in padded for state in US_STATES)

        bc_match = False
        ca_match = False

        # --- PRIMARY: spaCy NER ---
        if nlp is not None:
            doc = nlp(story["title"])
            gpe_set = {_normalize_text(ent.text) for ent in doc.ents if ent.label_ == "GPE"}

            # Strong: fire regardless of US state presence (unambiguous names)
            # Weak: suppressed when a US state appears in the title
            bc_match = bool(gpe_set & BC_STRONG) or (
                (not has_us_state) and bool(gpe_set & BC_WEAK)
            )
            ca_match = bool(gpe_set & CA_STRONG) or (
                (not has_us_state) and bool(gpe_set & CA_WEAK)
            )

        # --- FALLBACK: signal matching (runs when spaCy missed or is unavailable) ---
        if not bc_match:
            bc_match = any(f" {sig} " in padded for sig in BC_STRONG) or (
                (not has_us_state)
                and any(f" {sig} " in padded for sig in BC_WEAK)
                and has_geo_context
            )

        if not ca_match:
            ca_match = any(f" {sig} " in padded for sig in CA_STRONG) or (
                (not has_us_state)
                and any(f" {sig} " in padded for sig in CA_WEAK)
                and has_geo_context
            )

        if bc_match:
            story["region"] = "canada_west"
            overridden += 1
            logger.debug("Region override → canada_west: %s", story["title"])
        elif ca_match:
            story["region"] = "canada"
            overridden += 1
            logger.debug("Region override → canada: %s", story["title"])

    logger.info("Geographic overrides applied: %d stories re-tagged", overridden)
    return stories


def load_active_feeds(path: str = "rss_feeds.json") -> list[dict]:
    """Return feeds where active=True."""
    with open(path) as f:
        return [feed for feed in json.load(f) if feed.get("active", True)]


def parse_published_date(entry: object) -> datetime:
    """
    Parse publish date from a feedparser entry.
    Returns UTC-aware datetime. Falls back to datetime.now(UTC) if unparseable.
    """
    # feedparser populates published_parsed (struct_time) when possible.
    # RSS 1.0 / RDF feeds (e.g. Bank of Canada) use updated_parsed instead.
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                ts = calendar.timegm(val)
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
        region = feed.get("region", "world")
        feed_name = feed.get("name", category)

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

            # Markets & Economy: keyword gate — only pass on-topic financial/policy stories.
            if category == _MARKETS_ECONOMY:
                text_lower = f"{title} {summary}".lower()
                if not any(kw in text_lower for kw in _MARKETS_ECONOMY_KEYWORDS):
                    logger.debug("M&E keyword filter dropped: %s", title)
                    continue

            stories.append({
                "title": title,
                "link": link,
                "summary": summary,
                "published": published,
                "source": category,
                "category": category,
                "credibility_score": credibility,
                "region": region,
                "feed_name": feed_name,
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
    base = (config.RECENCY_WEIGHT * recency_score) + (config.CREDIBILITY_WEIGHT * credibility_score)
    region_multiplier = config.REGION_PRIORITY.get(story.get("region", "world"), 1.0)
    return base * region_multiplier


def apply_quality_gate(sorted_stories: list[dict], min_count: int = 3) -> list[dict]:
    """Return top stories up to MAX_STORIES_PER_CATEGORY, gating slots 4+ on score proximity.

    Always returns up to min_count stories. Adds slot 4 only if its score is >= STORY_QUALITY_GATE_RATIO
    of slot 3's score; adds slot 5 only if its score is >= STORY_QUALITY_GATE_RATIO of slot 4's score.
    """
    if len(sorted_stories) <= min_count:
        return sorted_stories
    result = list(sorted_stories[:min_count])
    for i in range(min_count, min(len(sorted_stories), config.MAX_STORIES_PER_CATEGORY)):
        threshold = result[-1]["composite_score"] * config.STORY_QUALITY_GATE_RATIO
        if sorted_stories[i]["composite_score"] >= threshold:
            result.append(sorted_stories[i])
        else:
            break
    return result


_MARKETS_ECONOMY = "Markets & Economy"
_SPORTS_CAT = "Sports"
_ENTERTAINMENT_CAT = "Entertainment"

# Keywords that must appear in title or summary for a story to pass the Markets & Economy filter.
# Ensures off-topic stories from financial feeds (e.g. geopolitical Reuters) don't crowd out
# on-topic market/policy content. Case-insensitive substring match.
_MARKETS_ECONOMY_KEYWORDS: frozenset[str] = frozenset({
    "rate", "tsx", "s&p", "dow", "nasdaq", "fed", "inflation", "cpi", "gdp",
    "earnings", "yield", "bond", "deficit", "boc", "monetary", "interest",
    "trade", "tariff", "market", "bank of canada", "federal reserve",
    "stock", "equities", "recession", "quarter", "fiscal", "currency",
    "dollar", "loonie", "tsx composite", "bay street", "wall street",
})

# Signals required for a story to appear in the Canada Markets sub-section.
# Prevents Canadian news outlets (e.g. Globe and Mail) from filling Canada Markets
# with stories about US or global topics — only stories with a Canadian economic
# signal belong there.
_CANADA_MARKETS_SIGNALS: frozenset[str] = frozenset({
    "canada", "canadian", "bank of canada", "boc", "tsx", "tsxv",
    "loonie", "bay street", "toronto stock", "ottawa", "alberta",
    "federal budget", "canada's",
})

# World-region stories must contain one of these to appear in Canada Coverage.
# Ensures the Canada World sub-section shows Canada's role in global events,
# not generic international news that has nothing to do with Canada.
_CANADA_WORLD_SIGNALS: frozenset[str] = frozenset({
    "canada", "canadian", "canadians", "canada's", "trudeau", "carney",
    "ottawa", "parliament", "rcmp", "toronto", "montreal", "calgary",
    "edmonton", "vancouver", "alberta", "ontario", "quebec", "bc",
    "british columbia", "tsx", "loonie",
})

# World-region stories must contain one of these to appear in USA Coverage.
# Ensures the USA World sub-section shows US involvement in global events,
# not generic international news unrelated to the United States.
_USA_WORLD_SIGNALS: frozenset[str] = frozenset({
    "united states", "u.s.", "us ", "american", "americans", "america",
    "trump", "biden", "harris", "pentagon", "washington", "white house",
    "state department", "congress", "senate", "federal reserve", "fed ",
    "wall street", "nasdaq", "s&p", "dow jones",
})


def score_and_rank(
    stories: list[dict],
) -> tuple[
    dict[str, list[dict]],
    dict[str, list[dict]],
    list[dict],
    list[dict],
    list[dict],
    list[dict],
    list[dict],
    list[dict],
]:
    """
    1. Score every story.
    2. Split stories by geographic region; rank each slice per category.
    3. Compute normalized scores across full corpus; extract three regional Top 5s.

    Special category handling:
      - Markets & Economy: 3-way split (5 CA, 5 USA, 3 World) — no quality gate.
      - Sports / Entertainment: combined across all regions, top 3 each — no quality gate.

    Returns:
        ranked_by_category_ca        — CA/BC stories per category (excl. Sports, Ent)
        ranked_by_category_usa_world — intl stories per category; for M&E = USA only
        top5_canada                  — up to 5 top Canada/BC stories (normalized)
        top5_usa                     — up to 5 top USA stories (normalized)
        top5_world                   — up to 5 top World stories (normalized)
        markets_economy_world        — top 3 World stories for Markets & Economy
        sports_top3                  — top 3 Sports stories (all regions combined)
        entertainment_top3           — top 3 Entertainment stories (all regions combined)
    """
    now = datetime.now(tz=timezone.utc)

    # Score all stories
    for story in stories:
        story["composite_score"] = score_story(story, now)

    # Group by category (all regions)
    categories: dict[str, list[dict]] = {}
    for story in stories:
        cat = story["category"]
        categories.setdefault(cat, []).append(story)

    # Geographic category splits — re-rank within each slice
    ranked_by_category_ca: dict[str, list[dict]] = {}
    ranked_by_category_usa_world: dict[str, list[dict]] = {}
    markets_economy_world: list[dict] = []
    sports_all: list[dict] = []
    entertainment_all: list[dict] = []

    for cat, cat_stories in categories.items():
        if cat == _MARKETS_ECONOMY:
            # Canada Markets: canadian/bc stories that contain a Canadian economic signal
            me_ca_raw = [s for s in cat_stories if s.get("region") in ("canada", "canada_west")]
            me_ca = [
                s for s in me_ca_raw
                if any(sig in f"{s['title']} {s.get('summary', '')}".lower()
                       for sig in _CANADA_MARKETS_SIGNALS)
            ]
            me_ca.sort(key=lambda s: s["composite_score"], reverse=True)
            if me_ca:
                ranked_by_category_ca[cat] = me_ca[:5]

            me_usa = [s for s in cat_stories if s.get("region") == "usa"]
            me_usa.sort(key=lambda s: s["composite_score"], reverse=True)
            if me_usa:
                ranked_by_category_usa_world[cat] = me_usa[:5]

            me_world = [s for s in cat_stories if s.get("region") == "world"]
            me_world.sort(key=lambda s: s["composite_score"], reverse=True)
            markets_economy_world = me_world[:3]

        elif cat == _SPORTS_CAT:
            sports_all.extend(cat_stories)

        elif cat == _ENTERTAINMENT_CAT:
            entertainment_all.extend(cat_stories)

        else:
            def _has_signal(story: dict, signals: frozenset[str]) -> bool:
                text = f"{story['title']} {story.get('summary', '')}".lower()
                return any(sig in text for sig in signals)

            # Canada Coverage: CA/BC stories + world stories with a Canadian angle
            ca_domestic = [s for s in cat_stories if s.get("region") in ("canada", "canada_west")]
            ca_world = [
                s for s in cat_stories
                if s.get("region") == "world" and _has_signal(s, _CANADA_WORLD_SIGNALS)
            ]
            ca_combined = ca_domestic + ca_world
            if ca_combined:
                ca_combined.sort(key=lambda s: s["composite_score"], reverse=True)
                ranked_by_category_ca[cat] = apply_quality_gate(ca_combined)

            # USA Coverage: US stories + world stories with a US angle
            usa_domestic = [s for s in cat_stories if s.get("region") == "usa"]
            usa_world = [
                s for s in cat_stories
                if s.get("region") == "world" and _has_signal(s, _USA_WORLD_SIGNALS)
            ]
            usa_combined = usa_domestic + usa_world
            if usa_combined:
                usa_combined.sort(key=lambda s: s["composite_score"], reverse=True)
                ranked_by_category_usa_world[cat] = apply_quality_gate(usa_combined)

    # Sports & Entertainment combined, top 3 each (all regions)
    sports_all.sort(key=lambda s: s["composite_score"], reverse=True)
    sports_top3 = sports_all[:3]

    entertainment_all.sort(key=lambda s: s["composite_score"], reverse=True)
    entertainment_top3 = entertainment_all[:3]

    # Min-max normalization across full corpus
    all_scores = [s["composite_score"] for s in stories]
    score_min = min(all_scores) if all_scores else 0.0
    score_max = max(all_scores) if all_scores else 1.0
    score_range = score_max - score_min if score_max != score_min else 1.0

    for story in stories:
        story["normalized_score"] = (story["composite_score"] - score_min) / score_range

    # Collect the top story from every category section (the "champion").
    # Champions are reserved for their section — excluded from Top 5 so that
    # the Politics section always has its lead story, BC section has its lead story, etc.
    # Top 5 is then built from the next-best stories per region, never touching champions.
    champion_links: set[str] = set()
    for section_stories in ranked_by_category_ca.values():
        if section_stories:
            champion_links.add(section_stories[0]["link"])
    for section_stories in ranked_by_category_usa_world.values():
        if section_stories:
            champion_links.add(section_stories[0]["link"])
    # For small fixed-size sections (M&E World, Sports, Entertainment), protect ALL
    # their stories from Top 5 — not just the champion — since these sections have
    # their own dedicated rendering and must never overlap with Top 5.
    for section_stories in [markets_economy_world, sports_top3, entertainment_top3]:
        for s in section_stories:
            champion_links.add(s["link"])

    # Three regional Top 5s — champions excluded; mutually exclusive by region tag
    canada_stories = [s for s in stories if s.get("region") in ("canada", "canada_west")]
    top5_canada = sorted(
        [s for s in canada_stories if s["link"] not in champion_links],
        key=lambda s: s["normalized_score"], reverse=True,
    )[:5]

    usa_stories = [s for s in stories if s.get("region") == "usa"]
    top5_usa = sorted(
        [s for s in usa_stories if s["link"] not in champion_links],
        key=lambda s: s["normalized_score"], reverse=True,
    )[:5]

    world_stories = [s for s in stories if s.get("region") == "world"]
    top5_world = sorted(
        [s for s in world_stories if s["link"] not in champion_links],
        key=lambda s: s["normalized_score"], reverse=True,
    )[:5]

    logger.info(
        "Scored %d stories. Canadian: %d, USA: %d, World: %d. Top score: %.4f",
        len(stories),
        len(canada_stories),
        len(usa_stories),
        len(world_stories),
        max(all_scores) if all_scores else 0,
    )
    return (
        ranked_by_category_ca,
        ranked_by_category_usa_world,
        top5_canada,
        top5_usa,
        top5_world,
        markets_economy_world,
        sports_top3,
        entertainment_top3,
    )


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
    # Build per-story token sets: unigrams + bigrams (for counting distinct story appearances)
    story_token_sets: list[set[str]] = []
    for story in stories:
        text = (story["title"] + " " + story["summary"]).lower()
        words = re.findall(r"[a-z&']+", text)
        unigrams = {w for w in words if w not in STOPWORDS and len(w) >= 3}
        bigrams = {
            f"{a} {b}"
            for a, b in zip(words, words[1:])
            if a not in STOPWORDS and b not in STOPWORDS and len(a) >= 3 and len(b) >= 3
        }
        story_token_sets.append(unigrams | bigrams)

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
# Market snapshot
# ---------------------------------------------------------------------------

_MARKET_SYMBOLS: list[tuple[str, str]] = [
    ("^GSPC",    "S&P 500"),
    ("^GSPTSE",  "S&P/TSX"),
    ("^IXIC",    "NASDAQ"),
    ("^BSESN",   "BSE Sensex"),
    ("CADUSD=X", "CAD/USD"),
    ("CADINR=X", "CAD/INR"),
    ("^VIX",     "VIX"),
]


def fetch_market_snapshot() -> list[dict]:
    """
    Fetch previous-close data for 7 market indices/FX pairs via yfinance.

    Returns a list of dicts with keys: label, value, change_pct.
    If any symbol fails, returns "—" for that row.
    If the entire fetch fails, returns an empty list (section is skipped).

    All values are previous-close, not live prices — at 7 AM PDT most North American
    and Asian markets are pre-market or closed.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — skipping market snapshot")
        return []

    results: list[dict] = []
    for symbol, label in _MARKET_SYMBOLS:
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="2d")
            if hist.empty or len(hist) < 1:
                results.append({"label": label, "value": "—", "change_pct": 0.0})
                continue

            prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else float(hist["Close"].iloc[-1])
            last_close = float(hist["Close"].iloc[-1])
            change_pct = ((last_close - prev_close) / prev_close * 100) if prev_close != 0 else 0.0

            # Format value: FX pairs to 4 decimal places, indices to 2
            if "=" in symbol:
                value_str = f"{last_close:.4f}"
            elif symbol == "^VIX":
                value_str = f"{last_close:.2f}"
            else:
                value_str = f"{last_close:,.2f}"

            results.append({"label": label, "value": value_str, "change_pct": change_pct})

        except Exception as exc:
            logger.warning("Market snapshot failed for %s (%s): %s", label, symbol, exc)
            results.append({"label": label, "value": "—", "change_pct": 0.0})

    logger.info("Market snapshot fetched: %d symbols", len(results))
    return results


# ---------------------------------------------------------------------------
# HTML email generation
# ---------------------------------------------------------------------------

def _truncate(text: str, max_chars: int = 200) -> str:
    """Truncate text to max_chars, appending '...' if cut."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def generate_html(
    ranked_by_category_ca: dict[str, list[dict]],
    ranked_by_category_usa_world: dict[str, list[dict]],
    top5_canada: list[dict],
    top5_usa: list[dict],
    top5_world: list[dict],
    markets_economy_world: list[dict],
    sports_top3: list[dict],
    entertainment_top3: list[dict],
    market_snapshot: list[dict] | None = None,
) -> str:
    """
    Generate the full HTML email body.
    - Inline CSS only (no <style> blocks — email clients strip them).
    - Max width 600px, mobile-first.
    - No external images.
    - Section order: At a Glance (optional) -> Top 5 Canada -> Top 5 USA
                     -> Top 5 World -> Canada Coverage (CA + World) -> Markets & Economy (CA/USA/World)
                     -> USA Coverage -> Sports & Entertainment.
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
    FOOTER_STYLE = (
        "padding:16px 24px;font-size:11px;color:#aaa;"
        "border-top:1px solid #eeeeee;text-align:center;"
    )
    GROUP_HEADER_STYLE = (
        "background-color:#1a1a2e;color:#ffffff;padding:10px 24px;"
        "font-size:13px;font-weight:bold;text-transform:uppercase;letter-spacing:1.5px;"
    )

    def story_block(story: dict, index: int) -> str:
        summary = _truncate(story["summary"])
        # Publication time: "6:42 AM" for today's stories, "Yesterday 6:42 AM" for prior-day
        pub: datetime = story.get("published", now_utc)
        pub_utc = pub.astimezone(timezone.utc) if pub.tzinfo else pub.replace(tzinfo=timezone.utc)
        if pub_utc.date() == now_utc.date():
            time_label = pub_utc.strftime("%-I:%M %p")
        else:
            time_label = "Yesterday " + pub_utc.strftime("%-I:%M %p")
        return (
            f'<div style="{STORY_STYLE}">'
            f'<p style="{STORY_TITLE_STYLE}">'
            f'{index}. <a href="{story["link"]}" style="{STORY_LINK_STYLE}">{story["title"]}</a>'
            f"</p>"
            f'<p style="{STORY_META_STYLE}">{story["feed_name"]} {REGION_BADGE.get(story.get("region", "world"), "")} &middot; {time_label}</p>'
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

    # --- At a Glance market snapshot (optional) ---
    if market_snapshot:
        SNAPSHOT_STYLE = "padding:12px 24px;background-color:#f9f9f9;border-bottom:1px solid #e0e0e0;"
        SNAPSHOT_TABLE_STYLE = "width:100%;border-collapse:collapse;font-size:13px;"
        SNAPSHOT_LABEL_STYLE = "color:#555;padding:4px 8px 4px 0;"
        SNAPSHOT_VALUE_STYLE = "color:#333;font-weight:bold;padding:4px 0;"
        UP_STYLE = "color:#1a7a1a;font-weight:bold;"
        DOWN_STYLE = "color:#c0392b;font-weight:bold;"
        parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x1F4CA; At a Glance &mdash; vs. prev. close</div>')
        parts.append(f'<div style="{SNAPSHOT_STYLE}"><table style="{SNAPSHOT_TABLE_STYLE}">')
        for item in market_snapshot:
            arrow = "&#x25B2;" if item["change_pct"] >= 0 else "&#x25BC;"
            color_style = UP_STYLE if item["change_pct"] >= 0 else DOWN_STYLE
            sign = "+" if item["change_pct"] >= 0 else ""
            parts.append(
                f'<tr>'
                f'<td style="{SNAPSHOT_LABEL_STYLE}">{item["label"]}</td>'
                f'<td style="{SNAPSHOT_VALUE_STYLE}">{item["value"]}</td>'
                f'<td><span style="{color_style}">{arrow} {sign}{item["change_pct"]:.2f}%</span></td>'
                f'</tr>'
            )
        parts.append("</table></div>")

    # --- Top 5 Canada ---
    if top5_canada:
        parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x1F341; Top 5 Canada</div>')
        for i, story in enumerate(top5_canada, start=1):
            parts.append(story_block(story, i))

    # --- Top 5 USA ---
    if top5_usa:
        parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x1F1FA;&#x1F1F8; Top 5 USA</div>')
        for i, story in enumerate(top5_usa, start=1):
            parts.append(story_block(story, i))

    # --- Top 5 World ---
    if top5_world:
        parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x1F30D; Top 5 World</div>')
        for i, story in enumerate(top5_world, start=1):
            parts.append(story_block(story, i))

    # Top 5 links — excluded from category section rendering to prevent repeats.
    # Champions were already excluded when Top 5 was built (in score_and_rank),
    # so category sections always have their top story regardless of Top 5 content.
    top5_links: set[str] = {s["link"] for s in (top5_canada + top5_usa + top5_world)}

    # --- Canada Coverage (Canadian + BC + World stories) ---
    parts.append(f'<div style="{GROUP_HEADER_STYLE}">&#x1F1E8;&#x1F1E6; Canada Coverage</div>')
    for category in CA_CATEGORY_ORDER:
        cat_stories = ranked_by_category_ca.get(category, [])
        if not cat_stories:
            continue
        display = [s for s in cat_stories if s["link"] not in top5_links]
        if not display:
            continue
        parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x1F4C2; {category}</div>')
        for i, story in enumerate(display, start=1):
            parts.append(story_block(story, i))

    # --- Markets & Economy (standalone group — CA, USA, World sub-sections) ---
    me_ca = ranked_by_category_ca.get(_MARKETS_ECONOMY, [])
    me_usa = ranked_by_category_usa_world.get(_MARKETS_ECONOMY, [])
    me_world_stories = markets_economy_world

    me_ca_display = [s for s in me_ca if s["link"] not in top5_links]
    me_usa_display = [s for s in me_usa if s["link"] not in top5_links]
    # me_world_stories are all protected as champions — no filtering needed
    if me_ca_display or me_usa_display or me_world_stories:
        parts.append(f'<div style="{GROUP_HEADER_STYLE}">&#x1F4B9; Markets &amp; Economy</div>')
        if me_ca_display:
            parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x1F1E8;&#x1F1E6; Canada Markets</div>')
            for i, story in enumerate(me_ca_display, start=1):
                parts.append(story_block(story, i))
        if me_usa_display:
            parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x1F1FA;&#x1F1F8; US Markets</div>')
            for i, story in enumerate(me_usa_display, start=1):
                parts.append(story_block(story, i))
        if me_world_stories:
            parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x1F30D; Global Markets</div>')
            for i, story in enumerate(me_world_stories, start=1):
                parts.append(story_block(story, i))

    # --- USA Coverage (US-only stories) ---
    parts.append(f'<div style="{GROUP_HEADER_STYLE}">&#x1F1FA;&#x1F1F8; USA Coverage</div>')
    for category in INTL_CATEGORY_ORDER:
        cat_stories = ranked_by_category_usa_world.get(category, [])
        if not cat_stories:
            continue
        display = [s for s in cat_stories if s["link"] not in top5_links]
        if not display:
            continue
        parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x1F4C2; {category}</div>')
        for i, story in enumerate(display, start=1):
            parts.append(story_block(story, i))

    # --- Sports & Entertainment (combined, at end of digest) ---
    if sports_top3 or entertainment_top3:
        parts.append(f'<div style="{GROUP_HEADER_STYLE}">&#x1F3C6; Sports &amp; Entertainment</div>')
        if sports_top3:
            parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x26BD; Sports</div>')
            for i, story in enumerate(sports_top3, start=1):
                parts.append(story_block(story, i))
        if entertainment_top3:
            parts.append(f'<div style="{SECTION_HEADER_STYLE}">&#x1F3AC; Entertainment</div>')
            for i, story in enumerate(entertainment_top3, start=1):
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

    logger.info("=== Autonomous News Intelligence pipeline starting ===")

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

    stories = apply_geographic_overrides(stories)
    stories = deduplicate(stories)
    ranked_ca, ranked_intl, top5_canada, top5_usa, top5_world, me_world, sports3, ent3 = score_and_rank(stories)
    market_snapshot = fetch_market_snapshot()
    html = generate_html(
        ranked_ca, ranked_intl, top5_canada, top5_usa, top5_world,
        me_world, sports3, ent3, market_snapshot or None,
    )

    logger.info("HTML digest generated (%d bytes)", len(html))

    send_email(html)

    # Save HTML to a file so archive.py can read it
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    digest_path = f"digests/{today}.html"
    with open(digest_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("Digest saved to %s", digest_path)

    logger.info("=== Autonomous News Intelligence pipeline complete ===")


if __name__ == "__main__":
    main()
