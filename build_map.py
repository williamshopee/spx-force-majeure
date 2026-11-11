#!/usr/bin/env python3
"""
Build the station map.

  General Map (no network data embedded at all):
      python build_map.py --embed --out map.html

  SOC Map (network chain embedded):
      python build_map.py --embed --network network.csv --out map_soc.html

The network is only embedded when --network is passed, so the General Map's
HTML contains no network data whatsoever — not hidden, absent.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def station_type(name: str) -> int:
    if "First Mile" in name:
        return 1
    if name.endswith(("DC", "RDC", "EDC")):
        return 2
    return 0


def region(lat: float, lon: float) -> int:
    """
    Island group from coordinates. Boundaries hand-checked against the
    easternmost, westernmost, northernmost and southernmost station in each
    group; the awkward ones are commented.
    """
    if lon >= 137 or (lon >= 130.8 and lat > -5.2):
        return 6                                      # Papua, incl. Merauke
    if lon >= 125.9 and not (lat > 2.5 and lon < 127.5):
        return 5                                      # Maluku; Talaud/Sangihe excluded
    if lon >= 114.43 and lat <= -7.7:
        return 4                                      # Bali & Nusa Tenggara
    if lon >= 118.3 and lat >= -7.0:
        return 3                                      # Sulawesi, incl. Selayar + Wakatobi
    if lon >= 108.8 and lat > -4.5:
        return 2                                      # Kalimantan
    if lon >= 105.1 and lat <= -6.05:
        return 1                                      # Jawa, incl. Ujung Kulon
    if lon >= 105.85 and lat <= -5.5:
        return 1
    return 0                                          # Sumatera


def load_stations(path: Path) -> list:
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
            try:
                name = r["station_name"]
                la, lo = float(r["latitude"]), float(r["longitude"])
            except (KeyError, ValueError):
                continue
            rows.append([name, round(la, 5), round(lo, 5),
                         station_type(name), region(la, lo)])
    return rows


def load_network(path: Path, stations: list) -> list:
    """
    Origin/Destination pairs as station INDEX pairs, so the payload stays small
    (two integers per link instead of two names).

    Rows whose Origin or Destination does not match a station_name in
    latlong.csv are dropped and counted. An exact match is required: a name
    that differs by a space or spelling is a different facility as far as this
    is concerned, and guessing at near-matches would draw lines to the wrong
    place.
    """
    idx = {s[0]: i for i, s in enumerate(stations)}
    links: list[list[int]] = []
    seen: set[tuple[int, int]] = set()
    unmatched: set[str] = set()
    total = 0

    with path.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            o = r.get("origin") or ""
            d = r.get("destination") or ""
            if not o or not d:
                continue
            total += 1
            oi, di = idx.get(o), idx.get(d)
            if oi is None:
                unmatched.add(o)
            if di is None:
                unmatched.add(d)
            if oi is None or di is None or oi == di:
                continue
            key = (oi, di)
            if key in seen:
                continue
            seen.add(key)
            links.append([oi, di])

    print(f"  network: {len(links)} links from {total} rows", file=sys.stderr)
    if unmatched:
        print(f"  network: {len(unmatched)} names not in latlong.csv, ignored "
              f"(e.g. {', '.join(sorted(unmatched)[:3])})", file=sys.stderr)
    return links

# Columns to show in the popup, in order: (csv header, label, format)
# fmt: 'n' = number with thousands separator, 'p' = percent as-is,
#      'x' = plain text, '1' = one decimal
PROFILE_COLS = [
    ("Space",                     "Space (m2)",        "n"),
    ("Ops Model",                 "Ops model",         "x"),
    ("Dock Ratio",                "Dock ratio",        "1"),
    ("RTS %",                     "RTS",               "p"),
    ("STD %",                     "STD",               "p"),
    ("ECO %",                     "ECO",               "p"),
    ("Shipment Cycle STD",        "Cycle STD",         "x"),
    ("Shipment Cycle ECO",        "Cycle ECO",         "x"),
    ("% Dist Process Reguler",    "Dist reguler",      "p"),
    ("% Dist Process Crossdock",  "Dist crossdock",    "p"),
    ("Campaign Spike",            "Campaign spike",    "p"),
    ("% Dist Standard",           "Dist standard",     "p"),
    ("% Dist Bulky",              "Dist bulky",        "p"),
]


def load_profile(path: Path, stations: list) -> dict:
    """
    SOC capacity and operating profile, keyed by station index.

    The sheet has title rows above the real header, so the header row is
    found by looking for the one containing 'SOC Name' rather than assuming
    row 1. Facilities not in latlong.csv are ignored, same as the network.
    """
    idx = {s[0]: i for i, s in enumerate(stations)}
    raw = path.read_text(encoding="utf-8-sig").splitlines()

    head = None
    for n, line in enumerate(raw):
        if "SOC Name" in line:
            head = n
            break
    if head is None:
        print("  profile: no header row containing 'SOC Name'; skipped",
              file=sys.stderr)
        return {}

    out: dict[int, list] = {}
    unmatched = 0
    for r in csv.DictReader(raw[head:]):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        name = r.get("SOC Name") or ""
        if not name:
            continue
        i = idx.get(name)
        if i is None:
            unmatched += 1
            continue
        vals = []
        for col, _lab, _f in PROFILE_COLS:
            vals.append(r.get(col, ""))
        if any(vals):
            out[i] = vals

    print(f"  profile: {len(out)} facilities"
          + (f", {unmatched} names not in latlong.csv ignored" if unmatched else ""),
          file=sys.stderr)
    return out

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the SPX station map")
    ap.add_argument("--template", type=Path, default=HERE / "map_template.html")
    ap.add_argument("--stations", type=Path, default=HERE / "latlong.csv")
    ap.add_argument("--airports", type=Path, default=HERE / "airports.csv")
    ap.add_argument("--events", type=Path, default=HERE / "events.json")
    ap.add_argument("--network", type=Path, default=None,
                    help="Origin/Destination CSV. Omit to build without it.")
    ap.add_argument("--profile", type=Path, default=None,
                    help="SOC profile CSV. Omit to build without it.")
    ap.add_argument("--out", type=Path, default=HERE / "index.html")
    ap.add_argument("--embed", action="store_true",
                    help="embed events.json for file:// use (snapshot)")
    a = ap.parse_args(argv)

    if not a.template.exists():
        print(f"template not found: {a.template}", file=sys.stderr)
        return 1
    if not a.stations.exists():
        print(f"stations csv not found: {a.stations}", file=sys.stderr)
        return 1

    html = a.template.read_text(encoding="utf-8")
    turf_js = "/* TurfJS 7.2.0\n" + (HERE / "TURF-LICENSE").read_text(encoding="utf-8") + "\n*/\n"
    turf_js += (HERE / "turf.min.js").read_text(encoding="utf-8")
    html = html.replace("__TURF__", turf_js.replace("</", "<\\/"))
    html = html.replace("__NAVIGATION__", (HERE / "navigation.js").read_text(encoding="utf-8").replace("</", "<\\/"))
    html = html.replace("__STUCK_UI__", (HERE / "stuck_tracker.js").read_text(encoding="utf-8").replace("</", "<\\/"))
    html = html.replace("__FACILITY_INSIGHTS__", (HERE / "facility_insights.js").read_text(encoding="utf-8").replace("</", "<\\/"))
    stations = load_stations(a.stations)
    if not stations:
        print(f"no usable rows in {a.stations}", file=sys.stderr)
        return 1
    if "__DATA__" not in html:
        print("template has no __DATA__ placeholder", file=sys.stderr)
        return 1
    html = html.replace("__DATA__", json.dumps(stations, separators=(",", ":"),
                                               ensure_ascii=False))

    airports = []
    if a.airports.exists():
        lines = [l for l in a.airports.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.lstrip().startswith("#")]
        for r in csv.DictReader(lines):
            try:
                airports.append([r["iata"].strip().upper(), round(float(r["latitude"]), 5),
                                 round(float(r["longitude"]), 5),
                                 r["name"].strip(), (r.get("city") or "").strip(),
                                 1 if (r.get("tier") or "").strip() == "hub" else 0])
            except (KeyError, ValueError):
                continue
    else:
        print(f"warning: {a.airports} missing; air-linehaul view disabled",
              file=sys.stderr)
    html = html.replace("__AIRPORTS__", json.dumps(airports, separators=(",", ":"),
                                                   ensure_ascii=False))

    # Network is opt-in. Without --network the placeholder becomes null, so the
    # General Map carries no network data at all.
    network = []
    if a.network:
        if a.network.exists():
            network = load_network(a.network, stations)
        else:
            print(f"warning: {a.network} missing; building without network",
                  file=sys.stderr)
    html = html.replace("__NETWORK__",
                        json.dumps(network, separators=(",", ":")) if network else "null")

    # SOC profile is opt-in, same as the network: the General Map build
    # carries none of it.
    profile = {}
    if a.profile:
        if a.profile.exists():
            profile = load_profile(a.profile, stations)
        else:
            print(f"warning: {a.profile} missing; building without profile",
                  file=sys.stderr)
    html = html.replace("__PROFILE__",
                        json.dumps({"cols": [[c[1], c[2]] for c in PROFILE_COLS],
                                    "rows": profile}, separators=(",", ":"),
                                   ensure_ascii=False) if profile else "null")
  
    events_txt = "null"
    if a.embed:
        if a.events.exists():
            raw = a.events.read_text(encoding="utf-8")
            events_txt = json.dumps(json.loads(raw), separators=(",", ":"),
                                    ensure_ascii=False).replace("</", "<\\/")
        else:
            print(f"warning: {a.events} missing; embedding nothing", file=sys.stderr)
    html = html.replace("__EVENTS__", events_txt)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(html, encoding="utf-8")

    if not a.embed and a.events.exists() and a.events.resolve() != (a.out.parent / "events.json").resolve():
        (a.out.parent / "events.json").write_text(
            a.events.read_text(encoding="utf-8"), encoding="utf-8")

    kb = a.out.stat().st_size / 1024
    print(f"wrote {a.out}  ({kb:.0f} KB, {len(stations)} stations, "
          f"{len(airports)} airports, "
          f"{len(network)} network links, "
          f"{len(profile)} SOC profiles, "
          f"events {'embedded' if a.embed else 'fetched at runtime'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
