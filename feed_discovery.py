#!/usr/bin/env python3
"""
feed_discovery.py — Auto-discovers a working RSS feed URL from a site's homepage.

Called by validate_feeds.py when all URLs in the pool for a category fail.
Uses stdlib only (urllib.request, html.parser) — no new dependencies.
"""

import logging
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin

import feedparser

logger = logging.getLogger(__name__)

# Common feed path patterns tried when <link rel="alternate"> is not found.
COMMON_FEED_PATHS = ["/feed/", "/rss/", "/rss.xml", "/feed.xml", "/atom.xml", "/?feed=rss2"]

# Timeout for HTTP requests (seconds)
REQUEST_TIMEOUT = 10


class _AlternateLinkParser(HTMLParser):
    """Minimal HTML parser that extracts <link rel="alternate" type="application/rss+xml"> hrefs."""

    def __init__(self) -> None:
        super().__init__()
        self.feed_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "link":
            return
        attr_dict = dict(attrs)
        rel = (attr_dict.get("rel") or "").lower()
        mime = (attr_dict.get("type") or "").lower()
        href = attr_dict.get("href") or ""
        if rel == "alternate" and "rss" in mime and href:
            self.feed_urls.append(href)


def _fetch_html(url: str) -> str | None:
    """Fetch URL and return response body as string. Returns None on error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (feed-discovery)"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return None


def _validate_feed_url(url: str) -> bool:
    """Return True if feedparser finds >= 1 entry at the given URL."""
    try:
        parsed = feedparser.parse(url)
        return len(parsed.entries) > 0
    except Exception as exc:
        logger.warning("Feed validation failed for %s: %s", url, exc)
        return False


def discover_feed(site_root: str) -> str | None:
    """
    Attempt to discover a working RSS feed URL for the given site_root.

    Args:
        site_root: Canonical homepage URL (e.g., "https://techcrunch.com").

    Returns:
        A working feed URL string, or None if discovery fails.
    """
    logger.info("Starting feed discovery for: %s", site_root)

    # Step 1: Parse <link rel="alternate"> from homepage HTML
    html = _fetch_html(site_root)
    if html:
        parser = _AlternateLinkParser()
        parser.feed(html)
        for href in parser.feed_urls:
            absolute_url = urljoin(site_root, href)
            logger.info("Found <link rel=alternate>: %s — validating...", absolute_url)
            if _validate_feed_url(absolute_url):
                logger.info("Discovery succeeded via <link rel=alternate>: %s", absolute_url)
                return absolute_url

    # Step 2: Try common path patterns
    for path in COMMON_FEED_PATHS:
        candidate = site_root.rstrip("/") + path
        logger.info("Trying common path: %s", candidate)
        if _validate_feed_url(candidate):
            logger.info("Discovery succeeded via common path: %s", candidate)
            return candidate

    logger.warning("Feed discovery found no working URL for: %s", site_root)
    return None
