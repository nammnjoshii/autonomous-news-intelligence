"""
Unit tests for pure pipeline functions.

Run with:
    python -m unittest discover tests
"""

import sys
import os
import unittest
from datetime import datetime, timezone, timedelta

# Ensure project root is on the path regardless of where tests are invoked from
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from main import (
    normalize_title,
    deduplicate_exact,
    deduplicate_fuzzy,
    score_story,
    detect_trends,
    apply_geographic_overrides,
)


def _make_story(title: str, score: float = 0.0, region: str = "usa",
                credibility: int = 3, hours_old: float = 1.0) -> dict:
    """Helper: produce a minimal story dict for testing."""
    published = datetime.now(tz=timezone.utc) - timedelta(hours=hours_old)
    return {
        "title": title,
        "link": "https://example.com",
        "summary": "",
        "published": published,
        "source": "Test",
        "category": "Technology",
        "credibility_score": credibility,
        "region": region,
        "feed_name": "Test Feed",
        "composite_score": score,
    }


class TestNormalizeTitle(unittest.TestCase):

    def test_lowercases(self):
        self.assertEqual(normalize_title("Breaking News"), "breaking news")

    def test_strips_punctuation(self):
        self.assertEqual(normalize_title("Hello, World!"), "hello world")

    def test_collapses_whitespace(self):
        self.assertEqual(normalize_title("too   many   spaces"), "too many spaces")

    def test_empty_string(self):
        self.assertEqual(normalize_title(""), "")

    def test_preserves_numbers(self):
        result = normalize_title("Top 5 Stories of 2026")
        self.assertIn("5", result)
        self.assertIn("2026", result)


class TestDeduplicateExact(unittest.TestCase):

    def test_removes_identical_titles(self):
        stories = [
            _make_story("Government Passes Budget", score=0.5),
            _make_story("Government Passes Budget", score=0.3),
        ]
        result = deduplicate_exact(stories)
        self.assertEqual(len(result), 1)

    def test_keeps_higher_scored_duplicate(self):
        stories = [
            _make_story("Government Passes Budget", score=0.3),
            _make_story("Government Passes Budget", score=0.8),
        ]
        result = deduplicate_exact(stories)
        self.assertEqual(result[0]["composite_score"], 0.8)

    def test_case_insensitive_match(self):
        stories = [
            _make_story("BREAKING: Tech layoffs continue"),
            _make_story("breaking: tech layoffs continue"),
        ]
        result = deduplicate_exact(stories)
        self.assertEqual(len(result), 1)

    def test_punctuation_insensitive_match(self):
        stories = [
            _make_story("Markets rise, investors cheer"),
            _make_story("Markets rise investors cheer"),
        ]
        result = deduplicate_exact(stories)
        self.assertEqual(len(result), 1)

    def test_distinct_titles_preserved(self):
        stories = [
            _make_story("Story about tech"),
            _make_story("Story about finance"),
            _make_story("Story about health"),
        ]
        result = deduplicate_exact(stories)
        self.assertEqual(len(result), 3)


class TestDeduplicateFuzzy(unittest.TestCase):

    def test_removes_near_duplicate(self):
        # These titles are very similar — should be treated as duplicates
        stories = [
            _make_story("Bank of Canada raises interest rates again", score=0.6),
            _make_story("Bank of Canada raises interest rates once more", score=0.4),
        ]
        result = deduplicate_fuzzy(stories)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["composite_score"], 0.6)

    def test_keeps_clearly_distinct_stories(self):
        stories = [
            _make_story("Federal budget unveiled in Ottawa", score=0.5),
            _make_story("Vancouver housing prices hit new record", score=0.5),
        ]
        result = deduplicate_fuzzy(stories)
        self.assertEqual(len(result), 2)

    def test_empty_input(self):
        self.assertEqual(deduplicate_fuzzy([]), [])

    def test_single_story_unchanged(self):
        stories = [_make_story("Only one story")]
        result = deduplicate_fuzzy(stories)
        self.assertEqual(len(result), 1)


