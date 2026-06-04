# Multi-Season Architecture — Design Spec

- **Date:** 2026-06-03
- **Status:** Approved (pending written-spec review)
- **Topic:** Support multiple seasons on the Fayetteville Foxes site — freeze 2026, roll over to 2027 (and beyond) gracefully.

## Context

The site is a single `index.html` with auto-managed regions delimited by
`<!-- BEGIN auto:NAME -->` / `<!-- END auto:NAME -->` comments. A nightly
GitHub Actions cron runs `scripts/update_data.py`, which fetches the league
data from the gamesheetstats.com API (keyed by a hardcoded `SEASON_ID` and
`FAYETTEVILLE_TEAM_ID`) and rewrites those regions, then commits `index.html`.

Existing auto-regions: `standings`, `skaters`, `goalies`, `schedule-list`,
`ticker`, `matchup`, `playoff-results`. The matchup region already has three
visual states it can render: an upcoming-game card, a "Playoffs ahead" holding
card, and a season-complete wrap-up card.

The 2026 season is over (championship decided, bracket + scores populated).

### Why automatic season discovery is not possible

Investigated during design. gamesheet season IDs are **global and
non-sequential** across the whole platform — ID `14573` (one above ours) is a
different league entirely ("Spring Bantam/HS House"). The league page exposes
only the season *title* ("Triangle High School Hockey League - 2026"), with no
league ID and no season-list/search endpoint (all candidate endpoints 404).
There is therefore **no reliable way to auto-discover next year's season ID**;
a guessed ID would silently pull the wrong league. Hence the rollover supplies
the new season ID manually (decision below).

## Goals

- Preserve the 2026 season exactly as-is, viewable as a frozen archive.
- `/` always shows the current season with full functionality.
- A single manual command rolls over to a new season: archive the old one,
  scaffold a fresh current-season shell, and point the updater at the new IDs.
- The new season starts with graceful "preseason" placeholders and auto-fills
  from the API as roster/schedule/standings/games appear.

## Non-Goals (explicitly out of scope)

- No cross-season combined stats, records, or history — each season is
  independent; 2026 is simply frozen.
- No automatic carry-forward of player photos.
- No automatic discovery of new opponent team logos (added manually).
- No automatic (date-triggered) rollover — rollover is a manual command.

## Decisions (from brainstorming)

1. **Layout:** Live home + frozen archive. `/` = current season; `/<year>` =
   frozen archive.
2. **Flip mechanism:** Manual one-time rollover command. Run when the league
   publishes the new season; it takes the new season/team IDs as arguments.
3. **Preseason UI:** Graceful per-section placeholders that auto-fill.

## Design

### 1. File layout & archiving

Archives are **root-level `<year>.html` files**, not subdirectories.

- `index.html` → always the current season, served at `/`.
- `2026.html` → frozen 2026 archive, served at `/2026` (via existing
  `vercel.json`: `cleanUrls: true`, `trailingSlash: false`).

**Rationale (key call):** the page uses *relative* asset paths
(`brand_assets/…`, `South%20Apex.png`, `player_photos/…`). At the clean URL
`/2026` (no trailing slash), a relative path resolves against `/2026` →
`/brand_assets/…` (repo root), where the assets actually live. So a
**byte-for-byte copy of `index.html` works as the archive with zero
path rewriting**. A `/2026/index.html` subfolder would resolve relatives to
`/2026/brand_assets/…` and break every asset. Root-level archives keep the
mechanism trivial and robust.

The archive is a literal snapshot of the final current page. Its JS reads only
embedded data (standings table, `playoff-results` JSON, `season-meta`), so it
renders the frozen final state with no live/network dependency.

### 2. Season config

Externalize per-season identity into `scripts/season_config.json`:

```json
{
  "current":  { "year": 2026, "season_id": "14572", "team_id": "498107", "team_name": "Team Fayetteville" },
  "archives": []
}
```

- `update_data.py` reads `current` instead of the hardcoded `SEASON_ID` /
  `FAYETTEVILLE_TEAM_ID` / `FAYETTEVILLE_TEAM_NAME` constants.
- If the file is missing, fall back to today's hardcoded constants (migration
  safety — nothing breaks before the file is committed).
- **Note:** `team_id` is currently unused by the updater — all data is filtered
  by `team_name` ("Team Fayetteville"). It is kept in config for reference and
  potential future use, but the season identity that actually matters is
  `season_id` + `team_name`. The team name is expected to stay "Team
  Fayetteville" across seasons.
- `TEAM_DISPLAY` and `TEAM_LOGOS` stay in code (league-wide, slow-changing).
  New opponent logos for a future season are appended to `TEAM_LOGOS` manually;
  existing logos carry forward unchanged. Teams without a mapped logo fall back
  to the API-provided logo URL or a blank crest (existing behavior).

### 3. Rollover command

```
python scripts/update_data.py --start-season 2027 --season-id <new_id> [--team-id <new_id>] [--team-name "Team Fayetteville"]
```

