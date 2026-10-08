#!/usr/bin/env python3
"""Build thechrisabrahamshow.com from the Spotify for Creators feed.

    python3 build.py            fetch the feed, cache new art and transcripts, write the site
    python3 build.py --offline  rebuild from data/episodes.json without touching the network

Every episode keeps the address it was first given (data/episodes.json is the
record), so URLs never move when a title is edited on Spotify. Artwork and
transcripts are cached in the repo; the audio is never downloaded, and the
player uses each enclosure URL exactly as the feed gives it.
Needs Pillow (pip install pillow) for artwork.
"""
import datetime
import difflib
import hashlib
import html
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

FEED = "https://anchor.fm/s/c0f56fc/podcast/rss"
SITE = "https://thechrisabrahamshow.com"
SPOTIFY_SHOW = "https://open.spotify.com/show/5POax2UqdOPC2HhHxVU7O0"
HOME = "https://chrisabraham.com"
NAME = "The Chris Abraham Show"
AUTHOR = "Chris Abraham"
TZ = ZoneInfo("America/New_York")
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
IMAGES = os.path.join(ROOT, "assets", "images")
TRANSCRIPTS = os.path.join(DATA, "transcripts")
NS = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
      "podcast": "https://podcastindex.org/namespace/1.0"}
UA = {"User-Agent": "thechrisabrahamshow.com site builder"}
INDEXNOW_KEY = "3839412712300d0a246eead73969e20b"  # served at /<key>.txt; .github/indexnow.py submits with it
# Where to listen. Each link was checked live in October 2026.
LISTEN = [
    ("Spotify", SPOTIFY_SHOW),
    ("Apple Podcasts", "https://podcasts.apple.com/us/podcast/the-chris-abraham-show/id1468506258"),
    ("Overcast", "https://overcast.fm/itunes1468506258"),
    ("Pocket Casts", "https://pca.st/3YS9"),
    ("Castbox", "https://castbox.fm/channel/ChrisCast-id2169365"),
    ("Podcast Index", "https://podcastindex.org/podcast/1017827"),
    ("TuneIn", "https://tunein.com/podcasts/Comedy/ChrisCast-p1229481/"),
    ("RSS", FEED),
]
# Chris's profiles elsewhere (Person sameAs), as on hillmole.com.
SAME_AS = ["https://chrisabraham.com/", "https://hillmole.com/",
           "https://www.amazon.com/stores/Chris-Abraham/author/B0GD2HTQYC",
           "https://www.linkedin.com/in/chrisabraham", "https://x.com/chrisabraham",
           "https://chrisabraham.substack.com/", "https://www.reddit.com/user/chrisabraham",
           "https://github.com/chrisabraham", "https://www.youtube.com/chrisabraham"]
# Typos in the feed, fixed on this site only (the feed itself is untouched).
FIXES = [
    (r"\bwritine\b", "writing"), (r"\bdetergernt\b", "detergent"),
    (r"\bagressiveness\b", "aggressiveness"), (r"Mahalo newa, Aloha kako\b", "Mahalo nui, Aloha kakou"),
    (r"\b([Hh])yperfantasia\b", r"\1yperphantasia"), (r"prominent figur\b", "prominent figure"),
    (r"perception of relationsh\b", "perception of relationships"), (r"health mista\b", "health mistake"),
    (r"\b(another|subject|discourse|way|designed)a (<em>rambling)", r"\1: a \2"), (r"\btl:dr\b", "tl;dr"),
]
e = html.escape


# ---------------------------------------------------------------- fetching

def get(url, timeout=60, tries=3):
    """Fetch with two retries, so one network hiccup doesn't fail the daily run."""
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(10 * (attempt + 1))


def parse_feed(xml_bytes):
    ch = ET.fromstring(xml_bytes).find("channel")
    it = "{%s}" % NS["itunes"]
    show = {
        "title": ch.findtext("title", "").strip(),
        "description": ch.findtext("description", "").strip(),
        "image": (ch.find(it + "image").get("href") if ch.find(it + "image") is not None else ""),
    }
    episodes = []
    for item in ch.findall("item"):
        enc = item.find("enclosure")
        img = item.find(it + "image")
        tr = [t for t in item.findall("{%s}transcript" % NS["podcast"])]
        srt = next((t.get("url") for t in tr if "subrip" in (t.get("type") or "") or t.get("url", "").endswith(".srt")), "")
        episodes.append({
            "guid": item.findtext("guid", "").strip(),
            "title": item.findtext("title", "").strip(),
            "pubDate": item.findtext("pubDate", "").strip(),
            "season": item.findtext(it + "season", "").strip(),
            "episode": item.findtext(it + "episode", "").strip(),
            "duration": item.findtext(it + "duration", "").strip(),
            "description": (item.findtext("description") or item.findtext(it + "summary") or "").strip(),
            "image": img.get("href") if img is not None else "",
            "audio": enc.get("url") if enc is not None else "",
            "audio_type": enc.get("type", "audio/mpeg") if enc is not None else "",
            "link": item.findtext("link", "").strip(),
            "transcript_url": srt or "",
        })
    return show, episodes


# ---------------------------------------------------------------- records

def when(ep):
    d = datetime.datetime.strptime(ep["pubDate"], "%a, %d %b %Y %H:%M:%S %Z")
    return d.replace(tzinfo=datetime.timezone.utc).astimezone(TZ)


