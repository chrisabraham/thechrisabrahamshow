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
# Spotify for Creators copies each episode to YouTube on its own; the channel's feed names the new videos.
YOUTUBE = "https://www.youtube.com/@chrisabrahamshow"
YOUTUBE_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id=UCPSCtkNYQkCNW6t5m8PQ4zg"
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
UA = {"User-Agent": "thechrisabrahamshow.com site builder", "Cache-Control": "no-cache"}
INDEXNOW_KEY = "3839412712300d0a246eead73969e20b"  # served at /<key>.txt; .github/indexnow.py submits with it
# Where to listen. Each link was checked live in October 2026.
LISTEN = [
    ("Spotify", SPOTIFY_SHOW),
    ("YouTube", YOUTUBE),
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
           "https://chrisabraham.substack.com/", "https://www.reddit.com/user/chrisabraham", "https://mastodon.social/@chrisa",
           "https://github.com/chrisabraham", "https://www.youtube.com/chrisabraham"]
# Typos in the feed, fixed on this site only (the feed itself is untouched).
FIXES = [
    # compound modifiers take hyphens (Chris, 2026-10-15: "proper grammar and spelling conventions")
    (r"\blong term harm", "long-term harm"), (r"\bshort term harm", "short-term harm"), (r"\ba co founder\b", "a co-founder"),
    (r"\bfirst hand experience", "firsthand experience"), (r"\blong form audio", "long-form audio"), (r"\b([Aa]) brand new (SUV|RAV4)", r"\1 brand-new \2"),
    (r"\bCarey \((Traxidor|Perlan)", r"Cary (\1"),
    (r"\bwritine\b", "writing"), (r"\bdetergernt\b", "detergent"),
    (r"\bagressiveness\b", "aggressiveness"), (r"Mahalo newa, Aloha kako\b", "Mahalo nui, Aloha kakou"),
    (r"\b([Hh])yperfantasia\b", r"\1yperphantasia"), (r"prominent figur\b", "prominent figure"),
    (r"perception of relationsh\b", "perception of relationships"), (r"health mista\b", "health mistake"),
    (r"\b(another|subject|discourse|way|designed)a (<em>rambling)", r"\1: a \2"), (r"\btl:dr\b", "tl;dr"),
    (r"Just a 55-year-old cisgender", "Just a 56-year-old cisgender"),  # the show blurb; Chris is 56 (Oct 2026)
]
# Mishearings in Spotify's machine transcripts, fixed on this site only (the cached .srt files stay as delivered).
TRANSCRIPT_FIXES = [
    ('\\bLinda Going\\b', 'Linda Goin'), ('\\bLinda, go in\\.', 'Linda Goin.'),
    ('\\bJason Guljar\\b', 'Jason Gooljar'), ('\\bKaren Ch[iu]lups?k[iy]\\b', 'Karen Chalupsky'),
    ('\\bA Fantasia\\b', 'aphantasia'),
    ('\\b[Aa]dvantasia\\b', 'aphantasia'),
    ('\\b[Hh]yper ?[Ff]antasia\\b', 'hyperphantasia'),
    ('\\bS ?dam\\b', 'SDAM'),
    ("\\bAdido's\\b", "Idido's"),
    ('\\bIdido[s]? ?[Cc]offee', 'Idido Coffee'),
    ('\\bIdidos\\b', "Idido's"),
    ('\\bAdidas Coffee\\b', 'Idido Coffee'),
    ('\\b[Aa]braham ?\\.s ?[uU]\\b', 'abraham.su'),
    ('\\bMesh ?[Tt]astic\\b', 'Meshtastic'),
    ('\\bSync Thing\\b', 'Syncthing'),
    ('\\bEpiscopalian Complin\\b', 'Episcopal Compline'),
    ('\\bComplin\\b', 'Compline'),
    ('\\bTakidamas\\b', 'Tachikomas'),
    ('\\bLaura (?=[Rr]adio|Meshtastic)', 'LoRa '),
    ('\\bConcept 2\\b', 'Concept2'),
    ('\\bGo ?Ruck\\b', 'GORUCK'),
    ('\\bChris [Cc]ast\\b', 'ChrisCast'),
    ('\\bHey,? Gogol\\b', 'Hey Google'),
    ('\\bHey,? Gulu\\b', 'Hey Google'),
    ('\\bKaiser M3\\b', 'Keiser M3'),
    ('\\b[Ss]teel dossier\\b', 'Steele dossier'),
    ('\\bPhilando Castir\\b', 'Philando Castile'),
    ('\\b(?:Gerard|Jerrod) Depard(?:ue|io)\\b', 'Gérard Depardieu'),
    ('\\bEduardo Sav(?:rin|erins)\\b|\\bEduardo Severins\\b', 'Eduardo Saverin'),
    ('\\bZo(?:ra|hro)n M(?:am|an)dani\\b', 'Zohran Mamdani'),
    ('\\bMike D(?:i|e)[Gg]iorno\\b', 'Mike DelGiorno'),
    ('\\bGarrison K(?:yler|eller)\\b', 'Garrison Keillor'),
    ('\\bColean Noir\\b', 'Colion Noir'),
    ('\\bGuerrillo Princip\\b', 'Gavrilo Princip'),
    ('\\bJeff Cavan\\b', 'Jeff Cavins'),
    ('\\bSiddhartha Kotama\\b', 'Siddhartha Gautama'),
    ('\\bLord Haha\\b', 'Lord Haw-Haw'),
    ('\\bAntony Beaver\\b', 'Antony Beevor'),
    ('\\bGordon Gecko\\b', 'Gordon Gekko'),
    ('\\bPelphrey\\b', 'Palfrey'),
    ('\\bTamela Martin\\b', 'Pamela Martin'),
    ('\\bShoshanna Zubach\\b', 'Shoshana Zuboff'),
    ('\\bRenee Gerard\\b', 'René Girard'),
    ('\\bPeter Teal\\b', 'Peter Thiel'),
    ('\\bSmith[ -]?Mu?n(?:d|t|de|n)?t?( [Mm]odernization)? ?[Aa]ct\\b', 'Smith-Mundt\\1 Act'),
    ('\\b([Dd])r\\.? (?:Lux|Luck)\\b', '\\1r. Lucks'),
    ('\\bmy doctor dr\\. Lucks\\b', 'my doctor, Dr. Lucks'),
]
# Curse of Strahd names, applied only to the D&D session episodes so "Strad" or "Danica" elsewhere is left alone.
DND_FIXES = [
    ('\\bCarrie\\s+Abend, Abend Abend\\b', 'Cary Abend'), ('\\bCarrie\\s+Abend\\b', 'Cary Abend'),
    ('\\bCursive Strad\\b', 'Curse of Strahd'),
    ('\\b[Cc]urse of Str(?:ad|aud|oud|ud)\\b', 'Curse of Strahd'),
    ('\\bStr(?:ad|aud|ud)\\b', 'Strahd'),
    ('\\bStroud\\b', 'Strahd'),
    ('\\bBorovia(n?)\\b', 'Barovia\\1'),
    ('\\bVal(?:l)?achi\\b|\\bValaki\\b|\\bVolaki\\b', 'Vallaki'),
    ('\\b(?:Yuri ?[Hh]orne?|Eu?ro ?[Hh]orn|Yori Horn|Erie Horn|Udir Horn|Orihorn|Euro Horn)\\b', 'Urihorn'),
    ('\\b(?:Derm(?:ont|an|ot)|Darman|Dearman|Diamond) Cobain\\b', 'Daermon Cobain'),
    ('\\bDerm(?:ont|an|ot)\\b', 'Daermon'),
    ('\\bTrac(?:i|e)dor\\b|\\bTre?x(?:or|ors)\\b|\\bTracks(?:idar| ?[Dd]oor)\\b|\\bTra?ck Seder\\b|\\bTrek Seder\\b|\\bTracer Door\\b', 'Traxidor'),
    ('\\b(?:Soren|Surin|Sirn|Sarin) Ironwood\\b', 'Sören Ironwood'),
    ('\\bRadley F(?:olthorne?|ulthorn|ulhorn)\\b', 'Radley Fullthorn'),
    ('\\bLady W(?:atcher|alker|asher|ater|okter|affer|acker|atch)s?\\b', 'Lady Wachter'),
    ('\\bLady Voctor\\b', 'Lady Wachter'),
    ('\\bIsaac Strozny\\b', 'Izek Strazni'),
    ('\\bIrena\\b', 'Ireena'),
    ('\\bIzmark\\b', 'Ismark'),
    ('\\bMart[ia]koffs?\\b', 'Martikov'),
    ('\\bDanica\\b', 'Danika'),
    ('\\bKresk\\b', 'Krezk'),
    ('\\bKreskov\\b', 'Krezkov'),
    ('\\bKreskova\\b', 'Krezkova'),
    ('\\b(?:Arg[eo]n|Arj[eu]n|Argh?in|Arden|Arise) ?(?:[VvBbMm]o?(?:s|ss|st|l)(?:t?h?olt|t?h?old|t?hull|tel|tholt)|Vest ?[Hh]olt|Vostholt|Bostole|Boss ?Holt)\\b', 'Argynvostholt'),
    ('\\bRick T[ae][bv]io\\b', 'Rictavio'),
    ('\\b(?:Van|Ven|Vin|Then) Richt(?:on|y|e)\\b', 'Van Richten'),
    ('\\bRudolf Enrichten\\b', 'Rudolf van Richten'),
    ('\\bEsmer(?:alda|elda|osa) D(?:uvernier|avenir)\\b', "Ezmerelda d'Avenir"),
    ('\\bPear(?:land|lman|lin) Good ?[Ss]hadow\\b', 'Perlan Goodshadow'),
    ('\\bPearlin\\b', 'Perlan'),
    ('\\bSaint Mar[ck]ov(?:i)?a\\b', 'Saint Markovia'),
    ('\\bMount G(?:akis|affes|alacus|awkies|ehacas)\\b|\\bMount Caucus\\b', 'Mount Ghakis'),
    ('\\b(?:Sal[ie]nka|Selinka|Desolinka|Telenka) Pass\\b', 'Tsolenka Pass'),
    ('\\b(?:Swellidge|Sailage|Spalich) R(?:d|oad)\\b', 'Svalich Road'),
    ('\\bB[eu]rg[ae]?r? ?[Mm]aster\\b|\\bBurgo [Mm]aster\\b', 'Burgomaster'),
    ('\\bBurgo [Mm]istress(es)?\\b', 'Burgomistress\\1'),
    ('\\bRaven ?[Ll]ock?\\b|\\bRaven Loft\\b|\\bWavenloft\\b', 'Ravenloft'),
    ('\\bV(?:o|a)la?kovi(?:c|ch)\\b', 'Vallakovich'),
    ('\\bMadame Eva\\b', 'Madam Eva'),
    ('\\bErnst ?[Ll]ern?a[ck]k?\\b|\\bErnst Larnac\\b', 'Ernst Larnak'),
    ('\\bZ(?:ig|eig) ?Fr(?:ick|ag)\\b', 'Zygfrek'),
    ('\\bHenrik Vand(?:er|e) ?[Vv]?oort\\b|\\bHenrik Vander Fort\\b', 'Henrik van der Voort'),
    ('\\bEsmodeus\\b', 'Asmodeus'),
    ('\\bEldridge Knight\\b', 'Eldritch Knight'),
    ('\\bSun Sword\\b', 'Sunsword'),
    ('\\bSaint Andrews Church\\b', "Saint Andral's Church"),
    ('\\bSaint Andrews?\\b|\\bSan Andrews\\b', 'Saint Andral'),
]
DND_EPISODE = re.compile(r"Strahd|Barovia|Vallaki|Ravenloft|Argynvostholt")
e = html.escape


