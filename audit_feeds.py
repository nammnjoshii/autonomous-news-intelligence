#!/usr/bin/env python3
"""
audit_feeds.py — Weekly audit of all RSS feeds.

Run every Sunday via weekly-audit.yml. Unlike validate_feeds.py, this IGNORES
the cached feed state and retests all URLs fresh. Rebuilds feed_state.json.
Outputs a GitHub Actions step summary.
"""

import json
import logging
import os
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


def ping_url(url: str) -> int:
    """Parse a feed URL; return entry count. Returns 0 on error."""
    try:
        parsed = feedparser.parse(url)
        return len(parsed.entries)
    except Exception as exc:
        logger.warning("Exception parsing %s: %s", url, exc)
        return 0


def audit_all_feeds(feeds: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """
    Retest all URLs fresh. Run discovery for any category where all pool URLs fail.

    Args:
        feeds: Active feed dicts from rss_feeds.json.

    Returns:
        - new_state: rebuilt feed_state.json content
        - healthy_results: list of {category, url} for healthy feeds
        - dead_results: list of {category, dead_urls, discovered_url} for failed feeds
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    url_states: dict = {}
    healthy_results: list[dict] = []
    dead_results: list[dict] = []

    for feed in feeds:
        category = feed["category"]
        urls: list[str] = feed["urls"]
        site_root: str = feed["site_root"]
        working_url: str | None = None
        dead_urls: list[str] = []

        for url in urls:
            count = ping_url(url)
            if count > 0:
                logger.info("[OK] %s — %d entries: %s", category, count, url)
                url_states[url] = {"status": "healthy", "last_checked": now}
                working_url = url
                break
            else:
                logger.warning("[DEAD] %s — 0 entries: %s", category, url)
                url_states[url] = {"status": "dead", "last_checked": now}
                dead_urls.append(url)

        if working_url:
            healthy_results.append({"category": category, "url": working_url})
        else:
            # Run autodiscovery for dead categories
            logger.warning("[DISCOVER] %s — all pool URLs dead, running autodiscovery", category)
            discovered = discover_feed(site_root)
            if discovered:
                url_states[discovered] = {
                    "status": "healthy",
                    "last_checked": now,
                    "discovered_replacement": True,
                }
                logger.info("[DISCOVERED] %s — replacement: %s", category, discovered)
                dead_results.append(
                    {"category": category, "dead_urls": dead_urls, "discovered_url": discovered}
                )
            else:
                dead_results.append(
                    {"category": category, "dead_urls": dead_urls, "discovered_url": None}
                )

    new_state = {"last_updated": now, "urls": url_states}
    return new_state, healthy_results, dead_results


def write_step_summary(healthy: list[dict], dead: list[dict]) -> None:
    """Write GitHub Actions step summary to GITHUB_STEP_SUMMARY env file."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        logger.info("GITHUB_STEP_SUMMARY not set — skipping step summary (local run)")
        return

    lines = ["# Feed Audit Results\n\n"]
    lines.append(f"**Date:** {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n")
    lines.append(f"## Healthy ({len(healthy)} categories)\n\n")
    for h in healthy:
        lines.append(f"- **{h['category']}**: {h['url']}\n")

    if dead:
        lines.append(f"\n## Issues ({len(dead)} categories)\n\n")
        for d in dead:
            disc = d["discovered_url"] or "_Not found — update rss_feeds.json manually_"
            lines.append(f"- **{d['category']}**: all pool URLs dead. Discovery: {disc}\n")
    else:
        lines.append("\n_No issues detected._\n")

    with open(summary_path, "w") as f:
        f.writelines(lines)
    logger.info("Step summary written to %s", summary_path)


def main() -> None:
    feeds = load_registry()
    logger.info("Audit started: %d active feeds", len(feeds))

    new_state, healthy, dead = audit_all_feeds(feeds)

    # Save rebuilt state
    with open(STATE_PATH, "w") as f:
        json.dump(new_state, f, indent=2)
    logger.info("Rebuilt feed_state.json with %d URL entries", len(new_state["urls"]))

    write_step_summary(healthy, dead)

    logger.info("Audit complete. Healthy: %d, Issues: %d", len(healthy), len(dead))


if __name__ == "__main__":
    main()
