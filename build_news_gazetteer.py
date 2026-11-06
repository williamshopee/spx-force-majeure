"""Extract Indonesian administrative place centers from the GeoNames ID dump.

Download https://download.geonames.org/export/dump/ID.zip, then run:
    python build_news_gazetteer.py geonames-ID.zip
Data: GeoNames, CC BY 4.0. These are centers, not administrative boundaries.
"""
import argparse
import json
import re
import zipfile
from datetime import date
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("news_places.json"))
    args = parser.parse_args()
    places = []
    with zipfile.ZipFile(args.archive) as archive:
        for line in archive.read("ID.txt").decode("utf-8").splitlines():
            row = line.split("\t")
            if row[8] != "ID" or row[7] not in ("ADM1", "ADM2", "ADM3"):
                continue
            aliases = {row[1], row[2]}
            for alias in row[3].split(","):
                if re.match(r"^(Kabupaten|Kecamatan|Kota|Provinsi) ", alias):
                    aliases.add(alias)
            places.append([int(row[0]), row[1], row[7], row[10], row[11],
                           float(row[4]), float(row[5]), sorted(aliases)])
    args.out.write_text(json.dumps({
        "source": "https://download.geonames.org/export/dump/ID.zip",
        "license": "GeoNames CC BY 4.0 https://www.geonames.org/",
        "retrieved_on": date.today().isoformat(),
        "columns": ["id", "name", "level", "province_code", "regency_code", "lat", "lng", "aliases"],
        "places": places,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(places)} Indonesian administrative centers to {args.out}")


if __name__ == "__main__":
    main()
