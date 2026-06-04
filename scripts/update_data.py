#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pull live league data and regenerate auto-managed sections of index.html.

Source: gamesheetstats.com — the public JSON API behind the Triangle High School
Hockey League's LeagueApps portal at trianglehshl.leagueapps.com.

Auto-managed regions in index.html are wrapped in:
    <!-- BEGIN auto:NAME -->...<!-- END auto:NAME -->

The script replaces only what's between those markers; everything else
(roster cards, coaches, hero copy, brand styling, etc.) is preserved.

Usage:
    python scripts/update_data.py             # full update
    python scripts/update_data.py --first-run # skip goalie SV% (preserve manual override)
    python scripts/update_data.py --dry-run   # show diff but don't write
    python scripts/update_data.py --check-played  # exit 0 only if Foxes played today

Exits non-zero on any API failure or schema mismatch. The CI workflow
treats non-zero exit as a failure (email notification).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

# Import the season-config module as a top-level name. update_data.py is run as
# `python scripts/update_data.py` (its own dir is already on sys.path) and also
# imported by the test suite (which inserts the same dir) — so this resolves
# consistently to a single module identity either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import season_config as _season_config  # noqa: E402


def H(s: Any) -> str:
    """Escape a value for HTML text content (default) or attribute use.
    Always escapes <, >, &, ", '. Defense-in-depth: any value pulled from the
    league API is run through this before being inserted into index.html.
    """
    return html.escape("" if s is None else str(s), quote=True)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

API_BASE = "https://gamesheetstats.com/api"

# Season identity is initialized from season_config.json at runtime via
# _init_season(); these module globals are the live values the fetchers read.
SEASON_ID = _season_config.DEFAULTS["season_id"]
FAYETTEVILLE_TEAM_ID = _season_config.DEFAULTS["team_id"]
FAYETTEVILLE_TEAM_NAME = _season_config.DEFAULTS["team_name"]
SEASON_YEAR = _season_config.DEFAULTS["year"]
ENDPOINTS: dict[str, str] = {}


def _build_endpoints(season_id: str) -> dict[str, str]:
    return {
        "standings": f"{API_BASE}/useStandings/getDivisionStandings/{season_id}",
        "scores":    f"{API_BASE}/useScoredGames/getSeasonScores/{season_id}",
        "schedule":  f"{API_BASE}/useSchedule/getSeasonSchedule/{season_id}",
        "skaters":   f"{API_BASE}/usePlayers/getPlayerStandings/{season_id}",
        "goalies":   f"{API_BASE}/useGoalies/getGoalieStandings/{season_id}",
    }


def _init_season(cfg: dict | None = None) -> dict:
    """Load the current-season identity into the module globals the fetchers
    use. Returns the full config dict (with archives)."""
    global SEASON_ID, FAYETTEVILLE_TEAM_ID, FAYETTEVILLE_TEAM_NAME, SEASON_YEAR, ENDPOINTS
    cfg = cfg or _season_config.load_config()
    cur = cfg["current"]
    SEASON_ID = str(cur["season_id"])
    FAYETTEVILLE_TEAM_ID = str(cur.get("team_id", ""))
    FAYETTEVILLE_TEAM_NAME = cur.get("team_name", _season_config.DEFAULTS["team_name"])
    SEASON_YEAR = int(cur["year"])
    ENDPOINTS = _build_endpoints(SEASON_ID)
    return cfg


# Initialize at import so module-level/early callers have a valid season.
_init_season()

ET = ZoneInfo("America/New_York")
USER_AGENT = "FayettevilleFoxes-Updater/1.0 (+https://github.com/XI-ronin-IX/fayetteville-foxes)"

# Map league team → static logo file path (relative to site root)
TEAM_LOGOS: dict[str, str] = {
    "Team Durham": "Durham",
    "Team Fayetteville": "brand_assets/Fayetteville_Fox_Logo_BLK",
    "Team South Apex": "South%20Apex",
    "Team Chapel Hill": "Chapel%20Hill",
    "Team Greater Neuse": "Greater%20Nuese",  # filename ships with the typo
    "Team Rolesville": "Rolesville",
    "Team Raleigh": "Raleigh",
    "Team Pittsboro": "Pittsboro",
}

# Map league team → display name in our HTML (drops the "Team " prefix)
TEAM_DISPLAY: dict[str, str] = {
    "Team Durham": "Durham",
    "Team Fayetteville": "Fayetteville",
    "Team South Apex": "South Apex",
    "Team Chapel Hill": "Chapel Hill",
    "Team Greater Neuse": "Greater Neuse",
    "Team Rolesville": "Rolesville",
    "Team Raleigh": "Raleigh",
    "Team Pittsboro": "Pittsboro",
}


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────


def fetch_json(url: str, params: dict[str, str] | None = None) -> Any:
    """GET a URL with the gamesheet filter[…] params and return parsed JSON."""
    if params:
        # Encode as filter[k]=v — gamesheetstats is picky about bracket form.
        from urllib.parse import quote

        bits = []
        for k, v in params.items():
            bits.append(f"filter%5B{quote(str(k))}%5D={quote(str(v))}")
        url = url + ("&" if "?" in url else "?") + "&".join(bits)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
    return json.loads(body)


# ─────────────────────────────────────────────────────────────────────────────
# Data fetchers
# ─────────────────────────────────────────────────────────────────────────────


def fetch_standings() -> list[dict]:
    """Return list of teams sorted by rank, with all stat columns we need."""
    raw = fetch_json(
        ENDPOINTS["standings"],
        {"gametype": "overall", "limit": 50, "offset": 0, "timeZoneOffset": -240},
    )
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("standings: unexpected response shape")
    div = raw[0]  # Combined Division
    td = div["tableData"]
    teams = []
    for i in range(len(td["ranks"])):
        teams.append(
            {
                "rank": td["ranks"][i],
                "team": td["teamTitles"][i]["title"],
                "team_id": td["teamIds"][i],
                "gp": td["gp"][i],
                "w": td["w"][i],
                "l": td["l"][i],
                "t": td["t"][i],
                "pts": td["pts"][i],
                "gf": td["gf"][i],
                "ga": td["ga"][i],
                "diff": td["diff"][i],
                "stk": td["stk"][i],  # e.g. "Won 5", "Lost 1", "Tied 2"
            }
        )
    teams.sort(key=lambda t: t["rank"])
    return teams


def fetch_played_games(limit: int = 100) -> list[dict]:
    """Return all played games, most-recent first."""
    raw = fetch_json(
        ENDPOINTS["scores"],
        {
            "gametype": "overall",
            "limit": limit,
            "offset": 0,
            "timeZoneOffset": -240,
        },
    )
    if not isinstance(raw, list):
        raise RuntimeError("scores: unexpected response shape")
    return raw


