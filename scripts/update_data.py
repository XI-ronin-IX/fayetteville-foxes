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
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


def H(s: Any) -> str:
    """Escape a value for HTML text content (default) or attribute use.
    Always escapes <, >, &, ", '. Defense-in-depth: any value pulled from the
    league API is run through this before being inserted into index.html.
    """
    return html.escape("" if s is None else str(s), quote=True)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

SEASON_ID = "14572"
FAYETTEVILLE_TEAM_ID = "498107"
FAYETTEVILLE_TEAM_NAME = "Team Fayetteville"

API_BASE = "https://gamesheetstats.com/api"
ENDPOINTS = {
    "standings": f"{API_BASE}/useStandings/getDivisionStandings/{SEASON_ID}",
    "scores": f"{API_BASE}/useScoredGames/getSeasonScores/{SEASON_ID}",
    "schedule": f"{API_BASE}/useSchedule/getSeasonSchedule/{SEASON_ID}",
    "skaters": f"{API_BASE}/usePlayers/getPlayerStandings/{SEASON_ID}",
    "goalies": f"{API_BASE}/useGoalies/getGoalieStandings/{SEASON_ID}",
}

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


def normalize_venue(s: str) -> str:
    """Clean up venue strings from the API.

    The league sometimes records venues with duplicated trailing words like
    'Polar Ice Wake Forest Forest' — strip those to 'Polar Ice Wake Forest'.
    """
    if not s:
        return s
    # Collapse "Word Word" duplicates anywhere in the string.
    return re.sub(r"\b(\w+)\s+\1\b", r"\1", s).strip()


