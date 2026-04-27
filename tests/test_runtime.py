import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings
from app.services import runtime


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class RuntimeCacheTests(unittest.TestCase):
    def test_get_route_engine_refreshes_when_network_file_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            data_file = temp_path / "network.json"
            positions_file = temp_path / "positions.json"
            enrichment_file = temp_path / "enrichment.json"

            _write_json(
                data_file,
                {
                    "stations": [
                        {"id": "A", "name": "Alpha", "x": 0, "y": 0},
                        {"id": "B", "name": "Beta", "x": 10, "y": 0},
                    ],
                    "lines": [{"id": "red", "name": "Red Line", "color": "#f00"}],
                    "station_lines": [
                        {"station_id": "A", "line_id": "red", "seq": 1},
                        {"station_id": "B", "line_id": "red", "seq": 2},
                    ],
                    "segments": [],
                    "transfers": [],
                },
            )
            _write_json(positions_file, {"stations": []})
            _write_json(enrichment_file, {})

            settings = Settings(
                data_file=data_file,
                station_positions_file=positions_file,
                osm_enrichment_file=enrichment_file,
            )

            runtime.refresh_runtime_caches()
            with patch("app.services.runtime.get_settings", return_value=settings):
                engine = runtime.get_route_engine()
                with self.assertRaises(ValueError):
                    engine.find_route("A", "B")

                _write_json(
                    data_file,
                    {
                        "stations": [
                            {"id": "A", "name": "Alpha", "x": 0, "y": 0},
                            {"id": "B", "name": "Beta", "x": 10, "y": 0},
                        ],
                        "lines": [{"id": "red", "name": "Red Line", "color": "#f00"}],
                        "station_lines": [
                            {"station_id": "A", "line_id": "red", "seq": 1},
                            {"station_id": "B", "line_id": "red", "seq": 2},
                        ],
                        "segments": [
                            {"line_id": "red", "from_station_id": "A", "to_station_id": "B", "travel_sec": 60}
                        ],
                        "transfers": [],
                    },
                )

                refreshed_engine = runtime.get_route_engine()
                result = refreshed_engine.find_route("A", "B")

            self.assertEqual(result.station_ids, ["A", "B"])


if __name__ == "__main__":
    unittest.main()