# ---------------------------------------------------------------- fetching


def close_dashes(h):
    """Chris' rule: "--" becomes an em dash and every em dash is closed up (word—word).
    Code, scripts, styles, and HTML comments keep their text exactly."""
    parts = re.split(r"(<pre\b.*?</pre>|<code\b.*?</code>|<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->|<[^>]+>)", h, flags=re.S)
    return "".join(x if i % 2 else re.sub(r"\s*(?:--|—)\s*", "—", x) for i, x in enumerate(parts))

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
    fallback), favicons and the share card, made once. The banner
    (banner-700.webp and banner-1400.webp) was designed separately and is committed."""
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
    card = os.path.join(IMAGES, "card.jpg")
    if not os.path.exists(card):
        W, H = 1200, 630
        bg = im.resize((W, W), Image.LANCZOS).crop((0, (W - H) // 2, W, (W + H) // 2))
        bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(28))).enhance(.55)
        bg.paste(im.resize((H, H), Image.LANCZOS), ((W - H) // 2, 0))
        bg.save(card, "JPEG", quality=84, optimize=True, progressive=True)


CARD_VERSION = "1"  # bump to redraw every episode card


def episode_card(art, title):
    """A 1200x630 share card for an episode: its cover on a blurred copy of
    itself, the title beside it. Square art in a large card gets cropped by
    X, Facebook and LinkedIn; this fits their 1.91:1 frame. Named by a hash
    of art and title, so a retitled episode gets a fresh card."""
    from PIL import Image, ImageDraw, ImageFilter, ImageEnhance, ImageFont
    name = "card-" + hashlib.sha1(("%s|%s|%s" % (CARD_VERSION, art, title)).encode()).hexdigest()[:12] + ".jpg"
    path = os.path.join(IMAGES, name)
    if os.path.exists(path):
        return name
    W, H, S, M = 1200, 630, 470, 80
    im = Image.open(os.path.join(IMAGES, art)).convert("RGB")
    bg = im.resize((W, W), Image.LANCZOS).crop((0, (W - H) // 2, W, (W + H) // 2))
    bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(30))).enhance(.35)
    bg.paste(im.resize((S, S), Image.LANCZOS), (M, (H - S) // 2))
    d = ImageDraw.Draw(bg)
    x, width = M + S + 56, W - (M + S + 56) - 64
    fonts = os.path.join(ROOT, "fonts")
    for size in range(64, 29, -2):  # largest size that fits in five lines
        font = ImageFont.truetype(os.path.join(fonts, "LiberationSans-Bold.ttf"), size)
        lines, line = [], ""
        for word in title.split():
            test = (line + " " + word).strip()
            if d.textlength(test, font=font) <= width or not line:
                line = test
            else:
                lines.append(line); line = word
        lines.append(line)
        if len(lines) <= 5 and all(d.textlength(l, font=font) <= width for l in lines):
            break
    lead = int(size * 1.18)
    label = ImageFont.truetype(os.path.join(fonts, "LiberationSans-Regular.ttf"), 26)
    top = (H - (len(lines) * lead + 52)) // 2
    d.text((x, top), NAME.upper(), font=label, fill=(200, 200, 200))
    for n, l in enumerate(lines):
        d.text((x, top + 52 + n * lead), l, font=font, fill=(255, 255, 255))
    bg.save(path, "JPEG", quality=82, optimize=True, progressive=True)
    return name


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
    """House rule for titles and descriptions: no pipes and no dashes used as separators. Hyphens inside words stay (back-of-the-book)."""
    s = re.sub(r"\s*\|\s*", ", ", s)
    seen = []
    def dash(m):
        seen.append(1)
        return ": " if len(seen) == 1 and ":" not in s else ", "
    s = re.sub(r"\s*[—–]\s*|\s+-+\s+", dash, s)
    s = re.sub(r"(?<!\w)-+|-+(?!\w)", " ", s)
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


def unwrap(href):
    """Google's redirect wrapper (google.com/url?q=...), pasted into some notes, becomes the real address."""
    m = re.match(r"https?://(?:www\.)?google\.[a-z.]+/url\?", href.strip())
    if m:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(href.strip()).query)
        return (q.get("q") or q.get("url") or [href])[0]
    return href


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
                href = unwrap(dict(attrs).get("href", "") or "")
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


