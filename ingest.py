#!/usr/bin/env python3
"""
SPX force-majeure watch — ingestion pipeline.

Pulls active hazard events in Indonesia from official feeds, works out which
SPX facilities each one reaches, and writes a single events.json that the
station map reads.

    python ingest.py                    # normal run
    python ingest.py --dry-run          # fetch + print, write nothing
    python ingest.py --fixtures DIR     # read saved payloads instead of HTTP
    python ingest.py --save-fixtures DIR

Design rules, because they matter more than the code:

  1. Official feeds are facts. News is a lead. Anything sourced from news
     lands as status="unverified" and is kept out of SLA totals until a
     human promotes it.
  2. One failing source never fails the run. Degraded output beats no output.
  3. Never overwrite a good events.json with a broken one. The write is
     atomic and gated on a sanity check.
  4. Every number a human might act on carries its provenance.

Data sources and their licences are documented in README.md. BMKG requires
visible attribution wherever its data is displayed.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
LOG = logging.getLogger("fm")

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    """Read config.yaml. Falls back to a tiny parser if PyYAML is absent."""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        LOG.warning("PyYAML not installed; using the built-in minimal parser. "
                    "pip install pyyaml for full support.")
        return _mini_yaml(text)


def _mini_yaml(text: str) -> dict:
    """
    Enough YAML for this one config file: nested maps, lists, inline
    {k: v} maps, scalars. Not a general parser — install PyYAML in prod.
    """
    def scalar(v: str):
        v = v.strip()
        if not v:
            return None
        if v.startswith(("'", '"')) and v[-1] == v[0]:
            return v[1:-1]
        if v.startswith("[") and v.endswith("]"):
            return [scalar(x) for x in _split_top(v[1:-1])] if v[1:-1].strip() else []
        if v.startswith("{") and v.endswith("}"):
            out = {}
            for part in _split_top(v[1:-1]):
                if ":" in part:
                    k, _, rest = part.partition(":")
                    out[k.strip()] = scalar(rest)
            return out
        low = v.lower()
        if low in ("true", "false"):
            return low == "true"
        if low in ("null", "~", "none"):
            return None
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            pass
        return v

    root: dict = {}
    # stack of (indent, container)
    stack: list[tuple[int, Any]] = [(-1, root)]
    pending_key: list[tuple[int, dict, str]] = []

    for raw in text.splitlines():
        line = raw.split("#")[0].rstrip() if not _in_quotes_hash(raw) else raw.rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if body.startswith("- "):
            item = body[2:].strip()
            if not isinstance(parent, list):
                # promote the last pending key to a list
                if pending_key and pending_key[-1][0] < indent:
                    _, holder, key = pending_key[-1]
                    parent = holder[key] = []
                    stack.append((indent - 1, parent))
                else:
                    continue
            parent.append(scalar(item))
            continue

        if ":" in body:
            key, _, rest = body.partition(":")
            key, rest = key.strip(), rest.strip()
            if rest:
                if isinstance(parent, dict):
                    parent[key] = scalar(rest)
            else:
                child: dict = {}
                if isinstance(parent, dict):
                    parent[key] = child
                    pending_key.append((indent, parent, key))
                    stack.append((indent, child))
    return root


def _split_top(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def _in_quotes_hash(line: str) -> bool:
    q = 0
    for ch in line:
        if ch in "'\"":
            q ^= 1
        if ch == "#" and q:
            return True
    return False


# --------------------------------------------------------------------------
# geodesy
# --------------------------------------------------------------------------

R_KM = 6371.0088


def haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_KM * math.asin(math.sqrt(a))


def bearing(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def point_in_ring(lat, lon, ring: list[list[float]]) -> bool:
    """Ray casting. ring is [[lon,lat], ...] as GeoJSON gives it."""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            xint = x1 + (lat - y1) * (x2 - x1) / (y2 - y1 or 1e-12)
            if lon < xint:
                inside = not inside
    return inside


def in_polygons(lat, lon, polys: list[list[list[float]]]) -> bool:
    return any(point_in_ring(lat, lon, r) for r in polys)


def in_corridor(brg: float, corridors: list[list[float]]) -> bool:
    if not corridors:
        return True
    for a1, a2 in corridors:
        if a1 <= a2:
            if a1 <= brg <= a2:
                return True
        elif brg >= a1 or brg <= a2:   # wraps through north
            return True
    return False


# --------------------------------------------------------------------------
# http
# --------------------------------------------------------------------------

class Http:
    def __init__(self, cfg: dict, fixtures: Path | None = None,
                 save_fixtures: Path | None = None):
        h = cfg.get("http") or {}
        self.timeout = h.get("timeout_seconds", 25)
        self.retries = h.get("retries", 3)
        self.backoff = h.get("backoff_seconds", 4)
        self.ua = h.get("user_agent", "SPX-ForceMajeureWatch/1.0")
        self.fixtures = fixtures
        self.save_fixtures = save_fixtures

    @staticmethod
    def _slug(url: str) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "_", url)[:120] + ".txt"

    def get(self, url: str, params: dict | None = None) -> str:
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)

        if self.fixtures:
            p = self.fixtures / self._slug(url)
            if not p.exists():
                raise FileNotFoundError(f"no fixture for {url} (expected {p.name})")
            LOG.info("fixture %s", p.name)
            return p.read_text(encoding="utf-8")

        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": self.ua,
                    "Accept": "application/json, text/html;q=0.8, */*;q=0.5",
                })
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = r.read().decode("utf-8", errors="replace")
                if self.save_fixtures:
                    self.save_fixtures.mkdir(parents=True, exist_ok=True)
                    (self.save_fixtures / self._slug(url)).write_text(body, encoding="utf-8")
                return body
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
                last = e
                LOG.warning("GET %s failed (%d/%d): %s", url, attempt, self.retries, e)
                if attempt < self.retries:
                    time.sleep(self.backoff * attempt)
        raise RuntimeError(f"GET {url} failed after {self.retries} tries: {last}")

    def get_json(self, url: str, params: dict | None = None) -> Any:
        return json.loads(self.get(url, params))


# --------------------------------------------------------------------------
# normalized event
# --------------------------------------------------------------------------

HAZARD_FROM_GDACS = {
    "EQ": "earthquake", "TC": "cyclone", "FL": "flood",
    "VO": "volcano", "WF": "wildfire", "DR": "other",
}


def new_event(**kw) -> dict:
    """The one shape everything downstream understands."""
    ev = {
        "id": None,             # stable across runs: "<source>:<native id>"
        "hazard": "other",      # volcano|earthquake|flood|cyclone|wildfire|weather|unrest|other
        "name": "",
        "lat": None, "lng": None,
        "severity": None,       # human string, e.g. "M 5.4, depth 10 km"
        "severity_value": None, # numeric for sorting
        "alert": "unknown",     # green|orange|red|unknown, or Indonesian alert level
        "status": "active",     # active|unverified|stale
        "started": None,        # ISO8601
        "updated": None,
        "source": "",
        "source_url": "",
        "attribution": "",
        "notes": [],
        "polygons": [],         # [[ [lon,lat], ... ], ...] affected area, if known
        "impact": None,         # filled by apply_impact_model
        "observed": [],         # ground truth, e.g. BMKG felt-intensity list
    }
    ev.update(kw)
    return ev


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------

def parse_bmkg_coords(g: dict) -> tuple[float, float] | None:
    """
    BMKG gives Coordinates "-0.38,123.13" (lat,lon) and also Lintang/Bujur
    as "0.38 LS" / "123.13 BT". Prefer Coordinates; fall back to parsing the
    hemisphere strings, since older payloads have shown up missing one.
    """
    c = (g.get("Coordinates") or "").strip()
    if "," in c:
        try:
            lat, lon = (float(x) for x in c.split(",")[:2])
            return lat, lon
        except ValueError:
            pass
    lat = lon = None
    m = re.match(r"([\d.]+)\s*(LS|LU)", (g.get("Lintang") or "").strip())
    if m:
        lat = float(m.group(1)) * (-1 if m.group(2) == "LS" else 1)
    m = re.match(r"([\d.]+)\s*(BT|BB)", (g.get("Bujur") or "").strip())
    if m:
        lon = float(m.group(1)) * (-1 if m.group(2) == "BB" else 1)
    return (lat, lon) if lat is not None and lon is not None else None


def parse_felt(dirasakan: str) -> list[dict]:
    """
    'II - III Luwuk, II Banggai' -> [{place, mmi_text, mmi_max}]
    BMKG's observed shaking beats any attenuation model we could write.
    """
    out = []
    roman = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6,
             "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12}
    for chunk in re.split(r"[,;]", dirasakan or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^((?:[IVX]+)(?:\s*-\s*[IVX]+)?)\s+(.*)$", chunk)
        if not m:
            continue
        levels = [roman.get(x.strip().upper(), 0) for x in m.group(1).split("-")]
        out.append({"place": m.group(2).strip(),
                    "mmi_text": m.group(1).strip(),
                    "mmi_max": max(levels) if levels else None})
    return out


def load_airports(path: Path) -> list[dict]:
    """Scheduled-service airports, from OurAirports (public domain)."""
    import csv
    if not path.exists():
        LOG.warning("no airports.csv at %s; air-linehaul impact disabled", path)
        return []
    lines = [l for l in path.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.lstrip().startswith("#")]
    out = []
    for r in csv.DictReader(lines):
        try:
            out.append({"iata": r["iata"].strip().upper(),
                        "name": r["name"].strip(), "city": (r.get("city") or "").strip(),
                        "province": (r.get("province") or "").strip(),
                        "lat": float(r["latitude"]), "lng": float(r["longitude"]),
                        "tier": (r.get("tier") or "regional").strip()})
        except (KeyError, ValueError):
            continue
    LOG.info("airports: %d (%d hubs)", len(out),
             sum(1 for a in out if a["tier"] == "hub"))
    return out


def build_routing(facilities: list[tuple[str, float, float]], airports: list[dict],
                  cfg: dict) -> dict:
    """
    Assign every facility a gateway airport and a via-hub.

    THIS IS A PROXY, and the most load-bearing assumption in the whole file.
    Gateway = nearest scheduled airport. Via-hub = nearest hub-tier airport to
    that gateway. Real air freight does not always route through the nearest
    airport, so if you have lane data, lanes.csv overrides this entirely:

        station_name,gateway_iata,hub_iata

    Facilities further than gateway_max_km from any airport are treated as not
    air-served, because a 900 km road leg means the parcel is not flying.
    """
    al = (cfg.get("air_linehaul") or {})
    maxkm = float(al.get("gateway_max_km", 400))
    hubs = [a for a in airports if a["tier"] == "hub"] or airports
    gw: dict[str, dict] = {}

    # nearest hub for each airport, computed once
    hub_of = {}
    for a in airports:
        best, bd = None, 1e9
        for h in hubs:
            d = haversine(a["lat"], a["lng"], h["lat"], h["lng"])
            if d < bd:
                best, bd = h, d
        hub_of[a["iata"]] = (best["iata"] if best else None, round(bd, 1))

    for name, flat, flon in facilities:
        best, bd = None, 1e9
        for a in airports:
            d = haversine(flat, flon, a["lat"], a["lng"])
            if d < bd:
                best, bd = a, d
        if not best or bd > maxkm:
            continue
        h, hd = hub_of[best["iata"]]
        gw[name] = {"gateway": best["iata"], "gateway_km": round(bd, 1),
                    "hub": h, "hub_km": hd}

    # optional real lane data wins
    lanes = Path(cfg.get("lanes", "")) if cfg.get("lanes") else None
    if lanes and not lanes.is_absolute():
        lanes = HERE / cfg["lanes"]
    if lanes and lanes.exists():
        import csv
        applied = 0
        with lanes.open(newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                n = (r.get("station_name") or "").strip()
                if not n:
                    continue
                e = gw.setdefault(n, {"gateway_km": None, "hub_km": None})
                if r.get("gateway_iata"):
                    e["gateway"] = r["gateway_iata"].strip().upper()
                if r.get("hub_iata"):
                    e["hub"] = r["hub_iata"].strip().upper()
                applied += 1
        LOG.info("lanes.csv overrode routing for %d facilities", applied)

    LOG.info("routing: %d/%d facilities within %.0f km of an airport",
             len(gw), len(facilities), maxkm)
    return gw


def apply_air_linehaul(ev: dict, cfg: dict, airports: list[dict],
                       routing: dict) -> None:
    """
    The gap distance models cannot see: an event that shuts an airport disrupts
    every facility that flies through it, however far away.

    Two ways an event touches an airport:
      declared - hazard is airport_closure and lists IATA codes
      inferred - an airport falls inside the event's own impact zone

    The result is deliberately NOT summed into the distance bands. Ashfall at
    a hub 40 km away and an SLA risk at a facility 1,500 km away that routes
    through it are different problems, and adding them produces a number
    nobody can defend.
    """
    al = (cfg.get("air_linehaul") or {})
    if not al.get("enabled") or not airports:
        return

    codes: list[dict] = []
    if ev["hazard"] == "airport_closure":
        want = {c.upper() for c in (ev.get("airports") or [])}
        for a in airports:
            if a["iata"] in want:
                codes.append({"iata": a["iata"], "name": a["name"], "km": None,
                              "how": "declared closed"})
        missing = want - {c["iata"] for c in codes}
        if missing:
            ev["notes"].append("Not in the airport gazetteer, so not modelled: "
                               + ", ".join(sorted(missing)))
    elif ev["lat"] is not None:
        imp = ev.get("impact") or {}
        bands = imp.get("bands") or []
        outer = max((b["to_km"] for b in bands), default=0)
        for a in airports:
            d = haversine(ev["lat"], ev["lng"], a["lat"], a["lng"])
            inside = (in_polygons(a["lat"], a["lng"], ev["polygons"])
                      if ev["polygons"] else
                      (d <= outer and in_corridor(
                          bearing(ev["lat"], ev["lng"], a["lat"], a["lng"]),
                          imp.get("corridors") or [])))
            if inside:
                codes.append({"iata": a["iata"], "name": a["name"],
                              "km": round(d, 1), "how": "inside the impact zone"})
    if not codes:
        return
    codes.sort(key=lambda c: (c["km"] is None, c["km"]))
    hit = {c["iata"] for c in codes}

    direct, via = [], []
    for name, r in routing.items():
        if r.get("gateway") in hit:
            direct.append(name)
        elif r.get("hub") in hit:
            via.append(name)

    ev["air"] = {
        "airports": codes,
        "direct": sorted(direct),
        "via_hub": sorted(via),
        "direct_count": len(direct),
        "via_hub_count": len(via),
        "sla_days": al.get("sla_days", 1),
        "note": "Routing uses nearest-airport as a proxy unless lanes.csv is "
                "supplied. Counts are separate from the distance bands above.",
    }


def fetch_bmkg(http: Http, cfg: dict) -> list[dict]:
    sc = cfg["sources"]["bmkg_quake"]
    events: dict[str, dict] = {}
    minmag = float(sc.get("min_magnitude", 4.5))

    for url in sc["endpoints"]:
        try:
            data = http.get_json(url)
        except Exception as e:
            LOG.error("bmkg %s: %s", url, e)
            continue

        raw = ((data or {}).get("Infogempa") or {}).get("gempa")
        if raw is None:
            LOG.warning("bmkg %s: unexpected shape, keys=%s", url, list((data or {}).keys()))
            continue
        rows = raw if isinstance(raw, list) else [raw]

        for g in rows:
            if not isinstance(g, dict):
                continue
            coords = parse_bmkg_coords(g)
            if not coords:
                continue
            lat, lon = coords
            try:
                mag = float(str(g.get("Magnitude", "")).strip())
            except ValueError:
                continue
            if mag < minmag:
                continue

            depth = 0.0
            m = re.search(r"([\d.]+)", str(g.get("Kedalaman", "")))
            if m:
                depth = float(m.group(1))

            when = (g.get("DateTime") or "").strip()
            # native id: BMKG has none, so build one that is stable
            key = f"bmkg:{when}_{lat:.3f}_{lon:.3f}"
            if key in events:
                continue

            felt = parse_felt(g.get("Dirasakan", ""))
            shake = (g.get("Shakemap") or "").strip()
            ev = new_event(
                id=key, hazard="earthquake",
                name=f"M {mag} earthquake — {g.get('Wilayah') or 'Indonesia'}",
                lat=lat, lng=lon,
                severity=f"M {mag}, depth {depth:g} km",
                severity_value=mag,
                alert="red" if mag >= 6.5 else "orange" if mag >= 5.5 else "green",
                started=when or None, updated=when or None,
                source="BMKG", source_url="https://data.bmkg.go.id/gempabumi",
                attribution="Data gempabumi: BMKG",
                observed=felt,
            )
            ev["_depth_km"] = depth
            if g.get("Potensi"):
                ev["notes"].append(str(g["Potensi"]))
            if shake:
                ev["notes"].append(f"Shakemap: https://static.bmkg.go.id/{shake}")
            if felt:
                ev["notes"].append(
                    "BMKG felt intensity (MMI): "
                    + ", ".join(f"{f['mmi_text']} {f['place']}" for f in felt))
            events[key] = ev

    LOG.info("bmkg: %d quakes >= M%s", len(events), minmag)
    return list(events.values())


def fetch_gdacs(http: Http, cfg: dict) -> list[dict]:
    sc = cfg["sources"]["gdacs"]
    now = datetime.now(timezone.utc)
    params = {
        "eventlist": ";".join(sc["event_types"]),
        "alertlevel": ";".join(sc["alert_levels"]),
        "fromdate": (now - timedelta(days=int(sc.get("lookback_days", 14)))).strftime("%Y-%m-%d"),
        "todate": now.strftime("%Y-%m-%d"),
        "country": "IDN",
    }
    try:
        fc = http.get_json(sc["search_url"], params)
    except Exception as e:
        LOG.error("gdacs search: %s", e)
        return []

    feats = (fc or {}).get("features") or []
    west, south, east, north = cfg["indonesia_bbox"]
    out: list[dict] = []
    geo_budget = int(sc.get("max_geometry_fetches", 25))
    want_geo = set(sc.get("fetch_geometry_for") or [])

    for f in feats:
        p = f.get("properties") or {}
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates")
        if not coords or len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])

        # GDACS country filtering is by affected country, so an event can be
        # tagged IDN while its centroid sits elsewhere. Keep both checks.
        iso = (p.get("iso3") or "").upper()
        affected = {(c.get("iso3") or "").upper() for c in (p.get("affectedcountries") or [])}
        in_box = west <= lon <= east and south <= lat <= north
        if not in_box and "IDN" not in affected | {iso}:
            continue

        etype = p.get("eventtype")
        hazard = HAZARD_FROM_GDACS.get(etype, "other")
        sev = p.get("severitydata") or {}
        eid, epi = p.get("eventid"), p.get("episodeid")

        ev = new_event(
            id=f"gdacs:{etype}:{eid}",
            hazard=hazard,
            name=p.get("name") or p.get("description") or f"{hazard} in Indonesia",
            lat=lat, lng=lon,
            severity=sev.get("severitytext") or None,
            severity_value=sev.get("severity"),
            alert=(p.get("alertlevel") or "unknown").lower(),
            started=p.get("fromdate"), updated=p.get("datemodified") or p.get("todate"),
            source=f"GDACS/{p.get('source') or 'JRC'}",
            source_url=(p.get("url") or {}).get("report") or "https://www.gdacs.org",
            attribution="Global Disaster Alert and Coordination System (GDACS), EC-JRC",
        )
        if not in_box:
            ev["notes"].append("Centroid outside Indonesia; listed as an affected country.")

        if etype in want_geo and geo_budget > 0 and eid:
            geo_budget -= 1
            ev["polygons"] = fetch_gdacs_polygons(http, sc, etype, eid, epi)
            if ev["polygons"]:
                ev["notes"].append("Affected-area polygon from GDACS.")

        out.append(ev)

    LOG.info("gdacs: %d Indonesia events", len(out))
    return out


def fetch_gdacs_polygons(http: Http, sc: dict, etype, eid, epi) -> list[list[list[float]]]:
    """Pull the Poly_Affected footprint. Beats a guessed radius every time."""
    try:
        fc = http.get_json(sc["geometry_url"], {
            "eventtype": etype, "eventid": eid, "episodeid": epi or 1})
    except Exception as e:
        LOG.warning("gdacs geometry %s/%s: %s", etype, eid, e)
        return []

    rings: list[list[list[float]]] = []
    for f in (fc or {}).get("features") or []:
        cls = ((f.get("properties") or {}).get("Class") or "")
        if "Affected" not in cls:
            continue
        g = f.get("geometry") or {}
        t, c = g.get("type"), g.get("coordinates") or []
        if t == "Polygon":
            rings.extend([r for r in c if len(r) >= 4])
        elif t == "MultiPolygon":
            for poly in c:
                rings.extend([r for r in poly if len(r) >= 4])
    return rings[:40]   # keep the payload sane


# PVMBG alert level -> official exclusion radius, km. These are the standard
# defaults; a specific volcano's VAR often sets a different figure, and the
# VAR always wins. Level II is included because SPX facilities sit close to
# several Level II volcanoes.
LEVEL_EXCLUSION_KM = {"II": 2.0, "III": 3.0, "IV": 5.0}


def load_gazetteer(path: Path) -> list[dict]:
    """name -> coordinates for volcanoes, since MAGMA publishes neither."""
    import csv
    out = []
    if not path.exists():
        LOG.warning("no volcano gazetteer at %s; MAGMA alert levels will have "
                    "no coordinates and will be dropped", path)
        return out
    lines = [l for l in path.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.lstrip().startswith("#")]
    for r in csv.DictReader(lines):
        try:
            names = {r["name"].strip().lower()}
            names |= {a.strip().lower() for a in (r.get("aliases") or "").split("|") if a.strip()}
            out.append({"name": r["name"].strip(),
                        "lat": float(r["latitude"]), "lng": float(r["longitude"]),
                        "province": (r.get("province") or "").strip(),
                        "names": names})
        except (KeyError, ValueError):
            continue
    LOG.info("gazetteer: %d volcanoes", len(out))
    return out


def resolve_volcano(name: str, gaz: list[dict]) -> dict | None:
    n = re.sub(r"^g(?:unung)?\.?\s+", "", name.strip().lower())
    for v in gaz:
        if n in v["names"] or name.strip().lower() in v["names"]:
            return v
    for v in gaz:                       # substring, e.g. "Lewotobi" vs full name
        if any(n in a or a in n for a in v["names"] if len(a) > 4):
            return v
    return None


def merge_volcano_levels(events: list[dict], gaz: list[dict]) -> list[dict]:
    """
    GDACS VO events have coordinates but no Indonesian alert level.
    MAGMA has the level but no coordinates. Marry them.

    A GDACS VO event within 30 km of a gazetteer volcano that MAGMA reports at
    Level II+ absorbs that level, its name, and the matching exclusion radius.
    Unmatched MAGMA entries keep their own gazetteer coordinates and stand as
    events in their own right, because a volcano at Level III with no eruption
    yet is exactly the thing you want to see coming.
    """
    magma = [e for e in events if e["source"].startswith("PVMBG")]
    others = [e for e in events if not e["source"].startswith("PVMBG")]

    for m in magma:
        raw = m["name"].split(" — ")[0]
        v = resolve_volcano(raw, gaz)
        if v:
            m["lat"], m["lng"] = v["lat"], v["lng"]
            m["name"] = f"{v['name']} — {m['severity']}"
            if v["province"]:
                m["notes"].append(f"Province: {v['province']}")
        else:
            LOG.warning("volcano not in gazetteer: %r — add it to volcanoes.csv "
                        "so this event can be placed", raw)

    used = set()
    for ev in others:
        if ev["hazard"] != "volcano" or ev["lat"] is None:
            continue
        best, bestd = None, 1e9
        for i, m in enumerate(magma):
            if i in used or m["lat"] is None:
                continue
            d = haversine(ev["lat"], ev["lng"], m["lat"], m["lng"])
            if d < bestd:
                best, bestd = i, d
        if best is not None and bestd <= 30:
            m = magma[best]
            used.add(best)
            lvl = re.search(r"Level\s+(IV|III|II|I)\b", m["severity"] or "")
            ev["name"] = m["name"]
            ev["alert"] = m["alert"]
            ev["severity"] = m["severity"]
            ev["notes"].extend(m["notes"])
            ev["notes"].append(f"Alert level from PVMBG (matched {bestd:.0f} km "
                               f"from the GDACS centroid).")
            ev["attribution"] = ev["attribution"] + " | " + m["attribution"]
            if lvl:
                ev["exclusion_km"] = LEVEL_EXCLUSION_KM.get(lvl.group(1))

    leftover = [m for i, m in enumerate(magma)
                if i not in used and m["lat"] is not None]
    for m in leftover:
        lvl = re.search(r"Level\s+(IV|III|II|I)\b", m["severity"] or "")
        if lvl:
            m["exclusion_km"] = LEVEL_EXCLUSION_KM.get(lvl.group(1))
        m["notes"].append("Alert level only — no eruption event from GDACS. "
                          "Impact zone is the configured default, not an observed plume.")
    return others + leftover


def fetch_magma(http: Http, cfg: dict) -> list[dict]:
    """
    Volcano alert levels from PVMBG. HTML scrape — no JSON API exists.
    Returns level info keyed by volcano name so GDACS VO events can be
    enriched; also emits standalone events for Level III/IV volcanoes.
    """
    sc = cfg["sources"]["magma_volcano"]
    try:
        html = http.get(sc["activity_url"])
    except Exception as e:
        LOG.warning("magma unreachable (%s). Volcano alert levels unavailable "
                    "this run; GDACS VO events will lack Indonesian levels.", e)
        return []

    # The page groups volcanoes under "Level II (Waspada)" style headings.
    # Deliberately loose: we want it to degrade to zero results rather than
    # emit nonsense when the markup changes.
    text = re.sub(r"<[^>]+>", "\n", html)
    text = re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    out: list[dict] = []
    level = None
    lvl_re = re.compile(r"Level\s+(I{1,3}V?|IV)\s*\(\s*(Normal|Waspada|Siaga|Awas)\s*\)", re.I)
    for i, line in enumerate(lines):
        m = lvl_re.search(line)
        if m:
            level = f"Level {m.group(1).upper()} ({m.group(2).title()})"
            continue
        if not level or level.startswith("Level I "):
            continue
        # candidate volcano name: short, title-ish, no digits
        if 3 <= len(line) <= 40 and not re.search(r"\d", line) and line[0].isupper():
            out.append(new_event(
                id=f"magma:{re.sub(r'[^a-z0-9]+', '-', line.lower())}",
                hazard="volcano",
                name=f"{line} — {level}",
                lat=None, lng=None,       # resolved later from the gazetteer
                severity=level, alert=level,
                source="PVMBG / MAGMA Indonesia",
                source_url=sc["activity_url"],
                attribution="Pusat Vulkanologi dan Mitigasi Bencana Geologi (PVMBG), Badan Geologi ESDM",
                notes=["Alert level scraped from HTML; verify against MAGMA before acting."],
            ))
            if len(out) > 40:
                break
    LOG.info("magma: %d volcanoes at Level III+", len(out))
    return out


def load_overrides(path: Path) -> list[dict]:
    """Manual events. A confirmed fact beats an automated guess every time."""
    if not path.exists():
        return []
    try:
        cfg = load_config(path)
    except Exception as e:                              # noqa: BLE001
        LOG.error("overrides.yaml unreadable (%s); skipping", e)
        return []
    now = datetime.now(timezone.utc)
    out = []

    def iso(v):
        # PyYAML turns an unquoted timestamp into a datetime; the rest of the
        # pipeline and the JSON payload both want a string.
        if isinstance(v, datetime):
            return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
        return str(v) if v is not None else None

    for i, o in enumerate(((cfg or {}).get("events") or [])):
        if not isinstance(o, dict) or not o.get("hazard"):
            continue
        until = _parse_dt(o.get("until"))
        if until and until < now:
            LOG.info("override expired, skipping: %s", o.get("name") or o.get("id"))
            continue
        ev = new_event(
            id=o.get("id") or f"manual:{i}",
            hazard=o["hazard"], name=o.get("name") or o["hazard"],
            lat=o.get("lat"), lng=o.get("lng"),
            severity=o.get("severity"), severity_value=o.get("severity_value"),
            alert=(o.get("alert") or "unknown"),
            started=iso(o.get("started")),
            updated=iso(o.get("updated") or o.get("started")),
            source=o.get("source") or "Manual override",
            source_url=o.get("source_url") or "",
            attribution=o.get("attribution") or "Entered manually by SPX ops",
            notes=list(o.get("notes") or []) + ["Manually entered, not from a feed."],
        )
        if o.get("airports"):
            ev["airports"] = [str(c).upper() for c in o["airports"]]
        if o.get("corridors"):
            ev["corridors"] = [list(c) for c in o["corridors"]]
        if o.get("bands"):
            ev["_bands"] = [dict(b) for b in o["bands"]]
        if o.get("until"):
            ev["notes"].append(f"Expires {iso(o['until'])}.")
        out.append(ev)
    if out:
        LOG.info("overrides: %d manual events", len(out))
    return out


def fetch_gdelt(http: Http, cfg: dict) -> list[dict]:
    """
    News leads. Everything here is status='unverified' and excluded from SLA
    totals — see README. No geocoding is attempted from headlines, because
    guessing coordinates from prose is how you extend SLA on the wrong region.
    """
    sc = cfg["sources"]["gdelt_news"]
    out: list[dict] = []
    for q in sc.get("queries") or []:
        try:
            data = http.get_json(sc["url"], {
                "query": q, "mode": "artlist", "format": "json",
                "timespan": sc.get("timespan", "48h"),
                "maxrecords": sc.get("max_records", 60), "sort": "datedesc",
            })
        except Exception as e:
            LOG.warning("gdelt query %r: %s", q[:40], e)
            continue
        for a in (data or {}).get("articles") or []:
            url = a.get("url") or ""
            if not url:
                continue
            out.append(new_event(
                id="news:" + re.sub(r"[^a-z0-9]+", "", url.lower())[-40:],
                hazard="other",
                name=(a.get("title") or "").strip()[:180],
                lat=None, lng=None,
                status="unverified",
                started=a.get("seendate"), updated=a.get("seendate"),
                source=f"news/{a.get('domain') or 'unknown'}",
                source_url=url,
                attribution="GDELT Project",
                notes=["Unverified news lead. Not counted toward SLA until a human "
                       "confirms it and attaches coordinates."],
            ))
    LOG.info("gdelt: %d leads (all unverified)", len(out))
    return out


# --------------------------------------------------------------------------
# dedupe
# --------------------------------------------------------------------------

SOURCE_RANK = {"BMKG": 0, "PVMBG / MAGMA Indonesia": 1}   # lower wins


def _parse_dt(s) -> datetime | None:
    if not s:
        return None
    t = str(s).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%dT%H%M%SZ", "%Y-%m-%d"):
        try:
            d = datetime.fromisoformat(t) if fmt is None else datetime.strptime(str(s).strip(), fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def dedupe(events: list[dict]) -> list[dict]:
    """
    BMKG and GDACS both report the same Indonesian earthquake. Collapse pairs
    that match on hazard, time (within 120 s) and location (within 150 km),
    keeping the more authoritative record and merging the other's notes.
    """
    kept: list[dict] = []
    seen_titles: dict[str, dict] = {}
    for ev in sorted(events, key=lambda e: SOURCE_RANK.get(e["source"], 5)):
        if ev["status"] == "unverified":
            t = re.sub(r"[^a-z0-9 ]+", "", (ev["name"] or "").lower())
            t = " ".join(t.split()[:9])
            if t and t in seen_titles:
                first = seen_titles[t]
                first.setdefault("other_sources", []).append(
                    {"source": ev["source"], "url": ev["source_url"]})
                continue
            if t:
                seen_titles[t] = ev
            kept.append(ev)
            continue
        merged = False
        for k in kept:
            if k["hazard"] != ev["hazard"] or ev["hazard"] != "earthquake":
                continue
            if None in (k["lat"], k["lng"], ev["lat"], ev["lng"]):
                continue
            t1, t2 = _parse_dt(k["started"]), _parse_dt(ev["started"])
            if not t1 or not t2 or abs((t1 - t2).total_seconds()) > 120:
                continue
            if haversine(k["lat"], k["lng"], ev["lat"], ev["lng"]) > 150:
                continue
            k["notes"].append(f"Also reported by {ev['source']}.")
            if ev.get("source_url") and ev["source_url"] not in (k.get("source_url") or ""):
                k.setdefault("other_sources", []).append(
                    {"source": ev["source"], "url": ev["source_url"]})
            merged = True
            break
        if not merged:
            kept.append(ev)
    if len(kept) != len(events):
        LOG.info("dedupe: %d -> %d", len(events), len(kept))
    return kept


# --------------------------------------------------------------------------
# impact
# --------------------------------------------------------------------------

def eq_bands(cfg: dict, mag: float | None, depth_km: float) -> list[dict]:
    m = cfg["impact_models"]["earthquake"]
    table = {float(k): v for k, v in (m["magnitude_radius_km"] or {}).items()}
    if not table or mag is None:
        return []
    keys = sorted(table)
    pick = keys[0]
    for k in keys:
        if mag >= k:
            pick = k
    radii = list(table[pick])
    if depth_km and depth_km >= float(m.get("deep_km", 300)):
        f = float(m.get("deep_radius_multiplier", 0.55))
        radii = [r * f for r in radii]
    labels = m.get("band_labels") or []
    slas = m.get("band_sla_days") or []
    return [{"to_km": round(r, 1),
             "label": labels[i] if i < len(labels) else f"Band {i+1}",
             "sla_days": slas[i] if i < len(slas) else 1}
            for i, r in enumerate(radii)]


def apply_impact_model(ev: dict, cfg: dict, facilities: list[tuple[str, float, float]]) -> None:
    """
    Attach ev['impact'] = {model, bands, corridors, exclusion_km, counts,
    facilities:[...]}.

    Two models:
      polygon — the hazard footprint is known (GDACS affected area). Exact.
      radial  — distance bands, optionally limited to bearing corridors.
                An approximation, and labelled as one.
    """
    model_cfg = (cfg.get("impact_models") or {}).get(ev["hazard"]) or \
                (cfg.get("impact_models") or {}).get("other") or {}

    if ev.get("_bands"):
        bands = [dict(b) for b in ev["_bands"]]
    elif ev["hazard"] == "earthquake":
        bands = eq_bands(cfg, ev.get("severity_value"), ev.get("_depth_km") or 0.0)
    else:
        bands = [dict(b) for b in (model_cfg.get("bands") or [])]

    corridors = [list(c) for c in (ev.get("corridors") or model_cfg.get("corridors") or [])]
    excl = ev.get("exclusion_km", model_cfg.get("exclusion_km"))

    hits: list[dict] = []

    if ev["polygons"]:
        model = "polygon"
        for name, flat, flon in facilities:
            if not in_polygons(flat, flon, ev["polygons"]):
                continue
            # Inside the footprint is a yes/no fact. Severity within it still
            # varies with distance from the source, so keep banding when we
            # have both. Anything beyond the last band is clamped into it
            # rather than dropped: the polygon already said it is affected.
            if ev["lat"] is None:
                d = brg = None
                bi = 0
            else:
                d = haversine(ev["lat"], ev["lng"], flat, flon)
                brg = bearing(ev["lat"], ev["lng"], flat, flon)
                bi = next((i for i, b in enumerate(bands) if d <= b["to_km"]),
                          max(len(bands) - 1, 0)) if bands else 0
            hits.append({"name": name, "lat": flat, "lng": flon,
                         "km": round(d, 1) if d is not None else None,
                         "bearing": round(brg, 1) if brg is not None else None,
                         "band": bi,
                         "band_label": bands[bi]["label"] if bands else "Affected area",
                         "sla_days": bands[bi]["sla_days"] if bands else 1})
    elif ev["lat"] is not None and bands:
        model = "radial"
        outer = max(b["to_km"] for b in bands)
        for name, flat, flon in facilities:
            d = haversine(ev["lat"], ev["lng"], flat, flon)
            if d > outer:
                continue
            brg = bearing(ev["lat"], ev["lng"], flat, flon)
            if not in_corridor(brg, corridors):
                continue
            bi = next((i for i, b in enumerate(bands) if d <= b["to_km"]), None)
            if bi is None:
                continue
            hits.append({"name": name, "lat": flat, "lng": flon,
                         "km": round(d, 1), "bearing": round(brg, 1),
                         "band": bi, "band_label": bands[bi]["label"],
                         "sla_days": bands[bi]["sla_days"]})
    elif ev["hazard"] == "airport_closure":
        # No radius applies: the affected set is the airports' catchment,
        # filled in by apply_air_linehaul.
        ev["impact"] = {"model": "catchment", "bands": [], "corridors": [],
                        "counts": [], "total": 0, "facilities": [],
                        "reason": "impact is the airport catchment, not a radius"}
        return
    else:
        # No location (news lead, or an unresolved volcano name). Honest zero.
        ev["impact"] = {"model": "none", "bands": [], "corridors": [],
                        "counts": [], "total": 0, "facilities": [],
                        "reason": "no coordinates available"}
        return

    hits.sort(key=lambda h: (h["km"] if h["km"] is not None else 1e9))
    counts = [0] * max(len(bands), 1)
    for h in hits:
        if h["band"] < len(counts):
            counts[h["band"]] += 1

    ev["impact"] = {
        "model": model,
        "bands": bands,
        "corridors": corridors,
        "exclusion_km": excl,
        "counts": counts,
        "total": len(hits),
        "facilities": hits,
    }


# --------------------------------------------------------------------------
# retention / state
# --------------------------------------------------------------------------

def apply_retention(events: list[dict], cfg: dict, state: dict) -> list[dict]:
    now = datetime.now(timezone.utc)
    ret = cfg.get("retention_hours") or {}
    seen = state.setdefault("first_seen", {})
    out = []
    for ev in events:
        seen.setdefault(ev["id"], now.isoformat())
        ev["first_seen"] = seen[ev["id"]]
        ref = _parse_dt(ev.get("updated")) or _parse_dt(ev.get("started")) \
              or _parse_dt(ev["first_seen"]) or now
        age_h = (now - ref).total_seconds() / 3600.0
        limit = float(ret.get(ev["hazard"], ret.get("other", 72)))
        if age_h > limit:
            continue
        ev["age_hours"] = round(age_h, 1)
        if age_h > limit * 0.75 and ev["status"] == "active":
            ev["status"] = "stale"
        out.append(ev)
    # prune the ledger so state.json does not grow without bound
    live = {e["id"] for e in out}
    state["first_seen"] = {k: v for k, v in seen.items()
                           if k in live or (_parse_dt(v) or now) > now - timedelta(days=30)}
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def load_facilities(path: Path) -> list[tuple[str, float, float]]:
    import csv
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r = { (k or "").strip(): (v or "").strip() for k, v in r.items() }
            try:
                rows.append((r["station_name"], float(r["latitude"]), float(r["longitude"])))
            except (KeyError, ValueError):
                continue
    if not rows:
        raise SystemExit(f"no usable rows in {path} "
                         f"(need station_name, latitude, longitude)")
    LOG.info("facilities: %d", len(rows))
    FAC_XY.clear()
    FAC_XY.update({n: (a, b) for n, a, b in rows})
    return rows


FAC_XY: dict[str, tuple[float, float]] = {}


def write_impact_csv(path: Path, events: list[dict]) -> int:
    """
    One row per (event, facility). This is the artifact that actually feeds an
    SLA change — paste it into a sheet, or have a job read it. Kept out of
    events.json so the map payload stays small enough to poll.
    """
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    n = 0
    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["event_id", "hazard", "event_name", "alert", "status",
                    "event_started", "source", "station_name", "latitude",
                    "longitude", "distance_km", "bearing_deg", "severity_band",
                    "sla_extension_days", "impact_model", "source_url"])
        for ev in events:
            imp = ev.get("impact") or {}
            seen = set()
            for h in imp.get("facilities") or []:
                seen.add(h["name"])
                w.writerow([ev["id"], ev["hazard"], ev["name"], ev["alert"],
                            ev["status"], ev.get("started") or "", ev["source"],
                            h["name"], h["lat"], h["lng"], h.get("km"),
                            h.get("bearing"), h.get("band_label"),
                            h.get("sla_days"), imp.get("model"),
                            ev.get("source_url") or ""])
                n += 1
            air = ev.get("air") or {}
            if not air:
                continue
            codes = ", ".join(c["iata"] for c in air["airports"])
            for kind, names in (("direct", air.get("direct") or []),
                                ("via hub", air.get("via_hub") or [])):
                for name in names:
                    if name in seen:
                        # already listed with a physical band; a second row
                        # would double-count it in a pivot
                        continue
                    la, lo_ = FAC_XY.get(name, (None, None))
                    w.writerow([ev["id"], ev["hazard"], ev["name"], ev["alert"],
                                ev["status"], ev.get("started") or "", ev["source"],
                                name, la, lo_, "", "",
                                f"Air linehaul ({kind}: {codes})",
                                air.get("sla_days"), "catchment",
                                ev.get("source_url") or ""])
                    n += 1
    os.replace(tmp, path)
    return n


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SPX force-majeure ingestion")
    ap.add_argument("--config", type=Path, default=HERE / "config.yaml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixtures", type=Path)
    ap.add_argument("--save-fixtures", type=Path)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    cfg = load_config(a.config)
    base = a.config.parent
    fac_path = Path(cfg["facilities"])
    if not fac_path.is_absolute():
        fac_path = base / fac_path if (base / fac_path).exists() else base.parent / fac_path if (base.parent / fac_path).exists() else base / Path(fac_path).name
    facilities = load_facilities(fac_path)

    http = Http(cfg, a.fixtures, a.save_fixtures)
    state_path = base / Path(cfg.get("state", "state.json")).name
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            LOG.warning("state.json unreadable; starting fresh")

    airports = load_airports(base / "airports.csv")
    routing = build_routing(facilities, airports, cfg)

    fetchers = [
        ("bmkg_quake", fetch_bmkg),
        ("gdacs", fetch_gdacs),
        ("magma_volcano", fetch_magma),
        ("gdelt_news", fetch_gdelt),
    ]

    events: list[dict] = []
    health: list[dict] = []
    for key, fn in fetchers:
        sc = (cfg.get("sources") or {}).get(key) or {}
        if not sc.get("enabled"):
            health.append({"source": key, "state": "disabled", "events": 0})
            continue
        t0 = time.time()
        try:
            got = fn(http, cfg)
            events.extend(got)
            health.append({"source": key, "state": "ok", "events": len(got),
                           "seconds": round(time.time() - t0, 2)})
        except Exception as e:                      # noqa: BLE001 — isolation is the point
            LOG.exception("source %s failed", key)
            health.append({"source": key, "state": "failed", "events": 0,
                           "error": str(e)[:300]})
            if not sc.get("optional"):
                LOG.error("  ^ non-optional source failed; continuing with degraded data")

    # Overrides last so a confirmed manual event survives dedupe against the
    # automated guess it replaces.
    manual = load_overrides(base / "overrides.yaml")
    events.extend(manual)
    health.append({"source": "overrides", "state": "ok", "events": len(manual)})

    events = dedupe(events)
    events = merge_volcano_levels(events, load_gazetteer(base / "volcanoes.csv"))
    for ev in events:
        apply_impact_model(ev, cfg, facilities)
        apply_air_linehaul(ev, cfg, airports, routing)
    events = apply_retention(events, cfg, state)

    if cfg.get("drop_events_with_zero_facilities"):
        before = len(events)
        events = [e for e in events
                  if (e["impact"] or {}).get("total", 0) > 0
                  or (e.get("air") or {}).get("direct_count", 0) > 0
                  or (e.get("air") or {}).get("via_hub_count", 0) > 0
                  or e["status"] == "unverified"]
        if before != len(events):
            LOG.info("dropped %d events with no facility in range", before - len(events))

    def exposure(e):
        air = e.get("air") or {}
        return ((e["impact"] or {}).get("total", 0)
                + air.get("direct_count", 0) + air.get("via_hub_count", 0))
    events.sort(key=lambda e: (-exposure(e), e["status"] != "active"))

    for ev in events:
        ev.pop("_depth_km", None)
        ev.pop("exclusion_km", None)
        ev.pop("_bands", None)

    # The map recomputes facility impact itself from station coordinates, so
    # it only needs each event's geometry. Shipping 6k facility rows in the
    # JSON made it 1.3 MB and unpollable.
    light = []
    for ev in events:
        lo = {k: v for k, v in ev.items() if k != "impact"}
        imp = dict(ev.get("impact") or {})
        imp.pop("facilities", None)
        lo["impact"] = imp
        if ev.get("air"):
            air = dict(ev["air"])
            air.pop("direct", None)         # the map recomputes catchments itself
            air.pop("via_hub", None)
            lo["air"] = air
        light.append(lo)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "facility_count": len(facilities),
        "event_count": len(events),
        "verified_event_count": sum(1 for e in events if e["status"] != "unverified"),
        "source_health": health,
        "attribution": sorted({part.strip()
                               for e in events for part in (e.get("attribution") or "").split("|")
                               if part.strip()}),
        "events": light,
    }

    ok_sources = sum(1 for h in health if h["state"] == "ok")
    if ok_sources == 0:
        LOG.error("every source failed — refusing to overwrite events.json")
        return 2

    if a.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=1)[:4000])
        LOG.info("dry run: nothing written")
        return 0

    out_path = base / Path(cfg["output"]).name
    atomic_write_json(out_path, payload)
    atomic_write_json(state_path, state)
    csv_path = base / "impacted_facilities.csv"
    rows = write_impact_csv(csv_path, events)
    LOG.info("wrote %s — %d facility rows", csv_path, rows)
    LOG.info("wrote %s — %d events (%d verified), %d/%d sources ok",
             out_path, len(events), payload["verified_event_count"],
             ok_sources, len([h for h in health if h["state"] != "disabled"]))
    for e in events[:12]:
        imp = e["impact"] or {}
        air = e.get("air") or {}
        extra = ""
        if air:
            extra = (" + air %d/%d via %s"
                     % (air["direct_count"], air["via_hub_count"],
                        ",".join(c["iata"] for c in air["airports"][:4])))
        LOG.info("  [%-15s] %-50s %5d in zone (%s)%s",
                 e["hazard"], e["name"][:50], imp.get("total", 0),
                 imp.get("model"), extra)
    return 0


if __name__ == "__main__":
    sys.exit(main())
