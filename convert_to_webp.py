"""Convert PNG/JPG assets to WebP for faster mobile delivery.

Keeps originals untouched so the <picture> fallback still works on any browser
that can't decode WebP. Skips anything already converted (idempotent).
"""
from __future__ import annotations
import os
import sys
from PIL import Image

ROOTS = ["brand_assets", "player_photos", "."]
SKIP_NAMES = {"icon", "favicon"}
TARGETS_EXT = {".png", ".jpg", ".jpeg"}
WEBP_QUALITY = 82   # visually lossless enough for logos/headshots, ~50% size of JPEG

def should_convert(path: str) -> bool:
    low = path.lower()
    if not any(low.endswith(ext) for ext in TARGETS_EXT):
        return False
    if any(s in os.path.basename(low) for s in SKIP_NAMES):
        return False
    return True

def convert(path: str) -> tuple[int, int] | None:
    out = os.path.splitext(path)[0] + ".webp"
    if os.path.exists(out):
        return None  # already converted; skip
    try:
        with Image.open(path) as im:
            if im.mode in ("P", "LA"):
                im = im.convert("RGBA")
            elif im.mode == "CMYK":
                im = im.convert("RGB")
            im.save(out, "WEBP", quality=WEBP_QUALITY, method=6)
        return os.path.getsize(path), os.path.getsize(out)
    except Exception as e:
        print(f"[FAIL] {path}: {e}")
        return None

def main() -> int:
    seen = 0
    made = 0
    saved_src = 0
    saved_dst = 0
    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        for entry in os.listdir(root):
            full = os.path.join(root, entry)
            if not os.path.isfile(full):
                continue
            if not should_convert(full):
                continue
            seen += 1
            result = convert(full)
            if result is None:
                print(f"[SKIP]   {full}")
                continue
            src_sz, dst_sz = result
            saved_src += src_sz
            saved_dst += dst_sz
            made += 1
            print(f"[OK]     {full:<60}  {src_sz//1024:>4} KB -> {dst_sz//1024:>4} KB")
    if made:
        pct = 100 * (1 - saved_dst / saved_src) if saved_src else 0
        print(f"\nConverted {made} of {seen} files. {saved_src//1024} KB -> {saved_dst//1024} KB ({pct:.0f}% smaller).")
    else:
        print(f"\nNothing to convert ({seen} candidates, all already had .webp siblings).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
