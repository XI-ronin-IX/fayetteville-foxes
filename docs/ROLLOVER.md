# Rolling over to a new season

When the league publishes the next season on gamesheetstats.com:

1. **Find the new season ID.** Open the league's new-season page on
   gamesheetstats.com; the ID is the number in the URL (`/seasons/<ID>`). The
   team name stays "Team Fayetteville". (There is no reliable way to discover
   this automatically — season IDs are global and non-sequential — so it is
   supplied by hand here.)

2. **Run the rollover once**, from the repo root:

   ```
   python scripts/update_data.py --start-season <YEAR> --season-id <NEW_ID>
   ```

   This:
   - copies the current `index.html` to `<previous-year>.html` (the frozen
     archive, served at `/<previous-year>` thanks to `cleanUrls`),
   - updates `scripts/season_config.json` (old season → `archives`, new season →
     `current`),
   - regenerates `index.html` as a preseason shell — standings/schedule/stats
     show "coming soon" placeholders, the record/standing read 0-0-0 / `#/8`,
     and the roster + coaching staff reset to "TBA" placeholder cards (last
     season's players/coaches do not carry over).

3. **Review the diff, then commit and push.** The nightly cron takes over and
   fills in roster / schedule / standings / scores automatically as the league
   publishes them. No further manual data entry is needed.

4. **(Optional) Update player photos for the new roster.** Edit the `PLAYERS`
   list in `fetch_ep_photos.py` with each player's EliteProspects profile URL
   (slug must match the site's `photoSlug`), then run:

   ```
   python fetch_ep_photos.py
   ```

   Photos land in `player_photos/` (shared across seasons; returning players
   keep their existing file).

5. **(Optional) New opponent logos.** Add the image files to the repo root and
   a matching entry to `TEAM_LOGOS` / `TEAM_DISPLAY` in
   `scripts/update_data.py`. Existing logos carry forward automatically; teams
   without a mapped logo fall back to the API logo or a blank crest.

## Safety

The rollover command refuses to:
- overwrite an existing `<year>.html` archive (pass `--force` to override),
- run without `--season-id`,
- roll over to the year that is already current.

## Tests

```
python scripts/test_update_data.py
```

Covers the config logic, the preseason empty-states, the Seasons nav, and an
end-to-end rollover (archive + scaffold) with the API mocked out.