AFFILIATE = json.load(open(os.path.join(DATA, "affiliate.json"))) if os.path.exists(os.path.join(DATA, "affiliate.json")) else {"tag": "", "items": []}
TAG = AFFILIATE["tag"]
DISCLOSURE = '<p class="disclosure">As an Amazon Associate I earn from qualifying purchases.</p>'


def amazon_url(item):
    if item.get("asin"):
        return "https://www.amazon.com/dp/%s?tag=%s" % (item["asin"], TAG)
    return "https://www.amazon.com/s?k=%s&tag=%s" % (urllib.parse.quote_plus(item["search"]), TAG)


def _pattern(item):
    words = re.escape(e(item["match"], quote=False)).replace("'", "['’]").replace("\\ ", "\\s+")
    return re.compile(r"(?<![\w&])%s(?![\w])" % words, re.I if item.get("ci") else 0)


for _it in AFFILIATE["items"]:
    _it["re"] = _pattern(_it)


def tag_amazon(h):
    """Every Amazon link in the notes carries Chris' Associates tag and rel="sponsored"."""
    def fix(m):
        url = html.unescape(m.group(1))
        short = {"https://amzn.to/1Pny8kw": "B00CHB9DVM", "https://a.co/d/bdx9hOR": "B0DD7VZJJB"}  # old tag, no tag
        if url in short:
            url = "https://www.amazon.com/dp/%s?tag=%s" % (short[url], TAG)
        elif re.match(r"https?://(www\.)?amazon\.com/", url) and "tag=" not in url:
            url += ("&" if "?" in url else "?") + "tag=" + TAG
        return '<a href="%s" rel="sponsored noopener">' % e(url)
    return re.sub(r'<a href="(https?://(?:www\.)?(?:amazon\.com|amzn\.to|a\.co)/[^"]*)">', fix, h)


def affiliate(h):
    """Link the first mention of each product in a piece of HTML, outside existing links and tags."""
    used = set()
    parts = re.split(r"(<a\b.*?</a>|<[^>]+>)", h, flags=re.S)
    for i in range(0, len(parts), 2):
        text = parts[i]
        for n, it in enumerate(AFFILIATE["items"]):
            if n in used:
                continue
            m = it["re"].search(text)
            if m and not re.search(r"<a\b[^>]*>[^<]*$", text[:m.start()]):
                used.add(n)
                text = text[:m.start()] + '<a href="%s" rel="sponsored noopener">%s</a>' % (e(amazon_url(it)), m.group(0)) + text[m.end():]
        parts[i] = text
    return "".join(parts)


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


def link_youtube(eps):
    """Give each episode its YouTube video, matched by exact title. The channel feed lists only the latest
    15 uploads, so a run that misses one leaves it for tomorrow; a failed fetch never fails the build."""
    try:
        root = ET.fromstring(get(YOUTUBE_FEED))
    except Exception as err:
        print("  YouTube feed not read:", err, file=sys.stderr)
        return
    atom, yt = "{http://www.w3.org/2005/Atom}", "{http://www.youtube.com/xml/schemas/2015}"
    key = lambda t: re.sub(r"\s+", " ", html.unescape(t or "")).strip().casefold()
    videos = {key(v.findtext(atom + "title")): v.findtext(yt + "videoId") for v in root.iter(atom + "entry")}
    for ep in eps:
        if not ep.get("youtube") and videos.get(key(ep["title"])):
            ep["youtube"] = videos[key(ep["title"])]


def youtube_link(ep):
    return "https://www.youtube.com/watch?v=" + ep["youtube"] if ep.get("youtube") else ""


def spotify_link(ep):
    if ep.get("spotify"):
        return ep["spotify"]
    m = re.search(r"transcript-files\.spotifycdn\.com/[A-Za-z0-9]+/([A-Za-z0-9]+)/", ep.get("transcript_url", ""))
    return "https://open.spotify.com/episode/" + m.group(1) if m else ep.get("link") or SPOTIFY_SHOW


# ---------------------------------------------------------------- pages