def slugify(title, limit=60):
    s = re.sub(r"['’]", "", title.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "episode"
    if len(s) > limit:
        words = s[:limit + 1].split("-")[:-1]  # whole words only
        while len(words) > 3 and (len(words[-1]) <= 2 or words[-1] in STOP):
            words.pop()  # no slug ending in "-a-21" or "-of-the"
        s = "-".join(words)
    return s


STOP = {"the", "and", "for", "with", "from", "into", "that", "this", "what", "why", "how", "are", "was", "but", "not", "you", "your"}


def merge(old, fresh):
    """Update records from the feed, keep every known episode and its path."""
    by_guid = {ep["guid"]: ep for ep in old}
    taken = {ep["path"] for ep in old}
    for f in fresh:
        rec = by_guid.get(f["guid"])
        if rec:
            rec.update({k: v for k, v in f.items() if v or k not in rec})
            continue
        d = when(f)
        base = "episodes/%04d/%02d/%s" % (d.year, d.month, slugify(f["title"]))
        path, n = base + ".html", 2
        while path in taken:
            path, n = "%s-%d.html" % (base, n), n + 1
        taken.add(path)
        f["path"] = path
        by_guid[f["guid"]] = f
    eps = sorted(by_guid.values(), key=lambda ep: (when(ep), ep["guid"]))
    return eps


# ---------------------------------------------------------------- assets

def image_name(url):
    return "ep-" + hashlib.sha1(url.encode()).hexdigest()[:12] + ".jpg"


def cache_image(url, name, size=800):
    """Download artwork once, store a resized JPEG. Returns the file name or ''."""
    path = os.path.join(IMAGES, name)
    if os.path.exists(path):
        return name
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(get(url))).convert("RGB")
        im.thumbnail((size, size), Image.LANCZOS)
        im.save(path, "JPEG", quality=84, optimize=True, progressive=True)
        return name
    except Exception as err:  # keep building; the report lists the gap
        print("  artwork failed:", url, err, file=sys.stderr)
        return ""


