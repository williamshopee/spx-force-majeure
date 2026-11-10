"""Private, read-only SOC tracker connection. Never writes tracker data to disk."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import re
from threading import Lock
from urllib.parse import quote

HEADERS = ["SOC", "# absolute", "% of inbound", "%_stuck_still_alive",
           "Stuck Aging < 16 Days", "Most Last Status", "% of Last Status"]
PAYLOAD_TAG = '<script id="stuck-tracker" type="application/json">null</script>'


class TrackerError(Exception):
    """An expected connection/layout error with a safe, user-facing message."""


class SnapshotStore:
    """Process-memory last-good data, isolated by source and credential identity."""
    def __init__(self):
        self.lock = Lock()
        self.snapshots = {}

    def refresh(self, key, settings, service_info, stations, reader=None):
        with self.lock:
            snapshot = fetch_snapshot(settings, service_info, stations,
                                      previous=self.snapshots.get(key), reader=reader or read_sheet)
            if snapshot["status"] == "ok":
                self.snapshots[key] = snapshot
            return snapshot


def normalize_name(value):
    # Whitespace and case only. No fuzzy matching, removed prefixes or aliases.
    return " ".join(str(value or "").split()).casefold()


def number(value, percent=False):
    text = str(value if value is not None else "").strip()
    if not text or text.startswith("#"):
        return None
    if percent and not text.endswith("%"):
        return None  # API is requested as FORMATTED_VALUE; don't guess ratios.
    if not percent and text.endswith("%"):
        return None
    try:
        result = float(text.rstrip("%").replace(",", ""))
    except ValueError:
        return None
    if not math.isfinite(result) or result < 0 or (not percent and not result.is_integer()):
        return None
    # Inbound share may exceed 100%; preserve the source rather than clamping it.
    return result if percent else int(result)


def parse_rows(values, stations):
    header = next((i for i, row in enumerate(values[:20])
                   if [str(v or "").strip() for v in row[:7]] == HEADERS), None)
    if header is None:
        raise TrackerError("Tracker columns changed. Expected the SOC summary in columns A:G.")
    names = defaultdict(list)
    for i, station in enumerate(stations):
        if station[3] == 2:
            names[normalize_name(station[0])].append(i)
    candidates = []
    for row in values[header + 1:]:
        row = list(row[:7]) + [""] * max(0, 7 - len(row))
        key = normalize_name(row[0])
        if not key or key in {"nationwide", "total", "grand total"}:
            continue
        # A name-only row is not a zero-volume SOC report.
        if not any(str(v or "").strip() for v in row[1:]):
            continue
        candidates.append((key, row))
    if not candidates:
        raise TrackerError("Tracker has no populated SOC summary rows. Previous data is retained if available.")
    counts = Counter(key for key, _ in candidates)
    result, unmatched, ambiguous, invalid = {}, 0, set(), 0
    for key, row in candidates:
        if counts[key] > 1 or len(names.get(key, [])) > 1:
            ambiguous.add(key)
            continue
        if key not in names:
            unmatched += 1
            continue
        absolute, inbound, alive, aging, status, share = row[1:7]
        metrics = {
            "absolute": number(absolute), "inbound_pct": number(inbound, True),
            "alive_pct": number(alive, True), "aging_lt16": number(aging),
            "last_status": None if str(status).startswith("#") else str(status or "").strip() or None,
            "last_status_pct": number(share, True),
        }
        if all(value is None for value in metrics.values()):
            invalid += 1
            continue
        result[str(names[key][0])] = metrics
    return {"rows": result, "matched": len(result), "unmatched": unmatched,
            "ambiguous": len(ambiguous), "invalid": invalid, "source_rows": len(candidates)}


def read_sheet(settings, service_info):
    """Read only A:G from the configured tab; no parcel IDs or employee columns."""
    from google.oauth2.service_account import Credentials
    from google.auth.transport.requests import AuthorizedSession

    sheet_id = str(settings.get("spreadsheet_id", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", sheet_id):
        raise TrackerError("Set the tracker spreadsheet_id in Streamlit Secrets.")
    try:
        gid = int(settings["worksheet_gid"])
    except (KeyError, ValueError, TypeError):
        raise TrackerError("Set worksheet_gid in Streamlit Secrets.") from None
    info = dict(service_info)
    info["token_uri"] = "https://oauth2.googleapis.com/token"
    try:
        credentials = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        with AuthorizedSession(credentials, refresh_timeout=20) as session:
            base = "https://sheets.googleapis.com/v4/spreadsheets/" + sheet_id

            def get(url, params):
                response = session.get(url, params=params, timeout=20)
                if response.status_code in (401, 403):
                    raise TrackerError("Google denied access. Enable Sheets API and share the tracker with the service account as Viewer.")
                if response.status_code == 404:
                    raise TrackerError("Tracker not found or not shared with the service account.")
                if response.status_code != 200:
                    raise TrackerError("Google Sheets could not be checked. The connection will retry automatically.")
                return response.json()

            metadata = get(base, {"fields": "sheets(properties(sheetId,title))"})
            title = next((s["properties"]["title"] for s in metadata.get("sheets", [])
                          if s["properties"]["sheetId"] == gid), None)
            if title is None:
                raise TrackerError("The configured tracker tab was not found.")
            cell_range = "'" + title.replace("'", "''") + "'!A1:G1000"
            data = get(base + "/values/" + quote(cell_range, safe=""),
                       {"valueRenderOption": "FORMATTED_VALUE"})
            values = data.get("values", [])
            if len(values) >= 1000:
                raise TrackerError("Tracker summary reached the read limit. Review the source range before refreshing.")
            return values, title
    except TrackerError:
        raise
    except Exception:
        # Never print auth exceptions, tokens, private keys or raw API responses.
        raise TrackerError("Could not authenticate or read Google Sheets. Check the private service-account configuration.") from None


def fetch_snapshot(settings, service_info, stations, previous=None, reader=read_sheet, now=None):
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    try:
        values, title = reader(settings, service_info)
        parsed = parse_rows(values, stations)
        return {**parsed, "status": "ok", "checked_at": stamp, "fetched_at": stamp,
                "sheet_title": title, "source_url": "https://docs.google.com/spreadsheets/d/" +
                settings["spreadsheet_id"] + "/edit#gid=" + str(settings["worksheet_gid"]),
                "message": "Google Sheets checked successfully."}
    except TrackerError as error:
        old = dict(previous or {})
        return {**old, "rows": old.get("rows", {}), "status": "cached" if old.get("fetched_at") else "error",
                "checked_at": stamp, "message": str(error)}


def embed_snapshot(html, snapshot, view):
    # General account only. Static builds and the SOC account keep null.
    if view != "general" or snapshot is None:
        return html
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    payload = payload.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return html.replace(PAYLOAD_TAG, PAYLOAD_TAG.replace("null</script>", payload + "</script>"), 1)