`--season-id` is the only required input. `--team-id` is optional (stored but
unused; see §2). `--team-name` defaults to the current value ("Team
Fayetteville").

Steps, in order:

1. Load `season_config.json`; `old_year = current.year`.
2. **Safety guards** — abort with a clear message if: `--season-id` missing;
   new year == current year; or `<old_year>.html` already exists. `--force`
   overrides the last check.
3. Copy current `index.html` → `<old_year>.html` (the archive; never touched
   again by the updater).
4. Rewrite `season_config.json`: append `{year: old_year}` to `archives`; set
   `current` to the new year + IDs (`team_name` defaults to the prior value).
5. Update `index.html`'s `season-meta` and `seasons-nav` regions (§4, §5).
6. Run the normal update pipeline against the new season — fills every region
   with whatever the API currently exposes (typically all preseason
   placeholders at first).
7. Print a summary. The user reviews the diff and commits (the rollover does
   not push on its own).

### 4. Data-driven year (no brittle find-replace)

Add a small embedded `season-meta` auto-region (same pattern as
`playoff-results`):

```html
<!-- BEGIN auto:season-meta -->
<script type="application/json" id="season-meta">{"year":2026}</script>
<!-- END auto:season-meta -->
```

A small JS reads it and stamps the year into display hooks:

- `<span data-season-year>2026</span>` — full year (hero eyebrow, etc.)
- `data-season-yy` — the hero "26" numeral (computed `year % 100`)

Implementation audits `index.html` for every hardcoded "2026"/"26" in static
chrome and converts them to hooks. Because each archive's `season-meta` holds
its own year, the data-driven year renders correctly on both the live page and
every frozen archive.

### 5. "Seasons" nav menu

A `seasons-nav` auto-region the updater regenerates from config — a dropdown
listing the current season (`/`) plus each archive (`/2026`, …). Mirrored into
the mobile menu. Frozen archives keep the menu they were snapshotted with, but
always include a "Current season →" link back to `/` (so you can always reach
the latest, which lists everything). Static for now; a runtime `seasons.json`
fetch (to keep old archives showing newer seasons) is a deferred option — not
needed for two seasons.

### 6. Preseason empty-states

A third matchup state joins the existing two. Logic in `build_matchup_block`:

- champion decided → **season-complete card** (existing)
- games played, none upcoming, no champion → **"Playoffs ahead"** (existing)
- **no Foxes games played at all → "Preseason — schedule TBA"** (new)

Graceful empties when the API returns nothing yet:

- **Standings** → "The `<year>` season hasn't started yet."
- **Roster** (skaters/goalies) → "Roster announced closer to the season."
- **Schedule** → "Schedule TBA."
- **Ticker** → hidden (already returns empty string).
- **Bracket** → seeds show TBD (already degrades with empty standings).

All sections auto-fill the moment the API exposes data; no further action.

### 7. Player photos & logos

- `fetch_ep_photos.py` is **retained** as the manual roster-photo tool. For a
  new season the user updates its `PLAYERS` list (slug + EliteProspects profile
  URL) and runs it; images land in the shared `player_photos/` directory. Slugs
  must match the site's `photoSlug`/`slugify` so photos resolve. The rollover
  does **not** touch this script or `player_photos/`. Photos are not
  auto-carried-forward; returning players keep their existing file by slug, new
  players are added via this script, and unmatched players fall back to the
  existing photo placeholder.
- Team logos: see §2 — carry existing forward, add new ones manually.

### 8. Unchanged

The nightly cron (`update-data.yml`) is untouched: it keeps running
`update_data.py`, which now targets `config.current` and never touches
archives. The rollover is a separate, manually invoked mode of the same script.

## Implementation outline (components)

1. **Season config module** — load/save `season_config.json`; derive
   `SEASON_ID`/`TEAM_ID`/`TEAM_NAME`/`year` from it with constant fallback.
2. **`season-meta` region + year hooks** — new auto-region; builder; JS
   stamper; audit & convert hardcoded years in `index.html`.
3. **Preseason states** — new matchup branch + empty-state output for
   standings/skaters/goalies/schedule builders.
4. **Rollover command** — `--start-season` argparse mode: archive + config
   rewrite + region updates + pipeline run + safety guards.
5. **Seasons nav** — `seasons-nav` (+ mobile) auto-region; builder from config;
   "Current season →" link present on archives.
6. **Docs** — short README/runbook note on how to roll over a season.

## Edge cases & safety

- Refuse to overwrite an existing `<year>.html` archive (guard + `--force`).
- Missing/malformed `season_config.json` → fall back to hardcoded constants.
- API returns empty collections mid-rollover → preseason placeholders (not
  errors); a genuine API/HTTP failure still exits non-zero and fails the cron.
- Archives must remain self-contained: no region in an archive may depend on a
  network fetch (verified: all read embedded data).

## Testing / verification

- Unit-level: builders produce correct output for empty inputs (preseason) and
  populated inputs (regression on current 2026 data — output unchanged).
- Rollover dry-run: run `--start-season` against a scratch copy; assert
  `2026.html` created, config updated, `index.html` reset to preseason, year
  hooks read 2027.
- Browser check (localhost): 2027 preseason shell renders placeholders; `/2026`
  archive renders the frozen final state with all assets resolving; Seasons nav
  links work both directions; mobile layout intact.

## Deferred / future considerations

- Runtime `seasons.json` so old archives always list newer seasons.
- Automatic new-opponent logo fetch (currently manual).
