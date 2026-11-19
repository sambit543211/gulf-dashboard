#!/usr/bin/env python3
"""Turn one Adfactors "Gulf Oil Lubricants India News" Outlook .msg into an edition JSON.

Usage:
    python3 ingest_msg.py <email.msg> <out_dir> [--no-fetch]

Writes <out_dir>/<YYYY-MM-DD>.json and, unless --no-fetch, downloads each print
story's newspaper clipping into <out_dir>/clips/<YYYY-MM-DD>/ (resized JPEG).

Prototype ingester: it reads the plain-text body of the email, groups stories under
their section headings, unwraps Outlook SafeLinks, and for Media-Tracker links
(mtrack.merryspiders.com) fetches the detail page for the full article text and
clipping image(s).
"""
import html
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import olefile

MONTHS = {m: i for i, m in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
UA = {"User-Agent": "Mozilla/5.0 (news-bulletin-ingest)"}


def read_msg(path):
    ole = olefile.OleFileIO(path)

    def prop(tag):
        name = f"__substg1.0_{tag}"
        if ole.exists(name):
            return ole.openstream(name).read().decode("utf-16-le", "ignore")
        return ""

    return {"subject": prop("0037001F"), "body": prop("1000001F")}


TRACKING = ("utm_", "rcm", "bqrl", "igsh", "fbclid")


def unwrap(url):
    if "safelinks.protection.outlook.com" in url:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        url = q.get("url", [url])[0]
    # Drop share/tracking parameters (some carry the sharer's identity).
    parts = urllib.parse.urlsplit(url)
    keep = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query)
            if not k.startswith(TRACKING)]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(keep)))


def parse_meta(line):
    """'Thursday, 19 November 2026, Newspaper: X, Edition: Y, ..., Page: 4,' -> dict"""
    meta = {}
    m = re.match(r"\s*\w+day,\s*(\d{1,2})\s+(\w+)\s+(\d{4})\s*,?(.*)", line)
    if not m:
        return None
    d, mon, y, rest = m.groups()
    meta["date"] = f"{y}-{MONTHS.get(mon, 1):02d}-{int(d):02d}"
    keys = ["Newspaper", "Publication", "Edition", "Language", "Source",
            "Page", "Social Media Platform"]
    pattern = r"(" + "|".join(re.escape(k) for k in keys) + r")\s*:"
    parts = re.split(pattern, rest)
    for i in range(1, len(parts) - 1, 2):
        val = parts[i + 1].strip().strip(",").strip()
        meta[parts[i]] = val
    return meta


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def parse_body(body, subject):
    body = body.replace("\r\n", "\n").replace("\t", "")
    blocks = body.split("________________________________")
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})", subject)
    edition_date = f"{m.group(3)}-{MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}" if m else None

    section = "Gulf in the News"
    stories = []
    for block in blocks:
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        # A section heading is a short line that sits before a headline<link> line.
        head_idx = next((i for i, l in enumerate(lines) if re.search(r"<https?://[^>]+>\s*$", l)), None)
        if head_idx is None:
            continue
        for l in lines[:head_idx]:
            if len(l) < 40 and not l.startswith(("Please", "Good", "CAUTION", "Gulf Oil")) \
                    and not re.search(r"\d{4}", l):
                section = l.strip()
        hl = re.match(r"(.*?)\s*<(https?://[^>]+)>\s*$", lines[head_idx])
        title, url = hl.group(1).strip(), unwrap(hl.group(2))
        meta, summary = {}, ""
        for l in lines[head_idx + 1:]:
            if not meta:
                pm = parse_meta(l)
                if pm:
                    meta = pm
                    continue
            if l.startswith("Summary"):
                summary = l.split(":", 1)[1].strip()
            elif summary and not l.startswith(("Regards", "Saniya", "Adfactors")):
                summary += " " + l
        if "Regards" in summary:
            summary = summary.split("Regards")[0].strip()
        outlet = meta.get("Newspaper") or meta.get("Publication") or ""
        platform = meta.get("Social Media Platform")
        kind = "social" if platform else ("print" if meta.get("Newspaper") else "online")
        stories.append({
            "id": f"{len(stories)+1:02d}-{slug(title)}",
            "section": section,
            "title": title,
            "summary": re.sub(r"\.{3,}$", "…", summary.strip()),
            "url": url,
            "date": meta.get("date"),
            "outlet": outlet,
            "kind": kind,
            "platform": platform,
            "edition": meta.get("Edition"),
            "language": meta.get("Language"),
            "author": (meta.get("Source") or None),
            "page": meta.get("Page") or None,
        })
    return edition_date, stories


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def enrich_mtrack(story, clip_dir, edition_date):
    page = fetch(story["url"]).decode("utf-8", "ignore")
    m = re.search(r'TabPanel2_lblNews">(.*?)</span>', page, re.S)
    if m:
        text = re.sub(r"<br\s*/?>", "\n", m.group(1))
        text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
        story["fullText"] = re.sub(r"\n{3,}", "\n\n", text)
    for key, field in (("lblPublicationType", "outletType"), ("lblHt", "heightCm")):
        mm = re.search(rf'{key}">([^<]*)<', page)
        if mm and mm.group(1).strip():
            story[field] = html.unescape(mm.group(1).strip())
    imgs = re.findall(r'TabPanel1_Image\d+"\s+src="([^"]+)"', page)
    story["clips"] = []
    for i, src in enumerate(imgs):
        try:
            name = f"{story['id'][:40]}-{i+1}.jpg"
            im = save_image(fetch(src.replace(" ", "%20")), clip_dir, name)
            story["clips"].append({"src": f"clips/{edition_date}/{name}",
                                   "thumb": f"clips/{edition_date}/{name[:-4]}-t.jpg",
                                   "w": im.width, "h": im.height, "origin": src})
        except Exception as e:  # keep going; a missing clipping is not fatal
            print("  clip failed:", e, file=sys.stderr)


