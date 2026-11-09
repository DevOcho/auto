#!/usr/bin/env python3
"""Fail if the built docs site links to a page or heading that doesn't exist.

`zensical build --strict` catches a missing page, but not a link to a heading
anchor that was renamed. Run after the build: python3 .github/scripts/check_site_links.py site
"""

import re
import sys
import urllib.parse
from pathlib import Path


def main(site_dir):
    """Check every relative link in every built page; return the number broken."""
    root = Path(site_dir).resolve()
    # 404.html is served from any depth, so its links are absolute (/auto/...)
    pages = [page for page in root.rglob("*.html") if page.name != "404.html"]
    anchors = {
        page: set(re.findall(r'\bid="([^"]+)"', page.read_text())) for page in pages
    }
    broken = set()
    for page in pages:
        for href in re.findall(r'href="([^"]+)"', page.read_text()):
            if href.startswith(("http:", "https:", "mailto:", "javascript:", "data:")):
                continue
            url, _, fragment = href.partition("#")
            target = (
                page if not url else (page.parent / urllib.parse.unquote(url)).resolve()
            )
            if target.is_dir():
                target = target / "index.html"
            where = page.relative_to(root).as_posix()
            if not target.exists():
                broken.add(f"{where}: {href} (no such page)")
            elif (
                fragment
                and target.suffix == ".html"
                and fragment not in anchors.get(target, set())
            ):
                broken.add(f"{where}: {href} (no such heading)")
    for line in sorted(broken):
        print(line)
    print(f"{len(pages)} pages checked, {len(broken)} broken links")
    return len(broken)


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1] if len(sys.argv) > 1 else "site") else 0)