def fetch_upcoming_games(start: date | None = None, limit: int = 50) -> list[dict]:
    """Return upcoming games (status=scheduled), earliest first.

    Each game is returned as a flat dict with the date string lifted in.
    """
    if start is None:
        start = datetime.now(ET).date()
    raw = fetch_json(
        ENDPOINTS["schedule"],
        {
            "gametype": "overall",
            "limit": limit,
            "offset": 0,
            "start": start.isoformat(),
            "timeZoneOffset": -240,
        },
    )
    if not isinstance(raw, dict):
        raise RuntimeError("schedule: unexpected response shape")
    flat: list[dict] = []
    for key, days in raw.items():
        for day in days:
            for g in day.get("games", []):
                flat.append({"date_str": day.get("date", ""), **g})
    return flat


def fetch_skater_stats() -> list[dict]:
    """Return Fayetteville skaters with the columns we display."""
    raw = fetch_json(
        ENDPOINTS["skaters"],
        {
            "gametype": "overall",
            "sort": "-pts",
            "limit": 500,
            "offset": 0,
        },
    )
    td = raw["tableData"]
    out = []
    for i, name in enumerate(td["names"]):
        teams = td["teamNames"]["data"][i]
        if not any(t.get("title") == FAYETTEVILLE_TEAM_NAME for t in teams):
            continue
        out.append(
            {
                "first": name["firstName"],
                "last": name["lastName"],
                "display_name": titlecase_name(name["firstName"], name["lastName"]),
                "jersey": td["jersey"]["data"][i],
                "gp": td["gp"]["data"][i],
                "g": td["g"]["data"][i],
                "a": td["a"]["data"][i],
                "pts": td["pts"]["data"][i],
                "ppg": td["ppg"]["data"][i],
                "gwg": td["gwg"]["data"][i],
                "pim": td["pim"]["data"][i],
            }
        )
    # Sort by points desc, then by goals desc, then by jersey asc — stable.
    out.sort(key=lambda p: (-p["pts"], -p["g"], int_or(p["jersey"])))
    return out


def fetch_goalie_stats() -> list[dict]:
    """Return Fayetteville goalies with the columns we display."""
    raw = fetch_json(
        ENDPOINTS["goalies"],
        {"gametype": "overall", "limit": 100, "offset": 0},
    )
    td = raw["tableData"]
    out = []
    for i, name in enumerate(td["names"]):
        teams = td["teamNames"]["data"][i]
        if not any(t.get("title") == FAYETTEVILLE_TEAM_NAME for t in teams):
            continue
        out.append(
            {
                "first": name["firstName"],
                "last": name["lastName"],
                "display_name": titlecase_name(name["firstName"], name["lastName"]),
                "jersey": td["jersey"]["data"][i],
                "gp": td["gp"]["data"][i],
                "gs": td["gs"]["data"][i],
                "sa": td["sa"]["data"][i],
                "ga": td["ga"]["data"][i],
                "gaa": td["gaa"]["data"][i],
                "svpct": td["svpct"]["data"][i],
                "w": td["wins"]["data"][i],
                "l": td["losses"]["data"][i],
                "t": td["ties"]["data"][i],
            }
        )
    # Sort by GP descending so the starter shows first.
    out.sort(key=lambda g: -g["gp"])
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def titlecase_name(first: str, last: str) -> str:
    """ALL CAPS league name → Title Case for display. Strips stray whitespace
    that occasionally appears in the league source (e.g. "CALEB ").
    """
    return f"{first.strip().title()} {last.strip().title()}"


def slugify(name: str) -> str:
    """Match the JS `photoSlug` exactly so we can find the right photo."""
    s = unicodedata.normalize("NFD", name).lower()
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def int_or(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def fmt_diff(n: int) -> tuple[str, str]:
    """Return (display_text, css_class) for a goal-differential cell."""
    if n > 0:
        return f"+{n}", "diff-pos"
    if n < 0:
        return f"{n}", "diff-neg"
    return "0", "diff-pos"


def fmt_streak(s: str) -> tuple[str, str]:
    """'Won 5' → ('W5', 'stk-w'), 'Lost 1' → ('L1', 'stk-l'), 'Tied 2' → ('T2', 'stk-t')."""
    s = s.strip()
    m = re.match(r"^(Won|Lost|Tied)\s+(\d+)$", s)
    if not m:
        return s, "stk-t"
    word, n = m.group(1), m.group(2)
    if word == "Won":
        return f"W{n}", "stk-w"
    if word == "Lost":
        return f"L{n}", "stk-l"
    return f"T{n}", "stk-t"


def fmt_record(w: int, l: int, t: int) -> str:
    """W-L-T like '0W · 5L · 0T' for the matchup card."""
    return f"{w}W · {l}L · {t}T"


def parse_schedule_start(s: str | None) -> datetime | None:
    """Parse the API's `scheduleStartTime` (ISO 8601, may end in 'Z') to a
    timezone-aware datetime. Returns None on missing/malformed input.
    """
    if not s:
        return None
    try:
        # Python's fromisoformat in 3.11+ handles 'Z'; older needs a swap.
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def is_future(g: dict, now_utc: datetime) -> bool:
    """True if the game's scheduleStartTime is in the future (UTC).

    The league API leaves played games in the 'upcoming' endpoint until they
    manually mark them complete — so we need this filter to skip past games
    that were never moved out of upcoming.
    """
    sst = parse_schedule_start(g.get("scheduleStartTime"))
    return sst is not None and sst > now_utc


def normalize_venue(s: str) -> str:
    """Clean up venue strings from the API.

    The league sometimes records venues with duplicated trailing words like
    'Polar Ice Wake Forest Forest' — strip those to 'Polar Ice Wake Forest'.
    """
    if not s:
        return s
    # Collapse "Word Word" duplicates anywhere in the string.
    return re.sub(r"\b(\w+)\s+\1\b", r"\1", s).strip()


def parse_game_date(date_str: str) -> date | None:
    """Parse 'Apr 25, 2026' (game.date field) into a date object, or return
    None on malformed input. The league API occasionally produces funky
    strings; a single bad date shouldn't kill the entire daily refresh.
    """
    try:
        return datetime.strptime((date_str or "").strip(), "%b %d, %Y").date()
    except (ValueError, TypeError):
        return None


def fmt_short_date(d: date) -> str:
    """Apr 25 -> 'Apr 25' (no year). Used in schedule list and ticker."""
    return d.strftime("%b %d").replace(" 0", " ")


def fmt_dow_short(d: date) -> str:
    """date → 'Sat'."""
    return d.strftime("%a")


def fmt_iso_eastern(d: date, time_str: str) -> str:
    """Combine a date and a 12-hour 'h:mm AM' string into ISO 8601 with the
    correct DST-aware Eastern offset, e.g. '2026-05-10T07:45:00-04:00'.
    """
    t = datetime.strptime(time_str.strip(), "%I:%M %p").time()
    dt = datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=ET)
    return dt.isoformat(timespec="seconds")


def fmt_24h(time_str: str) -> str:
    """'7:45 AM' → '07:45'."""
    t = datetime.strptime(time_str.strip(), "%I:%M %p").time()
    return t.strftime("%H:%M")


def fmt_long_date(d: date) -> str:
    """date → 'Sun · May 10 · 2026' (matches matchup-meta date cell)."""
    return f"{d.strftime('%a')} · {d.strftime('%b')} {d.day} · {d.year}"


