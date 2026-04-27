import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.domain.models import Line
from app.domain.models import Station
from app.domain.models import SubwayNetwork
from app.services import gis_loader
from app.services.gis_loader import build_gis_payload
from app.services.gis_loader import get_cached_walk_graph


def _write_geojson(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class GisLoaderTests(unittest.TestCase):
    def test_build_gis_payload_can_skip_merging_missing_qgis_stations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            geojson_dir = Path(temp_dir)
            _write_geojson(
                geojson_dir / "stations.geojson",
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [121.5, 25.05]},
                            "properties": {"id": "present-station", "name": "Present Station"},
                        }
                    ],
                },
            )
            _write_geojson(
                geojson_dir / "lines.geojson",
                {"type": "FeatureCollection", "features": []},
            )
            network = SubwayNetwork(
                stations={
                    "present-station": Station(id="present-station", name="Present Station", x=100, y=100),
                    "fallback-station": Station(id="fallback-station", name="Fallback Station", x=200, y=200),
                },
                lines={"blue": Line(id="blue", name="Blue Line", color="#007ec7")},
                station_lines=[],
                segments=[],
                transfers=[],
                station_to_lines={
                    "present-station": {"blue"},
                    "fallback-station": {"blue"},
                },
            )

            payload = build_gis_payload(
                network=network,
                qgis_geojson_dir=geojson_dir,
                map_width=1000,
                map_height=1000,
                fallback_bounds=(121.0, 25.0, 122.0, 26.0),
                include_station_access_points=False,
                include_walk_network=False,
                merge_missing_stations=False,
            )

            station_ids = [
                feature.get("properties", {}).get("id")
                for feature in payload["stations"]["features"]
            ]

            self.assertEqual(payload["source"], "qgis_geojson_partial")
            self.assertEqual(station_ids, ["present-station"])

    def test_get_cached_walk_graph_reuses_graph_for_unchanged_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            geojson_dir = Path(temp_dir)
            _write_geojson(
                geojson_dir / "walk_network.geojson",
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[121.5, 25.05], [121.5005, 25.0505]],
                            },
                            "properties": {},
                        }
                    ],
                },
            )

            with patch("app.services.gis_loader.build_walk_graph", wraps=get_cached_walk_graph.__globals__["build_walk_graph"]) as mocked_build:
                first = get_cached_walk_graph(geojson_dir)
                second = get_cached_walk_graph(geojson_dir)

            self.assertIs(first, second)
            self.assertEqual(mocked_build.call_count, 1)

    def test_get_cached_walk_graph_reuses_persisted_cache_after_memory_clear(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            geojson_dir = Path(temp_dir)
            _write_geojson(
                geojson_dir / "walk_network.geojson",
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[121.5, 25.05], [121.5005, 25.0505]],
                            },
                            "properties": {},
                        }
                    ],
                },
            )

            gis_loader._load_walk_graph_cached.cache_clear()
            with patch("app.services.gis_loader.build_walk_graph", wraps=gis_loader.build_walk_graph) as mocked_build:
                first = get_cached_walk_graph(geojson_dir)
                gis_loader._load_walk_graph_cached.cache_clear()
                second = get_cached_walk_graph(geojson_dir)

            self.assertEqual(first.adjacency, second.adjacency)
            self.assertEqual(mocked_build.call_count, 1)


if __name__ == "__main__":
    unittest.main()
