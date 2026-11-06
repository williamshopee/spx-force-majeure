"""Package provider-simplified Indonesian ADM2/ADM3 boundaries, preserving holes."""
import gzip
import json
import argparse
import urllib.request
from pathlib import Path


def rounded(value):
    return [rounded(x) for x in value] if isinstance(value, list) else round(value, 5)


def vertices(coords):
    if coords and isinstance(coords[0], (int, float)):
        yield coords
    else:
        for child in coords:
            yield from vertices(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Download source geometry from the bundled provider metadata")
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    rows, metadata = [], {}
    for level, filename, meta_file in (
        ("ADM2", "boundaries_adm2_original.geojson", "boundary_adm2_meta.json"),
        ("ADM3", "boundaries_adm3_original.geojson", "boundary_adm3_meta.json"),
    ):
        meta = json.loads((base / meta_file).read_text(encoding="utf-8-sig"))
        if args.download:
            with urllib.request.urlopen(meta["simplifiedGeometryGeoJSON"], timeout=120) as response:
                (base / filename).write_bytes(response.read())
        metadata[level] = {"year": meta["boundaryYearRepresented"],
            "source": meta["boundarySource"], "license": meta["boundaryLicense"],
            "url": meta["simplifiedGeometryGeoJSON"]}
        data = json.loads((base / filename).read_text(encoding="utf-8-sig"))
        for feature in data["features"]:
            geometry = feature["geometry"]
            coords = rounded(geometry["coordinates"])
            points = list(vertices(coords))
            bbox = [min(p[0] for p in points), min(p[1] for p in points),
                    max(p[0] for p in points), max(p[1] for p in points)]
            props = feature["properties"]
            rows.append([props["shapeName"], level, props["shapeID"], bbox,
                         {"type": geometry["type"], "coordinates": coords}])
    payload = {"metadata": metadata, "precision": "Provider simplified, coordinates rounded to 5 decimals", "areas": rows}
    output = base / "admin_boundaries.json.gz"
    output.write_bytes(gzip.compress(json.dumps(payload, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8"), mtime=0))
    print(f"Packaged {len(rows)} administrative areas; {output.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
