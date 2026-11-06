"""Offline checks for CAP validity, warning isolation and administrative areas."""
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from admin_boundaries import contains, match_boundary
from source_freshness import record_health
from weather_cap import CAPResults, active_warning, fetch_cap, parse_cap
from ingest import apply_impact_model, main, new_event, write_impact_csv

NOW = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
URL = "https://www.bmkg.go.id/alerts/nowcast/id/test_alert.xml"


def cap(identifier="one", kind="Alert", refs="", status="Actual", expires="2026-11-06T12:00:00Z"):
    return f'''<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
    <identifier>{identifier}</identifier><sender>BMKG</sender><sent>2026-11-06T09:00:00Z</sent>
    <status>{status}</status><msgType>{kind}</msgType><scope>Public</scope><references>{refs}</references>
    <info><language>id-ID</language><event>Cuaca ekstrem</event><headline>Peringatan dini</headline>
    <severity>Moderate</severity><certainty>Observed</certainty><urgency>Immediate</urgency>
    <effective>2026-11-06T09:00:00Z</effective><expires>{expires}</expires>
    <area><polygon>-7,110 -7,111 -6,111 -6,110 -7,110</polygon></area></info></alert>'''


class MapUpgrades(unittest.TestCase):
    def test_cap_coordinates_expiry_and_actual_only(self):
        record = parse_cap(cap(), URL, NOW)[0][0]
        self.assertEqual(record["polygons"][0][0], [110, -7])
        self.assertTrue(active_warning(record, NOW))
        self.assertFalse(active_warning(record, NOW + timedelta(hours=2)))
        self.assertEqual(parse_cap(cap(status="Test"), URL, NOW)[0], [])
        self.assertEqual(parse_cap(cap(expires="2026-11-06T10:00:00Z"), URL, NOW)[0], [])
        with self.assertRaises(ValueError):
            parse_cap(cap().replace("<area>", "<area><!--").replace("</area>", "--></area>"), URL, NOW)

    def test_update_and_cancel_supersede_old_identifiers(self):
        class HTTP:
            def get(self, url):
                if url == "index":
                    return f'<rss><channel><item><link>{URL}</link></item><item><link>{URL.replace("test", "new")}</link></item></channel></rss>'
                return cap() if url == URL else cap("two", "Update", "BMKG,one,2026-11-06T09:00:00Z")
        result = fetch_cap(HTTP(), {"url":"index"}, NOW)
        self.assertEqual([r["cap_identifier"] for r in result], ["two"])
        self.assertEqual(result.superseded, {"one"})
        self.assertEqual(parse_cap(cap("two", "Cancel", "BMKG,one,date"), URL, NOW), ([], ["one"]))

    def test_bad_detail_url_and_all_details_fail_are_incomplete(self):
        class HTTP:
            def get(self, url):
                if url == "index":
                    return '<rss><channel><item><link>https://evil.example/test_alert.xml</link></item></channel></rss>'
                raise RuntimeError("offline")
        result = fetch_cap(HTTP(), {"url":"index"}, NOW)
        self.assertTrue(result.warnings)
        class Broken(HTTP):
            def get(self, url):
                return f'<rss><channel><item><link>{URL}</link></item></channel></rss>' if url=="index" else super().get(url)
        with self.assertRaises(RuntimeError):
            fetch_cap(Broken(), {"url":"index"}, NOW)

    def test_warning_overlap_count_is_separate_from_impact_and_csv(self):
        record = new_event(**parse_cap(cap(), URL, NOW)[0][0])
        record["polygons"] *= 2
        apply_impact_model(record, {}, [("Inside",-6.5,110.5),("Outside",0,100)])
        self.assertEqual(record["warning_exposure"]["facility_count"], 1)
        self.assertEqual(record["impact"]["total"], 0)
        self.assertEqual(record["impact"]["bands"], [])
        with TemporaryDirectory() as directory:
            self.assertEqual(write_impact_csv(Path(directory)/"impact.csv", [record]), 0)

    def test_polygon_holes_multipolygon_and_real_boundary(self):
        outer = [[100,0],[102,0],[102,2],[100,2],[100,0]]
        hole = [[100.5,.5],[101.5,.5],[101.5,1.5],[100.5,1.5],[100.5,.5]]
        geometry = {"type":"MultiPolygon", "coordinates":[[outer,hole]]}
        self.assertFalse(contains(geometry,1,101))
        self.assertTrue(contains(geometry,.25,100.25))
        boundary = match_boundary({"name":"Cawas","level":"ADM3","lat":-7.7584,"lng":110.7518})
        self.assertIsNotNone(boundary)
        self.assertEqual(str(boundary["year"]), "2019")
        self.assertIsNone(match_boundary({"name":"Aceh","level":"ADM1","lat":4,"lng":97}))

    def test_freshness_never_turns_a_failed_fetch_into_success(self):
        state = {}
        record_health(state, [{"source":"bmkg_cap","state":"ok"}], "first")
        health = [{"source":"bmkg_cap","state":"degraded"}]
        record_health(state, health, "second")
        self.assertEqual(health[0]["last_checked_at"], "second")
        self.assertEqual(health[0]["last_success_at"], "first")
        record_health(state, [{"source":"bmkg_cap","state":"failed"}], "third")
        self.assertEqual(state["source_freshness"]["bmkg_cap"]["last_success_at"], "first")

    def test_failed_cap_retains_only_unexpired_cache_and_no_sla(self):
        now = datetime.now(timezone.utc)
        good = parse_cap(cap(), URL, NOW)[0][0]
        good.update(issued_at=now.isoformat(), updated=now.isoformat(),
                    effective=(now-timedelta(minutes=10)).isoformat(),
                    expires=(now+timedelta(minutes=30)).isoformat())
        expired = dict(good, id="expired", expires=(now-timedelta(seconds=1)).isoformat())
        with TemporaryDirectory() as directory:
            base = Path(directory)
            (base/"facilities.csv").write_text("station_name,latitude,longitude\nInside,-6.5,110.5\n")
            (base/"state.json").write_text(json.dumps({"weather_warning_cache":[good,expired],
                "source_freshness":{"bmkg_cap":{"last_success_at":"previous"}}}))
            cfg={"facilities":"facilities.csv","output":"events.json", "sources":{
                "bmkg_quake":{"enabled":True},"bmkg_cap":{"enabled":True}}}
            with patch("ingest.load_config",return_value=cfg), patch("ingest.fetch_bmkg",return_value=[]), \
                    patch("ingest.fetch_bmkg_cap",side_effect=RuntimeError("offline")):
                self.assertEqual(main(["--config",str(base/"config.yaml")]),0)
            output=json.loads((base/"events.json").read_text())
            self.assertEqual(output["verified_event_count"],0)
            self.assertEqual(output["official_warning_count"],1)
            self.assertTrue(output["events"][0]["cached"])
            self.assertEqual(output["events"][0]["impact"]["total"],0)
            health=next(h for h in output["source_health"] if h["source"]=="bmkg_cap")
            self.assertEqual(health["last_success_at"],"previous")
            self.assertEqual(health["retained_warnings"],1)


if __name__ == '__main__':
    unittest.main()