# ─────────────────────────────────────────────────────────────────────────────
# Block builders
# ─────────────────────────────────────────────────────────────────────────────


def build_standings_block(teams: list[dict]) -> str:
    rows: list[str] = []
    for t in teams:
        diff_text, diff_cls = fmt_diff(t["diff"])
        stk_text, stk_cls = fmt_streak(t["stk"])
        team_disp = TEAM_DISPLAY.get(t["team"], t["team"])
        logo = TEAM_LOGOS.get(t["team"])
        if logo:
            # Logo paths come from our internal map (TEAM_LOGOS) — trusted, no escape needed.
            logo_html = (
                f'<picture><source type="image/webp" srcset="{logo}.webp">'
                f'<img class="team-logo" src="{logo}.png" alt="{H(team_disp)}" loading="lazy"></picture>'
            )
        else:
            logo_html = ""
        row_class = "stats-row us" if t["team"] == FAYETTEVILLE_TEAM_NAME else "stats-row"
        rows.append(
            f'      <div class="{row_class}">\n'
            f'        <span class="jr">{H(t["rank"])}</span>'
            f'<span class="pn">{H(team_disp)}{logo_html}</span>'
            f'<span>{H(t["gp"])}</span>'
            f'<span>{H(t["w"])}</span>'
            f'<span>{H(t["l"])}</span>'
            f'<span>{H(t["t"])}</span>'
            f'<span class="pts">{H(t["pts"])}</span>'
            f'<span>{H(t["gf"])}</span>'
            f'<span>{H(t["ga"])}</span>'
            f'<span class="{diff_cls}">{H(diff_text)}</span>'
            f'<span class="{stk_cls}">{H(stk_text)}</span>\n'
            f'      </div>'
        )
    return "\n".join(rows)


def build_skater_block(players: list[dict], existing_jerseys: dict[str, str]) -> str:
    """Build the skaters table rows. existing_jerseys maps name → jersey
    (used as fallback for players whose jersey isn't in the API response)."""
    rows: list[str] = []
    for p in players:
        jersey = p["jersey"] or existing_jerseys.get(p["display_name"], "")
        rows.append(
            f'      <div class="stats-row">'
            f'<span class="jr">{H(jersey)}</span>'
            f'<span class="pn">{H(p["display_name"])}</span>'
            f'<span>{H(p["gp"])}</span>'
            f'<span>{H(p["g"])}</span>'
            f'<span>{H(p["a"])}</span>'
            f'<span class="pts">{H(p["pts"])}</span>'
            f'<span>{H(p["pim"])}</span>'
            f'<span>{H(p["ppg"])}</span>'
            f'<span>{H(p["gwg"])}</span>'
            f'</div>'
        )
    return "\n".join(rows)


def build_goalie_block(
    goalies: list[dict],
    svp_overrides: dict[str, str] | None,
    existing_jerseys: dict[str, str],
) -> str:
    rows: list[str] = []
    for g in goalies:
        # Roster jersey is authoritative; API can have it wrong for goalies.
        jersey = existing_jerseys.get(g["display_name"], g["jersey"] or "")
        svpct = svp_overrides.get(g["display_name"]) if svp_overrides else None
        if svpct is None:
            # Goalie API returns SV% as a decimal like 0.913 — render as ".913".
            sv_raw = g["svpct"]
            try:
                sv_f = float(sv_raw)
                if sv_f >= 1:
                    svpct = f"{sv_f / 1000:.3f}".lstrip("0") if sv_f > 100 else f"{sv_f:.3f}".lstrip("0")
                else:
                    svpct = f"{sv_f:.3f}".lstrip("0")
            except (TypeError, ValueError):
                svpct = ".000"
        rows.append(
            f'      <div class="stats-row">'
            f'<span class="jr">{H(jersey)}</span>'
            f'<span class="pn">{H(g["display_name"])}</span>'
            f'<span>{H(g["gp"])}</span>'
            f'<span>{H(g["gs"])}</span>'
            f'<span>{H(g["sa"])}</span>'
            f'<span>{H(g["ga"])}</span>'
            f'<span class="pts">{H(g["gaa"])}</span>'
            f'<span>{H(svpct)}</span>'
            f'<span>{H(g["w"])}</span>'
            f'<span>{H(g["l"])}</span>'
            f'<span>{H(g["t"])}</span>'
            f'</div>'
        )
    return "\n".join(rows)


def build_schedule_list_block(
    upcoming: list[dict], played: list[dict]
) -> str:
    """Build the .sched-row rows: [featured, upcoming…, played (newest first)]."""
    now_utc = datetime.now(timezone.utc)
    foxes_upcoming = [
        g for g in upcoming
        if FAYETTEVILLE_TEAM_NAME in (g["homeTeam"]["name"], g["visitorTeam"]["name"])
        and is_future(g, now_utc)
    ]
    foxes_upcoming.sort(key=lambda g: g.get("scheduleStartTime", ""))
    foxes_played = [
        g for g in played
        if FAYETTEVILLE_TEAM_NAME in (
            g["game"]["homeTeam"]["name"],
            g["game"]["visitorTeam"]["name"],
        )
        and parse_game_date(g["game"]["date"]) is not None
    ]
    foxes_played.sort(
        key=lambda g: parse_game_date(g["game"]["date"]) or date.min,
        reverse=True,
    )

    rows: list[str] = []
    for idx, g in enumerate(foxes_upcoming):
        is_home = g["homeTeam"]["name"] == FAYETTEVILLE_TEAM_NAME
        opp = TEAM_DISPLAY.get(
            g["visitorTeam" if is_home else "homeTeam"]["name"],
            g["visitorTeam" if is_home else "homeTeam"]["name"],
        )
        d = parse_game_date(g["date"])
        if d is None:
            continue  # skip rows with unparseable dates
        time_24 = fmt_24h(g["time"])
        prefix = "vs" if is_home else "@"
        venue = normalize_venue(g.get("location", "TBD"))
        if idx == 0:
            opp_html = f'<em>{H(prefix)} {H(opp)}</em>'
            badge = '<span class="mono hot">Next Up →</span>'
            row_classes = "sched-row featured upcoming"
        else:
            opp_html = f'{H(prefix)} {H(opp)}'
            badge = '<span class="mono dim">Upcoming</span>'
            row_classes = "sched-row upcoming"
        rows.append(
            f'      <div class="{row_classes}">\n'
            f'        <div class="date"><span class="m">{H(fmt_short_date(d))}</span>{H(fmt_dow_short(d))}</div>\n'
            f'        <div class="ha">{"Home" if is_home else "Away"}</div>\n'
            f'        <div class="opp">{opp_html}<span class="sub">{H(venue)}</span></div>\n'
            f'        <div class="time">{H(time_24)} ET</div>\n'
            f'        <div class="result">{badge}</div>\n'
            f'      </div>'
        )

    for g in foxes_played:
        gm = g["game"]
        is_home = gm["homeTeam"]["name"] == FAYETTEVILLE_TEAM_NAME
        opp_team = gm["visitorTeam"] if is_home else gm["homeTeam"]
        opp = TEAM_DISPLAY.get(opp_team["name"], opp_team["name"])
        venue = normalize_venue(gm.get("location", "TBD"))
        d = parse_game_date(gm["date"])
        if d is None:
            continue  # unparseable date — skip the row rather than crash
        prefix = "vs" if is_home else "@"
        # Goal counts run through int_or so they're guaranteed to be safe ints,
        # but escaping costs nothing and protects future readers.
        fox_goals = int_or(gm["finalScore"]["homeGoals" if is_home else "visitorGoals"])
        opp_goals = int_or(gm["finalScore"]["visitorGoals" if is_home else "homeGoals"])
        if fox_goals > opp_goals:
            result_html = f'<div class="result w">W {H(fox_goals)}–{H(opp_goals)}</div>'
        elif fox_goals < opp_goals:
            result_html = f'<div class="result l">L {H(fox_goals)}–{H(opp_goals)}</div>'
        else:
            result_html = (
                f'<div class="result"><span style="color:var(--smoke);font-weight:700;">'
                f'T {H(fox_goals)}–{H(opp_goals)}</span></div>'
            )
        rows.append(
            f'      <div class="sched-row">\n'
            f'        <div class="date"><span class="m">{H(fmt_short_date(d))}</span>{H(fmt_dow_short(d))}</div>\n'
            f'        <div class="ha">{"Home" if is_home else "Away"}</div>\n'
            f'        <div class="opp">{H(prefix)} {H(opp)}<span class="sub">{H(venue)}</span></div>\n'
            f'        <div class="time">—</div>\n'
            f'        {result_html}\n'
            f'      </div>'
        )

    return "\n".join(rows)


