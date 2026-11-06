import unittest
from news import BulletinHTML
from news_estimates import PlaceMatcher, estimate_news_locations


PLACES = [
    [1, "Aceh", "ADM1", "01", "", 4.6, 96.9, []],
    [2, "Kabupaten Aceh Selatan", "ADM2", "01", "1103", 3.16, 97.4, []],
    [3, "Kecamatan Trumon Tengah", "ADM3", "01", "1103", 2.91, 97.65, []],
    [4, "Kecamatan Trumon", "ADM3", "01", "1103", 2.66, 97.72, []],
    [5, "Kecamatan Lembo", "ADM3", "21", "7212", -2.12, 121.26, []],
    [6, "Kecamatan Lembo", "ADM3", "22", "", -3.78, 122.33, []],
    [7, "Sulawesi Tengah", "ADM1", "21", "", -.91, 122.35, []],
    [8, "Kabupaten Morowali Utara", "ADM2", "21", "7212", -1.72, 121.24, []],
    [9, "Kabupaten Bandung", "ADM2", "30", "3204", -7.1, 107.6, []],
    [10, "Kota Bandung", "ADM2", "30", "3273", -6.9, 107.6, []],
    [11, "Jakarta", "ADM1", "04", "", -6.2, 106.8, []],
]


class EstimateTests(unittest.TestCase):
    def setUp(self):
        self.matcher = PlaceMatcher(PLACES)

    def test_specific_districts_replace_parent_center_and_dateline_is_ignored(self):
        result = self.matcher.infer("Situasi Bencana", [
            "JAKARTA – Banjir di Kabupaten Aceh Selatan, Provinsi Aceh.",
            "Banjir berdampak di Kecamatan Trumon Tengah dan Kecamatan Trumon."])
        self.assertEqual({p["id"] for p in result["points"]}, {"3", "4"})
        self.assertTrue(all(p["screenable"] for p in result["points"]))

    def test_ambiguous_district_is_not_guessed(self):
        result = self.matcher.infer("Banjir di Kecamatan Lembo", [])
        self.assertEqual(result["points"], [])
        self.assertEqual(result["unresolved_names"], ["lembo"])

    def test_province_context_resolves_duplicate_district(self):
        result = self.matcher.infer("Karhutla", [
            "Karhutla di Kabupaten Morowali Utara, Provinsi Sulawesi Tengah.",
            "Karhutla terjadi di Kecamatan Lembo."])
        self.assertEqual([p["id"] for p in result["points"]], ["5"])
        self.assertEqual(result["points"][0]["confidence"], "explicit administrative name")

    def test_province_only_is_marker_not_local_impact_zone(self):
        result = self.matcher.infer("Banjir di Provinsi Aceh", [])
        self.assertEqual(len(result["points"]), 1)
        self.assertFalse(result["points"][0]["screenable"])
        self.assertIn("not an impact probability", result["limitations"])

    def test_city_and_regency_are_distinguished(self):
        for prefix, expected in (("Kota", "10"), ("Kabupaten", "9")):
            result = self.matcher.infer(f"Banjir di {prefix} Bandung", [])
            self.assertEqual([p["id"] for p in result["points"]], [expected])

    def test_briefing_location_does_not_become_impact_area(self):
        self.assertEqual(self.matcher.infer("Rapat kesiapan", [
            "Rapat membahas banjir di kantor pusat Jakarta."])["points"], [])

    def test_topic_change_without_province_name_resets_regency_context(self):
        result = self.matcher.infer("Bencana di Indonesia", [
            "Karhutla di Kabupaten Morowali Utara, Provinsi Sulawesi Tengah.",
            "Banjir di Kabupaten Aceh Selatan."])
        self.assertEqual({p["id"] for p in result["points"]}, {"8", "2"})

    def test_body_excludes_caption_and_related_news(self):
        html = '<h1>Banjir</h1><p>Foto: Kabupaten Bandung</p><article><p>Banjir di Kabupaten Aceh Selatan.</p></article><footer><p>Banjir Jakarta</p></footer>'
        parsed = BulletinHTML(html)
        self.assertEqual(parsed.paragraphs, ["Banjir di Kabupaten Aceh Selatan."])
        self.assertEqual([p["id"] for p in self.matcher.infer("Banjir", parsed.paragraphs)["points"]], ["2"])

    def test_real_gazetteer_contains_indonesia_only_and_can_match_aceh(self):
        result = estimate_news_locations("Banjir di Kabupaten Aceh Selatan, Provinsi Aceh")
        self.assertTrue(any(p["name"] == "Kabupaten Aceh Selatan" for p in result["points"]))
        self.assertTrue(all(94 < p["lng"] < 142 and -12 < p["lat"] < 7 for p in result["points"]))


if __name__ == "__main__":
    unittest.main()
