"""Download Elite Prospects profile photos for each player to player_photos/{slug}.ext.

One-time script. The site's hero image references files.eliteprospects.com which is
reached via either the <meta property="og:image"> tag or a direct <img> in the profile
header. We try og:image first and fall back to the first files.eliteprospects.com hit.
"""
from __future__ import annotations
import os
import re
import sys
import time
import urllib.request
from urllib.error import URLError, HTTPError

PLAYERS = [
    ("john-bishop",      "https://www.eliteprospects.com/player/1023554/john-bishop"),
    ("anthony-trujillo", "https://www.eliteprospects.com/player/1195911/anthony-trujillo"),
    ("austin-bertsch",   "https://www.eliteprospects.com/player/1023544/austin-bertsch"),
    ("david-carey",      "https://www.eliteprospects.com/player/1023569/david-carey"),
    ("colton-reuter",    "https://www.eliteprospects.com/player/986989/colton-reuter"),
    ("liam-tutor",       "https://www.eliteprospects.com/player/1195855/liam-tutor"),
    ("brody-howk",       "https://www.eliteprospects.com/player/1195716/brody-howk"),
    ("caden-mackeen",    "https://www.eliteprospects.com/player/1040864/caden-mackeen"),
    ("logan-stoeckel",   "https://www.eliteprospects.com/player/1195322/logan-stoeckel"),
    ("seth-marx",        "https://www.eliteprospects.com/player/1195188/seth-marx"),
    ("james-williamson", "https://www.eliteprospects.com/player/1195097/james-williamson"),
    ("wyatt-chatterson", "https://www.eliteprospects.com/player/1195841/wyatt-chatterson"),
    ("cale-rhodes",      "https://www.eliteprospects.com/player/1195715/cale-rhodes"),
    ("nathaniel-crom",   "https://www.eliteprospects.com/player/1195904/nathaniel-crom"),
    ("gabriel-gagnon",   "https://www.eliteprospects.com/player/1195088/gabriel-gagnon"),
    ("seth-waller",      "https://www.eliteprospects.com/player/1195183/seth-waller"),
    ("ean-reuter",       "https://www.eliteprospects.com/player/1195822/ean-reuter"),
    ("colton-lilge",     "https://www.eliteprospects.com/player/1195818/colton-lilge"),
]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
OUTDIR = "player_photos"
os.makedirs(OUTDIR, exist_ok=True)

OG_RE    = re.compile(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', re.I)
IMG_RE   = re.compile(r'https://files\.eliteprospects\.com/[^"\'\s>]+\.(?:jpe?g|png)', re.I)
PLACE_RE = re.compile(r'/placeholder|/default|/empty', re.I)

def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()

def find_image_url(html: str) -> str | None:
    og = OG_RE.search(html)
    if og and "files.eliteprospects.com" in og.group(1) and not PLACE_RE.search(og.group(1)):
        return og.group(1)
    for m in IMG_RE.finditer(html):
        if not PLACE_RE.search(m.group(0)):
            return m.group(0)
    return None

def main() -> int:
    failures = []
    for slug, page_url in PLAYERS:
        try:
            html = fetch(page_url).decode("utf-8", errors="replace")
            img_url = find_image_url(html)
            if not img_url:
                print(f"[ NO IMG ] {slug}")
                failures.append(slug)
                continue
            ext = ".png" if img_url.lower().split("?")[0].endswith(".png") else ".jpg"
            out = os.path.join(OUTDIR, slug + ext)
            img_bytes = fetch(img_url)
            with open(out, "wb") as f:
                f.write(img_bytes)
            size_kb = len(img_bytes) // 1024
            print(f"[   OK   ] {slug:<20} {size_kb:>4} KB  ({img_url.rsplit('/', 1)[-1]})")
            time.sleep(0.3)  # be a good citizen
        except HTTPError as e:
            print(f"[ HTTP {e.code} ] {slug}  {page_url}")
            failures.append(slug)
        except URLError as e:
            print(f"[ URL ERR ] {slug}  {e.reason}")
            failures.append(slug)
        except Exception as e:
            print(f"[ ERROR ] {slug}  {type(e).__name__}: {e}")
            failures.append(slug)

    print()
    if failures:
        print(f"Failed: {len(failures)} / {len(PLAYERS)}  —  " + ", ".join(failures))
        return 1
    print(f"Downloaded {len(PLAYERS)} / {len(PLAYERS)} photos.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
