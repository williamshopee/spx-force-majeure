#!/usr/bin/env python3
"""
Build the station map.

Two ways to run the map, and this script serves both:

  Served (recommended)
      python build_map.py --template map_template.html --out ../out/index.html
      cd ../out && python -m http.server 8080
    The page fetches events.json itself and re-polls every 5 minutes, so a
    cron'd ingest.py keeps the map current with no rebuild.

  Single file, for e-mail or Slack
      python build_map.py --template map_template.html --out ../out/map.html --embed
    Embeds events.json into the HTML. Opens with a double click, no server,
    but it is a snapshot: rebuild it to refresh.

Station coordinates are always embedded, because they change rarely and
fetching them separately would just add a request.
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the SPX station map")
    ap.add_argument("--template", type=Path, default=HERE / "map_template.html")
    ap.add_argument("--stations", type=Path, default=HERE / "latlong.csv")
    ap.add_argument("--airports", type=Path, default=HERE / "airports.csv")
    ap.add_argument("--events", type=Path, default=HERE.parent / "out" / "events.json")
    ap.add_argument("--out", type=Path, default=HERE.parent / "out" / "index.html")
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
    stations = load_stations(a.stations)
    if not stations:
        print(f"no usable rows in {a.stations}", file=sys.stderr)
        return 1
    if "__DATA__" not in html:
        print("template has no __DATA__ placeholder", file=sys.stderr)
        return 1
    html = html.replace("__DATA__", json.dumps(stations, separators=(",", ":"),
                                               ensure_ascii=False))

    # Airports are embedded so the map can work out facility-to-airport
    # catchments itself, the same way it recomputes distance bands.
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

    events_txt = "null"
    if a.embed:
        if a.events.exists():
            raw = a.events.read_text(encoding="utf-8")
            # </script> inside the JSON would close the tag early. No station or
            # event name should contain it, but a news headline could.
            events_txt = json.dumps(json.loads(raw), separators=(",", ":"),
                                    ensure_ascii=False).replace("</", "<\\/")
        else:
            print(f"warning: {a.events} missing; embedding nothing", file=sys.stderr)
    html = html.replace("__EVENTS__", events_txt)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(html, encoding="utf-8")

    # events.json must sit next to the page for the served mode to work
    if not a.embed and a.events.exists() and a.events.resolve() != (a.out.parent / "events.json").resolve():
        (a.out.parent / "events.json").write_text(
            a.events.read_text(encoding="utf-8"), encoding="utf-8")

    kb = a.out.stat().st_size / 1024
    print(f"wrote {a.out}  ({kb:.0f} KB, {len(stations)} stations, "
          f"{len(airports)} airports, "
          f"events {'embedded' if a.embed else 'fetched at runtime'})")
    if not a.embed:
        print(f"  serve it:  cd {a.out.parent} && python -m http.server 8080")
    return 0


if __name__ == "__main__":
    sys.exit(main())
