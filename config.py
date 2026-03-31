# config.py — Single source of truth for all tunable parameters.
# Change values here; never hardcode them in pipeline logic.

# --- Scoring ---
RECENCY_WEIGHT = 0.6          # Weight for recency in composite score (0.0–1.0)
CREDIBILITY_WEIGHT = 0.4      # Weight for source credibility (must sum to 1.0 with RECENCY_WEIGHT)
RECENCY_DECAY_HOURS = 48      # Hours over which recency score decays from 1.0 to 0.0

# --- Deduplication ---
FUZZY_MATCH_THRESHOLD = 0.85  # difflib SequenceMatcher ratio. Range 0.80–0.90.
                               # Lower = more aggressive dedup.

# --- Ranking ---
MAX_STORIES_PER_CATEGORY = 5  # Hard cap on stories per category in the email

# --- Trend Detection ---
TREND_MIN_APPEARANCES = 3     # Minimum cross-story appearances to flag a keyword
TREND_TOP_N = 5               # Number of trending keywords to surface

# --- Archive ---
ARCHIVE_RETENTION_DAYS = 90   # Files older than this are deleted by archive.py

# --- Timezone ---
# Adjust CRON_UTC_OFFSET when DST transitions (update daily-news.yml cron expression too)
CRON_UTC_OFFSET = 15          # Hour (UTC) the cron fires. 15 = 7 AM PST. 14 = 7 AM PDT.
