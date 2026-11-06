"""Match administrative names and centers to licensed boundary geometry."""
import gzip
import json
import math
import re
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path


def key(name):
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())
    return re.sub(r"^(?:kabupaten|kecamatan|kab|kec|distrik)\s+", "", text)


def in_ring(lat, lng, ring):
    inside = False
    for first, second in zip(ring, ring[1:] + ring[:1]):
        x1, y1 = first[:2]
        x2, y2 = second[:2]
        if (y1 > lat) != (y2 > lat) and lng < x1 + (lat-y1)*(x2-x1)/(y2-y1):
            inside = not inside
    return inside


def contains(geometry, lat, lng):
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    return any(rings and in_ring(lat, lng, rings[0]) and
               not any(in_ring(lat, lng, hole) for hole in rings[1:]) for rings in polygons)


@lru_cache(maxsize=1)
def dataset():
    path = Path(__file__).with_name("admin_boundaries.json.gz")
    if not path.exists():
        return {}, {}
    data = json.loads(gzip.decompress(path.read_bytes()))
    index = defaultdict(list)
    for row in data["areas"]:
        index[(row[1], key(row[0]))].append(row)
    return index, data["metadata"]


@lru_cache(maxsize=2048)
def _match(level, name, lat, lng):
    index, metadata = dataset()
    candidates = index.get((level, key(name)), [])
    enclosed = [r for r in candidates if r[3][0] <= lng <= r[3][2]
                and r[3][1] <= lat <= r[3][3] and contains(r[4], lat, lng)]
    method = "name and center inside boundary"
    if len(enclosed) == 1:
        row = enclosed[0]
    else:
        ranked = sorted(candidates, key=lambda r: (
            ((r[3][0]+r[3][2])/2-lng)*math.cos(math.radians(lat)))**2 +
            ((r[3][1]+r[3][3])/2-lat)**2)
        if len(ranked) != 1:
            return None
        row = ranked[0]
        dx = max(row[3][0]-lng, 0, lng-row[3][2]) * math.cos(math.radians(lat)) * 111
        dy = max(row[3][1]-lat, 0, lat-row[3][3]) * 111
        if math.hypot(dx, dy) > (25 if level == "ADM3" else 100):
            return None
        method = "unique name and nearby center; review alignment"
    meta = metadata[level]
    return {"id": row[2], "name": row[0], "level": level, "geometry": row[4],
            "year": meta["year"], "source": meta["source"], "license": meta["license"],
            "url": meta["url"], "match": method}


def match_boundary(place):
    if place.get("level") not in ("ADM2", "ADM3"):
        return None
    return _match(place["level"], place["name"], round(place["lat"], 5), round(place["lng"], 5))


def attach_boundaries(estimate):
    for point in (estimate or {}).get("points", []):
        boundary = match_boundary(point)
        if boundary:
            point["boundary"] = boundary
    return estimate