def show_assets(url):
    """Show artwork (Chris's cover, committed as show.jpg; the feed art is the
    fallback), favicons, the 700x206 banner and the share card, made once."""
    from PIL import Image, ImageFilter, ImageEnhance
    art = os.path.join(IMAGES, "show.jpg")
    if not os.path.exists(art):
        im = Image.open(io.BytesIO(get(url))).convert("RGB")
        big = im.copy(); big.thumbnail((1400, 1400), Image.LANCZOS)
        big.save(art, "JPEG", quality=86, optimize=True, progressive=True)
    im = Image.open(art)
    for px, name in ((180, "icon-180.png"), (32, "favicon-32.png")):
        p = os.path.join(IMAGES, name)
        if not os.path.exists(p):
            im.resize((px, px), Image.LANCZOS).save(p, optimize=True)
    banner = os.path.join(IMAGES, "banner.jpg")
    if not os.path.exists(banner):
        W, H = 1400, 412  # 2x of the 700x206 Hill Mole banner
        bg = im.resize((W, W), Image.LANCZOS).crop((0, (W - H) // 2, W, (W + H) // 2))
        bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(28))).enhance(.55)
        bg.paste(im.resize((H, H), Image.LANCZOS), ((W - H) // 2, 0))
        bg.save(banner, "JPEG", quality=84, optimize=True, progressive=True)
    card = os.path.join(IMAGES, "card.jpg")
    if not os.path.exists(card):
        W, H = 1200, 630
        bg = im.resize((W, W), Image.LANCZOS).crop((0, (W - H) // 2, W, (W + H) // 2))
        bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(28))).enhance(.55)
        bg.paste(im.resize((H, H), Image.LANCZOS), ((W - H) // 2, 0))
        bg.save(card, "JPEG", quality=84, optimize=True, progressive=True)


def cache_transcript(ep):
    path = os.path.join(TRANSCRIPTS, ep["guid"] + ".srt")
    if os.path.exists(path) or not ep.get("transcript_url"):
        return
    try:
        body = get(ep["transcript_url"]).decode("utf-8-sig")
    except Exception as err:
        print("  transcript not ready:", ep["title"], err, file=sys.stderr)
        return
    if "-->" in body:
        with open(path, "w") as f:
            f.write(body)


# ---------------------------------------------------------------- text

def fixed(s):
    for pat, to in FIXES:
        s = re.sub(pat, to, s or "")
    return s


def seo_text(s):
    """House rule for titles and descriptions: no dashes, hyphens or pipes."""
    s = re.sub(r"\s*\|\s*", ", ", s)
    seen = []
    def dash(m):
        seen.append(1)
        return ": " if len(seen) == 1 and ":" not in s else ", "
    s = re.sub(r"\s*[—–]\s*|\s+-+\s+", dash, s)
    s = re.sub(r"(?<=\w)-(?=\w)", " ", s).replace("-", " ")
    s = re.sub(r"\s*:\s*:", ":", s)
    return re.sub(r"\s+", " ", s).strip(" ,:;")


def stamp(t):
    h, m, rest = t.strip().replace(".", ",").split(":")
    s, ms = (rest.split(",") + ["0"])[:2]
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def transcript_paragraphs(srt):
    """SRT cues to paragraphs: break on a pause of 2s+, or after ~5 sentences."""
    paras, cur, sentences, last_end = [], [], 0, None
    for block in re.split(r"\n\s*\n", srt.strip()):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        start, end = (stamp(x) for x in lines[ti].split("-->"))
        text = " ".join(lines[ti + 1:]).strip()
        if not text:
            continue
        if cur and ((last_end is not None and start - last_end >= 2.0) or
                    (sentences >= 5 and re.search(r"[.!?][\"”’)]?$", cur[-1]))):
            paras.append(" ".join(cur)); cur, sentences = [], 0
        cur.append(text)
        sentences += len(re.findall(r"[.!?](?:\s|$)", text + " "))
        last_end = end
    if cur:
        paras.append(" ".join(cur))
    return [re.sub(r"\s+", " ", p_).strip() for p_ in paras]


class Clean(HTMLParser):
    """Show notes: keep paragraphs, links and simple formatting; drop scripts."""
    KEEP = {"p", "br", "a", "strong", "b", "em", "i", "u", "ul", "ol", "li", "blockquote", "h3", "h4"}
    DROP = {"script", "style", "iframe", "object", "embed", "noscript", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.DROP:
            self.skip += 1
        elif not self.skip and tag in self.KEEP:
            if tag == "a":
                href = dict(attrs).get("href", "") or ""
                if re.match(r"(https?:|mailto:)", href.strip(), re.I):
                    self.out.append('<a href="%s">' % e(href.strip()))
                else:
                    self.out.append("<a>")
            elif tag == "br":
                self.out.append("<br>")
            else:
                self.out.append("<%s>" % tag)

    def handle_startendtag(self, tag, attrs):
        if tag == "br" and not self.skip:
            self.out.append("<br>")

    def handle_endtag(self, tag):
        if tag in self.DROP:
            self.skip = max(0, self.skip - 1)
        elif not self.skip and tag in self.KEEP and tag != "br":
            self.out.append("</%s>" % tag)

    def handle_data(self, data):
        if not self.skip:
            self.out.append(e(data, quote=False))


def clean_html(s):
    if not re.search(r"<[a-zA-Z]", s):  # plain text notes: make paragraphs
        return "".join("<p>%s</p>" % e(p).replace("\n", "<br>") for p in re.split(r"\n\s*\n", s) if p.strip())
    c = Clean(); c.feed(s); c.close()
    out = "".join(c.out)
    return re.sub(r"<p>\s*(<br>\s*)*</p>", "", out)


def plain(s):
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def fit(text, limit):
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:.-–— ")
    return cut + "…"


def seconds(d):
    parts = [int(float(p)) for p in d.split(":") if p.strip() != ""] if d else []
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def clock(sec):
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def iso_duration(sec):
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return "PT" + (("%dH" % h) if h else "") + (("%dM" % m) if m else "") + ("%dS" % s)


def nice(d):
    return d.strftime("%B %-d, %Y")


def spotify_link(ep):
    m = re.search(r"transcript-files\.spotifycdn\.com/[A-Za-z0-9]+/([A-Za-z0-9]+)/", ep.get("transcript_url", ""))
    return "https://open.spotify.com/episode/" + m.group(1) if m else ep.get("link") or SPOTIFY_SHOW


# ---------------------------------------------------------------- pages

def jsonld(obj):
    return '<script type="application/ld+json">\n%s\n</script>' % json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def page(path, title, desc, body, *, image=None, image_alt=None, keywords=None, og_type="website", ld=None, robots=None, prev=None, nxt=None, current=None):
    url = SITE + "/" + ("" if path == "index.html" else path)
    if not image:
        image, image_alt = "/assets/images/card.jpg", "The Chris Abraham Show cover art beside a phone playing the podcast"
    nav = lambda label: '<nav class="site-nav" aria-label="%s">\n%s\n</nav>' % (label, '<span aria-hidden="true"> · </span>'.join(
        '<a href="%s"%s>%s</a>' % (u, ' aria-current="page"' if u == current else "", t)
        for t, u in (("Home", "/"), ("About", "/about.html"), ("Archives", "/archives.html"), ("Colophon", "/colophon.html"))))
    head = [
        '<!DOCTYPE html>', '<html lang="en">', '<head>', '<meta charset="utf-8">', GA4,
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<script>try{if(localStorage.getItem("tcas-theme")==="dark")document.documentElement.setAttribute("data-theme","dark")}catch(e){}</script>',
        '<title>%s</title>' % e(title),
        '<meta name="description" content="%s">' % e(desc),
        '<link rel="canonical" href="%s">' % e(url),
    ]
    if keywords:
        head.append('<meta name="keywords" content="%s">' % e(keywords))
    if robots:
        head.append('<meta name="robots" content="%s">' % robots)
    head += [
        '<meta name="author" content="%s">' % AUTHOR,
        '<meta property="og:site_name" content="%s">' % NAME,
        '<meta property="og:locale" content="en_US">',
        '<meta property="og:type" content="%s">' % og_type,
        '<meta property="og:title" content="%s">' % e(title),
        '<meta property="og:description" content="%s">' % e(desc),
        '<meta property="og:url" content="%s">' % e(url),
        '<meta property="og:image" content="%s">' % e(SITE + image),
        '<meta property="og:image:alt" content="%s">' % e(image_alt or ""),
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="twitter:title" content="%s">' % e(title),
        '<meta name="twitter:description" content="%s">' % e(desc),
        '<meta name="twitter:image" content="%s">' % e(SITE + image),
        '<meta name="twitter:image:alt" content="%s">' % e(image_alt or ""),
    ]
    if ld:
        head.append(jsonld(ld))
    head += [
        '<meta name="theme-color" content="#cccccc" id="theme-color">',
        '<link rel="icon" type="image/png" sizes="32x32" href="/assets/images/favicon-32.png">',
        '<link rel="apple-touch-icon" href="/assets/images/icon-180.png">',
        '<link rel="stylesheet" href="/assets/show.css">',
        '<link rel="alternate" type="application/rss+xml" title="%s (RSS)" href="%s">' % (NAME, FEED),
        '<link rel="alternate" type="text/plain" title="llms.txt" href="/llms.txt">',
    ]
    if prev:
        head.append('<link rel="prev" href="%s">' % prev)
    if nxt:
        head.append('<link rel="next" href="%s">' % nxt)
    head.append("</head>")
    return "\n".join(head + [
        "<body>", '<div id="container">', '  <header id="banner">',
        '    <a href="/" accesskey="1"><img src="/assets/images/banner.jpg" alt="The Chris Abraham Show banner: red and black cover art with a studio microphone" width="700" height="206" fetchpriority="high"></a>',
        '    <button type="button" class="theme-toggle" id="theme-toggle" hidden>Dark</button>',
        "  </header>", "  " + nav("Site"), '  <div class="columns">', '    <main class="content">',
        body, "    </main>", SIDEBAR, "  </div>", '  <footer class="site-footer">', "    " + nav("Footer"),
        '    <p>%s © %s–%s <a href="/about.html" rel="author">%s</a>.</p>' % (NAME, FIRST_YEAR, LAST_YEAR, AUTHOR),
        '    <p>Mirrored from Spotify for Creators. Static on GitHub Pages.</p>',
        "  </footer>", "</div>", THEME_SCRIPT, "</body>", "</html>", ""])


THEME_SCRIPT = """<script>
(function () {
  var root = document.documentElement, btn = document.getElementById("theme-toggle"),
      meta = document.getElementById("theme-color");
  function show(dark) {
    btn.textContent = dark ? "Light" : "Dark";
    btn.setAttribute("aria-label", dark ? "Switch to light reading mode" : "Switch to dark reading mode");
    meta.setAttribute("content", dark ? "#141414" : "#cccccc");
  }
  show(root.getAttribute("data-theme") === "dark");
  btn.hidden = false;
  btn.addEventListener("click", function () {
    var dark = root.getAttribute("data-theme") !== "dark";
    if (dark) root.setAttribute("data-theme", "dark"); else root.removeAttribute("data-theme");
    try { localStorage.setItem("tcas-theme", dark ? "dark" : "light"); } catch (e) {}
    show(dark);
  });
  var share = document.getElementById("share-native");
  if (share) {
    share.hidden = false;
    share.addEventListener("click", function () {
      var url = share.getAttribute("data-url"), title = share.getAttribute("data-title");
      if (navigator.share) { navigator.share({ title: title, url: url }).catch(function () {}); return; }
      navigator.clipboard.writeText(url).then(function () { share.textContent = "Link Copied"; });
    });
  }
})();
</script>"""

SIDEBAR = FIRST_YEAR = LAST_YEAR = ""

# Google Analytics 4 (Chris's property), as Google gives it.
GA4 = """<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-WQGSJJEDKP"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());

  gtag('config', 'G-WQGSJJEDKP');
</script>"""

ROBOTS = """# Search engines and AI assistants are welcome to read and cite everything here.
#
# Search engines get the HTML pages only. llms-full.txt repeats the show notes
# word for word, so search indexes skip it to avoid duplicate content. AI
# assistants, which read it in place of HTML, get everything.
User-agent: *
Allow: /
Disallow: /search.html

User-agent: Googlebot
User-agent: Bingbot
User-agent: Applebot
User-agent: DuckDuckBot
User-agent: YandexBot
Allow: /
Disallow: /search.html
Disallow: /llms-full.txt

User-agent: GPTBot
User-agent: OAI-SearchBot
User-agent: ChatGPT-User
User-agent: ClaudeBot
User-agent: Claude-SearchBot
User-agent: Claude-User
User-agent: PerplexityBot
User-agent: Perplexity-User
User-agent: Google-Extended
User-agent: Applebot-Extended
User-agent: Amazonbot
User-agent: DuckAssistBot
User-agent: MistralAI-User
User-agent: CCBot
Allow: /

Sitemap: %s/sitemap.xml
"""


def seo_title(t):
    """About 60 characters: the episode title, plus who made it when it fits."""
    t = seo_text(t)
    if len(t) > 66:  # "about 60": a whole title up to 66 beats a cut one
        if 25 <= t.find(":") <= 60:
            return t[:t.find(":")]
        words = t[:61].split(" ")[:-1]
        while len(words) > 3 and (len(words[-1]) <= 2 or words[-1].lower() in STOP | {"a", "of", "in", "on", "to", "an", "as", "at", "by", "or"}):
            words.pop()
        return " ".join(words).rstrip(" ,:;")
    for suffix in (", a podcast episode by Chris Abraham", ", Chris Abraham podcast episode", ", Chris Abraham podcast", ", podcast episode"):
        if len(t) + len(suffix) <= 60 and len(t) < 45:
            return t + suffix
    return t


def write(path, text):
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    old = open(full).read() if os.path.exists(full) else None
    if old != text:
        with open(full, "w") as f:
            f.write(text)


def build(show, eps):
    global SIDEBAR, FIRST_YEAR, LAST_YEAR
    total = len(eps)
    first, latest = eps[0], eps[-1]
    FIRST_YEAR, LAST_YEAR = when(first).year, when(latest).year
    SIDEBAR = "\n".join([
        '    <aside class="sidebar">',
        '      <form class="search" method="get" action="/search.html" role="search">',
        '        <label for="search" class="visually-hidden">Search the episodes</label>',
        '        <input id="search" name="q" type="search" placeholder="Search">',
        "      </form>",
        '      <p class="feeds">Listen on<br>%s</p>' % "<br>".join('<a href="%s">%s</a>' % (e(u), n) for n, u in LISTEN),
        '      <p>Start at the very <a href="/%s">beginning</a><br>a very good place to start.</p>' % first["path"],
        '      <p><a href="/about.html">About the show</a><br><a href="/archives.html">Every episode</a></p>',
        '      <p class="badge"><a href="%s"><img src="/assets/images/icon-180.png" alt="Listen to The Chris Abraham Show on Spotify" width="160" height="160" loading="lazy"></a></p>' % SPOTIFY_SHOW,
        "    </aside>"])
    person = {"@type": "Person", "@id": SITE + "/#author", "name": AUTHOR, "url": HOME, "sameAs": SAME_AS,
              "homeLocation": {"@type": "Place", "name": "South Arlington, Virginia"}}
    series_ref = {"@type": "PodcastSeries", "@id": SITE + "/#series", "name": NAME, "url": SITE + "/"}

    def meta_line(n, ep):
        bits = ["Episode %d of %d" % (n, total),
                '<time datetime="%s">%s</time>' % (when(ep).isoformat(), nice(when(ep)))]
        if seconds(ep["duration"]):
            bits.append(clock(seconds(ep["duration"])))
        if ep["season"] and ep["episode"]:
            bits.append("Season %s, Episode %s" % (ep["season"], ep["episode"]))
        return '<p class="date">%s</p>' % " · ".join(bits)

    def nav(i):
        parts = ['<a href="/%s">First</a>' % first["path"]]
        if i > 0:
            parts.append('<a href="/%s" rel="prev">&laquo; Previous</a>' % eps[i - 1]["path"])
        if i < total - 1:
            parts.append('<a href="/%s" rel="next">Next &raquo;</a>' % eps[i + 1]["path"])
        parts.append('<a href="/%s">Latest</a>' % latest["path"])
        return '<nav class="entry-nav" aria-label="Episodes">\n  %s\n</nav>' % " |\n  ".join(parts)

    def art(ep):
        return "/assets/images/" + (ep.get("art") or "show.jpg")

    def episode_block(i, ep, heading):
        n = i + 1
        notes = clean_html(fixed(ep["description"])) or "<p>No show notes for this episode.</p>"
        url = SITE + "/" + ep["path"]
        q = urllib.parse.quote
        share = ""
        if heading == "h1":
            share = '<p class="listen">Share: %s · <button type="button" class="linkish" id="share-native" data-url="%s" data-title="%s" hidden>Share or Copy Link</button></p>' % (" · ".join(
                '<a href="%s">%s</a>' % (e(u), n) for n, u in (
                    ("X", "https://x.com/intent/post?url=%s&text=%s" % (q(url), q(ep["title"]))),
                    ("Facebook", "https://www.facebook.com/sharer/sharer.php?u=" + q(url)),
                    ("LinkedIn", "https://www.linkedin.com/sharing/share-offsite/?url=" + q(url)),
                    ("Reddit", "https://www.reddit.com/submit?url=%s&title=%s" % (q(url), q(ep["title"]))),
                    ("Bluesky", "https://bsky.app/intent/compose?text=" + q(ep["title"] + " " + url)),
                    ("Threads", "https://www.threads.net/intent/post?text=" + q(ep["title"] + " " + url)),
                    ("Email", "mailto:?subject=%s&body=%s" % (q(ep["title"]), q(url))))), e(url), e(ep["title"]))
        return "\n".join([
            '<article class="entry">',
            '  <%s><a href="/%s">%s</a></%s>' % (heading, ep["path"], e(ep["title"]), heading),
            "  " + meta_line(n, ep),
            '  <div class="entry-body">',
            '<img class="episode-art" src="%s" alt="Cover art for the episode %s" width="800" height="800">' % (art(ep), e(ep["title"])),
            '<audio class="player" controls preload="none" src="%s"><a href="%s">Download the audio</a></audio>' % (e(ep["audio"]), e(ep["audio"])),
            '<p class="listen">Listen on <a href="%s">Spotify</a> · %s</p>' % (e(spotify_link(ep)), " · ".join('<a href="%s">%s</a>' % (e(u), n) for n, u in LISTEN[1:4] + LISTEN[-1:])),
            share,
            notes,
            "  </div>",
            "</article>"])

    # episode pages
    for i, ep in enumerate(eps):
        n = i + 1
        d = when(ep)
        sec = seconds(ep["duration"])
        url = SITE + "/" + ep["path"]
        has_t = os.path.exists(os.path.join(TRANSCRIPTS, ep["guid"] + ".srt"))
        title = seo_title(ep["title"])
        notes_text = seo_text(plain(fixed(ep["description"])))
        if notes_text and notes_text[-1] not in ".!?…":
            notes_text += "."
        extra = " and a full transcript" if has_t else ""
        tails = ["Listen to episode %d of Chris Abraham's podcast, %s, from %s, with the audio and show notes%s." % (n, seo_text(ep["title"]), nice(d), extra),
                 "Listen to episode %d of Chris Abraham's podcast, from %s, with the audio and show notes%s." % (n, nice(d), extra),
                 "Episode %d of Chris Abraham's podcast, %s." % (n, nice(d))]
        desc = fit(notes_text, 160)
        if len(notes_text) < 140:
            desc = next((c for c in ((notes_text + " " + t_).strip() for t_ in tails) if len(c) <= 160), desc)
        se = "Season %s Episode %s" % (ep["season"], ep["episode"]) if ep["season"] and ep["episode"] else ""
        keywords = ", ".join(x for x in (seo_text(ep["title"]), "Chris Abraham", "podcast", se, str(d.year)) if x)
        ld_ep = {"@type": "PodcastEpisode", "@id": url + "#episode", "url": url, "name": ep["title"],
                 "datePublished": d.isoformat(), "description": fit(plain(fixed(ep["description"])), 500) or desc, "isAccessibleForFree": True,
                 "image": SITE + art(ep), "inLanguage": "en", "author": {"@id": SITE + "/#author"},
                 "partOfSeries": series_ref,
                 "associatedMedia": {"@type": "AudioObject", "contentUrl": ep["audio"],
                                     "encodingFormat": ep.get("audio_type") or "audio/mpeg"}}
        if sec:
            ld_ep["duration"] = ld_ep["associatedMedia"]["duration"] = iso_duration(sec)
        if ep["episode"]:
            ld_ep["episodeNumber"] = ep["episode"]
        if ep["season"]:
            ld_ep["partOfSeason"] = {"@type": "PodcastSeason", "seasonNumber": ep["season"], "partOfSeries": {"@id": SITE + "/#series"}}
        tpath = os.path.join(TRANSCRIPTS, ep["guid"] + ".srt")
        transcript = ""
        if os.path.exists(tpath):
            paras = transcript_paragraphs(open(tpath).read())
            if paras:
                words = sum(len(p.split()) for p in paras)
                transcript = ('<section class="transcript" aria-labelledby="transcript">\n<h2 id="transcript">Transcript</h2>\n'
                              '<details>\n<summary>Read the full transcript (%s words, machine made)</summary>\n%s\n</details>\n</section>'
                              % ("{:,}".format(words), "\n".join("<p>%s</p>" % e(p) for p in paras)))
        ld = {"@context": "https://schema.org", "@graph": [person, ld_ep, {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": NAME, "item": SITE + "/"},
            {"@type": "ListItem", "position": 2, "name": "Archives", "item": SITE + "/archives.html"},
            {"@type": "ListItem", "position": 3, "name": ep["title"], "item": url}]}]}
        body = "\n".join([nav(i), episode_block(i, ep, "h1"), transcript, nav(i)])
        write(ep["path"], page(ep["path"], title, desc, body, image=art(ep), image_alt="Cover art for the episode " + ep["title"],
                               keywords=keywords, og_type="article", ld=ld,
                               prev="/" + eps[i - 1]["path"] if i else None,
                               nxt="/" + eps[i + 1]["path"] if i < total - 1 else None))

    show_desc = plain(fixed(show["description"]))
    home_desc = fit("Every episode of Chris Abraham's podcast since %d: politics, AI, SEO, fitness, D&D, aphantasia and South Arlington life, with notes and transcripts." % FIRST_YEAR, 160)
    sentence = "Every episode of %s, %s to %s, with show notes and transcripts." % (NAME, nice(when(first)), nice(when(latest)))

    # home
    body = "\n".join([
        '<h1 class="visually-hidden">%s</h1>' % NAME,
        '<p class="latest">Latest: <a href="/%s">%s</a>, %s</p>' % (latest["path"], e(latest["title"]), nice(when(latest))),
        episode_block(total - 1, latest, "h2"), nav(total - 1),
        '<p class="intro">%s</p>' % e(sentence)])
    ld = {"@context": "https://schema.org", "@graph": [
        person,
        {"@type": "WebSite", "@id": SITE + "/#website", "url": SITE + "/", "name": NAME, "inLanguage": "en", "publisher": {"@id": SITE + "/#author"}},
        {"@type": "PodcastSeries", "@id": SITE + "/#series", "name": NAME, "url": SITE + "/", "description": show_desc,
         "image": SITE + "/assets/images/show.jpg", "webFeed": FEED, "inLanguage": "en", "author": {"@id": SITE + "/#author"},
         "sameAs": [u for n, u in LISTEN if n != "RSS"], "startDate": when(first).date().isoformat(),
         "numberOfEpisodes": total, "genre": "Comedy"}]}
    write("index.html", page("index.html", "%s: Chris Abraham's podcast since %d" % (NAME, FIRST_YEAR), home_desc, body, ld=ld, current="/",
                             keywords="The Chris Abraham Show, Chris Abraham, podcast, ChrisCast, South Arlington"))

    # archives
    rows, year = [], None
    for i in range(total - 1, -1, -1):
        ep = eps[i]; d = when(ep)
        if d.year != year:
            if year is not None:
                rows.append("</ul>")
            year = d.year
            rows.append('<h3 class="year" id="y%d">%d</h3>\n<ul class="toc">' % (year, year))
        extra = []
        if seconds(ep["duration"]):
            extra.append(clock(seconds(ep["duration"])))
        if ep["season"] and ep["episode"]:
            extra.append("S%s E%s" % (ep["season"], ep["episode"]))
        rows.append('  <li><span class="meta">%s</span> <a href="/%s">%s</a> <span class="meta">%s</span></li>' % (
            d.strftime("%b %-d"), ep["path"], e(ep["title"]), " · ".join(extra)))
    rows.append("</ul>")
    body = '<h1 class="archive-title">Archives</h1>\n<p>All %d episodes, newest first.</p>\n%s' % (total, "\n".join(rows))
    write("archives.html", page("archives.html", "All %d Chris Abraham podcast episodes, %d to %d" % (total, FIRST_YEAR, LAST_YEAR),
                                fit("The full list of Chris Abraham's podcast episodes, newest first, %d to %d, with dates, running times, and season and episode numbers for each." % (FIRST_YEAR, LAST_YEAR), 160),
                                body, current="/archives.html", keywords="Chris Abraham, podcast episodes, archive",
                                ld={"@context": "https://schema.org", "@type": "CollectionPage", "url": SITE + "/archives.html", "name": "Archives", "isPartOf": {"@id": SITE + "/#website"}}))

    # about
    n_t = sum(1 for ep in eps if os.path.exists(os.path.join(TRANSCRIPTS, ep["guid"] + ".srt")))
    where = ", ".join(n for n, u in LISTEN if n != "RSS")
    faq = [
        ("Where can I listen to The Chris Abraham Show?",
         "On %s, or in any podcast app with the RSS feed. Every episode also plays on its own page here." % where),
        ("How many episodes are there?",
         "%d, from %s to %s. The archives list every one." % (total, nice(when(first)), nice(when(latest)))),
        ("Are there transcripts?",
         "Yes, for %d episodes so far. Spotify makes them, and each one appears at the bottom of its episode page." % n_t),
        ("Who is Chris Abraham?",
         "A writer and SEO consultant who lives in South Arlington, Virginia. He also writes Hill Mole, a serialized novel."),
    ]
    body = "\n".join([
        '<h1 class="archive-title">About</h1>', '<div class="entry-body">',
        '<img class="episode-art" src="/assets/images/show.jpg" alt="The Chris Abraham Show cover art: the title in white on red and black, over a studio microphone" width="1000" height="1000">',
        clean_html(fixed(show["description"])),
        '<h2>Listen and Subscribe</h2>',
        "<ul>%s</ul>" % "".join('<li><a href="%s">%s</a></li>' % (e(u), "RSS feed, for any podcast app" if n == "RSS" else n) for n, u in LISTEN),
        '<h2 class="faq-title">Questions</h2>',
        '<dl class="faq">%s</dl>' % "".join("<dt>%s</dt><dd>%s</dd>" % (e(q_), e(a)) for q_, a in faq),
        '<p>%s is made by <a href="%s" rel="author">Chris Abraham</a>, who also writes <a href="https://hillmole.com/">Hill Mole</a>.</p>' % (NAME, HOME),
        "</div>"])
    about_desc = "About Chris Abraham's podcast from South Arlington, Virginia: %d episodes since %d, where to listen and subscribe, and answers to common questions." % (total, FIRST_YEAR)
    write("about.html", page("about.html", "About The Chris Abraham Show and its host, Chris Abraham", fit(about_desc, 160), body, current="/about.html",
                             keywords="The Chris Abraham Show, Chris Abraham, podcast, subscribe",
                             ld={"@context": "https://schema.org", "@graph": [
                                 {"@type": "AboutPage", "url": SITE + "/about.html", "about": {"@id": SITE + "/#series"}},
                                 {"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": q_, "acceptedAnswer": {"@type": "Answer", "text": a}} for q_, a in faq]}]}))

    # colophon
    body = "\n".join([
        '<h1 class="archive-title">Colophon</h1>', '<div class="entry-body colophon">',
        '<p><strong>%s</strong> is recorded by <a href="/about.html" rel="author">Chris Abraham</a> and published on Spotify for Creators, formerly Anchor. The first episode went out on %s.</p>' % (NAME, nice(when(first))),
        "<h2>How the Show Is Made</h2>",
        "<p>Chris records on a Sony ICD-UX570 digital voice recorder and edits in <a href=\"https://www.audacityteam.org/\">Audacity</a>. When a recording has too much background noise, it goes through <a href=\"https://podcast.adobe.com/enhance\">Adobe Podcast Enhance</a> first. The video episodes, and the occasional over-the-top \"Deep Dive,\" come from running an episode through <a href=\"https://notebooklm.google.com/\">Google NotebookLM</a>.</p>",
        "<h2>Credits</h2>",
        "<p>The intro and outro music was made by <a href=\"https://www.fiverr.com/blarock/create-a-professional-podcast-intro-and-outro\">blarock on Fiverr</a>. The cover art, which is also this site's banner and icon, was designed by <a href=\"https://www.fiverr.com/brandzin/design-a-professional-podcast-cover-art\">brandzin on Fiverr</a>. Thank you both.</p>",
        "<h2>This Website</h2>",
        '<p>This site is a static mirror of the show\'s <a href="%s">podcast feed</a>. A small Python script reads the feed, keeps a copy of each episode\'s artwork and transcript, and writes one plain HTML page per episode. A GitHub Action runs it every day, so new episodes and late transcripts appear on their own. It is published by <a href="https://pages.github.com/">GitHub Pages</a>; the source is at <a href="https://github.com/chrisabraham/thechrisabrahamshow">github.com/chrisabraham/thechrisabrahamshow</a>.</p>' % FEED,
        "<h2>Audio</h2>",
        '<p>The audio stays on Spotify. Each player streams the episode straight from the feed, and <a href="%s">Spotify</a> remains the show\'s home.</p>' % SPOTIFY_SHOW,
        "<h2>Transcripts</h2>",
        "<p>Transcripts are generated by Spotify. They are machine made, so names and odd words are sometimes wrong.</p>",
        "<h2>Design</h2>",
        '<p>The layout is borrowed from <a href="https://hillmole.com/">Hill Mole</a>, Chris\'s serialized novel: a white column as wide as the 700-pixel banner, on a grey page, with grey links. Unlike Hill Mole, the headings keep their capitals. The button in the banner switches to a dark reading mode, and that choice is kept only in your own browser.</p>',
        "</div>"])
    write("colophon.html", page("colophon.html", "Colophon: how Chris Abraham's podcast is made",
                                "How Chris Abraham records and edits his podcast, who made the music and cover art, and how this site mirrors his Spotify feed every day.",
                                body, keywords="colophon, Sony ICD UX570, Audacity, podcast credits", current="/colophon.html"))

    # search
    body = "\n".join([
        '<h1 class="archive-title">Search</h1>',
        '<form class="search" method="get" action="/search.html" role="search"><label for="q" class="visually-hidden">Search the episodes</label><input id="q" name="q" type="search" placeholder="Search the episodes"></form>',
        '<p id="status" class="intro" aria-live="polite">Type a word or two.</p>',
        '<ul id="results" class="toc results"></ul>',
        '<script src="/assets/search.js" defer></script>'])
    write("search.html", page("search.html", "Search Chris Abraham's podcast episodes", "Search every episode of Chris Abraham's podcast by title and show notes.", body, robots="noindex, follow"))
    index = [{"t": ep["title"], "u": "/" + ep["path"], "d": nice(when(ep)), "n": fit(plain(fixed(ep["description"])), 1200)} for ep in reversed(eps)]
    write("search.json", json.dumps(index, ensure_ascii=False, separators=(",", ":")))

    # 404
    body = '<h1 class="archive-title">Not found</h1>\n<div class="entry-body"><p>There is no page at this address. Try the <a href="/archives.html">list of every episode</a>, the <a href="/search.html">search</a>, or the <a href="/">latest episode</a>.</p></div>'
    write("404.html", page("404.html", "Page not found", "There is no page at this address.", body, robots="noindex"))

    # sitemap and robots
    urls = [(SITE + "/", when(latest)), (SITE + "/archives.html", when(latest)), (SITE + "/about.html", None), (SITE + "/colophon.html", None)]
    urls += [(SITE + "/" + ep["path"], when(ep)) for ep in reversed(eps)]
    write("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s\n</urlset>\n' % "\n".join(
        "  <url><loc>%s</loc>%s</url>" % (e(u), "<lastmod>%s</lastmod>" % d.date().isoformat() if d else "") for u, d in urls))
    write("robots.txt", ROBOTS % SITE)
    lines = ["# %s" % NAME, "", "> %s" % show_desc, "",
             "Chris Abraham's podcast, %d episodes from %s to %s. Each episode page has the audio, show notes and, where Spotify has made one, a full transcript." % (total, nice(when(first)), nice(when(latest))),
             "", "## Pages", "", "- [About](%s/about.html): where to listen and subscribe" % SITE, "- [Archives](%s/archives.html): every episode" % SITE,
             "- [Full show notes](%s/llms-full.txt): every episode's notes in one plain text file" % SITE, "", "## Episodes", ""]
    lines += ["- [%s](%s/%s): %s%s" % (ep["title"], SITE, ep["path"], nice(when(ep)), ", " + clock(seconds(ep["duration"])) if seconds(ep["duration"]) else "") for ep in reversed(eps)]
    write("llms.txt", "\n".join(lines) + "\n")
    full = ["# %s: every episode's show notes" % NAME, ""]
    for i in range(total - 1, -1, -1):
        ep = eps[i]
        full += ["## %s" % ep["title"], "", "Episode %d of %d, %s. %s/%s" % (i + 1, total, nice(when(ep)), SITE, ep["path"]), "", plain(fixed(ep["description"])) or "(No show notes.)", ""]
    write("llms-full.txt", "\n".join(full))
    write("CNAME", "thechrisabrahamshow.com\n")
    write(INDEXNOW_KEY + ".txt", INDEXNOW_KEY)
    write(".nojekyll", "")


# ---------------------------------------------------------------- report

def report(eps):
    norm = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    print("\n%d episodes, %s to %s" % (len(eps), nice(when(eps[0])), nice(when(eps[-1]))))
    print("\nSame or nearly the same titles:")
    for a in range(len(eps)):
        for b in range(a + 1, len(eps)):
            ta, tb = norm(eps[a]["title"]), norm(eps[b]["title"])
            if ta == tb or difflib.SequenceMatcher(None, ta, tb).ratio() > .85:
                print("  %s  /%s\n  %s  /%s\n" % (nice(when(eps[a])), eps[a]["path"], nice(when(eps[b])), eps[b]["path"]))
    gaps = {"transcript": [ep for ep in eps if not os.path.exists(os.path.join(TRANSCRIPTS, ep["guid"] + ".srt"))],
            "artwork": [ep for ep in eps if not ep.get("art")],
            "duration": [ep for ep in eps if not seconds(ep["duration"])],
            "show notes": [ep for ep in eps if not plain(ep["description"])]}
    for k, v in gaps.items():
        print("Missing %s: %d" % (k, len(v)))
    import glob
    long_t = short_d = dashes = 0
    for f in glob.glob(os.path.join(ROOT, "**", "*.html"), recursive=True):
        h = open(f).read()
        t = html.unescape(re.search(r"<title>(.*?)</title>", h).group(1))
        d = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', h).group(1))
        long_t += len(t) > 66
        short_d += not 120 <= len(d) <= 160 and "noindex" not in h
        dashes += bool(re.search(r"[-–—|]", t + d)) and "noindex" not in h
    print("SEO: titles over 66: %d, descriptions outside 120 to 160: %d, with dashes or pipes: %d" % (long_t, short_d, dashes))


def main():
    offline = "--offline" in sys.argv
    os.makedirs(IMAGES, exist_ok=True); os.makedirs(TRANSCRIPTS, exist_ok=True)
    store = os.path.join(DATA, "episodes.json")
    saved = json.load(open(store)) if os.path.exists(store) else {"show": {}, "episodes": []}
    show, eps = saved["show"], saved["episodes"]
    if not offline:
        show, fresh = parse_feed(get(FEED))
        # Fail loudly (a red X and an email) rather than publish a broken feed.
        if not show["title"] or not fresh or len(fresh) < 0.9 * len(eps):
            sys.exit("Feed looks wrong: %d episodes in the feed, %d already on the site. Nothing published." % (len(fresh), len(eps)))
        eps = merge(eps, fresh)
        show_assets(show["image"])
        for ep in eps:
            if ep.get("image") and ep.get("art") != image_name(ep["image"]):
                ep["art"] = cache_image(ep["image"], image_name(ep["image"])) or ep.get("art", "")
            cache_transcript(ep)
        with open(store, "w") as f:
            json.dump({"show": show, "episodes": eps}, f, ensure_ascii=False, indent=1)
            f.write("\n")
    build(show, eps)
    report(eps)


if __name__ == "__main__":
    main()