def build_ticker_block(played: list[dict], n: int = 6) -> str:
    """Build the .ticker-track inner content (items + dots, duplicated for loop).

    Skips games with unparseable dates and games missing a finalScore — both
    are sometimes present in the API for upcoming games that haven't been
    scored yet but accidentally appear in the played endpoint.
    """
    foxes_played = [
        g for g in played
        if FAYETTEVILLE_TEAM_NAME in (
            g["game"]["homeTeam"]["name"],
            g["game"]["visitorTeam"]["name"],
        )
        and parse_game_date(g["game"]["date"]) is not None
        and g["game"].get("finalScore") is not None
    ]
    foxes_played.sort(key=lambda g: parse_game_date(g["game"]["date"]) or date.min, reverse=True)
    foxes_played = foxes_played[:n]

    items: list[str] = []
    for g in foxes_played:
        gm = g["game"]
        is_home = gm["homeTeam"]["name"] == FAYETTEVILLE_TEAM_NAME
        opp_team = gm["visitorTeam"] if is_home else gm["homeTeam"]
        opp_name = TEAM_DISPLAY.get(opp_team["name"], opp_team["name"])
        fox_goals = int_or(gm["finalScore"]["homeGoals" if is_home else "visitorGoals"])
        opp_goals = int_or(gm["finalScore"]["visitorGoals" if is_home else "homeGoals"])
        d = parse_game_date(gm["date"])
        if d is None:
            continue
        if fox_goals > opp_goals:
            indicator_class = "w"
            indicator = "W"
        elif fox_goals < opp_goals:
            indicator_class = "l"
            indicator = "L"
        else:
            indicator_class = "t"
            indicator = "T"
        items.append(
            f'    <span class="ticker-item">'
            f'<span class="{indicator_class}">{H(indicator)}</span> '
            f'<span class="team">Foxes</span> {H(fox_goals)} '
            f'<span class="mono dim">—</span> {H(opp_goals)} '
            f'<span class="team">{H(opp_name)}</span> '
            f'<span class="mono dim">{H(fmt_short_date(d))}</span></span>\n'
            f'    <span class="ticker-dot"></span>'
        )

    if not items:
        return ""
    half = "\n".join(items)
    # Duplicate for seamless loop (matches existing structure).
    return half + "\n    <!-- duplicate for seamless loop -->\n" + half


def build_tbd_matchup_block(standings: list[dict]) -> str:
    """Placeholder matchup card for when there are no upcoming Foxes games
    in the API (e.g. between the end of the regular season and the league
    publishing the playoff schedule). Keeps the same DOM shape so the JS
    sync helpers don't error, but signals 'TBD' via the data attributes
    and substitutes copy in the visible cells.

    JS readers should treat any value of 'TBD' in data-meta as 'no game
    scheduled' and adjust their UI accordingly.
    """
    by_team = {t["team"]: t for t in standings}
    fox_st = by_team.get(FAYETTEVILLE_TEAM_NAME, {"w": 0, "l": 0, "t": 0, "rank": "?"})
    fox_record = fmt_record(fox_st["w"], fox_st["l"], fox_st["t"])
    return (
        '      <div class="matchup-side home">\n'
        '        <span class="crest" style="transform:none;background:var(--black);"><picture>\n'
        '          <source type="image/webp" srcset="brand_assets/Fayetteville_Fox_Logo_BLK.webp">\n'
        '          <img src="brand_assets/Fayetteville_Fox_Logo_BLK.png" alt="Fayetteville Foxes" '
        'loading="lazy" />\n'
        '        </picture></span>\n'
        '        <div class="place mono">Playoffs ahead</div>\n'
        '        <div class="team-name">Fayetteville <em>Foxes</em></div>\n'
        f'        <div class="record" data-foxes-record="dotted">{H(fox_record)}</div>\n'
        '      </div>\n'
        '\n'
        '      <div class="matchup-vs">\n'
        '        <div class="vs-big">VS</div>\n'
        '        <div class="kick">Playoffs · Schedule pending</div>\n'
        '        <div class="count" id="countdown" aria-live="off">\n'
        '          <span class="seg"><span class="num" data-cd="d">—</span>'
        '<span class="u">: Days</span></span>\n'
        '          <span class="seg"><span class="num" data-cd="h">—</span>'
        '<span class="u">: Hours</span></span>\n'
        '          <span class="seg"><span class="num" data-cd="m">—</span>'
        '<span class="u">: Mins</span></span>\n'
        '          <span class="seg"><span class="num" data-cd="s">—</span>'
        '<span class="u">: Secs</span></span>\n'
        '        </div>\n'
        '      </div>\n'
        '\n'
        '      <div class="matchup-side">\n'
        '        <span class="crest" style="transform:none;background:var(--black);"></span>\n'
        '        <div class="place mono">Awaiting bracket</div>\n'
        '        <div class="team-name">TBD</div>\n'
        '        <div class="record">—</div>\n'
        '      </div>\n'
        '\n'
        '      <div class="matchup-meta" style="grid-column:1 / -1;">\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Date</span>\n'
        '          <span class="val" data-meta="date">TBD</span>\n'
        '        </div>\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Puck Drop</span>\n'
        '          <span class="val" data-puckdrop-iso="">'
        '<em data-meta="puckdrop-time">TBD</em></span>\n'
        '        </div>\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Venue</span>\n'
        '          <span class="val" data-meta="venue">TBD</span>\n'
        '        </div>\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Broadcast</span>\n'
        '          <span class="val"><a href="https://www.livebarn.com/" target="_blank" '
        'rel="noopener noreferrer" style="color:var(--orange);'
        'border-bottom:1px solid rgba(255,85,0,0.4);padding-bottom:1px;">LiveBarn ↗</a></span>\n'
        '        </div>\n'
        '      </div>'
    )