def save_image(data, clip_dir, name, max_w=900):
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)))
    clip_dir.mkdir(parents=True, exist_ok=True)
    im.save(clip_dir / name, "JPEG", quality=72, optimize=True)
    # Small card thumbnail; cards crop it to the top, where the headline sits.
    tw = 360
    th = im.resize((tw, round(im.height * tw / im.width)))
    th.save(clip_dir / name.replace(".jpg", "-t.jpg"), "JPEG", quality=68, optimize=True)
    return im


def enrich_web(story, clip_dir, edition_date):
    """Online/social stories: take the page's og:image as the story visual."""
    page = fetch(story["url"]).decode("utf-8", "ignore")
    m = re.search(r'<meta[^>]+(?:property|name)="og:image"[^>]+content="([^"]+)"', page) or \
        re.search(r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"', page)
    if not m:
        return
    src = html.unescape(m.group(1))
    name = f"{story['id'][:40]}-og.jpg"
    im = save_image(fetch(src), clip_dir, name)
    story["image"] = {"src": f"clips/{edition_date}/{name}",
                      "thumb": f"clips/{edition_date}/{name[:-4]}-t.jpg",
                      "w": im.width, "h": im.height}


def main():
    msg, out = Path(sys.argv[1]), Path(sys.argv[2])
    do_fetch = "--no-fetch" not in sys.argv
    data = read_msg(msg)
    edition_date, stories = parse_body(data["body"], data["subject"])
    clip_dir = out / "clips" / edition_date
    if do_fetch:
        for s in stories:
            print("fetching", s["id"], file=sys.stderr)
            if "mtrack.merryspiders.com" in s["url"]:
                fn = enrich_mtrack
            else:
                fn = enrich_web
            try:
                fn(s, clip_dir, edition_date)
            except Exception as e:
                print("  failed:", s["id"], e, file=sys.stderr)
    edition = {
        "date": edition_date,
        "subject": data["subject"],
        "ingestedAt": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="minutes"),
        "sections": list(dict.fromkeys(s["section"] for s in stories)),
        "stories": stories,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{edition_date}.json").write_text(json.dumps(edition, ensure_ascii=False, indent=1))
    # Keep a newest-first index of every edition in the folder.
    index = []
    for f in sorted(out.glob("????-??-??.json"), reverse=True):
        e = json.loads(f.read_text())
        index.append({"date": e["date"], "stories": len(e["stories"]),
                      "updatedAt": e["ingestedAt"],
                      "gulf": sum(1 for s in e["stories"] if s["section"] == "Gulf in the News")})
    (out / "index.json").write_text(json.dumps({"editions": index}, indent=1))
    print(f"{edition_date}: {len(stories)} stories in {len(edition['sections'])} sections")


if __name__ == "__main__":
    main()
