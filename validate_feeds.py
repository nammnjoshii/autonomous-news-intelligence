#!/usr/bin/env python3
"""
validate_feeds.py — Preflight health check for all RSS feeds.

Pipeline:
  1. Load feed_state.json (cached health state from previous runs)
  2. For each active feed, try URLs in order — first working URL wins
  3. If all pool URLs fail, run feed_discovery.discover_feed(site_root)
  4. Save updated feed_state.json
  5. Exit with code 1 only if no URL works for any category

Run before main.py. Run standalone: python validate_feeds.py
"""

import json
import logging
import sys
from datetime import datetime, timezone

import feedparser

from feed_discovery import discover_feed

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FEEDS_PATH = "rss_feeds.json"
STATE_PATH = "feed_state.json"


def load_registry(path: str = FEEDS_PATH) -> list[dict]:
    """Load active feeds from rss_feeds.json."""
    with open(path) as f:
        return [feed for feed in json.load(f) if feed.get("active", True)]


def load_state(path: str = STATE_PATH) -> dict:
    """Load feed_state.json if it exists; return empty state otherwise."""
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"last_updated": "", "urls": {}}


def save_state(state: dict, path: str = STATE_PATH) -> None:
    """Write updated feed_state.json."""
    state["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
    logger.info("Feed state saved to %s", path)


def ping_url(url: str) -> int:
    """Parse a feed URL; return entry count. Returns 0 on error."""
    try:
        parsed = feedparser.parse(url)
        return len(parsed.entries)
    except Exception as exc:
        logger.warning("Exception parsing %s: %s", url, exc)
        return 0


def validate_all_feeds(
    feeds: list[dict], state: dict
) -> tuple[list[dict], list[str]]:
    """
    For each feed, find a working URL using the 3-layer fallback.
    Sets feed["active_url"] on success.

    Args:
        feeds: Active feed dicts from rss_feeds.json.
        state: Current feed_state.json content (mutated in place with health updates).

    Returns:
        - validated: feeds with active_url set
        - failed_categories: categories where no URL worked
    """
    url_states: dict = state.setdefault("urls", {})
    validated: list[dict] = []
    failed_categories: list[str] = []
    now = datetime.now(tz=timezone.utc).isoformat()

    for feed in feeds:
        category = feed["category"]
        urls: list[str] = feed["urls"]
        site_root: str = feed["site_root"]
        active_url: str | None = None

        # Layer 1: Try URL pool in order
        for url in urls:
            url_info = url_states.get(url, {})
            if url_info.get("status") == "dead":
                logger.info("[SKIP] %s — known dead: %s", category, url)
                continue

            count = ping_url(url)
            if count > 0:
                logger.info("[OK] %s — %d entries: %s", category, count, url)
                url_states[url] = {"status": "healthy", "last_checked": now}
                active_url = url
                break
            else:
                logger.warning("[DEAD] %s — 0 entries: %s", category, url)
                url_states[url] = {"status": "dead", "last_checked": now}

        # Layer 2: Autodiscovery
        if active_url is None:
            logger.warning(
                "[DISCOVER] %s — all pool URLs failed, running autodiscovery on %s",
                category,
                site_root,
            )
            discovered = discover_feed(site_root)
            if discovered:
                logger.info("[DISCOVERED] %s — replacement found: %s", category, discovered)
                url_states[discovered] = {
                    "status": "healthy",
                    "last_checked": now,
                    "discovered_replacement": True,
                }
                active_url = discovered
            else:
                logger.error("[FAIL] %s — autodiscovery also failed", category)
                failed_categories.append(category)

        if active_url is not None:
            feed["active_url"] = active_url
            validated.append(feed)

    return validated, failed_categories


def main() -> None:
    feeds = load_registry()
    logger.info("Loaded %d active feeds from %s", len(feeds), FEEDS_PATH)

    state = load_state()
    logger.info("Loaded feed state (%d known URLs)", len(state.get("urls", {})))

    validated, failed = validate_all_feeds(feeds, state)

    save_state(state)

    print("\n--- Feed Validation Summary ---")
    print(f"  Feeds checked    : {len(feeds)}")
    print(f"  Feeds OK         : {len(validated)}")
    print(f"  Categories failed: {len(failed)}")
    if failed:
        print(f"  Failed categories: {', '.join(failed)}")
        print("\nAll URLs failed for these categories (including autodiscovery). Update rss_feeds.json.")
        sys.exit(1)

    print("\nAll feeds validated successfully.")


if __name__ == "__main__":
    main()