def _season_year(played: list[dict]) -> int:
    """Best-effort season year — the latest 4-digit year seen in played-game
    date strings ("May 29, 2026"), falling back to the current ET year."""
    years: list[int] = []
    for g in played:
        d = g.get("game", {}).get("date") or ""
        m = re.search(r"\b(\d{4})\b", d)
        if m:
            years.append(int(m.group(1)))
    return max(years) if years else datetime.now(ET).year


def build_season_complete_matchup_block(
    standings: list[dict], champion: str, year: int
) -> str:
    """Season-over matchup card. Shown once the championship is decided and no
    future Foxes games remain — replaces the 'next game' card with a season
    wrap-up (final record + standing) and a nod to the league champion.

    The `data-season-state="complete"` marker on the home side is the sentinel
    the page JS keys on to swap the 'Next Puck Drop' banner/title into a
    season-complete holding state instead of the playoffs-pending one.
    """
    by_team = {t["team"]: t for t in standings}
    fox_st = by_team.get(FAYETTEVILLE_TEAM_NAME, {"w": 0, "l": 0, "t": 0, "rank": "?"})
    fox_record = fmt_record(fox_st["w"], fox_st["l"], fox_st["t"])
    standing = f"#{fox_st['rank']} of {len(standings)}" if standings else "—"
    year_short = f"’{str(year)[-2:]}"  # ’26

    champ_full = "Team " + champion
    champ_logo = TEAM_LOGOS.get(champ_full)
    if champ_logo:
        # Logo path comes from our internal TEAM_LOGOS map — trusted, no escape needed.
        champ_logo_block = (
            '        <span class="crest" style="transform:none;background:var(--black);"><picture>\n'
            f'          <source type="image/webp" srcset="{champ_logo}.webp">\n'
            f'          <img src="{champ_logo}.png" alt="{H(champion)}" loading="lazy" />\n'
            '        </picture></span>'
        )
    else:
        champ_logo_block = '        <span class="crest" style="transform:none;background:var(--black);"></span>'

    return (
        f'      <div class="matchup-side home" data-season-state="complete" data-season-year="{year}">\n'
        '        <span class="crest" style="transform:none;background:var(--black);"><picture>\n'
        '          <source type="image/webp" srcset="brand_assets/Fayetteville_Fox_Logo_BLK.webp">\n'
        '          <img src="brand_assets/Fayetteville_Fox_Logo_BLK.png" alt="Fayetteville Foxes" '
        'loading="lazy" />\n'
        '        </picture></span>\n'
        f'        <div class="place mono">{year} Season</div>\n'
        '        <div class="team-name">Fayetteville <em>Foxes</em></div>\n'
        f'        <div class="record" data-foxes-record="dotted">{H(fox_record)}</div>\n'
        '      </div>\n'
        '\n'
        '      <div class="matchup-vs">\n'
        f'        <div class="vs-big">{year_short}</div>\n'
        '        <div class="kick">Season Complete</div>\n'
        '        <div class="season-note mono">That’s a wrap.<br>See you next year.</div>\n'
        '      </div>\n'
        '\n'
        '      <div class="matchup-side champ">\n'
        f'{champ_logo_block}\n'
        f'        <div class="place mono">{year} Champions</div>\n'
        f'        <div class="team-name">{H(champion)}</div>\n'
        '        <div class="record"><span class="champ-tag mono">\U0001f3c6 League Champions</span></div>\n'
        '      </div>\n'
        '\n'
        '      <div class="matchup-meta" style="grid-column:1 / -1;">\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Final Record</span>\n'
        f'          <span class="val">{H(fox_record)}</span>\n'
        '        </div>\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Final Standing</span>\n'
        f'          <span class="val">{H(standing)}</span>\n'
        '        </div>\n'
        '        <div class="cell">\n'
        '          <span class="label mono">League Champion</span>\n'
        f'          <span class="val"><em>{H(champion)}</em></span>\n'
        '        </div>\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Season Replays</span>\n'
        '          <span class="val"><a href="https://www.livebarn.com/" target="_blank" '
        'rel="noopener noreferrer" style="color:var(--orange);'
        'border-bottom:1px solid rgba(255,85,0,0.4);padding-bottom:1px;">LiveBarn ↗</a></span>\n'
        '        </div>\n'
        '      </div>'
    )


# Playoff-game keywords used to detect non-regular-season games in the API's
# `game.type` field. The league hasn't published playoff types yet so this is
# a best-effort match against common naming conventions.
PLAYOFF_TYPE_KEYWORDS = (
    "playoff", "semifinal", "semi-final", "semi final",
    "quarterfinal", "championship", "final",
)


def looks_like_playoff(game_type: str | None) -> bool:
    if not game_type:
        return False
    lower = game_type.lower()
    return any(kw in lower for kw in PLAYOFF_TYPE_KEYWORDS)