def jsonld(obj):
    return '<script type="application/ld+json">\n%s\n</script>' % json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def page(path, title, desc, body, *, image=None, image_alt=None, keywords=None, og_type="website", ld=None, robots=None, prev=None, nxt=None, current=None, published=None):
    url = SITE + "/" + ("" if path == "index.html" else path)
    if not image:
        image, image_alt = "/assets/images/card.jpg", "The Chris Abraham Show cover art beside a phone playing the podcast"
    nav = lambda label: '<nav class="site-nav" aria-label="%s">\n%s\n</nav>' % (label, '<span aria-hidden="true"> · </span>'.join(
        '<a href="%s"%s>%s</a>' % (u, ' aria-current="page"' if u == current else "", t)
        for t, u in (("Home", "/"), ("About", "/about.html"), ("Archives", "/archives.html"), ("Articles", "/articles.html"), ("Glossary", "/glossary.html"), ("Index", "/show-index.html"), ("Colophon", "/colophon.html"))))
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
    head.append('<meta name="robots" content="%s">' % (robots or "index, follow, max-image-preview:large, max-snippet:-1"))
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
    ]
    try:  # dimensions let Facebook, LinkedIn, and X lay out the preview on the first share
        from PIL import Image
        w, h = Image.open(os.path.join(ROOT, image.lstrip("/"))).size
        head += ['<meta property="og:image:type" content="image/%s">' % ("png" if image.endswith(".png") else "webp" if image.endswith(".webp") else "jpeg"),
                 '<meta property="og:image:width" content="%d">' % w, '<meta property="og:image:height" content="%d">' % h]
    except Exception:
        pass
    if published:
        head += ['<meta property="article:published_time" content="%s">' % published, '<meta property="article:author" content="%s">' % HOME]
    head += [
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
    head.append(SPECULATION)
    head.append('<link rel="me" href="https://mastodon.social/@chrisa">')
    if prev:
        head.append('<link rel="prev" href="%s">' % prev)
    if nxt:
        head.append('<link rel="next" href="%s">' % nxt)
    head.append("</head>")
    return "\n".join(head + [
        "<body>", '<a class="skip" href="#content">Skip to the content</a>', '<div id="container">', '  <header id="banner">',
        '    <a href="/" accesskey="1"><img src="/assets/images/banner-700.webp" srcset="/assets/images/banner-700.webp 700w, /assets/images/banner-1400.webp 1400w" sizes="(min-width: 700px) 700px, 100vw" alt="The Chris Abraham Show: home" width="700" height="240"></a>',
        '    <button type="button" class="theme-toggle" id="theme-toggle" hidden>Dark</button>',
        "  </header>", "  " + nav("Site"), '  <div class="columns">', '    <main class="content" id="content">',
        body, "    </main>", SIDEBAR, "  </div>", '  <footer class="site-footer">', "    " + nav("Footer"),
        '    <p>%s © %s–%s <a href="/about.html" rel="author">%s</a>.</p>' % (NAME, FIRST_YEAR, LAST_YEAR, AUTHOR),
        '    <p>Mirrored from Spotify for Creators. Static on GitHub Pages. <a href="/privacy.html">Privacy</a>.</p>',
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
# Google Analytics 4 (Chris's property). gtag.js loads on the first scroll, tap, or key,
# or 4 seconds after the page loads, so it never competes with the page itself; the
# commands below wait in dataLayer until it arrives.
GA4 = """<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  // Consent mode: no ad storage anywhere; no analytics cookies in the EU, EEA, UK, or
  // Switzerland, or when the browser sends Global Privacy Control or Do Not Track. See /privacy.html.
  gtag('consent', 'default', {ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied', analytics_storage: 'granted'});
  gtag('consent', 'default', {analytics_storage: 'denied', region: ['AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IE','IT','LV','LT','LU','MT','NL','PL','PT','RO','SK','SI','ES','SE','IS','LI','NO','GB','CH']});
  if (navigator.globalPrivacyControl === true || navigator.doNotTrack === '1') gtag('consent', 'default', {analytics_storage: 'denied'});
  gtag('js', new Date());
  gtag('config', 'G-WQGSJJEDKP', {allow_google_signals: false, allow_ad_personalization_signals: false});
  (function () {
    var done = false;
    function load() {
      if (done) return;
      done = true;
      var s = document.createElement('script');
      s.async = true;
      s.src = 'https://www.googletagmanager.com/gtag/js?id=G-WQGSJJEDKP';
      document.head.appendChild(s);
    }
    ['pointerdown', 'keydown', 'scroll', 'touchstart'].forEach(function (e) { addEventListener(e, load, {once: true, passive: true}); });
    addEventListener('load', function () { setTimeout(load, 4000); });
  })();
</script>"""

# Chrome prefetches a same-site page when a reader hovers or starts to tap its link,
# so the next page opens at once. Text, JSON, and XML files are left alone.
SPECULATION = '<script type="speculationrules">{"prefetch": [{"where": {"and": [{"href_matches": "/*"}, {"not": {"href_matches": ["/*.txt", "/*.json", "/*.xml"]}}]}, "eagerness": "moderate"}]}</script>'

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
User-agent: GoogleOther
User-agent: Google-CloudVertexBot
User-agent: Google-Agent
User-agent: Google-GeminiNotebook
User-agent: meta-webindexer
User-agent: meta-externalagent
User-agent: meta-externalfetcher
User-agent: Amzn-SearchBot
User-agent: Amzn-User
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


def srcset(src):
    """WebP copies of each cover at 400, 600, and 800px, so phones take the smallest
    one that stays sharp. The JPEG stays as src for old browsers and text browsers."""
    from PIL import Image
    full = os.path.join(ROOT, src.lstrip("/"))
    big = Image.open(full).size[0]
    widths = sorted({w for w in (400, 600, 800) if w < big} | {big})
    out = []
    for w in widths:
        name = src[:-4] + ("" if w == big else "-%d" % w) + ".webp"
        path = os.path.join(ROOT, name.lstrip("/"))
        if not os.path.exists(path):
            im = Image.open(full).convert("RGB")
            im.thumbnail((w, w), Image.LANCZOS)
            im.save(path, "WEBP", quality=78, method=6)
        out.append("%s %dw" % (name, w))
    return 'src="%s" srcset="%s" sizes="(min-width: 700px) 636px, calc(100vw - 32px)"' % (src, ", ".join(out))


def write(path, text):
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    old = open(full).read() if os.path.exists(full) else None
    if old != text:
        with open(full, "w") as f:
            f.write(text)


def share_line(url, title):
    q = urllib.parse.quote
    return '<p class="listen">Share: %s</p>' % " · ".join('<a href="%s">%s</a>' % (e(u), n) for n, u in (
        ("X", "https://x.com/intent/post?url=%s&text=%s" % (q(url), q(title))),
        ("Facebook", "https://www.facebook.com/sharer/sharer.php?u=" + q(url)),
        ("LinkedIn", "https://www.linkedin.com/sharing/share-offsite/?url=" + q(url)),
        ("Reddit", "https://www.reddit.com/submit?url=%s&title=%s" % (q(url), q(title))),
        ("Bluesky", "https://bsky.app/intent/compose?text=" + q(title + " " + url)),
        ("Threads", "https://www.threads.net/intent/post?text=" + q(title + " " + url)),
        ("Email", "mailto:?subject=%s&body=%s" % (q(title), q(url)))))


def _size(img):
    from PIL import Image
    return Image.open(os.path.join(ROOT, img.lstrip("/"))).size


ARTICLES = os.path.join(DATA, "articles")
NOTES = os.path.join(DATA, "notes")


def more_notes(ep):
    """Site-only notes: data/notes/<guid>.html, HTML shown below the Spotify notes on the episode's page.
    Spotify caps notes at 4,000 characters; this has no cap, and the daily feed update never touches it."""
    path = os.path.join(NOTES, ep["guid"] + ".html")
    return anchor_terms(close_dashes(open(path).read().strip())) if os.path.exists(path) else ""


def term_slug(t):
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    return "g-" + (re.sub(r"[^a-z0-9]+", "-", t.lower().strip(' "“”')).strip("-") or "term")


def anchor_terms(h):
    """Every glossary <dt> gets an id, so the site Glossary can link straight to the definition."""
    return re.sub(r"<dt>(.*?)</dt>", lambda m: '<dt id="%s">%s</dt>' % (term_slug(m.group(1)), m.group(1)), h, flags=re.S)


def glossary_terms(eps):
    """(term, sort key, [(episode, anchor)]) for every term in every episode's site notes."""
    terms = {}
    for ep in eps:
        path = os.path.join(NOTES, ep["guid"] + ".html")
        if not os.path.exists(path):
            continue
        for t in re.findall(r"<dt>(.*?)</dt>", open(path).read(), re.S):
            name = html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
            key = name.lower().strip(' "“”')
            terms.setdefault(key, [name, []])[1].append((ep, term_slug(t)))
    return sorted(terms.items())


def load_articles():
    """Written articles: data/articles/<slug>.html, a "key: value" header, a --- line, then the HTML body.
    They're kept apart from episodes everywhere: their own pages, archive, and schema (BlogPosting)."""
    out = []
    for f in sorted(os.listdir(ARTICLES)) if os.path.isdir(ARTICLES) else []:
        if not f.endswith(".html"):
            continue
        head, body = open(os.path.join(ARTICLES, f)).read().split("\n---\n", 1)
        a = dict(line.split(": ", 1) for line in head.strip().splitlines())
        # Chris' rule: "--" becomes an em dash, and every em dash is closed up tight (no spaces either side).
        body = close_dashes(body)
        a["body"], a["path"] = body.strip(), "articles/" + f
        a["when"] = datetime.datetime.fromisoformat(a["date"]).replace(hour=12, tzinfo=TZ)
        out.append(a)
    return sorted(out, key=lambda a: a["when"], reverse=True)


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
        '      <p><a href="/about.html">About the show</a><br><a href="/archives.html">Every episode</a><br><a href="/articles.html">Articles</a></p>',
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
        # Each link names its episode for screen readers and crawlers; the visible labels stay short.
        def named(ep):
            return '<span class="visually-hidden">: %s</span>' % e(ep["title"])
        parts = ['<a href="/%s">First%s</a>' % (first["path"], named(first))]
        if i > 0:
            parts.append('<a href="/%s" rel="prev">&laquo; Previous%s</a>' % (eps[i - 1]["path"], named(eps[i - 1])))
        if i < total - 1:
            parts.append('<a href="/%s" rel="next">Next%s &raquo;</a>' % (eps[i + 1]["path"], named(eps[i + 1])))
        parts.append('<a href="/%s">Latest%s</a>' % (latest["path"], named(latest)))
        return '<nav class="entry-nav" aria-label="Episodes">\n  %s\n</nav>' % " |\n  ".join(parts)

    alts = json.load(open(os.path.join(DATA, "alts.json"))) if os.path.exists(os.path.join(DATA, "alts.json")) else {}

    def alt(ep):
        """Each cover's own description (data/alts.json, written by looking at the art)."""
        return alts.get(ep.get("art") or "show.jpg") or 'Cover art for the episode "%s."' % ep["title"]

    def art(ep):
        return "/assets/images/" + (ep.get("art") or "show.jpg")

    def episode_block(i, ep, heading):
        n = i + 1
        notes = clean_html(fixed(ep["description"])) or "<p>No show notes for this episode.</p>"
        if heading == "h1" and more_notes(ep):
            notes += '\n<section class="more-notes">\n%s\n</section>' % more_notes(ep)
        notes = affiliate(tag_amazon(notes))
        if "tag=" + TAG in notes or re.search(r'href="https://(amzn\.to|a\.co)/', notes):
            notes += DISCLOSURE
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
            '<img class="episode-art" %s alt="%s" width="800" height="800" fetchpriority="high">' % (srcset(art(ep)), e(alt(ep))),
            '<audio class="player" controls preload="none" src="%s"><a href="%s">Download the audio</a></audio>' % (e(ep["audio"]), e(ep["audio"])),
            '<p class="listen">Play <a href="%s">this episode on Spotify</a>%s, or subscribe: %s</p>' % (
                e(spotify_link(ep)), ' or <a href="%s">watch it on YouTube</a>' % e(youtube_link(ep)) if youtube_link(ep) else "",
                " · ".join('<a href="%s">%s</a>' % (e(u), n) for n, u in LISTEN if n in ("Apple Podcasts", "Overcast", "Pocket Casts", "RSS"))),
            share,
            notes,
            "  </div>",
            "</article>"])

    # episode pages
    cards = set()
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
        if youtube_link(ep):
            ld_ep["sameAs"] = [youtube_link(ep)]
        if ep["episode"]:
            ld_ep["episodeNumber"] = ep["episode"]
        if ep["season"]:
            ld_ep["partOfSeason"] = {"@type": "PodcastSeason", "seasonNumber": ep["season"], "partOfSeries": {"@id": SITE + "/#series"}}
        block = episode_block(i, ep, "h1")
        tpath = os.path.join(TRANSCRIPTS, ep["guid"] + ".srt")
        transcript = ""
        if os.path.exists(tpath):
            paras = transcript_paragraphs(open(tpath).read())
            fixes = TRANSCRIPT_FIXES + (DND_FIXES if DND_EPISODE.search(ep["title"] + " " + (ep.get("description") or "")) else [])
            for pat, to in fixes:
                paras = [re.sub(pat, to, p_) for p_ in paras]
            if paras:
                words = sum(len(p.split()) for p in paras)
                transcript = ('<section class="transcript" aria-labelledby="transcript">\n<h2 id="transcript">Transcript</h2>\n'
                              '<details>\n<summary>Read the full transcript (%s words, machine made)</summary>\n%s\n</details>\n</section>'
                              % ("{:,}".format(words), affiliate("\n".join("<p>%s</p>" % e(p) for p in paras))))
                if "tag=" + TAG in transcript and DISCLOSURE not in block:
                    transcript = transcript.replace("\n</details>\n</section>", "\n</details>\n%s\n</section>" % DISCLOSURE)
        ld = {"@context": "https://schema.org", "@graph": [person, ld_ep, {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": NAME, "item": SITE + "/"},
            {"@type": "ListItem", "position": 2, "name": "Archives", "item": SITE + "/archives.html"},
            {"@type": "ListItem", "position": 3, "name": ep["title"], "item": url}]}]}
        body = "\n".join([nav(i), block, transcript, nav(i)])
        shown = ep["title"] if len(ep["title"]) <= 90 else title  # the SEO title when the full one won't fit
        card = "/assets/images/" + episode_card(ep.get("art") or "show.jpg", shown)
        cards.add(card.rsplit("/", 1)[1])
        write(ep["path"], page(ep["path"], title, desc, body, image=card,
                               image_alt='%s Beside it, the episode title: "%s."' % (alt(ep), shown.rstrip(".")),
                               keywords=keywords, og_type="article", ld=ld,
                               prev="/" + eps[i - 1]["path"] if i else None,
                               nxt="/" + eps[i + 1]["path"] if i < total - 1 else None))

    for old in os.listdir(IMAGES):  # cards for retitled episodes
        if old.startswith("card-") and old not in cards:
            os.remove(os.path.join(IMAGES, old))

    # articles
    arts = load_articles()
    for a in arts:
        url = SITE + "/" + a["path"]
        img = a.get("image")
        small = img[:-4] + "-600.jpg" if img else ""
        about = [{"@type": "Thing", "name": n.strip(), "sameAs": u.strip()} for n, u in (x.split("|") for x in a["about"].split(";;"))] if a.get("about") else []
        ld = {"@context": "https://schema.org", "@graph": [person, {
            "@type": "BlogPosting", "@id": url + "#article", "url": url, "headline": a["title"], "description": a["description"],
            "datePublished": a["when"].isoformat(), "author": {"@id": SITE + "/#author"}, "publisher": {"@id": SITE + "/#author"},
            "image": SITE + img if img else None, "inLanguage": "en", "isPartOf": {"@id": SITE + "/#website"}, "about": about},
            {"@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": NAME, "item": SITE + "/"},
                {"@type": "ListItem", "position": 2, "name": "Articles", "item": SITE + "/articles.html"},
                {"@type": "ListItem", "position": 3, "name": a["title"], "item": url}]}]}
        figure = ('<figure class="article-art"><img src="%s" srcset="%s 600w, %s 1200w" sizes="(min-width: 700px) 636px, calc(100vw - 32px)" alt="%s" width="%d" height="%d" fetchpriority="high">%s</figure>'
                  % ((img, small, img, e(a["image_alt"])) + _size(img) + ("<figcaption>%s</figcaption>" % e(a["image_credit"]) if a.get("image_credit") else "",))) if img else ""
        a_body = affiliate(a["body"])
        if "tag=" + TAG in a_body:
            a_body += "\n" + DISCLOSURE
        body = "\n".join(['<article class="entry">', '  <h1>%s</h1>' % e(a["title"]),
                          '  <p class="meta">An article by <a href="/about.html" rel="author">%s</a>, %s</p>' % (AUTHOR, nice(a["when"])),
                          '  <div class="entry-body">', figure, share_line(url, a["title"]), a_body, share_line(url, a["title"]), "  </div>", "</article>",
                          '<p class="intro">More writing in the <a href="/articles.html">articles</a>, and every episode of the show in the <a href="/archives.html">archives</a>.</p>'])
        card = img[:-4] + "-card.jpg" if img and os.path.exists(os.path.join(ROOT, img[:-4].lstrip("/") + "-card.jpg")) else img
        write(a["path"], page(a["path"], a["seo_title"], a["description"], body, image=card, image_alt=a.get("image_alt"),
                              keywords=a.get("keywords"), og_type="article", ld=ld, published=a["when"].isoformat()))
    rows = "\n".join('<li><a href="/%s">%s</a>, %s<br>%s</li>' % (a["path"], e(a["title"]), nice(a["when"]), e(a["description"])) for a in arts)
    write("articles.html", page("articles.html", "Articles by Chris Abraham, written alongside his podcast",
                                "Written articles by Chris Abraham, the host of the show: essays, confessions, and enthusiasms that work better on the page than out loud.",
                                '<h1 class="archive-title">Articles</h1>\n<div class="entry-body"><p>Writing that works better on the page than out loud. The episodes are in the <a href="/archives.html">archives</a>.</p>\n<ul class="articles">\n%s\n</ul></div>' % rows,
                                current="/articles.html", keywords="Chris Abraham, articles, essays",
                                ld={"@context": "https://schema.org", "@type": "CollectionPage", "url": SITE + "/articles.html", "name": "Articles", "isPartOf": {"@id": SITE + "/#website"}}))

    # glossary: an index of every term in the episodes' site notes, linking to each definition
    gterms = glossary_terms(eps)
    if gterms:
        rows, letter = [], None
        for key, (name, uses) in gterms:
            initial = key[0].upper() if key[0].isalpha() else "#"
            if initial != letter:
                if letter is not None:
                    rows.append("</ul>")
                rows += ['<h2 id="letter-%s">%s</h2>' % (initial.lower() if initial != "#" else "num", initial), "<ul class=\"glossary-index\">"]
                letter = initial
            links = ", ".join('<a href="/%s#%s">%s</a>' % (ep["path"], a, e(ep["title"])) for ep, a in uses)
            rows.append("<li><strong>%s</strong>: %s</li>" % (e(name), links))
        rows.append("</ul>")
        n_eps = len({ep["guid"] for _, (_, uses) in gterms for ep, _ in uses})
        body = "\n".join(['<h1 class="archive-title">Glossary</h1>', '<div class="entry-body">',
                          "<p>Every term defined in the show's extended episode notes, %d of them from %d episodes so far. Each one links to its definition on the episode page, and the list grows as new episodes add terms. People, places, and subjects are in the <a href=\"/show-index.html\">index</a>.</p>" % (len(gterms), n_eps)]
                         + rows + ["</div>"])
        write("glossary.html", page("glossary.html", "Glossary of terms from The Chris Abraham Show episodes",
                                    fit("An index of every term defined in the extended notes of Chris Abraham's podcast episodes, from Ampelmann to Moonlighting, each linked to its definition.", 160),
                                    body, current="/glossary.html", keywords="Chris Abraham, podcast glossary, terms, definitions",
                                    ld={"@context": "https://schema.org", "@type": "DefinedTermSet", "name": "Glossary", "url": SITE + "/glossary.html",
                                        "hasDefinedTerm": [{"@type": "DefinedTerm", "name": name, "url": SITE + "/" + uses[0][0]["path"] + "#" + uses[0][1]} for _, (name, uses) in gterms]}))

    # show index: a hand-built back-of-the-book index, data/show_index.json, keyed by episode guid
    sidx_path = os.path.join(DATA, "show_index.json")
    if os.path.exists(sidx_path):
        sidx = json.load(open(sidx_path))
        num = {ep["guid"]: n for n, ep in enumerate(eps, 1)}
        reviewed = set(sidx.get("reviewed", []))
        missing = [n for n, ep in enumerate(eps, 1) if ep["guid"] not in reviewed]
        if missing:
            print("show index: %d episode(s) not indexed yet: %s" % (len(missing), ", ".join(map(str, missing))))
        sort_key = lambda h: re.sub(r"^(series: |the |a |an )", "", re.sub(r"^[\"'“‘(]+", "", h.lower()))

        def refs(guids):
            out = []
            for n in sorted({num[g] for g in guids if g in num}):
                ep = eps[n - 1]
                out.append('<a href="/%s" title="%s">%d</a>' % (ep["path"], e(close_dashes(ep["title"])), n))
            return ", ".join(out)

        heads = sorted(sidx["entries"], key=lambda h: (sort_key(h), h))
        rows, letter, n_people, used = [], None, 0, set()
        for h in heads:
            ent = sidx["entries"][h]
            subs = [(s, refs(g)) for s, g in sorted(ent["subs"].items(), key=lambda kv: sort_key(kv[0])) if refs(g)]
            main = refs(ent["eps"])
            if not main and not subs:
                continue
            used.update(g for g in ent["eps"] + [g for l in ent["subs"].values() for g in l] if g in num)
            n_people += ent["kind"] == "person"
            fl = sort_key(h)[:1].upper()
            initial = fl if fl.isalpha() else "#"
            if initial != letter:
                if letter is not None:
                    rows += ["</ul>", '<p class="index-top"><a href="#index-letters">Back to the letters</a></p>']
                rows += ['<h2 id="letter-%s">%s</h2>' % (initial.lower() if initial != "#" else "num", "0–9" if initial == "#" else initial), '<ul class="show-index">']
                letter = initial
            label = e(close_dashes(h))
            if h.startswith("series: "):
                label = "<em>series:</em> " + e(close_dashes(h[8:]))
            line = "  <li>%s%s" % (label, ", " + main if main else "")
            see = [s for s in ent.get("see", []) if s in sidx["entries"] and s != h]
            if subs:
                line += '\n    <ul>\n' + "\n".join("      <li>%s, %s</li>" % (e(close_dashes(s)), r) for s, r in subs) + "\n    </ul>"
            if see:
                line += ' <span class="see-also"><em>See also</em> %s.</span>' % "; ".join(
                    '<a href="#ix-%s">%s</a>' % (re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-"), e(close_dashes(s))) for s in see)
            rows.append(line.replace("<li>", '<li id="ix-%s">' % re.sub(r"[^a-z0-9]+", "-", h.lower()).strip("-"), 1) + "</li>")
        rows += ["</ul>", '<p class="index-top"><a href="#index-letters">Back to the letters</a></p>']
        n_heads = sum(1 for r in rows if r.startswith('  <li id="ix-'))
        present = {m for r in rows for m in re.findall(r'^<h2 id="letter-([^"]+)">', r)}
        jump = '<nav class="index-letters" id="index-letters" aria-label="Jump to a letter"><p>%s</p></nav>' % " ".join(
            ('<a href="#letter-%s">%s</a>' % (k, lab)) if k in present else '<span class="index-empty" title="No headings under %s">%s</span>' % (lab, lab)
            for k, lab in [("num", "0–9")] + [(chr(c).lower(), chr(c)) for c in range(65, 91)])
        intro = ("<p>Every book about Washington has an index in the back, and everyone in town reads it first. This is the index for my show. "
                 "It covers %s, from the first ChrisCast in %s to the latest, with %s headings for people, places, books, shows, arguments, and stories, %d of them people. "
                 "I named a lot of people over the years, and every one of them is here, surname first. Friends who only ever got a first name keep it, with a word about who they are.</p>"
                 "<p>The numbers are episode numbers, the same &ldquo;Episode N of %d&rdquo; at the top of every episode page, and each one links to that episode. "
                 "Hover over a number to see the episode&rsquo;s title. Big subjects, like guns, diet, heart health, aphantasia, and the deportation industrial complex, break into subentries. "
                 "Recurring segments and the Curse of Strahd campaign are filed under <em>series</em>. "
                 "The terms I define in the episode notes are in the <a href=\"/glossary.html\">glossary</a>, and every episode in order is in the <a href=\"/archives.html\">archives</a>.</p>") % (
                     ("all %d episodes" % total if len(used) == total else "%d of the %d episodes" % (len(used), total)), when(first).year, format(n_heads, ","), n_people, total)
        body = "\n".join(['<h1 class="archive-title">Index</h1>', '<div class="entry-body">', intro, jump] + rows + ["</div>"])
        write("show-index.html", page("show-index.html", "Index of The Chris Abraham Show: every person, place, and subject",
                                      fit("A back-of-the-book index to all %d episodes of Chris Abraham's podcast: every person named on the show, plus subjects from aphantasia to South Arlington." % total, 160),
                                      body, current="/show-index.html", keywords="Chris Abraham, podcast index, people, subjects, episodes",
                                      ld={"@context": "https://schema.org", "@type": "CollectionPage", "url": SITE + "/show-index.html", "name": "Index",
                                          "description": "A back-of-the-book index to The Chris Abraham Show", "isPartOf": {"@id": SITE + "/#website"}}))

    show_desc = plain(fixed(show["description"]))
    home_desc = fit("Every episode of Chris Abraham's podcast since %d: politics, AI, SEO, fitness, D&D, aphantasia and South Arlington life, with notes and transcripts." % FIRST_YEAR, 160)
    sentence = "Every episode of %s, %s to %s, with show notes and transcripts." % (NAME, nice(when(first)), nice(when(latest)))

    # home
    body = "\n".join([
        '<h1 class="visually-hidden">%s</h1>' % NAME,
        '<p class="latest">Latest: <a href="/%s">%s</a>, %s</p>' % (latest["path"], e(latest["title"]), nice(when(latest))),
        episode_block(total - 1, latest, "h2"), nav(total - 1),
        '<p class="intro">%s</p>' % e(sentence)]
        + (['<p class="latest">Latest article: <a href="/%s">%s</a>, %s</p>' % (arts[0]["path"], e(arts[0]["title"]), nice(arts[0]["when"]))] if arts else []))
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
    body = '<h1 class="archive-title">Archives</h1>\n<p>All %d episodes, newest first. To find an episode by person or subject, use the <a href="/show-index.html">index</a>.</p>\n%s' % (total, "\n".join(rows))
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
        '<img class="episode-art" ' + srcset("/assets/images/show.jpg") + ' alt="%s" width="1000"' % e(alts.get("show.jpg", "The Chris Abraham Show cover art")) + ' height="1000">',
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
        "<p>Chris records on a <a href=\"https://www.amazon.com/dp/B082QL6KLG?tag=gerris-20\" rel=\"sponsored noopener\">Sony ICD-UX570 digital voice recorder</a> and edits in <a href=\"https://www.audacityteam.org/\">Audacity</a>. When a recording has too much background noise, it goes through <a href=\"https://podcast.adobe.com/enhance\">Adobe Podcast Enhance</a> first. The video episodes, and the occasional over-the-top \"Deep Dive,\" come from running an episode through <a href=\"https://notebooklm.google.com/\">Google NotebookLM</a>.</p>",
        "<h2>Credits</h2>",
        "<p>The intro and outro music was made by <a href=\"https://www.fiverr.com/blarock/create-a-professional-podcast-intro-and-outro\">blarock on Fiverr</a>. The cover art, which is also this site's banner and icon, was designed by <a href=\"https://www.fiverr.com/brandzin/design-a-professional-podcast-cover-art\">brandzin on Fiverr</a>. Thank you both.</p>",
        "<p>Every episode also gets its own cover. The early ChrisCast covers were made by hand; these days Chris makes a custom cover for each episode with ChatGPT.</p>",
        "<h2>This Website</h2>",
        '<p>This site is a static mirror of the show\'s <a href="%s">podcast feed</a>. A small Python script reads the feed, keeps a copy of each episode\'s artwork and transcript, and writes one plain HTML page per episode. A GitHub Action runs it every day, so new episodes and late transcripts appear on their own. It is published by <a href="https://pages.github.com/">GitHub Pages</a>; the source is at <a href="https://github.com/chrisabraham/thechrisabrahamshow">github.com/chrisabraham/thechrisabrahamshow</a>.</p>' % FEED,
        "<h2>Audio</h2>",
        '<p>The audio stays on Spotify. Each player streams the episode straight from the feed, and <a href="%s">Spotify</a> remains the show\'s home.</p>' % SPOTIFY_SHOW,
        "<h2>Transcripts</h2>",
        "<p>Transcripts are generated by Spotify. They are machine made, so names and odd words are sometimes wrong.</p>",
        "<h2>Design</h2>",
        '<p>The layout is borrowed from <a href="https://hillmole.com/">Hill Mole</a>, Chris\'s serialized novel: a white column as wide as the 700-pixel banner, on a grey page, with grey links. Unlike Hill Mole, the headings keep their capitals. The button in the banner switches to a dark reading mode, and that choice is kept only in your own browser.</p>',
        "<h2>Amazon Links</h2>",
        "<p>Links to books and gear on Amazon, in the colophon, the show notes, and the transcripts, carry Chris\'s Amazon Associates tag. As an Amazon Associate I earn from qualifying purchases.</p>",
        "</div>"])
    write("colophon.html", page("colophon.html", "Colophon: how Chris Abraham's podcast is made",
                                "How Chris Abraham records and edits his podcast, who made the music and cover art, and how this site mirrors his Spotify feed every day.",
                                body, keywords="colophon, Sony ICD UX570, Audacity, podcast credits", current="/colophon.html"))

    # privacy
    body = "\n".join([
        '<div class="entry-body">',
        "<h1>Privacy</h1>",
        "<p>This site is a static mirror of a podcast. It has no ads, no comments, no forms, and no sign-ups. Here is everything it does with visitor data.</p>",
        "<h2>Google Analytics</h2>",
        "<p>Google Analytics 4 counts visits and shows which episodes people read and how they found the site. Advertising features, ad storage, and Google signals are switched off for everyone, so nothing here is used to target ads. Visitors in the European Union, the EEA, the United Kingdom, and Switzerland get no analytics cookies; Google receives only cookieless pings from them. The same goes for anyone whose browser sends Global Privacy Control or Do Not Track. Everyone else may get Google's first-party <code>_ga</code> cookies, which hold a random identifier so a returning listener is counted once. The script waits until you scroll, tap, or type, or four seconds after the page loads. Block it however you like, including with <a href=\"https://tools.google.com/dlpage/gaoptout\">Google's opt-out add-on</a>, and the site works the same. Google's policy is at <a href=\"https://policies.google.com/privacy\">policies.google.com/privacy</a>.</p>",
        "<h2>Audio and Transcripts</h2>",
        "<p>The players stream each episode from the podcast's host, Spotify for Creators, only when you press play, so Spotify's servers see that request under <a href=\"https://www.spotify.com/legal/privacy-policy/\">Spotify's privacy policy</a>. Transcripts and cover art are copies kept on this site.</p>",
        "<h2>Your Device</h2>",
        "<p>The dark mode button saves your choice in your browser under <code>tcas-theme</code>. It never leaves your device.</p>",
        "<h2>Hosting</h2>",
        "<p>GitHub Pages serves the site and may log visitors' IP addresses for security under <a href=\"https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement\">GitHub's privacy statement</a>. The domain's DNS is at Cloudflare, which does not sit between you and the pages.</p>",
        "<h2>Amazon Links</h2>",
        "<p>Some links to books and gear carry Chris's Amazon Associates tag, which Amazon uses to credit purchases. As an Amazon Associate I earn from qualifying purchases.</p>",
        "<h2>Questions</h2>",
        '<p>Chris holds no personal information from this site beyond email you send him. Write to <a href="mailto:cja@well.com">cja@well.com</a>.</p>',
        "</div>"])
    write("privacy.html", page("privacy.html", "Privacy: what this podcast site collects and why",
                               "What Chris Abraham's podcast site collects: Google Analytics with ad features off, no analytics cookies in Europe or with privacy signals, and no ads.",
                               body, keywords="privacy, Google Analytics consent mode, cookies, Global Privacy Control", current="/privacy.html"))

    # search
    body = "\n".join([
        '<h1 class="archive-title">Search</h1>',
        '<form class="search" method="get" action="/search.html" role="search"><label for="q" class="visually-hidden">Search the episodes</label><input id="q" name="q" type="search" placeholder="Search the episodes"></form>',
        '<p id="status" class="intro" aria-live="polite">Type a word or two.</p>',
        '<ul id="results" class="toc results"></ul>',
        '<script src="/assets/search.js" defer></script>'])
    write("search.html", page("search.html", "Search Chris Abraham's podcast episodes", "Search every episode of Chris Abraham's podcast by title and show notes.", body, robots="noindex, follow"))
    index = [{"t": ep["title"], "u": "/" + ep["path"], "d": nice(when(ep)), "n": fit(plain(fixed(ep["description"]) + " " + more_notes(ep)), 1200)} for ep in reversed(eps)]
    write("search.json", json.dumps(index, ensure_ascii=False, separators=(",", ":")))

    # 404
    body = '<h1 class="archive-title">Not found</h1>\n<div class="entry-body"><p>There is no page at this address. Try the <a href="/archives.html">list of every episode</a>, the <a href="/search.html">search</a>, or the <a href="/">latest episode</a>.</p></div>'
    write("404.html", page("404.html", "Page not found", "There is no page at this address.", body, robots="noindex"))

    # sitemap and robots
    urls = [(SITE + "/", when(latest)), (SITE + "/archives.html", when(latest)), (SITE + "/about.html", None), (SITE + "/colophon.html", None), (SITE + "/privacy.html", None)]
    urls += [(SITE + "/" + ep["path"], when(ep)) for ep in reversed(eps)]
    urls += [(SITE + "/glossary.html", None), (SITE + "/show-index.html", None)]
    urls += [(SITE + "/articles.html", arts[0]["when"] if arts else None)] + [(SITE + "/" + a["path"], a["when"]) for a in arts]
    write("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s\n</urlset>\n' % "\n".join(
        "  <url><loc>%s</loc>%s</url>" % (e(u), "<lastmod>%s</lastmod>" % d.date().isoformat() if d else "") for u, d in urls))
    write("robots.txt", ROBOTS % SITE)
    lines = ["# %s" % NAME, "", "> %s" % show_desc, "",
             "Chris Abraham's podcast, %d episodes from %s to %s. Each episode page has the audio, show notes and, where Spotify has made one, a full transcript." % (total, nice(when(first)), nice(when(latest))),
             "", "## Pages", "", "- [About](%s/about.html): where to listen and subscribe" % SITE, "- [Archives](%s/archives.html): every episode" % SITE, "- [Articles](%s/articles.html): written articles by the host" % SITE, "- [Index](%s/show-index.html): people, places, and subjects, with the episodes where each appears" % SITE,
             "- [Full show notes](%s/llms-full.txt): every episode's notes in one plain text file" % SITE, "", "## Episodes", ""]
    lines += ["- [%s](%s/%s): %s%s" % (ep["title"], SITE, ep["path"], nice(when(ep)), ", " + clock(seconds(ep["duration"])) if seconds(ep["duration"]) else "") for ep in reversed(eps)]
    if arts:
        lines += ["", "## Articles", ""] + ["- [%s](%s/%s): %s" % (a["title"], SITE, a["path"], nice(a["when"])) for a in arts]
    write("llms.txt", "\n".join(lines) + "\n")
    full = ["# %s: every episode's show notes" % NAME, ""]
    for i in range(total - 1, -1, -1):
        ep = eps[i]
        full += ["## %s" % ep["title"], "", "Episode %d of %d, %s. %s/%s" % (i + 1, total, nice(when(ep)), SITE, ep["path"]), "", plain(fixed(ep["description"])) or "(No show notes.)", ""] + ([plain(more_notes(ep)), ""] if more_notes(ep) else [])
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
        if f.startswith(DATA):  # article sources, not pages
            continue
        h = open(f).read()
        t = html.unescape(re.search(r"<title>(.*?)</title>", h).group(1))
        d = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', h).group(1))
        long_t += len(t) > 66
        short_d += not 120 <= len(d) <= 160 and "noindex" not in h
        dashes += bool(re.search(r"\||[–—]|(?<!\w)-|-(?!\w)", t + " " + d)) and "noindex" not in h  # hyphens inside words are fine
    print("SEO: titles over 66: %d, descriptions outside 120 to 160: %d, with dashes or pipes: %d" % (long_t, short_d, dashes))


def main():
    offline = "--offline" in sys.argv
    os.makedirs(IMAGES, exist_ok=True); os.makedirs(TRANSCRIPTS, exist_ok=True)
    store = os.path.join(DATA, "episodes.json")
    saved = json.load(open(store)) if os.path.exists(store) else {"show": {}, "episodes": []}
    show, eps = saved["show"], saved["episodes"]
    if not offline:
        # Spotify's CDN can hand out a stale copy; ask for a fresh one every time.
        show, fresh = parse_feed(get("%s?fresh=%d" % (FEED, int(time.time()))))
        # Fail loudly (a red X and an email) rather than publish a broken feed.
        if not show["title"] or not fresh or len(fresh) < 0.9 * len(eps):
            sys.exit("Feed looks wrong: %d episodes in the feed, %d already on the site. Nothing published." % (len(fresh), len(eps)))
        eps = merge(eps, fresh)
        link_youtube(eps)
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
