import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.subway_loader import merge_network_enrichment


class SubwayLoaderTests(unittest.TestCase):
    def test_merge_network_enrichment_adds_stops_walk_transfers_and_metadata(self):
        raw = {
            "stations": [{"id": "A", "name": "Alpha", "x": 10, "y": 20}],
            "lines": [{"id": "red", "name": "Red Line", "color": "#f00"}],
            "station_lines": [{"station_id": "A", "line_id": "red", "seq": 1}],
            "segments": [],
            "transfers": [],
            "metadata": {"source": "base"},
        }
        enrichment = {
            "stops": [
                {
                    "id": "A",
                    "station_id": "A",
                    "name": "Alpha",
                    "latitude": 25.0,
                    "longitude": 121.5,
                }
            ],
            "walk_transfers": [
                {
                    "from_station_id": "A",
                    "to_station_id": "B",
                    "duration_sec": 180,
                }
            ],
            "metadata": {
                "source": "osm",
                "station_osm_mapping": {"A": {"osm_node_id": 123}},
            },
        }

        merged = merge_network_enrichment(raw, enrichment)

        self.assertEqual(merged["stops"][0]["id"], "A")
        self.assertEqual(merged["walk_transfers"][0]["to_station_id"], "B")
        self.assertEqual(merged["metadata"]["source"], "osm")
        self.assertEqual(merged["metadata"]["station_osm_mapping"]["A"]["osm_node_id"], 123)

    def test_merge_network_enrichment_replaces_duplicate_stop_and_walk_transfer(self):
        raw = {
            "stations": [],
            "lines": [],
            "station_lines": [],
            "segments": [],
            "transfers": [],
            "stops": [
                {
                    "id": "A",
                    "station_id": "A",
                    "name": "Alpha Old",
                    "latitude": 1.0,
                    "longitude": 2.0,
                }
            ],
            "walk_transfers": [
                {
                    "from_station_id": "A",
                    "to_station_id": "B",
                    "duration_sec": 300,
                }
            ],
        }
        enrichment = {
            "stops": [
                {
                    "id": "A",
                    "station_id": "A",
                    "name": "Alpha New",
                    "latitude": 25.0,
                    "longitude": 121.5,
                }
            ],
            "walk_transfers": [
                {
                    "from_station_id": "A",
                    "to_station_id": "B",
                    "duration_sec": 180,
                }
            ],
        }

        merged = merge_network_enrichment(raw, enrichment)

        self.assertEqual(len(merged["stops"]), 1)
        self.assertEqual(merged["stops"][0]["name"], "Alpha New")
        self.assertEqual(len(merged["walk_transfers"]), 1)
        self.assertEqual(merged["walk_transfers"][0]["duration_sec"], 180)


if __name__ == "__main__":
    unittest.main()
