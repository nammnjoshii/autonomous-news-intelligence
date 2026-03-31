#!/usr/bin/env python3
"""
archive.py — Manages the /digests/ rolling archive.

Responsibilities:
  1. Verify the most recent digest file exists (written by main.py).
  2. Delete digest files older than ARCHIVE_RETENTION_DAYS.

Run after main.py completes. Failures log a warning but do NOT raise —
a failed archive is not worth blocking the pipeline.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DIGESTS_DIR = "digests"


def prune_old_digests(digests_dir: str = DIGESTS_DIR) -> None:
    """
    Delete HTML files in digests_dir older than ARCHIVE_RETENTION_DAYS days.
    Logs a warning on any file-level error; does not raise.
    """
    if not os.path.isdir(digests_dir):
        logger.warning("Digests directory not found: %s", digests_dir)
        return

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=config.ARCHIVE_RETENTION_DAYS)
    deleted = 0

    for filename in os.listdir(digests_dir):
        if not filename.endswith(".html"):
            continue
        filepath = os.path.join(digests_dir, filename)
        try:
            mtime = os.path.getmtime(filepath)
            file_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
            if file_dt < cutoff:
                os.remove(filepath)
                logger.info("Pruned old digest: %s", filename)
                deleted += 1
        except Exception as exc:
            logger.warning("Could not process archive file %s: %s", filepath, exc)

    logger.info("Archive pruning complete. Deleted %d file(s).", deleted)


def main() -> None:
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    expected_path = os.path.join(DIGESTS_DIR, f"{today}.html")

    if os.path.exists(expected_path):
        size = os.path.getsize(expected_path)
        logger.info("Today's digest found: %s (%d bytes)", expected_path, size)
    else:
        logger.warning(
            "Today's digest not found at %s — main.py may not have run yet",
            expected_path,
        )

    prune_old_digests()


if __name__ == "__main__":
    main()
