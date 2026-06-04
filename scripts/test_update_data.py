"""Unit tests for the season config + update_data builders.

Run from the repo root (or anywhere):
    python scripts/test_update_data.py            # all tests
    python scripts/test_update_data.py TestRollover -v   # one class

We insert the scripts/ directory onto sys.path and import the modules as
top-level names — the same way update_data.py imports season_config — so there
is a single module identity regardless of how the suite is invoked.
"""
from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import season_config as sc  # noqa: E402
import update_data as ud  # noqa: E402


class TestSeasonConfig(unittest.TestCase):
    def test_load_missing_returns_defaults(self):
        cfg = sc.load_config(Path(tempfile.gettempdir()) / "does-not-exist-xyz.json")
        self.assertEqual(cfg["current"]["year"], sc.DEFAULTS["year"])
        self.assertEqual(cfg["archives"], [])

    def test_load_reads_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps({
                "current": {"year": 2026, "season_id": "14572",
                            "team_id": "498107", "team_name": "Team Fayetteville"},
                "archives": [],
            }), encoding="utf-8")
            cfg = sc.load_config(p)
            self.assertEqual(cfg["current"]["season_id"], "14572")

    def test_rollover_moves_current_to_archives(self):
        cfg = {"current": {"year": 2026, "season_id": "14572",
                           "team_id": "498107", "team_name": "Team Fayetteville"},
               "archives": []}
        new = sc.rollover(cfg, new_year=2027, season_id="20000")
        self.assertEqual(new["current"]["year"], 2027)
        self.assertEqual(new["current"]["season_id"], "20000")
        self.assertEqual(new["current"]["team_name"], "Team Fayetteville")  # carried
        self.assertIn({"year": 2026}, new["archives"])

    def test_rollover_rejects_same_year(self):
        cfg = {"current": {"year": 2026, "season_id": "14572",
                           "team_id": "498107", "team_name": "Team Fayetteville"},
               "archives": []}
        with self.assertRaises(ValueError):
            sc.rollover(cfg, new_year=2026, season_id="20000")

    def test_rollover_requires_season_id(self):
        cfg = {"current": {"year": 2026, "season_id": "14572",
                           "team_id": "498107", "team_name": "Team Fayetteville"},
               "archives": []}
        with self.assertRaises(ValueError):
            sc.rollover(cfg, new_year=2027, season_id="")


class TestSeasonMeta(unittest.TestCase):
    def test_season_meta_block_contains_year(self):
        block = ud.build_season_meta_block(2027)
        self.assertIn('"year": 2027', block)
        self.assertIn('id="season-meta"', block)
        self.assertNotIn("</script></script>", block)


class TestEmptyStates(unittest.TestCase):
    def test_standings_empty_shows_placeholder(self):
        out = ud.build_standings_block([])
        self.assertIn("hasn", out.lower())  # "hasn't started"
        self.assertIn("table-empty", out)

    def test_skaters_empty_shows_placeholder(self):
        out = ud.build_skater_block([], {})
        self.assertIn("table-empty", out)
        self.assertIn("roster", out.lower())

    def test_goalies_empty_shows_placeholder(self):
        out = ud.build_goalie_block([], {}, {})
        self.assertIn("table-empty", out)

    def test_schedule_empty_shows_placeholder(self):
        out = ud.build_schedule_list_block([], [])
        self.assertIn("sched-empty", out)
        self.assertIn("tba", out.lower())

    def test_matchup_preseason_when_no_games(self):
        # No upcoming, no played Foxes games, no champion -> preseason card.
        block = ud.build_matchup_block([], [], [], None)
        self.assertIn("Preseason", block)
        self.assertIn('data-season-state="preseason"', block)


class TestSeasonsNav(unittest.TestCase):
    def test_nav_lists_current_and_archives(self):
        cfg = {"current": {"year": 2027, "season_id": "x", "team_id": "",
                           "team_name": "Team Fayetteville"},
               "archives": [{"year": 2026}]}
        out = ud.build_seasons_nav_block(cfg)
        self.assertIn('href="/"', out)          # current season
        self.assertIn('href="/2026"', out)      # archive link
        self.assertIn("2026", out)

    def test_nav_no_archives_still_has_current(self):
        cfg = {"current": {"year": 2026, "season_id": "x", "team_id": "",
                           "team_name": "Team Fayetteville"},
               "archives": []}
        out = ud.build_seasons_nav_block(cfg)
        self.assertIn('href="/"', out)


if __name__ == "__main__":
    unittest.main()
