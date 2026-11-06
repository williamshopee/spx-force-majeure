"""Fetch only BNPB news and build a local preview without replacing ops data."""

import json
from datetime import datetime, timezone
from pathlib import Path

from build_map import main as build_map
from ingest import Http, apply_impact_model, fetch_bnpb, load_config


def main():
    base = Path(__file__).resolve().parent
    cfg = load_config(base / "config.yaml")
    records = fetch_bnpb(Http(cfg), cfg)
    for record in records:
        apply_impact_model(record, cfg, [])
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event_count": len(records), "verified_event_count": 0,
        "source_health": [{"source": "bnpb_news",
                           "state": "degraded" if records.warnings else "ok",
                           "events": len(records), "warnings": records.warnings}],
        "attribution": ["BNPB. News preview only; no confirmed impact geometry."],
        "events": records,
    }
    output = base / "latest_news.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for record in records:
        print(record["published_at"], record["name"])
    print(f"{len(records)} bulletins; {len(records.warnings)} retrieval warnings; 0 inferred facility impacts")
    return build_map(["--embed", "--events", str(output), "--out", str(base / "preview_news.html")])


if __name__ == "__main__":
    raise SystemExit(main())
