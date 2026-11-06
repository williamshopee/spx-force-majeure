"""Explainable news location screening; no physical hazard forecast or SLA model."""
import json
import re
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path


def normalize(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def short_name(name):
    return re.sub(r"^(?:kabupaten|kecamatan|kota|provinsi|kab|kec)\s+", "", normalize(name))


@lru_cache(maxsize=1)
def load_places():
    path = Path(__file__).with_name("news_places.json")
    return json.loads(path.read_text(encoding="utf-8"))["places"] if path.exists() else []


class PlaceMatcher:
    def __init__(self, places):
        self.aliases = defaultdict(list)
        for place in places:
            for alias in set([short_name(place[1])] + [short_name(a) for a in place[7]]):
                if len(alias) >= 4:
                    self.aliases[alias].append(place)
        alternatives = sorted(self.aliases, key=lambda name: (-len(name), name))
        self.pattern = re.compile(r"\b(?:" + "|".join(re.escape(a) for a in alternatives) + r")\b") if alternatives else None

    def infer(self, title, paragraphs):
        points, unresolved = {}, set()
        province = regency = None
        active_context = 0
        source_kind = "article body" if paragraphs else "headline only"
        for paragraph in paragraphs or [title]:
            # A news agency's dateline is not the incident location.
            clean = re.sub(r"^\s*[A-Z .]{3,}\s*[–—-]\s*", "", paragraph)
            text = normalize(clean)
            if not self.pattern:
                break
            # Avoid mapping the location of a briefing/office/aircraft departure.
            if re.search(r"\b(rapat|seminar|konferensi|melepas armada|bandara halim|kantor pusat)\b", text):
                continue
            hazard = bool(re.search(r"\b(banjir|longsor|karhutla|kebakaran|kekeringan|erupsi|gempa|tsunami|angin kencang|cuaca ekstrem|jalan putus|akses terputus)\b", text))
            if hazard:
                active_context = 4
            else:
                active_context -= 1
            if active_context <= 0:
                continue
            matches = [(m, self.aliases[m.group()]) for m in self.pattern.finditer(text)]
            explicit_provinces = {p[3] for _, ps in matches for p in ps if p[2] == "ADM1"}
            if len(explicit_provinces) == 1:
                new_province = next(iter(explicit_provinces))
                if province != new_province:
                    regency = None
                province = new_province
            elif len(explicit_provinces) > 1:
                province = regency = None
            explicit_regencies = [p for m, ps in matches for p in ps if p[2] == "ADM2"
                                  and re.search(r"(?:kabupaten|kab|kota) $", text[:m.start()])
                                  and normalize(p[1]).startswith("kota ") == bool(
                                      re.search(r"kota $", text[:m.start()]))]
            if len({p[0] for p in explicit_regencies}) == 1:
                regency = explicit_regencies[0]
                province = regency[3]
            for match, candidates in matches:
                prefix = text[:match.start()]
                wanted = "ADM3" if re.search(r"(?:kecamatan|kec) $", prefix) else \
                         "ADM2" if re.search(r"(?:kabupaten|kab|kota) $", prefix) else \
                         "ADM1" if re.search(r"provinsi $", prefix) else None
                choices = [p for p in candidates if (not wanted or p[2] == wanted)
                           and (not province or p[3] == province)]
                if wanted == "ADM2":
                    is_city = bool(re.search(r"kota $", prefix))
                    choices = [p for p in choices if normalize(p[1]).startswith("kota ") == is_city]
                if regency:
                    choices = [p for p in choices if p[2] != "ADM3" or not p[4] or p[4] == regency[4]]
                # Bare common district names are too easy to confuse with ordinary words.
                if not wanted and paragraphs:
                    choices = [p for p in choices if p[2] != "ADM3"]
                unique = {p[0]: p for p in choices}
                if len(unique) != 1:
                    if choices or wanted:
                        unresolved.add(match.group())
                    continue
                place = next(iter(unique.values()))
                broad = place[2] == "ADM1"
                points.setdefault(place[0], {
                    "id": str(place[0]), "name": place[1], "level": place[2],
                    "lat": place[5], "lng": place[6], "province_code": place[3],
                    "regency_context": str(regency[0]) if regency else None,
                    "screenable": not broad,
                    "confidence": "broad province context" if broad else
                                  "explicit administrative name" if wanted else "unique name match",
                    "evidence": clean[:220], "evidence_kind": source_kind,
                    "geocoder": "GeoNames administrative center (CC BY 4.0)",
                })
        values = list(points.values())
        # Prefer districts mentioned in an incident section to their parent center.
        district_parents = {p["regency_context"] for p in values if p["level"] == "ADM3"}
        local_provinces = {p["province_code"] for p in values if p["screenable"]}
        values = [p for p in values if p["id"] not in district_parents
                  and not (p["level"] == "ADM1" and p["province_code"] in local_provinces)]
        return {
            "method": "news place-name screening v1", "points": values[:20],
            "unresolved_names": sorted(unresolved), "default_radius_km": 10,
            "limitations": "Centers and user-adjustable proximity zones, not predicted hazard boundaries. Location-match confidence is not an impact probability. No SLA change is inferred.",
            "attribution": "GeoNames CC BY 4.0 — https://www.geonames.org/",
        }


@lru_cache(maxsize=1)
def matcher():
    return PlaceMatcher(load_places())


def estimate_news_locations(title, paragraphs=None):
    return matcher().infer(title, paragraphs or [])
