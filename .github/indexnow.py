"""Tell IndexNow (Bing, Yandex, Seznam, Naver, Yep) which pages changed.

  python3 .github/indexnow.py <before-sha> <after-sha>   new or edited episodes + home and archives
  python3 .github/indexnow.py --all                      every URL in the live sitemap

New episodes are always sent. Edited ones are sent only when a handful
changed: a site-wide template change touches every page, and those don't
need re-crawling. Same approach as hillmole.com.
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

LIVE = "https://thechrisabrahamshow.com"
HOST = "thechrisabrahamshow.com"
EDITED_LIMIT = 20


def key():
    return re.search(r'^INDEXNOW_KEY = "([0-9a-f]+)"', open("build.py").read(), re.M).group(1)


def changed_urls(before, after):
    if not before or set(before) == {"0"}:
        return []
    diff = lambda f: subprocess.run(["git", "diff", "--name-only", "--diff-filter=" + f, before, after, "--", "episodes/"],
                                    capture_output=True, text=True, check=True).stdout.split()
    added, edited = diff("A"), diff("M")
    pages = added + (edited if len(edited) <= EDITED_LIMIT else [])
    if not pages:
        return []
    return [f"{LIVE}/{p}" for p in pages] + [LIVE + "/", LIVE + "/archives.html"]


def sitemap_urls():
    with urllib.request.urlopen(LIVE + "/sitemap.xml", timeout=30) as r:
        tree = ET.fromstring(r.read())
    return [e.text.strip() for e in tree.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]


def wait_live(urls, timeout=900):
    """GitHub Pages deploys after the push; wait until new pages answer."""
    deadline, pending = time.time() + timeout, list(urls)
    while pending and time.time() < deadline:
        still = []
        for u in pending:
            try:
                urllib.request.urlopen(urllib.request.Request(u, method="HEAD"), timeout=20)
            except Exception:
                still.append(u)
        pending = still
        if pending:
            time.sleep(30)
    return [u for u in urls if u not in pending]


def submit(urls, k):
    body = json.dumps({"host": HOST, "key": k, "keyLocation": f"{LIVE}/{k}.txt", "urlList": urls}).encode()
    req = urllib.request.Request("https://api.indexnow.org/indexnow", data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"IndexNow: HTTP {r.status} for {len(urls)} URLs")


def main():
    urls = sitemap_urls() if sys.argv[1:] == ["--all"] else changed_urls(*sys.argv[1:3])
    if not urls:
        print("no episode changes; nothing to submit")
        return
    urls = wait_live(sorted(set(urls)))
    for i in range(0, len(urls), 10000):
        submit(urls[i:i + 10000], key())


if __name__ == "__main__":
    main()