def build_matchup_block(
    upcoming: list[dict], standings: list[dict], played: list[dict],
    champion: str | None = None,
) -> str | None:
    """Build the matchup hero card (Fayetteville vs next opponent).

    Filters out past-but-still-in-upcoming-endpoint games — the league leaves
    completed games in the 'upcoming' feed until manually marked, so without
    this filter the matchup card would stay stuck on yesterday's game forever.

    When no future Foxes games exist, returns one of two placeholders:
      • If a league champion has been decided (`champion` set), the season is
        over → a 'Season Complete' wrap-up card.
      • Otherwise (e.g. between regular-season end and the playoff schedule
        being published) → a 'Playoffs ahead — TBD' holding card.
    """
    now_utc = datetime.now(timezone.utc)
    foxes_upcoming = [
        g for g in upcoming
        if FAYETTEVILLE_TEAM_NAME in (g["homeTeam"]["name"], g["visitorTeam"]["name"])
        and is_future(g, now_utc)
    ]
    if not foxes_upcoming:
        if champion:
            return build_season_complete_matchup_block(
                standings, champion, _season_year(played)
            )
        return build_tbd_matchup_block(standings)
    foxes_upcoming.sort(key=lambda g: g.get("scheduleStartTime", ""))
    g = foxes_upcoming[0]
    # Foxes-specific game number: how many games will this be for them?
    foxes_played_count = sum(
        1 for pg in played
        if FAYETTEVILLE_TEAM_NAME in (
            pg["game"]["homeTeam"]["name"],
            pg["game"]["visitorTeam"]["name"],
        )
    )
    foxes_game_num = foxes_played_count + 1

    is_home = g["homeTeam"]["name"] == FAYETTEVILLE_TEAM_NAME
    opp_name_full = g["visitorTeam"]["name"] if is_home else g["homeTeam"]["name"]
    opp_disp = TEAM_DISPLAY.get(opp_name_full, opp_name_full)
    opp_logo = TEAM_LOGOS.get(opp_name_full)
    venue = normalize_venue(g.get("location", "TBD"))
    fox_place = ("Home" if is_home else "Away · @") + (f" · {venue}" if is_home else f" {venue}")
    opp_place = ("Home" if not is_home else "Away · @") + (f" · {venue}" if not is_home else f" {venue}")

    d = parse_game_date(g["date"])
    if d is None:
        return None  # unparseable date — leave the existing matchup block alone
    iso_target = fmt_iso_eastern(d, g["time"])
    drop_24 = fmt_24h(g["time"])
    long_date = fmt_long_date(d)

    # Records from standings (already-fetched)
    by_team = {t["team"]: t for t in standings}
    fox_st = by_team.get(FAYETTEVILLE_TEAM_NAME, {"w": 0, "l": 0, "t": 0})
    opp_st = by_team.get(opp_name_full, {"w": 0, "l": 0, "t": 0})
    fox_record = fmt_record(fox_st["w"], fox_st["l"], fox_st["t"])
    opp_record = fmt_record(opp_st["w"], opp_st["l"], opp_st["t"])

    # Game label — use playoff/championship/semifinal naming when the API
    # game.type signals it, otherwise default to "Regular Season · Game NN".
    game_type = g.get("type") or ""
    if looks_like_playoff(game_type):
        # Capitalize and pass through the league's label (e.g. "Semifinal",
        # "Championship", "Playoffs").
        kick_label = game_type.strip().title()
    else:
        kick_label = f"Regular Season · Game {foxes_game_num:02d}"

    # Countdown values (initial fallback before JS tick)
    now = datetime.now(ET)
    target = datetime.fromisoformat(iso_target)
    diff = target - now
    days = max(0, diff.days)
    hours = max(0, (diff.seconds // 3600))
    mins = max(0, ((diff.seconds % 3600) // 60))

    if opp_logo:
        # opp_logo path comes from our internal TEAM_LOGOS map — trusted.
        opp_logo_block = (
            f'        <span class="crest" style="transform:none;background:var(--black);">\n'
            f'          <picture>\n'
            f'            <source type="image/webp" srcset="{opp_logo}.webp">\n'
            f'            <img src="{opp_logo}.png" alt="{H(opp_disp)}" loading="lazy" />\n'
            f'          </picture>\n'
            f'        </span>'
        )
    else:
        opp_logo_block = '        <span class="crest" style="transform:none;background:var(--black);"></span>'

    return (
        '      <div class="matchup-side home">\n'
        '        <span class="crest" style="transform:none;background:var(--black);"><picture>\n'
        '          <source type="image/webp" srcset="brand_assets/Fayetteville_Fox_Logo_BLK.webp">\n'
        '          <img src="brand_assets/Fayetteville_Fox_Logo_BLK.png" alt="Fayetteville Foxes" '
        'loading="lazy" />\n'
        '        </picture></span>\n'
        f'        <div class="place mono">{H(fox_place)}</div>\n'
        '        <div class="team-name">Fayetteville <em>Foxes</em></div>\n'
        f'        <div class="record" data-foxes-record="dotted">{H(fox_record)}</div>\n'
        '      </div>\n'
        '\n'
        '      <div class="matchup-vs">\n'
        '        <div class="vs-big">VS</div>\n'
        f'        <div class="kick">{H(kick_label)}</div>\n'
        '        <div class="count" id="countdown" aria-live="off">\n'
        f'          <span class="seg"><span class="num" data-cd="d">{days:02d}</span>'
        '<span class="u">: Days</span></span>\n'
        f'          <span class="seg"><span class="num" data-cd="h">{hours:02d}</span>'
        '<span class="u">: Hours</span></span>\n'
        f'          <span class="seg"><span class="num" data-cd="m">{mins:02d}</span>'
        '<span class="u">: Mins</span></span>\n'
        f'          <span class="seg"><span class="num" data-cd="s">00</span>'
        '<span class="u">: Secs</span></span>\n'
        '        </div>\n'
        '      </div>\n'
        '\n'
        '      <div class="matchup-side">\n'
        f'{opp_logo_block}\n'
        f'        <div class="place mono">{H(opp_place)}</div>\n'
        f'        <div class="team-name">{H(opp_disp)}</div>\n'
        f'        <div class="record">{H(opp_record)}</div>\n'
        '      </div>\n'
        '\n'
        '      <div class="matchup-meta" style="grid-column:1 / -1;">\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Date</span>\n'
        f'          <span class="val" data-meta="date">{H(long_date)}</span>\n'
        '        </div>\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Puck Drop</span>\n'
        f'          <span class="val" data-puckdrop-iso="{H(iso_target)}">'
        f'<em data-meta="puckdrop-time">{H(drop_24)}</em> Eastern</span>\n'
        '        </div>\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Venue</span>\n'
        f'          <span class="val" data-meta="venue">{H(venue)}</span>\n'
        '        </div>\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Broadcast</span>\n'
        '          <span class="val"><a href="https://www.livebarn.com/" target="_blank" '
        'rel="noopener noreferrer" style="color:var(--orange);'
        'border-bottom:1px solid rgba(255,85,0,0.4);padding-bottom:1px;">LiveBarn ↗</a></span>\n'
        '        </div>\n'
        '      </div>'  # close .matchup-meta
    )


# ─────────────────────────────────────────────────────────────────────────────
# HTML region replacement
# ─────────────────────────────────────────────────────────────────────────────


REGION_RE_TEMPLATE = (
    r"(<!--\s*BEGIN auto:{name}\s*-->)\n"
    r"(.*?)"
    r"\n([ \t]*)(<!--\s*END auto:{name}\s*-->)"
)


def replace_region(html: str, name: str, new_inner: str) -> str:
    """Replace inner content between BEGIN/END markers, preserving the
    original indentation of the END marker so we don't drift.

    Defensively requires *exactly one* BEGIN/END pair per region. If a marker
    was lost in a bad merge or a region was accidentally duplicated, this
    raises rather than silently corrupting hand-edited content outside the
    intended span.
    """
    pattern = re.compile(REGION_RE_TEMPLATE.format(name=re.escape(name)), re.DOTALL)
    matches = list(pattern.finditer(html))
    if len(matches) != 1:
        # Look for the markers individually to give a useful error.
        begin_count = len(re.findall(rf"<!--\s*BEGIN auto:{re.escape(name)}\s*-->", html))
        end_count = len(re.findall(rf"<!--\s*END auto:{re.escape(name)}\s*-->", html))
        raise RuntimeError(
            f"region {name!r}: expected exactly 1 BEGIN/END pair, "
            f"found {begin_count} BEGIN marker(s) and {end_count} END marker(s) "
            f"that pair into {len(matches)} region(s). "
            f"Refusing to write to avoid corrupting hand-edited content."
        )
    def _sub(m: re.Match) -> str:
        begin, _inner, end_indent, end = m.group(1), m.group(2), m.group(3), m.group(4)
        return f"{begin}\n{new_inner}\n{end_indent}{end}"
    new_html, count = pattern.subn(_sub, html, count=1)
    return new_html


def _extract_region_body(html: str, name: str) -> str:
    """Return just the content between <!-- BEGIN auto:NAME --> and the matching
    END marker, or '' if the region isn't found. Used by extract_existing_*
    helpers so they only look inside the right table (no cross-contamination
    between skaters, goalies, and standings regions)."""
    m = re.search(
        rf"<!--\s*BEGIN auto:{re.escape(name)}\s*-->(.*?)<!--\s*END auto:{re.escape(name)}\s*-->",
        html,
        re.DOTALL,
    )
    return m.group(1) if m else ""


def extract_existing_jerseys(html: str) -> dict[str, str]:
    """Pull `name -> jersey` from the existing skaters AND goalies tables so we
    can fall back if the API jersey field is missing or wrong for a player.
    Scoped to those auto regions so team-rank cells in standings ('Fayetteville'
    with rank 2, etc.) don't pollute the mapping.

    Fails loudly if a non-empty region produces zero matches — that means the
    row layout has drifted and the silent fallback would otherwise lose
    jersey-number overrides on the next bot run.
    """
    out: dict[str, str] = {}
    pattern = re.compile(
        r'<span class="jr">(\d+)</span><span class="pn">([^<]+)</span>'
    )
    for region in ("skaters", "goalies"):
        body = _extract_region_body(html, region)
        if not body.strip():
            continue
        matches = pattern.findall(body)
        if not matches and "stats-row" in body:
            raise RuntimeError(
                f"extract_existing_jerseys: {region!r} region has rows but "
                f"the jersey/name regex matched 0 of them. Row layout has "
                f"likely changed; refusing to write to avoid wiping jersey "
                f"overrides."
            )
        for jr, name in matches:
            out[name.strip()] = jr
    return out


def extract_existing_playoff_results(html: str) -> dict:
    """Read the current playoff-results JSON from its auto-region.

    Returns {} if the region is missing or the JSON is malformed; that's
    a safe default since the merge step below will then fall back to
    auto-detected values (or null) for every field.
    """
    body = _extract_region_body(html, "playoff-results")
    if not body:
        return {}
    m = re.search(
        r'<script[^>]*id="playoff-results"[^>]*>(.*?)</script>',
        body,
        re.DOTALL,
    )
    if not m:
        return {}
    try:
        return json.loads(m.group(1).strip())
    except (ValueError, TypeError):
        return {}


def detect_playoff_results(
    standings: list[dict], played: list[dict]
) -> dict[str, str | None]:
    """Inspect played games for playoffs (by `game.type` keywords) and figure
    out winners. Identifies which slot a game belongs to by the team-rank
    pair of its two participants: {#1, #4} = semi 1; {#2, #3} = semi 2;
    anything else (only top-4 pairing matters, so this is championship in
    practice) goes to the champion slot.

    Returns a dict with semi1Winner / semi2Winner / champion fields (each a
    display name or None) plus a `scores` dict mapping each decided slot
    ("semi1" / "semi2" / "championship") to {teamDisplayName: goals} for both
    participants. Slots with no decided game are absent from `scores`.
    """
    rank_by_team_full = {t["team"]: t["rank"] for t in standings}
    out: dict = {
        "semi1Winner": None,
        "semi2Winner": None,
        "champion":    None,
        "scores":      {},
    }

    # Sort played games chronologically so later games (championship) win
    # over earlier ones in any edge-case overlap.
    sorted_played = sorted(
        played,
        key=lambda g: g.get("game", {}).get("scheduleStartTime") or "",
    )

    for g in sorted_played:
        gm = g.get("game", {})
        if not looks_like_playoff(gm.get("type")):
            continue
        final = gm.get("finalScore")
        if not final:
            continue
        home_name = gm.get("homeTeam", {}).get("name")
        visitor_name = gm.get("visitorTeam", {}).get("name")
        home_goals = int_or(final.get("homeGoals"))
        visitor_goals = int_or(final.get("visitorGoals"))
        if home_goals == visitor_goals:
            continue  # tie — no winner; bracket can't advance
        winner_full = home_name if home_goals > visitor_goals else visitor_name
        winner_disp = TEAM_DISPLAY.get(winner_full, winner_full)

        # Identify the bracket slot via team-rank pair (regular-season seeds).
        home_rank = rank_by_team_full.get(home_name)
        visitor_rank = rank_by_team_full.get(visitor_name)
        if home_rank is None or visitor_rank is None:
            continue
        pair = tuple(sorted([home_rank, visitor_rank]))
        if pair == (1, 4):
            slot, winner_key = "semi1", "semi1Winner"
        elif pair == (2, 3):
            slot, winner_key = "semi2", "semi2Winner"
        else:
            # Any other pairing of playoff teams is the championship.
            slot, winner_key = "championship", "champion"
        out[winner_key] = winner_disp
        out["scores"][slot] = {
            TEAM_DISPLAY.get(home_name, home_name): home_goals,
            TEAM_DISPLAY.get(visitor_name, visitor_name): visitor_goals,
        }

    return out


def build_season_meta_block(year: int) -> str:
    """Embedded JSON consumed by the page's year-stamper JS. Same JSON-in-HTML
    escaping discipline as build_playoff_results_block."""
    payload = json.dumps({"year": int(year)}).replace("</", "<\\/")
    return (
        '    <script type="application/json" id="season-meta">\n'
        f'{payload}\n'
        '    </script>'
    )


def build_playoff_results_block(merged: dict) -> str:
    """Generate the <script type="application/json"> block for the auto-region.

    Always emits the canonical fields in stable order for clean diffs;
    other keys in `merged` are dropped. `scores` (per-slot game scores) is
    only emitted when non-empty so the common no-playoffs-yet diff stays tiny.

    The replace("</", "<\\/") step is the standard JSON-in-HTML defense:
    json.dumps does not escape "/", so a malicious string containing
    "</script>" would otherwise close the tag prematurely. Encoding the slash
    as "<\\/script>" is still valid JSON (JSON treats "\\/" as "/") but the
    HTML parser no longer sees a closing tag.
    """
    canonical = {
        "semi1Winner": merged.get("semi1Winner"),
        "semi2Winner": merged.get("semi2Winner"),
        "champion":    merged.get("champion"),
    }
    scores = merged.get("scores") or {}
    if scores:
        canonical["scores"] = scores
    payload = json.dumps(canonical).replace("</", "<\\/")
    return (
        '    <script type="application/json" id="playoff-results">\n'
        f'{payload}\n'
        '    </script>'
    )


def extract_existing_svpct(html: str) -> dict[str, str]:
    """Pull `name -> svpct` from the existing goalies table. Scoped to the
    goalies auto-region so it can't match a row in standings/skaters even if
    those ever change column counts in the future.

    Goalie row layout: jr, pn, GP, GS, SA, GA, GAA(.pts), SV%, W, L, T

    Fails loudly if the goalies region has rows but the regex returns no
    matches — without this guard the next bot run would silently wipe the
    user's manually-maintained SV% values.
    """
    out: dict[str, str] = {}
    body = _extract_region_body(html, "goalies")
    if not body.strip():
        return out
    pattern = re.compile(
        r'<span class="jr">\d+</span><span class="pn">([^<]+)</span>'
        r'(?:<span>[^<]*</span>){4}'        # GP, GS, SA, GA
        r'<span class="pts">[^<]*</span>'    # GAA
        r'<span>([.\d]+)</span>'             # SV%
    )
    matches = pattern.findall(body)
    if not matches and "stats-row" in body:
        raise RuntimeError(
            "extract_existing_svpct: goalies region has rows but the SV% "
            "regex matched 0 of them. Row layout has likely changed; "
            "refusing to write to avoid wiping manual SV% values."
        )
    for name, svpct in matches:
        out[name.strip()] = svpct
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def did_foxes_play_today(played: list[dict]) -> bool:
    today = datetime.now(ET).date()
    for g in played:
        gm = g["game"]
        if FAYETTEVILLE_TEAM_NAME not in (gm["homeTeam"]["name"], gm["visitorTeam"]["name"]):
            continue
        if parse_game_date(gm["date"]) == today:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 stdout on Windows so we can print arrows, em-dashes, etc.
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    p = argparse.ArgumentParser(description="Update auto-managed sections of index.html")
    p.add_argument("--first-run", action="store_true",
                   help="(Deprecated) No-op flag, kept for backwards compat. "
                        "Manual goalie save percentage is now always preserved.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print diff but don't write")
    p.add_argument("--check-played", action="store_true",
                   help="Probe-only mode for the cron gate. Exit codes: "
                        "0 = a Foxes game was played today (proceed with full update); "
                        "1 = no Foxes game today (skip full update); "
                        "2 = API error or other failure (treat as workflow failure)")
    p.add_argument("--index", default="index.html",
                   help="Path to index.html (default: ./index.html)")
    args = p.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    index_path = (repo_root / args.index).resolve()
    if not index_path.exists():
        print(f"index.html not found at {index_path}", file=sys.stderr)
        return 2

    # --check-played mode: isolate API failures so the cron gate can distinguish
    # "no game today" (rc=1, silent skip) from "API broke" (rc=2, workflow fails
    # and emails us).
    if args.check_played:
        try:
            print(f"[fetch] played-games (probe)", flush=True)
            played = fetch_played_games()
            played_today = did_foxes_play_today(played)
        except Exception as e:
            print(f"[error] check-played failed: {e!r}", file=sys.stderr)
            return 2
        print(f"foxes-played-today: {played_today}")
        return 0 if played_today else 1

    print(f"[fetch] standings", flush=True)
    standings = fetch_standings()
    print(f"[fetch] played-games", flush=True)
    played = fetch_played_games()

    print(f"[fetch] upcoming-schedule", flush=True)
    upcoming = fetch_upcoming_games()
    print(f"[fetch] skaters", flush=True)
    skaters = fetch_skater_stats()
    print(f"[fetch] goalies", flush=True)
    goalies = fetch_goalie_stats()

    print(f"  standings: {len(standings)} teams")
    print(f"  played:    {len(played)} games (league-wide)")
    print(f"  upcoming:  {len(upcoming)} games (league-wide)")
    print(f"  skaters:   {len(skaters)} Fayetteville players")
    print(f"  goalies:   {len(goalies)} Fayetteville goalies")

    html_old = index_path.read_text(encoding="utf-8")
    existing_jerseys = extract_existing_jerseys(html_old)
    # Always preserve manual goalie SV% — the league portal returns 0.000 (their
    # SA/GA accounting differs from ours), so the API value is not reliable.
    # The user maintains SV% by hand in index.html; the script touches everything
    # else but leaves SV% alone. The --first-run flag is now a no-op for SV%
    # and is kept only for any future "preserve more on first run" needs.
    svp_overrides = extract_existing_svpct(html_old)
    if svp_overrides:
        print(f"  preserving manual SV% for {list(svp_overrides)}")

    # Playoff results: merge auto-detected winners with whatever's already in
    # the JSON block (manual overrides win when auto-detect comes up empty).
    existing_playoffs = extract_existing_playoff_results(html_old)
    auto_playoffs = detect_playoff_results(standings, played)
    merged_playoffs = {
        key: (auto_playoffs.get(key) if auto_playoffs.get(key) is not None
              else existing_playoffs.get(key))
        for key in ("semi1Winner", "semi2Winner", "champion")
    }
    # Merge per-slot scores: auto-detected wins for a slot, else keep existing
    # (so a manually-entered score survives until the API exposes that game).
    merged_scores = dict(existing_playoffs.get("scores") or {})
    merged_scores.update(auto_playoffs.get("scores") or {})
    merged_playoffs["scores"] = merged_scores
    detected_now = [k for k, v in auto_playoffs.items()
                    if k != "scores" and v is not None]
    if detected_now:
        print(f"  detected playoff winners from API: {detected_now}")

    season_meta_block = build_season_meta_block(SEASON_YEAR)
    standings_block = build_standings_block(standings)
    skater_block = build_skater_block(skaters, existing_jerseys)
    goalie_block = build_goalie_block(goalies, svp_overrides, existing_jerseys)
    schedule_list_block = build_schedule_list_block(upcoming, played)
    ticker_block = build_ticker_block(played)
    matchup_block = build_matchup_block(
        upcoming, standings, played, merged_playoffs.get("champion")
    )
    playoff_results_block = build_playoff_results_block(merged_playoffs)

    html_new = html_old
    html_new = replace_region(html_new, "season-meta", season_meta_block)
    html_new = replace_region(html_new, "standings", standings_block)
    html_new = replace_region(html_new, "skaters", skater_block)
    html_new = replace_region(html_new, "goalies", goalie_block)
    html_new = replace_region(html_new, "schedule-list", schedule_list_block)
    if ticker_block:
        html_new = replace_region(html_new, "ticker", ticker_block)
    if matchup_block:
        html_new = replace_region(html_new, "matchup", matchup_block)
    html_new = replace_region(html_new, "playoff-results", playoff_results_block)

    if html_new == html_old:
        print("[result] no changes — exiting without writing")
        return 0

    if args.dry_run:
        # Show line-count change and a small unified diff snippet
        old_lines = html_old.splitlines()
        new_lines = html_new.splitlines()
        import difflib

        diff = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile="index.html (old)",
            tofile="index.html (new)",
            n=2,
            lineterm="",
        ))
        print(f"[dry-run] {len(diff)} diff lines, {len(new_lines)-len(old_lines):+d} net lines")
        # Print at most 200 diff lines
        for line in diff[:200]:
            print(line)
        if len(diff) > 200:
            print(f"… ({len(diff)-200} more diff lines)")
        return 0

    index_path.write_text(html_new, encoding="utf-8")
    print(f"[result] wrote {index_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
