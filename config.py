# config.py — Single source of truth for all tunable parameters.
# Change values here; never hardcode them in pipeline logic.

# --- Scoring ---
RECENCY_WEIGHT = 0.6          # Weight for recency in composite score (0.0–1.0)
CREDIBILITY_WEIGHT = 0.4      # Weight for source credibility (must sum to 1.0 with RECENCY_WEIGHT)
RECENCY_DECAY_HOURS = 36      # Hours over which recency score decays from 1.0 to 0.0

# Geographic priority multiplier applied to composite score after recency+credibility.
# 1.25 means a BC story scoring 0.80 base becomes 1.00 — ranks above a US story at 0.90.
# Adjust these values to tune how strongly regional preference overrides quality signals.
REGION_PRIORITY: dict = {
    "canada_west": 1.25,
    "canada":      1.15,
    "usa":         1.00,
    "world":       0.85,
}

# --- Deduplication ---
FUZZY_MATCH_THRESHOLD = 0.85  # difflib SequenceMatcher ratio. Range 0.80–0.90.
                               # Lower = more aggressive dedup.

# --- Ranking ---
MAX_STORIES_PER_CATEGORY = 5  # Absolute ceiling on stories per category
STORY_QUALITY_GATE_RATIO = 0.90  # 4th story included only if score >= 90% of 3rd story's score;
                                  # 5th only if >= 90% of 4th. Applies to all categories except
                                  # Markets & Economy (which uses fixed regional caps).

# --- Trend Detection ---
TREND_MIN_APPEARANCES = 3     # Minimum cross-story appearances to flag a keyword
TREND_TOP_N = 5               # Number of trending keywords to surface

# --- Archive ---
ARCHIVE_RETENTION_DAYS = 90   # Files older than this are deleted by archive.py

# --- Timezone ---
# Adjust CRON_UTC_OFFSET when DST transitions (update daily-news.yml cron expression too)
CRON_UTC_OFFSET = 14          # Hour (UTC) the cron fires. 14 = 7 AM PDT (summer). 15 = 7 AM PST (winter).
