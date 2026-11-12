import json
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch, MagicMock

from stuck_tracker import (HEADERS, THRESHOLD_HEADERS, PAYLOAD_TAG, SnapshotStore, TrackerError,
                           embed_snapshot, fetch_snapshot, number, parse_rows, parse_threshold_rows, read_sheet, refresh_due)

STATIONS = [["Example DC", -6, 110, 2, 1], ["Other DC", -7, 110, 2, 1],
            ["Example Hub", -6, 110, 0, 1], ["Example First Mile Hub", -6, 110, 1, 1]]
SETTINGS = {"spreadsheet_id": "example_sheet_identifier_123456", "worksheet_gid": 123}
class FakeRefreshError(Exception):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable

class FakeTransportError(Exception):
    pass

class FakeRequestError(Exception):
    pass
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
    def test_store_uses_twenty_hours_for_success_and_five_minutes_after_failure(self):
        store = SnapshotStore()
        now = datetime(2026, 11, 12, tzinfo=timezone.utc)
        reader = MagicMock(return_value=(threshold_fixture([["Example DC", "10"]]), "Summary"))
        def check(at, force=False):
            return store.refresh("source", SETTINGS, {}, STATIONS, reader=reader, force=force, now=at)
        good = check(now)
        self.assertEqual(good["status"], "ok")
        check(now + timedelta(hours=19))
        self.assertEqual(reader.call_count, 1, "normal reruns do not make extra reads")
        reader.side_effect = TrackerError("Temporary network error")
        failed = check(now + timedelta(hours=20))
        self.assertEqual(failed["status"], "cached")
        self.assertEqual(failed["top_stuck"], good["top_stuck"])
        self.assertEqual(failed["fetched_at"], good["fetched_at"])
        check(now + timedelta(hours=20, minutes=4))
        self.assertEqual(reader.call_count, 2, "failed attempts have a shared retry delay")
        reader.side_effect = None
        recovered = check(now + timedelta(hours=20, minutes=5))
        self.assertEqual(recovered["status"], "ok")
        check(now + timedelta(hours=21))
        self.assertEqual(reader.call_count, 3, "successful recovery resets the 20-hour interval")
        check(now + timedelta(hours=21), force=True)
        self.assertEqual(reader.call_count, 4, "manual refresh bypasses the normal interval")

    def test_first_failure_and_partial_table_failure_retry_without_fake_data(self):
        now = datetime(2026, 11, 12, tzinfo=timezone.utc)
        store = SnapshotStore()
        reader = MagicMock(side_effect=TrackerError("Unavailable"))
        failed = store.refresh("source", SETTINGS, {}, STATIONS, reader=reader, now=now, force=False)
        self.assertEqual(failed["rows"], {})
        self.assertNotIn("top_stuck", failed)
        self.assertFalse(refresh_due(failed, now + timedelta(minutes=4)))
        self.assertTrue(refresh_due(failed, now + timedelta(minutes=5)))
        partial = fetch_snapshot(SETTINGS, {}, STATIONS, reader=lambda *_:(ROWS,"Summary"), now=now)
        self.assertEqual(partial["status"], "ok")
        self.assertTrue(refresh_due(partial, now + timedelta(minutes=5)))

    def test_top_stuck_ranks_full_summary_including_unmapped_socs(self):
        rows = [["All Parcels"], HEADERS, ["Nationwide", "999999"],
                ["Example DC", "1,500"], ["Unknown DC", "3000"],
                ["Bad DC", "#DIV/0!"], ["Blank DC"], ["Example Hub", "9999"],
                ["Duplicate DC", "7000"], ["DUPLICATE DC", "7001"]]
        rows += [[f"SOC {i:02} DC", str(1000 - i)] for i in range(12)]
        result = parse_rows(rows, STATIONS)
        top = result["top_stuck"]
        self.assertEqual(len(top), 10)
        self.assertEqual(top[0], {"name": "Unknown DC", "absolute": 3000, "station_index": None})
        self.assertEqual(top[1], {"name": "Example DC", "absolute": 1500, "station_index": 0})
        self.assertEqual(result["ranked_socs"], 14)
        self.assertTrue(all(top[i]["absolute"] >= top[i+1]["absolute"] for i in range(9)))

    def test_top_stuck_preserves_zero_and_orders_ties(self):
        result = parse_rows([HEADERS, ["Zed DC", "0"], ["Alpha DC", "0"],
                             ["Invalid DC", "-1"], ["Fraction DC", "1.5"]], STATIONS)
        self.assertEqual([r["name"] for r in result["top_stuck"]], ["Alpha DC", "Zed DC"])

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
        self.assertEqual(cached["top_stuck"], good["top_stuck"])
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
                   "google.auth.exceptions": MagicMock(RefreshError=FakeRefreshError, TransportError=FakeTransportError),
                   "requests": MagicMock(), "requests.exceptions": MagicMock(RequestException=FakeRequestError),
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
            for error, message in [(FakeRefreshError("PRIVATE TOKEN"), "rejected service-account"),
                                   (FakeRefreshError("PRIVATE TOKEN", retryable=True), "temporarily unavailable"),
                                   (FakeTransportError("PRIVATE KEY"), "temporary network"),
                                   (FakeRequestError("PRIVATE HTTP DATA"), "temporary network"),
                                   (RuntimeError("PRIVATE RESPONSE"), "could not finish")]:
                session.get.side_effect = error
                with self.assertRaisesRegex(TrackerError, message) as caught:
                    read_sheet(SETTINGS, {})
                self.assertNotIn("PRIVATE", str(caught.exception))
            creds.from_service_account_info.side_effect = ValueError("PRIVATE KEY")
            with self.assertRaisesRegex(TrackerError, "configuration is invalid") as caught:
                read_sheet(SETTINGS, {})
            self.assertNotIn("PRIVATE", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
