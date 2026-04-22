"""Emit a minified sibling file `index.min.html` with inline <style> and <script>
blocks squeezed. The original index.html stays human-editable; the min file is
what you deploy when you want the smallest payload on mobile.
"""
from __future__ import annotations
import re
import sys

SRC = "index.html"
OUT = "index.min.html"

def minify_css(css: str) -> str:
    # Strip block comments
    css = re.sub(r"/\*[\s\S]*?\*/", "", css)
    # Collapse whitespace (but preserve space-separated values inside rules)
    css = re.sub(r"\s+", " ", css)
    # Tighten spaces around punctuation
    css = re.sub(r"\s*([{};:,>+~])\s*", r"\1", css)
    # Recover the one space needed between selectors (e.g. `.a .b`) — handled by not touching
    # spaces inside selector parens / attribute brackets. The regex above may have over-collapsed;
    # re-expand leading spaces at line starts where CSS would need them. In practice the next
    # regex keeps valid selectors intact because we only trim around punctuation.
    # Remove the final semicolon before each `}`
    css = re.sub(r";}", "}", css)
    return css.strip()

def minify_js(js: str) -> str:
    # Strip // line comments (but not inside strings — naive, works for our code)
    out_lines = []
    for line in js.splitlines():
        # Find '//' not inside quotes — simple heuristic: only at start of stripped line
        stripped = line.lstrip()
        if stripped.startswith("//"):
            continue
        # Inline '// …' trailing comment (again, naive)
        # Keep original line but drop trailing ' // …' after a semicolon/brace
        line = re.sub(r"\s+//\s.*$", "", line)
        out_lines.append(line)
    js = "\n".join(out_lines)
    # Strip /* ... */ comments
    js = re.sub(r"/\*[\s\S]*?\*/", "", js)
    # Collapse runs of blank lines
    js = re.sub(r"\n\s*\n+", "\n", js)
    # Collapse surrounding whitespace
    js = re.sub(r"[ \t]+", " ", js)
    js = re.sub(r" ?\n ?", "\n", js)
    return js.strip()

def main() -> int:
    with open(SRC, "r", encoding="utf-8") as f:
        html = f.read()

    # Minify <style> blocks
    def style_sub(m):
        return m.group(1) + minify_css(m.group(2)) + m.group(3)
    html = re.sub(r"(<style[^>]*>)([\s\S]*?)(</style>)", style_sub, html)

    # Minify <script> blocks. Skip external (src=) scripts.
    def script_sub(m):
        open_tag = m.group(1)
        body = m.group(2)
        close_tag = m.group(3)
        if "src=" in open_tag:
            return m.group(0)
        if body.strip():
            body = minify_js(body)
        return open_tag + body + close_tag
    html = re.sub(r"(<script\b[^>]*>)([\s\S]*?)(</script>)", script_sub, html)

    # Collapse HTML-level runs of blank lines between tags (lightweight)
    html = re.sub(r"\n\s*\n+", "\n", html)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)

    import os
    src_kb = os.path.getsize(SRC) // 1024
    out_kb = os.path.getsize(OUT) // 1024
    pct = 100 * (1 - out_kb / src_kb) if src_kb else 0
    print(f"{SRC}: {src_kb} KB -> {OUT}: {out_kb} KB  ({pct:.0f}% smaller)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
