# Multi-Season Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the site carry multiple seasons — freeze 2026 as a static archive at `/2026`, keep `/` as the always-current season, and add a one-command rollover that scaffolds a fresh preseason that auto-fills from the API.

**Architecture:** Externalize per-season identity into `scripts/season_config.json` (read by the updater, fallback to today's constants). Archives are root-level `<year>.html` byte copies served at `/<year>` via the existing `cleanUrls` config (relative asset paths resolve to root — no rewriting). A new `--start-season` mode of `update_data.py` archives the old season, rewrites the config, and regenerates `index.html` into a preseason shell. Year display is data-driven via a `season-meta` region + JS stamper. Each data region gains a graceful empty-state.

**Tech Stack:** Python 3.12 stdlib only (`json`, `argparse`, `shutil`, `pathlib`, `unittest`), static HTML/CSS/vanilla JS. No new dependencies. Tests run with `python -m unittest`.

**Spec:** `docs/superpowers/specs/2026-06-03-multi-season-architecture-design.md`

---

## File Structure

- **Create** `scripts/season_config.json` — current-season identity + archive list. Single source of truth.
- **Create** `scripts/season_config.py` — pure config load/save + `rollover()` transform. No network, no HTML. Unit-tested in isolation.
- **Create** `scripts/test_update_data.py` — `unittest` tests for config logic, empty-state builders, season-meta, seasons-nav, and a rollover integration test.
- **Modify** `scripts/update_data.py` — read identity from config; add `build_season_meta_block`, `build_seasons_nav_block`, `build_preseason_matchup_block`; add empty-states to data builders; add `--start-season` rollover mode; replace new regions in `main()`.
- **Modify** `index.html` — add `season-meta` + `seasons-nav` regions, year hooks, empty-state CSS, JS year-stamper; replace hardcoded years with hooks.
- **Create** `docs/ROLLOVER.md` — short runbook for rolling over to a new season.

> **Note on existing code:** `update_data.py` uses module-level constants (`SEASON_ID`, `ENDPOINTS`, `FAYETTEVILLE_TEAM_NAME`, …) referenced throughout the fetchers. To stay minimal, we initialize those globals from config inside an `_init_season()` helper called at the top of every entry path, and re-initialize them after a rollover. We do NOT thread identity through every fetcher signature.

---

## Task 1: Season config module + migration

**Files:**
- Create: `scripts/season_config.json`
- Create: `scripts/season_config.py`
- Create: `scripts/test_update_data.py`
- Modify: `scripts/update_data.py` (config-driven identity)

- [ ] **Step 1: Create the config file**

Create `scripts/season_config.json`:

```json
{
  "current": {
    "year": 2026,
    "season_id": "14572",
    "team_id": "498107",
    "team_name": "Team Fayetteville"
  },
  "archives": []
}
```

- [ ] **Step 2: Write the failing tests for the config module**

Create `scripts/test_update_data.py`:

```python
"""Unit tests for the season config + update_data builders. Run with:
    python -m unittest scripts.test_update_data   (from repo root)
"""
from __future__ import annotations
import json
import unittest
from pathlib import Path
import tempfile

from scripts import season_config as sc


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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m unittest scripts.test_update_data -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'scripts.season_config'`

- [ ] **Step 4: Implement the config module**

Create `scripts/season_config.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest scripts.test_update_data -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Wire update_data.py to read identity from config**

In `scripts/update_data.py`, just below the existing `API_BASE` / `ENDPOINTS` definitions, replace the hardcoded identity constants. Find:

```python
SEASON_ID = "14572"
FAYETTEVILLE_TEAM_ID = "498107"
```

(and the `FAYETTEVILLE_TEAM_NAME = "Team Fayetteville"` line, wherever it sits) and the `ENDPOINTS = {...}` dict. Replace the identity + endpoints setup with a config-driven init:

```python
from scripts import season_config as _season_config  # add near top imports

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
```

> If `ENDPOINTS` was previously referenced at module top-level (it was a literal dict), this preserves the same keys. Verify no other code path reads `ENDPOINTS` before `_init_season()` runs — it runs at import, so all good.

- [ ] **Step 7: Run the existing updater end-to-end to confirm no regression**

Run: `python scripts/update_data.py --dry-run`
Expected: same fetch summary as before (standings: 8 teams, etc.); exits cleanly. The config drives the same 2026 season, so output is unchanged.

- [ ] **Step 8: Commit**

```bash
git add scripts/season_config.json scripts/season_config.py scripts/test_update_data.py scripts/update_data.py
git commit -m "Multi-season: externalize season identity into season_config.json"
```

---

## Task 2: season-meta region + data-driven year

**Files:**
- Modify: `scripts/update_data.py` (add `build_season_meta_block`, replace region in `main`)
- Modify: `index.html` (add region, year hooks, JS stamper)
- Modify: `scripts/test_update_data.py` (test the builder)

- [ ] **Step 1: Write the failing test**

Add to `scripts/test_update_data.py`:

```python
from scripts import update_data as ud


class TestSeasonMeta(unittest.TestCase):
    def test_season_meta_block_contains_year(self):
        block = ud.build_season_meta_block(2027)
        self.assertIn('"year": 2027', block)
        self.assertIn('id="season-meta"', block)
        # JSON-in-HTML hardening: no raw </ that could break the tag
        self.assertNotIn("</script></script>", block)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest scripts.test_update_data.TestSeasonMeta -v`
Expected: FAIL — `AttributeError: module 'scripts.update_data' has no attribute 'build_season_meta_block'`

- [ ] **Step 3: Implement the builder**

In `scripts/update_data.py`, near `build_playoff_results_block`, add:

```python
def build_season_meta_block(year: int) -> str:
    """Embedded JSON consumed by the page's year-stamper JS. Same JSON-in-HTML
    escaping discipline as build_playoff_results_block."""
    payload = json.dumps({"year": int(year)}).replace("</", "<\\/")
    return (
        '    <script type="application/json" id="season-meta">\n'
        f'{payload}\n'
        '    </script>'
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m unittest scripts.test_update_data.TestSeasonMeta -v`
Expected: PASS

- [ ] **Step 5: Add the season-meta region to index.html**

In `index.html`, immediately after the opening `<body>` (or just inside the hero `<header>`, before visible content), add:

```html
<!-- BEGIN auto:season-meta -->
    <script type="application/json" id="season-meta">
{"year": 2026}
    </script>
<!-- END auto:season-meta -->
```

- [ ] **Step 6: Convert hardcoded year strings to hooks**

In `index.html`:
- Hero eyebrow — change `<span class="tag">Season 2026</span>` to
  `<span class="tag">Season <span data-season-year>2026</span></span>`
- Hero numeral — change `<span class="numeral">26</span>` to
  `<span class="numeral" data-season-yy>26</span>`
- Search `index.html` for any other literal `2026` in static chrome (footer
  copyright, `<meta name="description">`, og tags, JSON-LD if present). For each
  visible-text occurrence, wrap the year in `<span data-season-year>2026</span>`.
  (Leave occurrences inside auto-regions alone — those are regenerated.)

- [ ] **Step 7: Add the JS year-stamper**

In `index.html`, inside the main `<script>` block (near the other IIFEs, e.g. right after the season-complete handler), add:

```javascript
  // Stamp the season year from the embedded season-meta JSON into hooks.
  // Works on the live page and on every frozen archive (each carries its own
  // season-meta), so no hardcoded year drifts out of date.
  (function(){
    let year = null;
    const el = document.getElementById('season-meta');
    if (el) { try { year = JSON.parse(el.textContent).year; } catch (_) {} }
    if (year == null) return;
    document.querySelectorAll('[data-season-year]').forEach(n => { n.textContent = String(year); });
    const yy = String(year % 100).padStart(2, '0');
    document.querySelectorAll('[data-season-yy]').forEach(n => { n.textContent = yy; });
  })();
```

- [ ] **Step 8: Register the region in main()**

In `scripts/update_data.py` `main()`, where regions are built and replaced, add the build call alongside the others:

```python
    season_meta_block = build_season_meta_block(SEASON_YEAR)
```

and in the `replace_region` sequence:

```python
    html_new = replace_region(html_new, "season-meta", season_meta_block)
```

- [ ] **Step 9: Regenerate and verify in the browser**

Run: `python scripts/update_data.py`
Then start the server (`node serve.mjs` if not running) and screenshot:
Run: `node screenshot.mjs http://localhost:3000 year-hooks` (if `screenshot.mjs` is absent, use the Playwright MCP per CLAUDE.md).
Expected: hero still reads "Season 2026" / "Foxes /26" (stamped from season-meta), no visual change.

- [ ] **Step 10: Commit**

```bash
git add scripts/update_data.py scripts/test_update_data.py index.html
git commit -m "Multi-season: data-driven year via season-meta region + JS stamper"
```

---

## Task 3: Preseason empty-states

**Files:**
- Modify: `scripts/update_data.py` (`build_matchup_block` branch, `build_preseason_matchup_block`, empty-states in standings/skater/goalie/schedule builders)
- Modify: `index.html` (empty-state CSS)
- Modify: `scripts/test_update_data.py`

- [ ] **Step 1: Write failing tests for empty-states**

Add to `scripts/test_update_data.py`:

```python
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
        # No upcoming, no played Foxes games, no champion → preseason card.
        block = ud.build_matchup_block([], [], [], None)
        self.assertIn("Preseason", block)
        self.assertIn('data-season-state="preseason"', block)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m unittest scripts.test_update_data.TestEmptyStates -v`
Expected: FAIL (placeholders not present yet)

- [ ] **Step 3: Add empty-state guards to the table builders**

In `scripts/update_data.py`:

`build_standings_block` — add at the very top of the function body:

```python
    if not teams:
        return (
            '      <div class="table-empty">'
            f'The {SEASON_YEAR} season hasn’t started yet — standings will appear once games are played.'
            '</div>'
        )
```

`build_skater_block` — add at the top:

```python
    if not players:
        return (
            '      <div class="table-empty">'
            'Roster announced closer to the season.'
            '</div>'
        )
```

`build_goalie_block` — add at the top:

```python
    if not goalies:
        return (
            '      <div class="table-empty">'
            'Goalie roster announced closer to the season.'
            '</div>'
        )
```

`build_schedule_list_block` — add at the top (after computing whether there are any rows; simplest: guard on both inputs empty):

```python
    if not upcoming and not played:
        return (
            '      <div class="sched-empty">'
            f'Schedule TBA — the {SEASON_YEAR} slate hasn’t been published yet.'
            '</div>'
        )
```

- [ ] **Step 4: Add the preseason matchup branch + builder**

In `build_matchup_block`, change the no-upcoming branch. Find:

```python
    if not foxes_upcoming:
        if champion:
            return build_season_complete_matchup_block(
                standings, champion, _season_year(played)
            )
        return build_tbd_matchup_block(standings)
```

Replace with:

```python
    if not foxes_upcoming:
        if champion:
            return build_season_complete_matchup_block(
                standings, champion, _season_year(played)
            )
        foxes_played = any(
            FAYETTEVILLE_TEAM_NAME in (pg["game"]["homeTeam"]["name"],
                                       pg["game"]["visitorTeam"]["name"])
            for pg in played
        )
        if not foxes_played:
            return build_preseason_matchup_block(SEASON_YEAR)
        return build_tbd_matchup_block(standings)
```

Add the new builder near `build_tbd_matchup_block` (it keeps the same DOM shape so the JS sync helpers and the season-state handler behave; the home side carries `data-season-state="preseason"`, distinct from "complete"):

```python
def build_preseason_matchup_block(year: int) -> str:
    """Matchup card for before a season's first game — no opponent, no
    countdown. The data-season-state="preseason" marker lets the page JS show a
    'preseason' holding state for the banner/title instead of a next-game UI."""
    return (
        f'      <div class="matchup-side home" data-season-state="preseason" data-season-year="{year}">\n'
        '        <span class="crest" style="transform:none;background:var(--black);"><picture>\n'
        '          <source type="image/webp" srcset="brand_assets/Fayetteville_Fox_Logo_BLK.webp">\n'
        '          <img src="brand_assets/Fayetteville_Fox_Logo_BLK.png" alt="Fayetteville Foxes" '
        'loading="lazy" />\n'
        '        </picture></span>\n'
        f'        <div class="place mono">{year} Season</div>\n'
        '        <div class="team-name">Fayetteville <em>Foxes</em></div>\n'
        '        <div class="record" data-foxes-record="dotted">—</div>\n'
        '      </div>\n'
        '\n'
        '      <div class="matchup-vs">\n'
        '        <div class="vs-big">SOON</div>\n'
        '        <div class="kick">Preseason</div>\n'
        '        <div class="season-note mono">Schedule TBA.<br>Check back before puck drop.</div>\n'
        '      </div>\n'
        '\n'
        '      <div class="matchup-side">\n'
        '        <span class="crest" style="transform:none;background:var(--black);"></span>\n'
        '        <div class="place mono">First opponent</div>\n'
        '        <div class="team-name">TBA</div>\n'
        '        <div class="record">—</div>\n'
        '      </div>\n'
        '\n'
        '      <div class="matchup-meta" style="grid-column:1 / -1;">\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Status</span>\n'
        f'          <span class="val"><em>{year} preseason</em></span>\n'
        '        </div>\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Schedule</span>\n'
        '          <span class="val">TBA</span>\n'
        '        </div>\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Roster</span>\n'
        '          <span class="val">Coming soon</span>\n'
        '        </div>\n'
        '        <div class="cell">\n'
        '          <span class="label mono">Broadcast</span>\n'
        '          <span class="val"><a href="https://www.livebarn.com/" target="_blank" '
        'rel="noopener noreferrer" style="color:var(--orange);'
        'border-bottom:1px solid rgba(255,85,0,0.4);padding-bottom:1px;">LiveBarn ↗</a></span>\n'
        '        </div>\n'
        '      </div>'
    )
```

- [ ] **Step 5: Extend the season-state JS handler to cover "preseason"**

In `index.html`, the season-complete handler currently matches
`[data-season-state="complete"]`. Generalize it to also handle `"preseason"`.
Find the handler's first lines:

```javascript
    const marker = document.querySelector('[data-season-state="complete"]');
    if (!marker) return;
    window.__seasonComplete = true;
```

Replace with:

```javascript
    const marker = document.querySelector('[data-season-state="complete"], [data-season-state="preseason"]');
    if (!marker) return;
    const state = marker.dataset.seasonState;
    window.__seasonComplete = true;   // flag name kept; means "suppress next-game UI"
    const preseason = state === 'preseason';
```

Then make the copy conditional. Replace the banner/title/lede `set(...)` calls with:

```javascript
    set('[data-target="hero-when-label"]', (yr ? yr + ' ' : '') + 'Season');
    set('[data-target="hero-when"]', preseason ? 'Preseason' : 'Complete');
    set('[data-target="hero-where"]', preseason ? 'Schedule coming soon' : 'See you next year');
    set('[data-target="section-02-label"]', preseason ? '02 / Preseason' : '02 / The Final Whistle');
    set('[data-target="next-game-phrase"]', preseason
        ? 'The ' + (yr || 'new') + ' season is almost here'
        : 'That’s a wrap on ' + (yr || 'the season'));
    const lede = document.querySelector('#schedule .section-lede');
    if (lede) lede.textContent = preseason
      ? 'The ' + (yr ? yr + ' ' : '') + 'season hasn’t started yet — schedule, roster, and standings will fill in automatically as the league publishes them.'
      : 'The Foxes have closed out their ' + (yr ? yr + ' ' : '') +
        'season. Thanks for packing the stands all year — warmups will be back before you know it.';
```

- [ ] **Step 6: Add empty-state CSS**

In `index.html`, in the `<style>` block (near the `.stats-row` / `.sched-row` rules), add:

```css
  .table-empty, .sched-empty{
    padding:28px 18px;text-align:center;
    font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:0.06em;
    color:var(--smoke);text-transform:uppercase;line-height:1.7;
  }
```

- [ ] **Step 7: Run the tests**

Run: `python -m unittest scripts.test_update_data.TestEmptyStates -v`
Expected: PASS (5 tests)

- [ ] **Step 8: Smoke-test preseason rendering with a temporary empty config**

Run this throwaway check (does NOT write index.html):

```bash
python -c "import scripts.update_data as u; print(u.build_matchup_block([], [], [], None)[:200]); print('---'); print(u.build_standings_block([])); print(u.build_schedule_list_block([], []))"
```

Expected: preseason matchup HTML with `data-season-state="preseason"`, and the standings/schedule placeholders.

- [ ] **Step 9: Commit**

```bash
git add scripts/update_data.py scripts/test_update_data.py index.html
git commit -m "Multi-season: graceful preseason empty-states for matchup + data regions"
```

---

## Task 4: Seasons nav menu

**Files:**
- Modify: `scripts/update_data.py` (`build_seasons_nav_block`, register region)
- Modify: `index.html` (nav + mobile-menu regions, dropdown CSS)
- Modify: `scripts/test_update_data.py`

- [ ] **Step 1: Write the failing test**

Add to `scripts/test_update_data.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest scripts.test_update_data.TestSeasonsNav -v`
Expected: FAIL — no attribute `build_seasons_nav_block`

- [ ] **Step 3: Implement the builder**

In `scripts/update_data.py`, add:

```python
def build_seasons_nav_block(cfg: dict) -> str:
    """Dropdown links for the 'Seasons' nav menu. Always offers the live
    current season at '/', plus one link per archived year at '/<year>'.
    Archived years are listed newest-first."""
    items = ['        <a role="menuitem" href="/">Current season</a>']
    years = sorted({a.get("year") for a in cfg.get("archives", []) if a.get("year")},
                   reverse=True)
    for y in years:
        items.append(f'        <a role="menuitem" href="/{H(y)}">{H(y)}</a>')
    return "\n".join(items)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m unittest scripts.test_update_data.TestSeasonsNav -v`
Expected: PASS

- [ ] **Step 5: Add the nav regions to index.html**

In the desktop nav (near the existing `<a href="#playoffs">Playoffs</a>` link),
add a Seasons dropdown:

```html
        <div class="nav-seasons">
          <button type="button" class="nav-seasons-btn" aria-haspopup="true" aria-expanded="false">Seasons ▾</button>
          <div class="nav-seasons-menu" role="menu">
            <!-- BEGIN auto:seasons-nav -->
        <a role="menuitem" href="/">Current season</a>
            <!-- END auto:seasons-nav -->
          </div>
        </div>
```

In the mobile menu (near `<a class="mm-item" href="#playoffs">Playoffs</a>`), add a mirrored region:

```html
        <div class="mm-seasons-label mono">Seasons</div>
        <!-- BEGIN auto:seasons-nav-mobile -->
        <a class="mm-item" href="/">Current season</a>
        <!-- END auto:seasons-nav-mobile -->
```

- [ ] **Step 6: Add dropdown CSS + open/close JS**

CSS (in `<style>`):

```css
  .nav-seasons{position:relative;}
  .nav-seasons-btn{
    background:none;border:none;cursor:pointer;color:inherit;font:inherit;
    padding:6px 8px;letter-spacing:0.04em;
  }
  .nav-seasons-btn:hover,.nav-seasons-btn:focus-visible{color:var(--orange);}
  .nav-seasons-menu{
    position:absolute;right:0;top:calc(100% + 8px);min-width:180px;
    background:var(--ink);border:1px solid var(--line-strong);
    display:none;flex-direction:column;z-index:50;
    box-shadow:0 18px 40px -12px rgba(0,0,0,0.6);
  }
  .nav-seasons.open .nav-seasons-menu{display:flex;}
  .nav-seasons-menu a{padding:10px 14px;color:var(--bone);}
  .nav-seasons-menu a:hover,.nav-seasons-menu a:focus-visible{background:var(--ink-2);color:var(--orange);}
  .mm-seasons-label{margin-top:14px;color:var(--smoke);font-size:11px;letter-spacing:0.16em;text-transform:uppercase;}
```

JS (in the main `<script>`, a small IIFE):

```javascript
  // Seasons dropdown (desktop): toggle, close on outside click / Escape.
  (function(){
    const wrap = document.querySelector('.nav-seasons');
    if (!wrap) return;
    const btn = wrap.querySelector('.nav-seasons-btn');
    const close = () => { wrap.classList.remove('open'); btn.setAttribute('aria-expanded', 'false'); };
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = wrap.classList.toggle('open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    document.addEventListener('click', (e) => { if (!wrap.contains(e.target)) close(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  })();
```

- [ ] **Step 7: Register both regions in main()**

In `scripts/update_data.py` `main()`:

```python
    seasons_nav_block = build_seasons_nav_block(cfg)   # cfg from _init_season()
```

and in the replace sequence:

```python
    html_new = replace_region(html_new, "seasons-nav", seasons_nav_block)
    html_new = replace_region(html_new, "seasons-nav-mobile",
                              seasons_nav_block.replace('role="menuitem" ', 'class="mm-item" '))
```

> `main()` must hold the config. At the top of `main()` change `_init_season()` to `cfg = _init_season()` so `cfg` is in scope here.

- [ ] **Step 8: Regenerate + verify in the browser**

Run: `python scripts/update_data.py`
Start server, then screenshot the nav (Playwright MCP). Expected: a "Seasons ▾"
button that opens a menu with "Current season" (just one item for now, since no
archives yet). Click opens/closes; Escape closes; mobile menu shows a "Seasons"
group.

- [ ] **Step 9: Commit**

```bash
git add scripts/update_data.py scripts/test_update_data.py index.html
git commit -m "Multi-season: Seasons nav dropdown (desktop + mobile), data-driven from config"
```

---

## Task 5: Rollover command

**Files:**
- Modify: `scripts/update_data.py` (argparse `--start-season`, `run_rollover()`)
- Modify: `scripts/test_update_data.py` (integration test)

- [ ] **Step 1: Write the failing integration test**

Add to `scripts/test_update_data.py`:

```python
class TestRollover(unittest.TestCase):
    def test_rollover_archives_and_scaffolds(self):
        import shutil
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            # Minimal index.html with the regions the rollover touches.
            index = d / "index.html"
            index.write_text(
                '<!-- BEGIN auto:season-meta -->\n{"year": 2026}\n<!-- END auto:season-meta -->\n'
                '<div class="matchup"><!-- BEGIN auto:matchup -->old<!-- END auto:matchup --></div>\n'
                '<!-- BEGIN auto:standings -->old<!-- END auto:standings -->\n'
                '<!-- BEGIN auto:skaters -->old<!-- END auto:skaters -->\n'
                '<!-- BEGIN auto:goalies -->old<!-- END auto:goalies -->\n'
                '<!-- BEGIN auto:schedule-list -->old<!-- END auto:schedule-list -->\n'
                '<!-- BEGIN auto:ticker -->old<!-- END auto:ticker -->\n'
                '<!-- BEGIN auto:playoff-results -->old<!-- END auto:playoff-results -->\n'
                '<!-- BEGIN auto:seasons-nav -->old<!-- END auto:seasons-nav -->\n'
                '<!-- BEGIN auto:seasons-nav-mobile -->old<!-- END auto:seasons-nav-mobile -->\n',
                encoding="utf-8")
            config = d / "season_config.json"
            shutil.copy(Path("scripts/season_config.json"), config)

            # All fetchers return empty → preseason scaffold, no network.
            with mock.patch.multiple(
                ud,
                fetch_standings=lambda: [],
                fetch_played_games=lambda *a, **k: [],
                fetch_upcoming_games=lambda *a, **k: [],
                fetch_skater_stats=lambda: [],
                fetch_goalie_stats=lambda: [],
            ):
                rc = ud.run_rollover(
                    new_year=2027, season_id="20000", team_id=None, team_name=None,
                    index_path=index, config_path=config, force=False,
                )

            self.assertEqual(rc, 0)
            self.assertTrue((d / "2026.html").exists(), "archive not created")
            cfg = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(cfg["current"]["year"], 2027)
            self.assertIn({"year": 2026}, cfg["archives"])
            new_html = index.read_text(encoding="utf-8")
            self.assertIn('"year": 2027', new_html)
            self.assertIn("Preseason", new_html)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest scripts.test_update_data.TestRollover -v`
Expected: FAIL — no attribute `run_rollover`

- [ ] **Step 3: Refactor main()'s region build into a reusable function**

In `scripts/update_data.py`, extract the region-regeneration body of `main()`
into a helper so the rollover can reuse it. Add:

```python
def regenerate_index(html_old: str, cfg: dict) -> str:
    """Rebuild every auto-region in `html_old` from the current API + cfg and
    return the new HTML. Shared by the normal update and the rollover scaffold."""
    standings = fetch_standings()
    played = fetch_played_games()
    upcoming = fetch_upcoming_games()
    skaters = fetch_skater_stats()
    goalies = fetch_goalie_stats()

    existing_jerseys = extract_existing_jerseys(html_old)
    svp_overrides = extract_existing_svpct(html_old)

    existing_playoffs = extract_existing_playoff_results(html_old)
    auto_playoffs = detect_playoff_results(standings, played)
    merged_playoffs = {
        key: (auto_playoffs.get(key) if auto_playoffs.get(key) is not None
              else existing_playoffs.get(key))
        for key in ("semi1Winner", "semi2Winner", "champion")
    }
    merged_scores = dict(existing_playoffs.get("scores") or {})
    merged_scores.update(auto_playoffs.get("scores") or {})
    merged_playoffs["scores"] = merged_scores

    blocks = {
        "season-meta":   build_season_meta_block(SEASON_YEAR),
        "standings":     build_standings_block(standings),
        "skaters":       build_skater_block(skaters, existing_jerseys),
        "goalies":       build_goalie_block(goalies, svp_overrides, existing_jerseys),
        "schedule-list": build_schedule_list_block(upcoming, played),
        "matchup":       build_matchup_block(upcoming, standings, played,
                                             merged_playoffs.get("champion")),
        "playoff-results": build_playoff_results_block(merged_playoffs),
        "seasons-nav":   build_seasons_nav_block(cfg),
        "seasons-nav-mobile": build_seasons_nav_block(cfg).replace(
            'role="menuitem" ', 'class="mm-item" '),
    }
    ticker_block = build_ticker_block(played)

    html_new = html_old
    for name, block in blocks.items():
        if block is None:
            continue
        html_new = replace_region(html_new, name, block)
    if ticker_block:
        html_new = replace_region(html_new, "ticker", ticker_block)
    return html_new
```

> Then update the existing `main()` update path to call `regenerate_index(html_old, cfg)` instead of its inline region building, preserving the existing `--dry-run` / "no changes" / write behavior around it. Keep the existing print summary lines. This is a refactor — confirm `python scripts/update_data.py --dry-run` still produces an identical diff to before.

- [ ] **Step 4: Implement run_rollover()**

```python
def run_rollover(new_year: int, season_id: str, team_id: str | None,
                 team_name: str | None, index_path: Path,
                 config_path: Path = _season_config.CONFIG_PATH,
                 force: bool = False) -> int:
    """Archive the current season and scaffold a fresh one. Returns process
    exit code (0 ok, 2 on guard failure)."""
    cfg = _season_config.load_config(config_path)
    old_year = cfg["current"]["year"]
    archive_path = index_path.parent / f"{old_year}.html"

    if archive_path.exists() and not force:
        print(f"[error] archive {archive_path.name} already exists; use --force to overwrite",
              file=sys.stderr)
        return 2
    try:
        new_cfg = _season_config.rollover(cfg, new_year, season_id, team_id, team_name)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2

    # 1. Freeze the current page as the archive (byte-for-byte).
    import shutil
    shutil.copyfile(index_path, archive_path)
    print(f"[archive] {index_path.name} -> {archive_path.name}")

    # 2. Persist the new config and load it into the live globals.
    _season_config.save_config(new_cfg, config_path)
    _init_season(new_cfg)
    print(f"[config] current season is now {new_year} (season_id={season_id})")

    # 3. Regenerate index.html against the new (mostly empty) season.
    html_old = index_path.read_text(encoding="utf-8")
    html_new = regenerate_index(html_old, new_cfg)
    index_path.write_text(html_new, encoding="utf-8")
    print(f"[scaffold] wrote preseason {new_year} shell to {index_path.name}")
    return 0
```

- [ ] **Step 5: Wire argparse**

In `main()`, add the arguments:

```python
    p.add_argument("--start-season", type=int, metavar="YEAR",
                   help="Roll over to a new season: archive the current one and "
                        "scaffold a fresh YEAR shell. Requires --season-id.")
    p.add_argument("--season-id", help="gamesheet season ID for the new season")
    p.add_argument("--team-id", help="(optional) team ID for the new season")
    p.add_argument("--team-name", help="(optional) team name; defaults to current")
    p.add_argument("--force", action="store_true",
                   help="Overwrite an existing <year>.html archive")
```

And near the top of `main()` (after `args = p.parse_args(argv)` and the
`index_path` resolution, before the `--check-played` branch), add:

```python
    if args.start_season:
        return run_rollover(
            new_year=args.start_season, season_id=args.season_id or "",
            team_id=args.team_id, team_name=args.team_name,
            index_path=index_path, force=args.force,
        )
```

- [ ] **Step 6: Run the tests**

Run: `python -m unittest scripts.test_update_data -v`
Expected: PASS (all tests, including TestRollover)

- [ ] **Step 7: Confirm normal update still works**

Run: `python scripts/update_data.py --dry-run`
Expected: same behavior as before the refactor (2026 data, clean diff). No
archive created (we did not pass `--start-season`).

- [ ] **Step 8: Commit**

```bash
git add scripts/update_data.py scripts/test_update_data.py
git commit -m "Multi-season: --start-season rollover command (archive + scaffold + guards)"
```

---

## Task 6: Runbook docs + full verification

**Files:**
- Create: `docs/ROLLOVER.md`
- (verification only) `index.html`, archives

- [ ] **Step 1: Write the runbook**

Create `docs/ROLLOVER.md`:

```markdown
# Rolling over to a new season

When the league publishes the next season on gamesheetstats.com:

1. Find the new **season ID** — open the league's new-season page on
   gamesheetstats.com; the ID is the number in the URL
   (`/seasons/<ID>`). The team name stays "Team Fayetteville".
2. From the repo root, run once:

   ```
   python scripts/update_data.py --start-season <YEAR> --season-id <NEW_ID>
   ```

   This:
   - copies the current `index.html` to `<previous-year>.html` (the frozen
     archive, served at `/<previous-year>`),
   - updates `scripts/season_config.json`,
   - regenerates `index.html` as a preseason shell.
3. Review the diff, then commit and push. The nightly cron takes over and fills
   in roster/schedule/standings/scores automatically as the league publishes them.
4. (Optional) Update player photos for the new roster: edit the `PLAYERS` list
   in `fetch_ep_photos.py` with each player's EliteProspects URL and run
   `python fetch_ep_photos.py`. New opponent logos: add files to the repo root
   and an entry to `TEAM_LOGOS` in `scripts/update_data.py`.

Safety: the command refuses to overwrite an existing `<year>.html` archive
(pass `--force` to override) and refuses if `--season-id` is missing or the new
year equals the current one.
```

- [ ] **Step 2: Full local verification of the rollover (throwaway, not committed)**

```bash
cp index.html /tmp/index.backup.html
cp scripts/season_config.json /tmp/season_config.backup.json
python scripts/update_data.py --start-season 2027 --season-id 14572
```

(Using 14572 here only as a stand-in so the fetch returns data; this is a local
dry test.) Start the server and check in the browser (Playwright MCP):
- `/` shows the 2027 page; matchup card shows the Preseason state IF the season
  has no Foxes games for that ID (with 14572 it will show real 2026 data — that's
  fine for exercising the pipeline). Confirm the page renders, Seasons nav now
  lists "2026", and `data-season-year` reads 2027.
- `/2026` (the new `2026.html`) renders the frozen final 2026 page with all
  assets resolving (logos, photos, bracket scores), and its Seasons nav has a
  working "Current season" link.

- [ ] **Step 3: Restore after the throwaway test**

```bash
cp /tmp/index.backup.html index.html
cp /tmp/season_config.backup.json scripts/season_config.json
rm -f 2026.html
git status   # confirm working tree matches HEAD (only the committed changes remain)
```

Expected: `git status` shows no stray `2026.html` and `index.html` /
`season_config.json` restored to their committed state.

- [ ] **Step 4: Commit the runbook**

```bash
git add docs/ROLLOVER.md
git commit -m "Multi-season: add season rollover runbook"
```

- [ ] **Step 5: Run the full test suite one final time**

Run: `python -m unittest scripts.test_update_data -v`
Expected: PASS (all tests)

---

## Self-Review (completed during planning)

- **Spec coverage:** Layout/archives → Task 5 (`run_rollover` writes root-level
  `<year>.html`). Season config → Task 1. season-meta/year → Task 2. Preseason
  states → Task 3. Seasons nav → Task 4. Rollover command → Task 5. EP photos /
  logos / runbook → Task 6. Unchanged cron → no task needed (verified `main()`
  normal path preserved in Task 5 Step 3/7).
- **Type/name consistency:** `build_season_meta_block(year)`,
  `build_seasons_nav_block(cfg)`, `build_preseason_matchup_block(year)`,
  `run_rollover(...)`, `regenerate_index(html_old, cfg)`, `_init_season(cfg)`,
  `season_config.{load_config,save_config,rollover,CONFIG_PATH,DEFAULTS}` — used
  consistently across tasks.
- **Placeholder scan:** no TBD/TODO; every code step shows full code.
- **Known follow-ups (out of scope, documented in spec):** runtime
  `seasons.json` for archive menu freshness; auto opponent-logo fetch.
