import json
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch, MagicMock

from stuck_tracker import (HEADERS, THRESHOLD_HEADERS, PAYLOAD_TAG, SnapshotStore, TrackerError,
                           embed_snapshot, fetch_snapshot, number, parse_rows, parse_threshold_rows, read_sheet, refresh_due)

STATIONS = [["Example DC", -6, 110, 2, 1], ["Other DC", -7, 110, 2, 1],
            ["Example Hub", -6, 110, 0, 1], ["Example First Mile Hub", -6, 110, 1, 1]]
SETTINGS = {"spreadsheet_id": "example_sheet_identifier_123456", "worksheet_gid": 123}
ROWS = [["All Parcels"], HEADERS, ["Nationwide", "999"],
        [" Example   DC ", "543", "146.41%", "80.85%", "439", "SOC_Packed", "69.80%"],
        ["Other DC", "0", "0.00%", "#DIV/0!", "#DIV/0!", "No Data", "0.00%"],
        ["Example Hub", "5", "1%"], ["Example First Mile Hub", "2"], ["No Values DC"]]


def threshold_fixture(items):
    rows = [list(row) + [""] * (8 - len(row)) for row in ROWS]
    rows[1] += THRESHOLD_HEADERS
    for index, item in enumerate(items, 2):
        while len(rows) <= index:
            rows.append([""] * 8)
        rows[index] += list(item)
    return rows


