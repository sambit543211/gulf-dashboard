"""Read one Adfactors "Gulf Oil Lubricants India News" Outlook .msg into a draft edition.

parse_msg(bytes)        -> {date, subject, sections, stories}
enrich(stories)         -> fetches full text + clipping image URLs (print) or og:image (online/social)

Images are not copied: the edition stores the source image URLs (media-tracker clippings
live on a public S3 bucket) and the site serves them through Vercel image optimisation.
"""
import html
import io
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import olefile

MONTHS = {m: i for i, m in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
UA = {"User-Agent": "Mozilla/5.0 (GulfPulse news-bulletin ingest)"}
TRACKING = ("utm_", "rcm", "bqrl", "igsh", "fbclid")
GULF_SECTION = "Gulf in the News"


def _prop(ole, tag):
    name = f"__substg1.0_{tag}"
    if ole.exists(name):
        return ole.openstream(name).read().decode("utf-16-le", "ignore")
    return ""


def unwrap(url):
    if "safelinks.protection.outlook.com" in url:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        url = q.get("url", [url])[0]
    parts = urllib.parse.urlsplit(url)
    keep = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if not k.startswith(TRACKING)]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(keep)))


def _meta(line):
    m = re.match(r"\s*\w+day,\s*(\d{1,2})\s+(\w+)\s+(\d{4})\s*,?(.*)", line)
    if not m:
        return None
    d, mon, y, rest = m.groups()
    meta = {"date": f"{y}-{MONTHS.get(mon, 1):02d}-{int(d):02d}"}
    keys = ["Newspaper", "Publication", "Edition", "Language", "Source", "Page", "Social Media Platform"]
    parts = re.split(r"(" + "|".join(re.escape(k) for k in keys) + r")\s*:", rest)
    for i in range(1, len(parts) - 1, 2):
        meta[parts[i]] = parts[i + 1].strip().strip(",").strip()
    return meta


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48]


def parse_msg(data: bytes):
    ole = olefile.OleFileIO(io.BytesIO(data))
    subject, body = _prop(ole, "0037001F"), _prop(ole, "1000001F")
    if not body:
        raise ValueError("This file has no plain-text body. Upload the tracker email saved as .msg from Outlook.")
    body = body.replace("\r\n", "\n").replace("\t", "")
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})", subject)
    date = f"{m.group(3)}-{MONTHS.get(m.group(2), 1):02d}-{int(m.group(1)):02d}" if m and m.group(2) in MONTHS else None

    section, stories = GULF_SECTION, []
    for block in body.split("________________________________"):
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        head = next((i for i, l in enumerate(lines) if re.search(r"<https?://[^>]+>\s*$", l)), None)
        if head is None:
            continue
        for l in lines[:head]:
            if len(l) < 40 and not l.startswith(("Please", "Good", "CAUTION", "Gulf Oil")) and not re.search(r"\d{4}", l):
                section = l.strip()
        hl = re.match(r"(.*?)\s*<(https?://[^>]+)>\s*$", lines[head])
        title, url = hl.group(1).strip(), unwrap(hl.group(2))
        meta, summary = {}, ""
        for l in lines[head + 1:]:
            if not meta:
                pm = _meta(l)
                if pm:
                    meta = pm
                    continue
            if l.startswith("Summary"):
                summary = l.split(":", 1)[1].strip()
            elif summary and not l.startswith(("Regards", "Saniya", "Adfactors")):
                summary += " " + l
        summary = summary.split("Regards")[0].strip()
        platform = meta.get("Social Media Platform")
        stories.append({
            "id": f"{len(stories) + 1:02d}-{_slug(title)}",
            "key": url,
            "section": section,
            "title": title,
            "summary": re.sub(r"\.{3,}$", "…", summary),
            "url": url,
            "date": meta.get("date") or date,
            "outlet": meta.get("Newspaper") or meta.get("Publication") or "",
            "kind": "social" if platform else ("print" if meta.get("Newspaper") else "online"),
            "platform": platform,
            "edition": meta.get("Edition") or None,
            "language": meta.get("Language") or None,
            "author": meta.get("Source") or None,
            "page": (meta.get("Page") or "").strip(", ") or None,
        })
    if not stories:
        raise ValueError("No stories found. Is this the Adfactors news tracker email?")
    date = date or stories[0]["date"]
    return {"date": date, "subject": subject, "sections": list(dict.fromkeys(s["section"] for s in stories)),
            "stories": _dedupe(stories)}


def _dedupe(stories):
    """The same story often arrives twice (article + its LinkedIn post): keep one, list the other."""
    out, seen = [], {}
    for s in stories:
        k = (s["section"], re.sub(r"\W+", "", s["title"].lower()))
        if k in seen:
            seen[k].setdefault("alsoOn", []).append(
                {"label": s["platform"] or s["outlet"], "url": s["url"], "kind": s["kind"]})
        else:
            seen[k] = s
            out.append(s)
    return out


def _fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _enrich_one(s):
    try:
        page = _fetch(s["url"])
    except Exception as e:  # a story without extras is still publishable
        s["fetchError"] = str(e)[:120]
        return s
    if "mtrack.merryspiders.com" in s["url"]:
        m = re.search(r'TabPanel2_lblNews">(.*?)</span>', page, re.S)
        if m:
            text = re.sub(r"<br\s*/?>", "\n", m.group(1))
            s["fullText"] = re.sub(r"\n{3,}", "\n\n", html.unescape(re.sub(r"<[^>]+>", "", text)).strip())
        mm = re.search(r'lblPublicationType">([^<]*)<', page)
        if mm and mm.group(1).strip():
            s["outletType"] = html.unescape(mm.group(1).strip())
        s["clips"] = [{"src": src.replace(" ", "%20")}
                      for src in re.findall(r'TabPanel1_Image\d+"\s+src="([^"]+)"', page)]
    else:
        m = re.search(r'<meta[^>]+(?:property|name)="og:image"[^>]+content="([^"]+)"', page) or \
            re.search(r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"', page)
        if m:
            s["image"] = {"src": html.unescape(m.group(1))}
    return s


def enrich(stories, workers=8):
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(_enrich_one, stories))
