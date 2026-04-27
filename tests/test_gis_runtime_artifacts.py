import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.gis_runtime_artifacts import load_or_build_gis_runtime_artifacts


def _feature_collection(features):
    return {"type": "FeatureCollection", "features": features}


class GisRuntimeArtifactsTests(unittest.TestCase):
    def test_load_or_build_runtime_artifacts_reuses_persisted_artifact(self):
        gis_payload = {
            "stations": _feature_collection(
                [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [121.0, 25.0]},
                        "properties": {"id": "station-a", "name": "A", "line_ids": ["c2"]},
                    },
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [121.001, 25.0]},
                        "properties": {"id": "station-b", "name": "B", "line_ids": ["c2"]},
                    },
                ]
            ),
            "lines": _feature_collection(
                [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[121.0, 25.0], [121.001, 25.0]],
                        },
                        "properties": {"line_id": "c2", "line_color": "#ff0000"},
                    }
                ]
            ),
            "station_access_points": _feature_collection(
                [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [121.0, 25.0]},
                        "properties": {"station_id": "station-a", "name": "A1"},
                    }
                ]
            ),
        }
        station_coords_by_id = {
            "station-a": (121.0, 25.0),
            "station-b": (121.001, 25.0),
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            qgis_geojson_dir = project_root / "app" / "data" / "gis"
            qgis_geojson_dir.mkdir(parents=True)

            mocked_walk_graph = {"adjacency": {}}
            mocked_walk_targets = {"target": ["value"]}
            mocked_segment_index = {("c2", "station-a", "station-b"): [(121.0, 25.0), (121.001, 25.0)]}

            with patch("app.services.gis_runtime_artifacts.get_cached_walk_graph", return_value=mocked_walk_graph) as mocked_graph_loader:
                with patch("app.services.gis_runtime_artifacts.build_walk_targets_by_node", return_value=mocked_walk_targets) as mocked_targets_builder:
                    with patch("app.services.gis_runtime_artifacts.load_or_build_geojson_segment_index", return_value=mocked_segment_index) as mocked_segment_builder:
                        first = load_or_build_gis_runtime_artifacts(
                            project_root=project_root,
                            qgis_geojson_dir=qgis_geojson_dir,
                            gis_payload=gis_payload,
                            station_coords_by_id=station_coords_by_id,
                            signature="runtime-signature",
                        )
                        second = load_or_build_gis_runtime_artifacts(
                            project_root=project_root,
                            qgis_geojson_dir=qgis_geojson_dir,
                            gis_payload=gis_payload,
                            station_coords_by_id=station_coords_by_id,
                            signature="runtime-signature",
                        )

        self.assertEqual(first.walk_targets_by_node, second.walk_targets_by_node)
        self.assertEqual(first.geojson_segment_index, second.geojson_segment_index)
        self.assertEqual(mocked_graph_loader.call_count, 1)
        self.assertEqual(mocked_targets_builder.call_count, 1)
        self.assertEqual(mocked_segment_builder.call_count, 1)


if __name__ == "__main__":
    unittest.main()
