"""BNPB news discovery. A published bulletin is not a verified impact geometry."""

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

WIB = timezone(timedelta(hours=7))
MONTHS = dict(zip(
    "jan feb mar apr mei jun jul agu sep okt nov des".split(), range(1, 13)))
HAZARDS = (
    ("flood", r"banjir|tanggul"),
    ("volcano", r"erupsi|gunung api|vulkan|lahar"),
    ("earthquake", r"gempa|tsunami"),
    ("wildfire", r"karhutla|kebakaran"),
    ("weather", r"cuaca ekstrem|angin kencang|kekeringan"),
    ("other", r"longsor|jembatan|akses jalan"),
)


class NewsResults(list):
    def __init__(self, records=(), warnings=()):
        super().__init__(records)
        self.warnings = list(warnings)


class BulletinHTML(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.text = []
        self.headline = []
        self._anchor = None
        self._skip = 0
        self._h1 = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "a":
            self._anchor = [attrs.get("href", ""), []]
        if tag == "h1":
            self._h1 = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        if tag == "a" and self._anchor is not None:
            href, words = self._anchor
            self.links.append((href, " ".join(" ".join(words).split())))
            self._anchor = None
        if tag == "h1":
            self._h1 = False

    def handle_data(self, data):
        if self._skip:
            return
        self.text.append(data)
        if self._anchor is not None:
            self._anchor[1].append(data)
        if self._h1:
            self.headline.append(data)


def bulletin_url(href, base):
    """Only follow BNPB article links; strip query/fragment duplicate variants."""
    parsed = urlsplit(urljoin(base, href))
    if parsed.scheme not in ("http", "https") or parsed.hostname not in (
            "bnpb.go.id", "www.bnpb.go.id"):
        return None
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    if not re.match(r"^/(?:index.php/)?berita/[^/]+$", path):
        return None
    return urlunsplit(("https", "www.bnpb.go.id", path.replace("/index.php/", "/"), "", ""))


def publication_time(text):
    # BNPB's visible timestamp, e.g. "05 Nov 2026 11:11 WIB".
    match = re.search(
        r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s+(\d{2}):(\d{2})\s+WIB\b",
        text)
    if not match:
        return None
    day, month, year, hour, minute = match.groups()
    month = MONTHS.get(month.lower()[:3])
    if month is None:
        return None
    try:
        return datetime(int(year), month, int(day), int(hour), int(minute), tzinfo=WIB)
    except ValueError:
        return None


def fetch_bnpb_bulletins(http, settings, now=None):
    deadline = time.monotonic() + float(settings.get("fetch_budget_seconds", 30))
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=float(settings.get("lookback_hours", 72)))
    base = settings["url"]
    index = BulletinHTML(http.get(base))
    candidates = {}
    for href, title in index.links:
        url = bulletin_url(href, base)
        relevant = any(re.search(pattern, title, re.I) for _, pattern in HAZARDS)
        relevant = relevant or "perkembangan situasi dan penanganan bencana" in title.lower()
        if url and title and relevant:
            candidates.setdefault(url, title)
    if not candidates:
        raise ValueError("BNPB index has no recognizable bulletin links; check its HTML layout")

    records, failures, dated = [], [], 0
    for url, title in list(candidates.items())[:int(settings.get("max_articles", 8))]:
        if time.monotonic() >= deadline:
            failures.append("BNPB news request budget reached; remaining articles deferred")
            break
        try:
            article = BulletinHTML(http.get(url))
            published = publication_time(" ".join(article.text))
            if published is None:
                raise ValueError("publication timestamp missing")
            dated += 1
            if not cutoff <= published <= now:
                continue
            title = " ".join(" ".join(article.headline).split()) or title
            hazard = next((hazard for hazard, pattern in HAZARDS
                           if re.search(pattern, title, re.I)), "other")
            records.append(dict(
                id="bnpb-news:" + hashlib.sha256(url.encode()).hexdigest()[:20],
                name=title[:240], hazard=hazard, status="unverified",
                lat=None, lng=None, started=None,
                published_at=published.isoformat(), updated=published.isoformat(),
                source="BNPB bulletin", source_url=url, attribution="BNPB",
                notes=["Official BNPB bulletin. Publication time is not the incident start time.",
                       "Location and operational impact require review. No impact zone or SLA change is inferred from this report."],
            ))
        except Exception as exc:
            failures.append(f"{url}: {exc}")
    # Report partial failures too; a changed layout must never look like a healthy empty feed.
    if failures and not dated:
        raise RuntimeError("BNPB bulletin retrieval incomplete: " + "; ".join(failures)[:500])
    if not dated:
        raise ValueError("BNPB bulletins have no usable publication timestamps")
    return NewsResults(records, failures)
