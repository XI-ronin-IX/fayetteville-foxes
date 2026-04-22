# Fayetteville Foxes — Varsity Ice Hockey

Official site for the Fayetteville Foxes, a high school ice hockey team in the Triangle HS Hockey League (Combined Division). Single-page static site hosted on Vercel at https://www.fayettevillehighschoolhockey.com.

## Local dev

```bash
node serve.mjs      # serves the project root at http://localhost:3000
```

`serve.mjs` is a zero-dependency Node server — no `npm install` required.

## Build tools

Two optional Python scripts for maintenance:

| Script | What it does |
|---|---|
| `convert_to_webp.py` | Converts every `.png`/`.jpg` asset under `brand_assets/`, `player_photos/`, and the project root into a `.webp` sibling. Idempotent. |
| `minify_inline.py` | Produces `index.min.html` with the inline `<style>`/`<script>` blocks squeezed. |
| `fetch_ep_photos.py` | Downloads player headshots from Elite Prospects profile pages into `player_photos/`. |

Each requires `pip install pillow openpyxl` (as applicable). Both are safe to re-run after edits.

## Deployment

Pushed to GitHub; auto-deployed to Vercel on every `main` merge. Custom domain is `www.fayettevillehighschoolhockey.com`.