def parse_game_date(date_str: str) -> date:
    """Parse 'Apr 25, 2026' (game.date field) into a date object."""
    return datetime.strptime(date_str, "%b %d, %Y").date()


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
    foxes_upcoming = [
        g for g in upcoming
        if FAYETTEVILLE_TEAM_NAME in (g["homeTeam"]["name"], g["visitorTeam"]["name"])
    ]
    foxes_upcoming.sort(key=lambda g: g.get("scheduleStartTime", ""))
    foxes_played = [
        g for g in played
        if FAYETTEVILLE_TEAM_NAME in (
            g["game"]["homeTeam"]["name"],
            g["game"]["visitorTeam"]["name"],
        )
    ]
    foxes_played.sort(
        key=lambda g: parse_game_date(g["game"]["date"]),
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
    """Build the .ticker-track inner content (items + dots, duplicated for loop)."""
    foxes_played = [
        g for g in played
        if FAYETTEVILLE_TEAM_NAME in (
            g["game"]["homeTeam"]["name"],
            g["game"]["visitorTeam"]["name"],
        )
    ]
    foxes_played.sort(key=lambda g: parse_game_date(g["game"]["date"]), reverse=True)
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
        if fox_goals > opp_goals:
            indicator_class = "w"
            indicator = "W"
        elif fox_goals < opp_goals:
            indicator_class = "l"
            indicator = "L"
        else:
            indicator_class = "l"
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


def build_matchup_block(
    upcoming: list[dict], standings: list[dict], played: list[dict]
) -> str | None:
    """Build the matchup hero card (Fayetteville vs next opponent)."""
    foxes_upcoming = [
        g for g in upcoming
        if FAYETTEVILLE_TEAM_NAME in (g["homeTeam"]["name"], g["visitorTeam"]["name"])
    ]
    if not foxes_upcoming:
        return None
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
    iso_target = fmt_iso_eastern(d, g["time"])
    drop_24 = fmt_24h(g["time"])
    long_date = fmt_long_date(d)

    # Records from standings (already-fetched)
    by_team = {t["team"]: t for t in standings}
    fox_st = by_team.get(FAYETTEVILLE_TEAM_NAME, {"w": 0, "l": 0, "t": 0})
    opp_st = by_team.get(opp_name_full, {"w": 0, "l": 0, "t": 0})
    fox_record = fmt_record(fox_st["w"], fox_st["l"], fox_st["t"])
    opp_record = fmt_record(opp_st["w"], opp_st["l"], opp_st["t"])

    # Foxes-specific game number, two-digit padded
    game_num = f"{foxes_game_num:02d}"

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
            f'            <img src="{opp_logo}.png" alt="{H(opp_disp)}" loading="lazy" '
            f'style="width:170px;height:auto;display:block;" />\n'
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
        'loading="lazy" style="width:170px;height:auto;display:block;" />\n'
        '        </picture></span>\n'
        f'        <div class="place mono">{H(fox_place)}</div>\n'
        '        <div class="team-name">Fayetteville <em>Foxes</em></div>\n'
        f'        <div class="record" data-foxes-record="dotted">{H(fox_record)}</div>\n'
        '      </div>\n'
        '\n'
        '      <div class="matchup-vs">\n'
        '        <div class="vs-big">VS</div>\n'
        f'        <div class="kick">Regular Season · Game {H(game_num)}</div>\n'
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
    original indentation of the END marker so we don't drift."""
    pattern = re.compile(REGION_RE_TEMPLATE.format(name=re.escape(name)), re.DOTALL)
    def _sub(m: re.Match) -> str:
        begin, _inner, end_indent, end = m.group(1), m.group(2), m.group(3), m.group(4)
        return f"{begin}\n{new_inner}\n{end_indent}{end}"
    new_html, count = pattern.subn(_sub, html, count=1)
    if count == 0:
        raise RuntimeError(
            f"region marker not found in index.html: <!-- BEGIN auto:{name} -->"
        )
    return new_html


def extract_existing_jerseys(html: str) -> dict[str, str]:
    """Pull `name -> jersey` from the existing skaters table so we can fall
    back if the API jersey field is missing for a player."""
    out: dict[str, str] = {}
    pattern = re.compile(
        r'<span class="jr">(\d+)</span><span class="pn">([^<]+)</span>'
    )
    for jr, name in pattern.findall(html):
        out[name.strip()] = jr
    return out


def extract_existing_svpct(html: str) -> dict[str, str]:
    """Pull `name -> svpct` from the existing goalies table for first-run preserve.

    Goalie row layout: jr, pn, GP, GS, SA, GA, GAA(.pts), SV%, W, L, T
    So the SV% is the span immediately following the .pts (GAA) span.
    """
    out: dict[str, str] = {}
    pattern = re.compile(
        r'<span class="jr">\d+</span><span class="pn">([^<]+)</span>'
        r'(?:<span>[^<]*</span>){4}'        # GP, GS, SA, GA
        r'<span class="pts">[^<]*</span>'    # GAA
        r'<span>([.\d]+)</span>'             # SV%
    )
    for name, svpct in pattern.findall(html):
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
                   help="Preserve existing manual goalie save percentage values")
    p.add_argument("--dry-run", action="store_true",
                   help="Print diff but don't write")
    p.add_argument("--check-played", action="store_true",
                   help="Exit 0 only if a Foxes game was played today")
    p.add_argument("--index", default="index.html",
                   help="Path to index.html (default: ./index.html)")
    args = p.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    index_path = (repo_root / args.index).resolve()
    if not index_path.exists():
        print(f"index.html not found at {index_path}", file=sys.stderr)
        return 2

    print(f"[fetch] standings", flush=True)
    standings = fetch_standings()
    print(f"[fetch] played-games", flush=True)
    played = fetch_played_games()

    if args.check_played:
        played_today = did_foxes_play_today(played)
        print(f"foxes-played-today: {played_today}")
        return 0 if played_today else 1

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
    svp_overrides = None
    if args.first_run:
        svp_overrides = extract_existing_svpct(html_old)
        print(f"  --first-run: preserving SV% for {list(svp_overrides)}")

    standings_block = build_standings_block(standings)
    skater_block = build_skater_block(skaters, existing_jerseys)
    goalie_block = build_goalie_block(goalies, svp_overrides, existing_jerseys)
    schedule_list_block = build_schedule_list_block(upcoming, played)
    ticker_block = build_ticker_block(played)
    matchup_block = build_matchup_block(upcoming, standings, played)

    html_new = html_old
    html_new = replace_region(html_new, "standings", standings_block)
    html_new = replace_region(html_new, "skaters", skater_block)
    html_new = replace_region(html_new, "goalies", goalie_block)
    html_new = replace_region(html_new, "schedule-list", schedule_list_block)
    if ticker_block:
        html_new = replace_region(html_new, "ticker", ticker_block)
    if matchup_block:
        html_new = replace_region(html_new, "matchup", matchup_block)

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