class TestScoreStory(unittest.TestCase):

    def test_fresh_high_credibility_scores_highest(self):
        now = datetime.now(tz=timezone.utc)
        story = _make_story("Fresh story", hours_old=0.1, credibility=5, region="usa")
        score = score_story(story, now)
        self.assertGreater(score, 0.8)

    def test_old_story_scores_lower_than_fresh(self):
        now = datetime.now(tz=timezone.utc)
        fresh = _make_story("Fresh", hours_old=1, credibility=3)
        old = _make_story("Old", hours_old=config.RECENCY_DECAY_HOURS + 5, credibility=3)
        self.assertGreater(score_story(fresh, now), score_story(old, now))

    def test_beyond_decay_window_recency_is_zero(self):
        now = datetime.now(tz=timezone.utc)
        story = _make_story("Very old story", hours_old=config.RECENCY_DECAY_HOURS + 10,
                             credibility=1, region="usa")
        score = score_story(story, now)
        # Recency = 0.0, credibility of 1 normalizes to 0.0, region multiplier = 1.0
        self.assertAlmostEqual(score, 0.0, places=4)

    def test_canada_west_region_multiplier_applied(self):
        now = datetime.now(tz=timezone.utc)
        usa_story = _make_story("Same story", hours_old=2, credibility=3, region="usa")
        bc_story = _make_story("Same story", hours_old=2, credibility=3, region="canada_west")
        usa_score = score_story(usa_story, now)
        bc_score = score_story(bc_story, now)
        self.assertGreater(bc_score, usa_score)

    def test_score_is_non_negative(self):
        now = datetime.now(tz=timezone.utc)
        story = _make_story("Any story", hours_old=999, credibility=1, region="world")
        self.assertGreaterEqual(score_story(story, now), 0.0)


class TestDetectTrends(unittest.TestCase):

    def _make_story_with_content(self, title: str, summary: str = "") -> dict:
        s = _make_story(title)
        s["summary"] = summary
        return s

    def test_returns_top_n_keywords(self):
        stories = [
            self._make_story_with_content("federal reserve raises rates"),
            self._make_story_with_content("federal reserve holds rates steady"),
            self._make_story_with_content("federal reserve policy under scrutiny"),
        ]
        trends = detect_trends(stories)
        self.assertIn("federal", trends)
        self.assertIn("reserve", trends)

    def test_stopwords_excluded(self):
        stories = [
            self._make_story_with_content("the economy is doing well"),
            self._make_story_with_content("the economy is struggling"),
            self._make_story_with_content("the economy shows signs"),
        ]
        trends = detect_trends(stories)
        # Stopwords like "the", "is", "and" must not appear
        for stopword in ("the", "is", "and", "are", "a", "an"):
            self.assertNotIn(stopword, trends)

    def test_minimum_appearances_threshold(self):
        # "unique" only appears in one story — should not be trending
        stories = [
            self._make_story_with_content("unique technology story here"),
            self._make_story_with_content("different topic entirely"),
            self._make_story_with_content("another separate subject"),
        ]
        trends = detect_trends(stories)
        self.assertNotIn("unique", trends)

    def test_empty_corpus_returns_empty(self):
        self.assertEqual(detect_trends([]), [])

    def test_respects_top_n_config(self):
        # Generate many distinct trending keywords and confirm output is capped
        keywords = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta"]
        stories = [
            self._make_story_with_content(" ".join(keywords))
            for _ in range(config.TREND_MIN_APPEARANCES)
        ]
        trends = detect_trends(stories)
        self.assertLessEqual(len(trends), config.TREND_TOP_N)


