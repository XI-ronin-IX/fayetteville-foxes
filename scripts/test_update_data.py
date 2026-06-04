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


if __name__ == "__main__":
    unittest.main()