class TrackerTests(unittest.TestCase):
    def test_threshold_membership_is_not_recomputed(self):
        rows = threshold_fixture([["Example DC", "1", "Region 1", "0", "0.00%", "No Data", "0.00%", "0.00%"],
                                  ["Other DC"], ["Example Hub", "999"], ["Missing DC", "1000"]])
        result = parse_threshold_rows(rows, STATIONS)
        self.assertEqual(set(result["rows"]), {"0", "1"})
        self.assertEqual(result["rows"]["0"]["absolute"], 0, "source membership remains authoritative even for zero metrics")
        self.assertIsNone(result["rows"]["1"]["absolute"], "name-only listed SOC is not invented as zero")
        self.assertEqual(result["unmatched"], 2)
        self.assertEqual(result["rows"]["0"]["region"], "Region 1")

    def test_threshold_empty_list_clears_and_duplicates_do_not_overwrite(self):
        self.assertEqual(parse_threshold_rows(threshold_fixture([]), STATIONS)["rows"], {})
        result = parse_threshold_rows(threshold_fixture([["Example DC"], ["EXAMPLE DC"]]), STATIONS)
        self.assertEqual(result["ambiguous"], 1)
        self.assertEqual(result["rows"], {})
        with self.assertRaises(TrackerError):
            parse_threshold_rows(threshold_fixture([["#REF!"]]), STATIONS)

    def test_threshold_failure_retains_its_own_original_timestamp(self):
        rows = threshold_fixture([["Example DC", "10"]])
        good = fetch_snapshot(SETTINGS, {}, STATIONS, reader=lambda *_: (rows, "Summary"),
                              now=datetime(2026, 11, 11, tzinfo=timezone.utc))
        partial = fetch_snapshot(SETTINGS, {}, STATIONS, previous=good,
                                 reader=lambda *_: (ROWS, "Summary"),
                                 now=datetime(2026, 11, 12, tzinfo=timezone.utc))
        self.assertEqual(partial["status"], "ok", "main summary can succeed independently")
        self.assertEqual(partial["threshold"]["status"], "cached")
        self.assertEqual(partial["threshold"]["fetched_at"], good["threshold"]["fetched_at"])
        self.assertNotEqual(partial["fetched_at"], partial["threshold"]["fetched_at"])
        empty = fetch_snapshot(SETTINGS, {}, STATIONS, previous=good,
                               reader=lambda *_: (threshold_fixture([]), "Summary"))
        self.assertEqual(empty["threshold"]["status"], "ok")
        self.assertEqual(empty["threshold"]["rows"], {})
        def failed(*_):
            raise TrackerError("Unavailable")
        cached = fetch_snapshot(SETTINGS, {}, STATIONS, previous=good, reader=failed)
        self.assertEqual(cached["threshold"]["status"], "cached")

    def test_refresh_due_is_independent_of_frequent_map_refreshes(self):
        checked = datetime(2026, 11, 11, 14, 59, tzinfo=timezone.utc)
        snapshot = {"status": "ok", "checked_at": checked.isoformat()}
        for minutes in (10, 60, 600, 1199):
            self.assertFalse(refresh_due(snapshot, checked + timedelta(minutes=minutes)))
        self.assertTrue(refresh_due(snapshot, checked + timedelta(hours=20)))
        self.assertTrue(refresh_due(snapshot, checked + timedelta(days=2)))
        self.assertFalse(refresh_due({"status": "unconfigured"}, checked))
        self.assertFalse(refresh_due(None, checked))
        self.assertTrue(refresh_due({"status": "error", "checked_at": "invalid"}, checked))

    def test_parse_scope_and_missing_values(self):
        result = parse_rows(ROWS, STATIONS)
        self.assertEqual(result["matched"], 2)
        self.assertEqual(result["rows"]["0"]["inbound_pct"], 146.41)
        self.assertEqual(result["rows"]["0"]["absolute"], 543)
        self.assertEqual(result["rows"]["1"]["absolute"], 0)
        self.assertIsNone(result["rows"]["1"]["aging_lt16"])
        self.assertNotIn("2", result["rows"])
        self.assertEqual(result["unmatched"], 2)

    def test_no_guessing_and_no_duplicate_overwrite(self):
        parsed = parse_rows(ROWS + [["EXAMPLE DC", "99"], ["Other 2 DC", "10"]], STATIONS)
        self.assertNotIn("0", parsed["rows"])
        self.assertEqual(parsed["ambiguous"], 1)
        parsed = parse_rows(ROWS, STATIONS + [["example dc", -5, 110, 2, 1]])
        self.assertNotIn("0", parsed["rows"])
        self.assertEqual(parsed["ambiguous"], 1)

    def test_invalid_numeric_cells(self):
        for raw in ["", "#REF!", "#DIV/0!", "NaN", "Infinity", "-1", "1.2", "30%"]:
            self.assertIsNone(number(raw))
        self.assertIsNone(number("0.55", percent=True))
        self.assertEqual(number("15,034"), 15034)
        self.assertEqual(number("0"), 0)
        self.assertEqual(number("211.11%", True), 211.11)

    def test_layout_or_empty_data_fails(self):
        for values in [[], [["SOC", "other"]], [HEADERS, ["Name Only DC"]]]:
            with self.assertRaises(TrackerError):
                parse_rows(values, STATIONS)

    def test_failed_refresh_retains_original_timestamp(self):
        now = datetime(2026, 11, 10, tzinfo=timezone.utc)
        good = fetch_snapshot(SETTINGS, {}, STATIONS, reader=lambda *_: (ROWS, "Stuck Alert (2)"), now=now)
        def failed(*_):
            raise TrackerError("Unavailable")
        cached = fetch_snapshot(SETTINGS, {}, STATIONS, previous=good, reader=failed,
                                now=datetime(2026, 11, 11, tzinfo=timezone.utc))
        self.assertEqual(cached["status"], "cached")
        self.assertEqual(cached["fetched_at"], good["fetched_at"])
        self.assertNotEqual(cached["checked_at"], good["checked_at"])
        self.assertEqual(cached["rows"], good["rows"])
        first = fetch_snapshot(SETTINGS, {}, STATIONS, reader=failed)
        self.assertEqual(first["status"], "error")
        self.assertEqual(first["rows"], {})

    def test_memory_cache_isolates_sources(self):
        store = SnapshotStore()
        good = store.refresh("one", SETTINGS, {}, STATIONS, reader=lambda *_: (ROWS, "Summary"))
        self.assertEqual(good["status"], "ok")
        def failed(*_):
            raise TrackerError("Unavailable")
        self.assertEqual(store.refresh("one", SETTINGS, {}, STATIONS, reader=failed)["status"], "cached")
        self.assertEqual(store.refresh("two", SETTINGS, {}, STATIONS, reader=failed)["status"], "error")

    def test_embedding_general_only_and_script_escape(self):
        snapshot = {"rows": {"0": {"last_status": "</script><script>alert(1)</script>"}}}
        html = embed_snapshot(PAYLOAD_TAG, snapshot, "general")
        self.assertNotIn("<script>alert", html)
        self.assertEqual(json.loads(html.split(">", 1)[1].rsplit("</script>", 1)[0]), snapshot)
        self.assertEqual(embed_snapshot(PAYLOAD_TAG, snapshot, "soc"), PAYLOAD_TAG)
        self.assertEqual(embed_snapshot(PAYLOAD_TAG, None, "general"), PAYLOAD_TAG)

    def test_api_tab_scope_bounded_range_and_denied_access(self):
        creds, session_class = MagicMock(), MagicMock()
        session = session_class.return_value.__enter__.return_value
        meta = MagicMock(status_code=200)
        meta.json.return_value = {"sheets": [{"properties": {"sheetId": 123, "title": "Summary's Tab"}}]}
        cells = MagicMock(status_code=200)
        cells.json.return_value = {"values": ROWS}
        session.get.side_effect = [meta, cells]
        modules = {"google": MagicMock(), "google.oauth2": MagicMock(),
                   "google.oauth2.service_account": MagicMock(Credentials=creds),
                   "google.auth": MagicMock(), "google.auth.transport": MagicMock(),
                   "google.auth.transport.requests": MagicMock(AuthorizedSession=session_class)}
        with patch.dict("sys.modules", modules):
            values, title = read_sheet(SETTINGS, {"token_uri": "https://untrusted.invalid"})
            self.assertEqual(values, ROWS)
            self.assertEqual(title, "Summary's Tab")
            self.assertEqual(creds.from_service_account_info.call_args.kwargs["scopes"],
                             ["https://www.googleapis.com/auth/spreadsheets.readonly"])
            self.assertEqual(creds.from_service_account_info.call_args.args[0]["token_uri"],
                             "https://oauth2.googleapis.com/token")
            url = session.get.call_args_list[1].args[0]
            self.assertTrue(url.endswith("%27Summary%27%27s%20Tab%27%21A1%3AP1000"), "the bounded read stops before employee contact column Q")
            self.assertEqual(session.get.call_args.kwargs["params"]["valueRenderOption"], "FORMATTED_VALUE")
            session.get.side_effect = [MagicMock(status_code=403)]
            with self.assertRaisesRegex(TrackerError, "denied access"):
                read_sheet(SETTINGS, {})


if __name__ == "__main__":
    unittest.main()
