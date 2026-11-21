import unittest

from pydantic import ValidationError

from travel_api import Trip
from travel_departures import directory, search


class DepartureTest(unittest.TestCase):
    def test_keyword_search_and_authoritative_station_name(self):
        rows = search("high_speed", "长沙南")["items"]
        station = next(row for row in rows if row["name"] == "长沙南站")
        trip = Trip(travel_mode="high_speed", departure_id=station["id"], origin="随意填写", travelers=4)
        self.assertEqual((trip.origin, trip.budget_scope), ("长沙南站", "group"))

    def test_rejects_wrong_mode_unknown_id_and_invalid_group(self):
        for fields in ({"travel_mode":"air", "departure_id":"rail:KQW"},
                       {"travel_mode":"car", "departure_id":"city:invalid"},
                       {"travel_mode":"high_speed", "departure_id":"rail:KQW", "budget_scope":"person"},
                       {"travelers":0}, {"travelers":11}, {"travelers":1.5}, {"travelers":True}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                Trip(**fields)

    def test_airport_and_city(self):
        rows = search("air","KWE")["items"]
        self.assertTrue(any(row["name"] == "贵阳龙洞堡国际机场" for row in rows))
        self.assertTrue(any(row["kind"] == "city" for row in search("car","贵州")["items"]))
        self.assertTrue(directory()[0])
