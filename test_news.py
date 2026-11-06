import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from ingest import apply_impact_model, fetch_bnpb, fetch_gdelt, load_config, recent_cached_news
from news import bulletin_url, fetch_bnpb_bulletins, publication_time

BASE = "https://www.bnpb.go.id/berita"
ARTICLE = BASE + "/banjir-aceh"
NOW = datetime(2026, 11, 6, 7, tzinfo=timezone.utc)


class FakeHTTP:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url):
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page


def pages(date="05 Nov 2026 11:11 WIB"):
    return {
        BASE: '<a href="/berita/banjir-aceh"><img></a>'
              '<a href="/berita/banjir-aceh">Banjir <b>Aceh</b></a>'
              '<a href="/berita/banjir-aceh?utm_source=x">Banjir Aceh</a>'
              '<a href="https://evil.example/berita/banjir">Banjir</a>',
        ARTICLE: f'<h1>Banjir Aceh &amp; respons</h1><p>{date}</p>',
    }


class NewsTests(unittest.TestCase):
    settings = {"url": BASE, "lookback_hours": 72, "max_articles": 8}

    def test_visible_date_uses_wib(self):
        self.assertEqual(publication_time("05 Nov 2026 11:11 WIB").isoformat(),
                         "2026-11-05T11:11:00+07:00")
        self.assertIsNone(publication_time("31 Feb 2026 11:11 WIB"))
        self.assertIsNone(publication_time("no date"))

    def test_same_article_has_stable_identity_and_no_geometry(self):
        http = FakeHTTP(pages())
        records = fetch_bnpb_bulletins(http, self.settings, NOW)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["name"], "Banjir Aceh & respons")
        self.assertEqual(record["hazard"], "flood")
        self.assertEqual(record["status"], "unverified")
        self.assertIsNone(record["started"])
        self.assertIsNone(record["lat"])
        self.assertEqual(record["id"], fetch_bnpb_bulletins(http, self.settings, NOW)[0]["id"])

    def test_old_and_future_publications_are_not_fresh_news(self):
        for date in ("01 Nov 2026 11:11 WIB", "07 Nov 2026 11:11 WIB"):
            with self.subTest(date=date):
                self.assertEqual(fetch_bnpb_bulletins(FakeHTTP(pages(date)), self.settings, NOW), [])

    def test_daily_bulletins_are_discovered(self):
        html = pages()
        html[BASE] = '<a href="/berita/banjir-aceh">Perkembangan Situasi dan Penanganan Bencana di Tanah Air</a>'
        self.assertEqual(len(fetch_bnpb_bulletins(FakeHTTP(html), self.settings, NOW)), 1)

    def test_layout_changes_are_feed_failures(self):
        for html in ({BASE: "<p>maintenance</p>"}, pages("no date")):
            with self.subTest(html=html):
                with self.assertRaises((ValueError, RuntimeError)):
                    fetch_bnpb_bulletins(FakeHTTP(html), self.settings, NOW)

    def test_partial_failure_keeps_successful_articles_and_reports_warning(self):
        html = pages()
        html[BASE] += '<a href="/berita/longsor">Longsor Jawa</a>'
        html[BASE + "/longsor"] = RuntimeError("upstream unavailable")
        result = fetch_bnpb_bulletins(FakeHTTP(html), self.settings, NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result.warnings), 1)

    def test_request_budget_keeps_completed_articles(self):
        html = pages()
        html[BASE] += '<a href="/berita/longsor">Longsor Jawa</a>'
        # Index + first article finish; do not start another request after budget.
        with patch("news.time.monotonic", side_effect=[0, 1, 31]):
            result = fetch_bnpb_bulletins(FakeHTTP(html), self.settings, NOW)
        self.assertEqual(len(result), 1)
        self.assertIn("budget", result.warnings[0])

    def test_cache_preserves_dates_and_excludes_old_or_undated_reports(self):
        records = fetch_bnpb_bulletins(FakeHTTP(pages()), self.settings, NOW)
        recent = records[0]
        old = dict(recent, published_at="2026-10-01T00:00:00+07:00")
        undated = dict(recent, published_at=None, updated=None)
        confirmed = dict(recent, status="active")
        recovered = recent_cached_news([recent, old, undated, confirmed], self.settings, NOW)
        self.assertEqual(len(recovered), 1)
        self.assertTrue(recovered[0]["cached"])
        self.assertEqual(recovered[0]["published_at"], recent["published_at"])
        self.assertIsNone(recovered[0]["lat"])
        self.assertNotIn("cached", recent)

    def test_only_bnpb_article_links_are_followed(self):
        for url in ("javascript:alert(1)", "https://evil.example/berita/banjir", "/berita", "//www.bnpb.go.id.evil.example/berita/banjir"):
            self.assertIsNone(bulletin_url(url, BASE))
        self.assertEqual(bulletin_url("https://bnpb.go.id//berita/banjir-aceh/?x=1#top", BASE), ARTICLE)

    def test_news_has_zero_facility_impact(self):
        # Exercise the actual adapter/model with a current fixture, no network.
        from datetime import timedelta
        date = (datetime.now(timezone.utc) - timedelta(hours=1)).astimezone(
            publication_time("05 Nov 2026 11:11 WIB").tzinfo)
        date_text = date.strftime("%d") + " " + ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"][date.month - 1] + date.strftime(" %Y %H:%M WIB")
        cfg = load_config(Path(__file__).parent / "config.yaml")
        cfg["sources"]["bnpb_news"] = self.settings
        record = fetch_bnpb(FakeHTTP(pages(date_text)), cfg)[0]
        apply_impact_model(record, cfg, [("Test facility", 5.0, 97.0)])
        self.assertEqual(record["impact"]["total"], 0)
        self.assertEqual(record["impact"]["bands"], [])

    def test_gdelt_total_failure_is_not_a_healthy_empty_feed(self):
        class BrokenGDELT:
            def get_json(self, *args):
                raise RuntimeError("rate limited")
        with self.assertRaises(RuntimeError):
            fetch_gdelt(BrokenGDELT(), {"sources": {"gdelt_news": {"url": "test", "queries": ["a", "b"]}}})

    def test_gdelt_rate_limit_defers_remaining_queries(self):
        calls = []
        class RateLimitedGDELT:
            def get_json(self, url, params):
                calls.append(params["query"])
                raise RuntimeError("HTTP Error 429: Too Many Requests")
        with self.assertRaises(RuntimeError):
            fetch_gdelt(RateLimitedGDELT(), {"sources": {"gdelt_news": {
                "url": "test", "queries": ["a", "b", "c"]}}})
        self.assertEqual(calls, ["a"])

    def test_gdelt_partial_failure_keeps_leads(self):
        class PartialGDELT:
            def get_json(self, url, params):
                if params["query"] == "a":
                    raise RuntimeError("rate limited")
                return {"articles": [{"title": "Banjir Aceh", "url": ARTICLE, "seendate": "20261105T040000Z"}]}
        result = fetch_gdelt(PartialGDELT(), {"sources": {"gdelt_news": {"url": "test", "queries": ["a", "b"]}}})
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result.warnings), 1)
        self.assertIsNone(result[0]["started"])
        self.assertEqual(result[0]["discovered_at"], "20261105T040000Z")

    def test_all_automatic_sources_failed_keeps_previous_snapshot(self):
        from ingest import main
        with TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "facilities.csv").write_text(
                "station_name,latitude,longitude\nTest facility,5,97\n", encoding="utf-8")
            output = base / "events.json"
            output.write_text('{"previous_snapshot":true}', encoding="utf-8")
            cfg = {"facilities": "facilities.csv", "output": "events.json",
                   "sources": {"bmkg_quake": {"enabled": True}}}
            with patch("ingest.load_config", return_value=cfg), patch(
                    "ingest.fetch_bmkg", side_effect=RuntimeError("offline")):
                result = main(["--config", str(base / "config.yaml")])
            self.assertEqual(result, 2)
            self.assertEqual(output.read_text(encoding="utf-8"), '{"previous_snapshot":true}')

    def test_failed_news_source_retains_cached_report_in_output(self):
        import json
        from ingest import main
        with TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "facilities.csv").write_text(
                "station_name,latitude,longitude\nTest facility,5,97\n", encoding="utf-8")
            record = dict(fetch_bnpb_bulletins(FakeHTTP(pages()), self.settings, NOW)[0])
            record["published_at"] = record["updated"] = datetime.now(timezone.utc).isoformat()
            (base / "state.json").write_text(json.dumps({
                "news_cache": {"bnpb_news": [record]}}), encoding="utf-8")
            cfg = {"facilities": "facilities.csv", "output": "events.json",
                   "sources": {"bmkg_quake": {"enabled": True},
                               "bnpb_news": {"enabled": True}}}
            with patch("ingest.load_config", return_value=cfg), patch(
                    "ingest.fetch_bmkg", return_value=[]), patch(
                    "ingest.fetch_bnpb", side_effect=RuntimeError("HTTP Error 503")):
                result = main(["--config", str(base / "config.yaml")])
            payload = json.loads((base / "events.json").read_text(encoding="utf-8"))
            self.assertEqual(result, 0)
            self.assertEqual(payload["events"][0]["published_at"], record["published_at"])
            self.assertTrue(payload["events"][0]["cached"])
            self.assertEqual(payload["events"][0]["impact"]["total"], 0)
            health = next(h for h in payload["source_health"] if h["source"] == "bnpb_news")
            self.assertEqual(health["state"], "failed")
            self.assertEqual(health["retained_news"], 1)


if __name__ == "__main__":
    unittest.main()