class TestGeographicOverrides(unittest.TestCase):
    """
    Tests for apply_geographic_overrides().

    The function has two paths: spaCy NER (primary) and signal matching (fallback).
    Tests that put signals in the summary exercise the fallback path directly,
    since spaCy only processes the title. Tests with signals in the title exercise
    both paths — whichever fires first wins.
    """

    def _make_override_story(self, title: str, summary: str = "",
                             region: str = "usa") -> dict:
        s = _make_story(title, region=region)
        s["summary"] = summary
        return s

    # --- Eligibility guard ---

    def test_canada_west_story_not_re_tagged(self):
        """Stories already tagged canada_west must be skipped."""
        story = self._make_override_story("Vancouver flooding", region="canada_west")
        result = apply_geographic_overrides([story])
        self.assertEqual(result[0]["region"], "canada_west")

    def test_canada_story_not_re_tagged(self):
        """Stories already tagged canada must be skipped."""
        story = self._make_override_story("Ottawa budget vote", region="canada")
        result = apply_geographic_overrides([story])
        self.assertEqual(result[0]["region"], "canada")

    # --- BC strong signals ---

    def test_bc_strong_signal_in_title_overrides_to_canada_west(self):
        """Unambiguous BC city in title → region becomes canada_west."""
        story = self._make_override_story("Vancouver mayor unveils housing plan")
        result = apply_geographic_overrides([story])
        self.assertEqual(result[0]["region"], "canada_west")

    def test_bc_strong_signal_in_summary_overrides_via_fallback(self):
        """BC strong signal in summary (not title) exercises the fallback path."""
        story = self._make_override_story(
            title="Wildfire update from western Canada",
            summary="Crews battling the blaze in the Okanagan region this morning"
        )
        result = apply_geographic_overrides([story])
        self.assertEqual(result[0]["region"], "canada_west")

    # --- BC weak signals ---

    def test_bc_weak_signal_with_geo_context_overrides(self):
        """Weak BC signal ('victoria') + geo_context word → canada_west."""
        story = self._make_override_story(
            title="Victoria residents protest new development",
            summary=""
        )
        result = apply_geographic_overrides([story])
        self.assertEqual(result[0]["region"], "canada_west")

    def test_bc_weak_signal_without_geo_context_does_not_override(self):
        """Weak BC signal alone, with no geo_context word, must not fire."""
        story = self._make_override_story(
            title="Victoria Beckham launches new fashion line",
            summary=""
        )
        result = apply_geographic_overrides([story])
        # Should remain usa — no geographic context to disambiguate
        self.assertNotEqual(result[0]["region"], "canada_west")

    def test_bc_weak_signal_suppressed_by_us_state(self):
        """Weak BC signal alongside a US state name must not trigger an override."""
        story = self._make_override_story(
            title="Richmond Virginia residents face flooding",
            summary=""
        )
        result = apply_geographic_overrides([story])
        self.assertNotEqual(result[0]["region"], "canada_west")

    # --- CA strong signals ---

    def test_ca_strong_signal_overrides_to_canada(self):
        """Unambiguous Canadian city in title → region becomes canada."""
        story = self._make_override_story("Toronto transit strike enters third day")
        result = apply_geographic_overrides([story])
        self.assertEqual(result[0]["region"], "canada")

    # --- BC takes priority over CA ---

    def test_bc_overrides_before_ca_when_both_signals_present(self):
        """When both BC and CA signals are present, BC wins (more specific)."""
        story = self._make_override_story(
            title="Vancouver and Toronto both announce housing plans"
        )
        result = apply_geographic_overrides([story])
        self.assertEqual(result[0]["region"], "canada_west")

    # --- World-tagged stories are eligible ---

    def test_world_tagged_story_is_eligible_for_override(self):
        """Stories tagged 'world' (not just 'usa') should also be eligible."""
        story = self._make_override_story(
            title="Calgary named top city for investment",
            region="world"
        )
        result = apply_geographic_overrides([story])
        self.assertEqual(result[0]["region"], "canada")


if __name__ == "__main__":
    unittest.main()
