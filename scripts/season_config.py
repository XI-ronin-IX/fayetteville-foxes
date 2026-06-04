"""Single source of truth for which season the site is currently showing.

Pure module: file IO + dict transforms only. No network, no HTML. The updater
(update_data.py) reads `current` from here to know which gamesheet season to
fetch, and the rollover command rewrites it.
"""
from __future__ import annotations
import json
from copy import deepcopy
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "season_config.json"

# Fallback identity if the config file is missing/unreadable — matches the
# values update_data.py shipped with before the config existed (2026 season).
DEFAULTS = {
    "year": 2026,
    "season_id": "14572",
    "team_id": "498107",
    "team_name": "Team Fayetteville",
}


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Return {"current": {...}, "archives": [...]}. Falls back to a 2026
    default if the file is missing or malformed (so nothing breaks pre-migration)."""
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(cfg, dict) or "current" not in cfg:
            raise ValueError("bad shape")
        cfg.setdefault("archives", [])
        return cfg
    except (OSError, ValueError, TypeError):
        return {"current": dict(DEFAULTS), "archives": []}


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    Path(path).write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def rollover(cfg: dict, new_year: int, season_id: str,
             team_id: str | None = None, team_name: str | None = None) -> dict:
    """Return a NEW config with the current season pushed into archives and a
    fresh current season set. Pure — does not write to disk. Raises ValueError
    on invalid input."""
    if not season_id:
        raise ValueError("season_id is required to start a new season")
    cur = cfg.get("current", {})
    if int(new_year) == int(cur.get("year", 0)):
        raise ValueError(f"new year {new_year} equals current year — refusing")
    out = deepcopy(cfg)
    out.setdefault("archives", [])
    out["archives"].append({"year": cur.get("year")})
    out["current"] = {
        "year": int(new_year),
        "season_id": str(season_id),
        "team_id": str(team_id) if team_id else cur.get("team_id", ""),
        "team_name": team_name or cur.get("team_name", DEFAULTS["team_name"]),
    }
    return out
