"""BMKG CAP warnings with official geometry and explicit validity windows."""
import copy
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit

from news import NewsResults

CAP = {"c": "urn:oasis:names:tc:emergency:cap:1.2"}


def dt(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def active_warning(record, now=None):
    expires = dt(record.get("expires"))
    return bool(expires and expires > (now or datetime.now(timezone.utc)))


def parse_cap(xml, url, now=None):
    now = now or datetime.now(timezone.utc)
    root = ET.fromstring(xml)
    if root.tag != "{" + CAP["c"] + "}alert":
        raise ValueError("Expected a CAP 1.2 alert")
    text = lambda name: (root.findtext("c:" + name, default="", namespaces=CAP) or "").strip()
    refs = [r.split(",")[1] for r in text("references").split() if len(r.split(",")) >= 2]
    identifier, sent = text("identifier"), text("sent")
    if text("status") != "Actual" or text("scope") != "Public":
        return [], []
    if text("msgType") == "Cancel":
        return [], refs
    if text("msgType") not in ("Alert", "Update"):
        return [], []
    if not identifier or not dt(sent) or dt(sent) > now:
        raise ValueError("Missing or invalid CAP identifier/sent timestamp")
    infos = root.findall("c:info", CAP)
    info = next((i for i in infos if (i.findtext("c:language", default="", namespaces=CAP)).startswith("id")), infos[0] if infos else None)
    if info is None:
        raise ValueError("CAP alert has no info section")
    field = lambda name: (info.findtext("c:" + name, default="", namespaces=CAP) or "").strip()
    effective, expires = field("effective") or sent, field("expires")
    if not dt(effective) or not dt(expires) or dt(expires) <= dt(effective):
        raise ValueError("CAP warning has no valid effective/expiry window")
    if dt(expires) <= now:
        return [], refs
    rings = []
    for polygon in info.findall("c:area/c:polygon", CAP):
        ring = []
        for pair in (polygon.text or "").split():
            lat, lng = map(float, pair.split(","))
            if not -12 <= lat <= 7 or not 94 <= lng <= 142:
                raise ValueError("CAP polygon is outside Indonesia bounds")
            ring.append([lng, lat])  # CAP is lat,lon; GeoJSON is lon,lat.
        if len({tuple(p) for p in ring}) < 3:
            raise ValueError("CAP polygon has fewer than three unique vertices")
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        rings.append(ring)
    if not rings:
        raise ValueError("CAP warning has no mappable polygon")
    points = [point for ring in rings for point in ring]
    record = {"id": "bmkg-cap:" + identifier, "cap_identifier": identifier,
        "name": field("headline") or field("event"), "hazard": "weather",
        "assessment": "official_warning", "status": "active",
        "lat": sum(p[1] for p in points)/len(points), "lng": sum(p[0] for p in points)/len(points),
        "started": effective, "effective": effective, "expires": expires,
        "updated": sent, "issued_at": sent, "severity": field("severity"),
        "certainty": field("certainty"), "urgency": field("urgency"),
        "source": "BMKG CAP", "source_url": url, "attribution": "BMKG (CAP nowcast)",
        "polygons": rings,
        "notes": [field("description"), "Official weather warning area; this does not confirm a delivery disruption or justify an automatic SLA extension."],
    }
    return [record], refs


class CAPResults(NewsResults):
    def __init__(self, records=(), warnings=(), superseded=()):
        super().__init__(records, warnings)
        self.superseded = set(superseded)


def fetch_cap(http, settings, now=None):
    now = now or datetime.now(timezone.utc)
    client = copy.copy(http)
    client.timeout, client.retries = float(settings.get("timeout_seconds", 5)), 1
    deadline = time.monotonic() + float(settings.get("fetch_budget_seconds", 30))
    root = ET.fromstring(client.get(settings["url"]))
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("BMKG weather index is not an RSS channel")
    urls, warnings = [], []
    for item in root.findall("channel/item"):
        url = (item.findtext("link") or "").strip()
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in ("www.bmkg.go.id", "bmkg.go.id") \
                or not parsed.path.startswith("/alerts/nowcast/id/") or not parsed.path.endswith("_alert.xml"):
            warnings.append("Skipped an unexpected CAP detail URL")
            continue
        if url not in urls:
            urls.append(url)
    limit = int(settings.get("max_alerts", 40))
    if len(urls) > limit:
        warnings.append("CAP detail limit reached; coverage is incomplete")
    def retrieve(url):
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError("CAP request budget reached")
            return parse_cap(client.get(url), url, now), None
        except Exception as exc:
            return None, f"{url}: {exc}"
    records, removed, successes = [], set(), 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result, error in pool.map(retrieve, urls[:limit]):
            if error:
                warnings.append(error)
            else:
                successes += 1
                events, refs = result
                records.extend(events)
                removed.update(refs)
    if urls and not successes:
        raise RuntimeError("All BMKG CAP detail requests failed: " + "; ".join(warnings)[:350])
    records = [r for r in records if r["cap_identifier"] not in removed]
    return CAPResults(records, warnings, removed)
