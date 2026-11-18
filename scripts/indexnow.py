#!/usr/bin/env python3
"""
indexnow.py — tell IndexNow (Bing, Yandex, Seznam, Naver…) which pages changed.

Two steps, both run by .github/workflows/main.yml:

  # In the build job, after `hugo` and BEFORE the new site is deployed:
  python3 scripts/indexnow.py diff --live https://eltelar.xyz --public public --out indexnow-urls.txt

  # In a job that runs AFTER the deploy succeeded:
  python3 scripts/indexnow.py submit --urls indexnow-urls.txt --host eltelar.xyz --key <key>

`diff` compares the live sitemap (old) with the freshly built one (new) and
writes every URL that is new or whose <lastmod> changed. With enableGitInfo,
lastmod is the date of the last commit touching that page, so edits count.

The key must also be served at https://<host>/<key>.txt (static/<key>.txt).
Pure stdlib, no dependencies.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
ENDPOINT = "https://api.indexnow.org/indexnow"
MAX_URLS = 10_000  # IndexNow per-request limit
UA = "eltelar.xyz-indexnow/1.0"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def parse(xml_bytes):
    """Return ("index", [child sitemap locs]) or ("urlset", {loc: lastmod})."""
    root = ET.fromstring(xml_bytes)
    if root.tag.endswith("sitemapindex"):
        return "index", [e.text.strip() for e in root.findall("sm:sitemap/sm:loc", NS)]
    urls = {}
    for u in root.findall("sm:url", NS):
        loc = u.find("sm:loc", NS).text.strip()
        lm = u.find("sm:lastmod", NS)
        urls[loc] = lm.text.strip() if lm is not None and lm.text else ""
    return "urlset", urls


def collect(read, root_loc):
    """Follow a sitemap (index) and return {loc: lastmod} for every page."""
    kind, data = parse(read(root_loc))
    if kind == "urlset":
        return data
    urls = {}
    for child in data:
        urls.update(collect(read, child))
    return urls


def cmd_diff(args):
    live = args.live.rstrip("/")

    def read_public(loc):
        # Map a sitemap URL on the live host to the file in the build output.
        if not loc.startswith(live + "/"):
            raise ValueError(f"sitemap {loc} is not under {live}")
        with open(os.path.join(args.public, loc[len(live) + 1 :]), "rb") as f:
            return f.read()

    new = collect(read_public, f"{live}/sitemap.xml")
    try:
        old = collect(fetch, f"{live}/sitemap.xml")
    except (urllib.error.URLError, ET.ParseError, OSError) as e:
        print(f"could not read live sitemap ({e}); submitting every page", file=sys.stderr)
        old = {}

    changed = sorted(loc for loc, lm in new.items() if old.get(loc) != lm)
    with open(args.out, "w") as f:
        f.write("\n".join(changed) + ("\n" if changed else ""))
    print(f"{len(changed)} of {len(new)} URLs are new or changed")


def cmd_submit(args):
    try:
        urls = [l.strip() for l in open(args.urls) if l.strip()]
    except FileNotFoundError:
        urls = []
    if not urls:
        print("nothing to submit")
        return
    for i in range(0, len(urls), MAX_URLS):
        batch = urls[i : i + MAX_URLS]
        body = json.dumps({
            "host": args.host,
            "key": args.key,
            "keyLocation": f"https://{args.host}/{args.key}.txt",
            "urlList": batch,
        }).encode()
        req = urllib.request.Request(
            ENDPOINT, data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                print(f"submitted {len(batch)} URLs: HTTP {r.status}")
        except urllib.error.HTTPError as e:
            # 200/202 = accepted. 4xx means the key or URLs were rejected: fail loudly.
            print(f"IndexNow rejected the request: HTTP {e.code} {e.read().decode(errors='replace')}", file=sys.stderr)
            sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diff")
    d.add_argument("--live", required=True, help="site root, e.g. https://eltelar.xyz")
    d.add_argument("--public", required=True, help="Hugo build output directory")
    d.add_argument("--out", required=True)
    s = sub.add_parser("submit")
    s.add_argument("--urls", required=True)
    s.add_argument("--host", required=True)
    s.add_argument("--key", required=True)
    args = ap.parse_args()
    {"diff": cmd_diff, "submit": cmd_submit}[args.cmd](args)


if __name__ == "__main__":
    main()
