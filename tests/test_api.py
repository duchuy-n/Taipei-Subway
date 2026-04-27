import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.responses import FileResponse
from starlette.responses import RedirectResponse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.routes import BuilderLinePayload
from app.api.routes import BuilderNetworkSaveRequest
from app.api.routes import BuilderStationLinePayload
from app.api.routes import BuilderStationPayload
from app.api.routes import CalibrationSaveRequest
from app.api.routes import GisPointRouteRequest
from app.api.routes import GisRouteContext
from app.api.routes import GisStationPositionPayload
from app.api.routes import GisStationSaveRequest
from app.api.routes import PointRouteRequest
from app.api.routes import RouteRequest
from app.api.routes import _route_candidate_evaluation
from app.api.routes import get_builder_network
from app.api.routes import get_gis_route_for_points
from app.api.routes import get_gis_network
from app.api.routes import get_network
from app.api.routes import get_route_for_points
from app.api.routes import get_route
from app.api.routes import delete_gis_station
from app.api.routes import save_gis_stations
from app.api.routes import save_builder_network
from app.api.routes import save_calibration
from app.config import get_settings
from app.domain.models import Line
from app.domain.models import RouteResult
from app.domain.models import RouteStep
from app.domain.models import Station
from app.domain.models import SubwayNetwork
from app.services.walk_network import build_walk_graph
from app.main import builder as builder_page
from app.main import calibrate as calibrate_page
from app.main import gis as gis_page
from app.main import health_check
from app.main import index as index_page


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_endpoint(self):
        body = await health_check()

        self.assertEqual(body, {"status": "ok"})

    async def test_root_route_serves_gis_shell(self):
        response = await index_page()

        self.assertIsInstance(response, FileResponse)
        self.assertTrue(str(response.path).endswith("app\\static\\gis-studio\\index.html"))

    async def test_gis_route_serves_gis_shell(self):
        response = await gis_page()

        self.assertIsInstance(response, FileResponse)
        self.assertTrue(str(response.path).endswith("app\\static\\gis-studio\\index.html"))

    async def test_legacy_pages_redirect_to_root_gis(self):
        for page in (builder_page, calibrate_page):
            with self.subTest(page=page.__name__):
                response = await page()
                self.assertIsInstance(response, RedirectResponse)
                self.assertEqual(response.headers.get("location"), "/")

    async def test_gis_network_endpoint_returns_geojson_payload(self):
        body = await get_gis_network()
        station_ids = {
            feature.get("properties", {}).get("id")
            for feature in body["stations"]["features"]
        }

        self.assertIn("source", body)
        self.assertIn("bounds", body)
        self.assertIn("stations", body)
        self.assertIn("lines", body)
        self.assertIn("station_catalog", body)
        self.assertIn("line_catalog", body)
        self.assertNotIn("station_access_points", body)
        self.assertNotIn("walk_network", body)
        self.assertEqual(body["stations"]["type"], "FeatureCollection")
        self.assertEqual(body["lines"]["type"], "FeatureCollection")
        self.assertEqual(body["source"], "qgis_geojson_partial")
        self.assertGreaterEqual(len(body["stations"]["features"]), 100)
        self.assertGreaterEqual(len(body["station_catalog"]), 150)
        self.assertGreaterEqual(len(body["line_catalog"]), 12)
        self.assertNotIn("taoyuan-sports-park", station_ids)

    async def test_gis_route_points_endpoint_returns_station_route(self):
        body = await get_gis_route_for_points(
            GisPointRouteRequest(
                start_lon=121.5010,
                start_lat=25.0420,
                end_lon=121.5515,
                end_lat=25.0238,
                walking_m_per_sec=1.1,
            )
        )

        self.assertIn("selected_start_station", body)
        self.assertIn("selected_end_station", body)
        self.assertIn("route", body)
        self.assertIn("candidate_pairs", body["route_diagnostics"])
        self.assertGreaterEqual(body["total_journey_time_sec"], body["route"]["total_time_sec"])
        self.assertGreater(len(body["route"]["station_ids"]), 1)

    async def test_gis_route_points_prefers_direct_walk_when_metro_saves_too_little_under_20_min(self):
        network = SubwayNetwork(
            stations={
                "station-a": Station(id="station-a", name="Station A", x=121.5, y=25.042),
                "station-b": Station(id="station-b", name="Station B", x=121.51, y=25.042),
            },
            lines={"blue": Line(id="blue", name="Blue Line", color="#007ec7")},
            station_lines=[],
            segments=[],
            transfers=[],
            station_to_lines={"station-a": {"blue"}, "station-b": {"blue"}},
            metadata={
                "admin_effects": {
                    "closed_segment_keys": ["blue:station-a:station-b"],
                    "scenarios": {"rain_zones": []},
                }
            },
        )
        gis_payload = {
            "source": "qgis_geojson",
            "stations": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [121.5, 25.042]},
                        "properties": {"id": "station-a", "name": "Station A"},
                    },
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [121.51, 25.042]},
                        "properties": {"id": "station-b", "name": "Station B"},
                    },
                ],
            },
            "station_access_points": None,
            "lines": {"type": "FeatureCollection", "features": []},
        }
        dummy_route = RouteResult(
            total_time_sec=5 * 60,
            walking_time_sec=0,
            transfer_count=0,
            stop_count=1,
            station_ids=["station-a", "station-b"],
            line_sequence=["blue"],
            steps=[
                RouteStep(
                    kind="ride",
                    station_id="station-a",
                    line_id="blue",
                    next_station_id="station-b",
                    duration_sec=5 * 60,
                )
            ],
        )

        class DummyEngine:
            def find_route_through_stations(self, station_ids):
                return dummy_route

        context = GisRouteContext(
            payload=gis_payload,
            station_coords_by_id={"station-a": (121.5, 25.042), "station-b": (121.51, 25.042)},
            walk_graph=build_walk_graph(None),
            walk_targets_by_node={},
            station_lookup={
                "station-a": {"id": "station-a", "name": "Station A", "line_ids": ["blue"]},
                "station-b": {"id": "station-b", "name": "Station B", "line_ids": ["blue"]},
            },
        )

        with (
            patch("app.api.routes.get_subway_network", return_value=network),
            patch("app.api.routes.get_gis_route_context", return_value=context),
            patch("app.api.routes.get_route_engine", return_value=DummyEngine()),
        ):
            body = await get_gis_route_for_points(
                GisPointRouteRequest(
                    start_lon=121.5000,
                    start_lat=25.0420,
                    end_lon=121.5005,
                    end_lat=25.0420,
                    walking_m_per_sec=1.1,
                )
            )

        self.assertEqual(body["journey_mode"], "walk_fallback")
        self.assertEqual(body["route_selection_reason"], "metro_not_enough_time_saving")
        self.assertIn("metro_not_enough_time_saving", body["warnings"])
        self.assertLessEqual(body["access_walk_distance_m"], 600)
        self.assertEqual(body["route_diagnostics"]["mode_decision"], "metro_not_enough_time_saving")
        self.assertEqual(body["route_diagnostics"]["metro_min_short_walk_saving_sec"], 7 * 60)

    async def test_gis_route_points_prefers_metro_over_uncomfortable_walk(self):
        network = SubwayNetwork(
            stations={
                "station-a": Station(id="station-a", name="Station A", x=121.5, y=25.042),
                "station-b": Station(id="station-b", name="Station B", x=121.5193, y=25.042),
            },
            lines={"blue": Line(id="blue", name="Blue Line", color="#007ec7")},
            station_lines=[],
            segments=[],
            transfers=[],
            station_to_lines={"station-a": {"blue"}, "station-b": {"blue"}},
            metadata={"admin_effects": {"scenarios": {"rain_zones": []}}},
        )
        gis_payload = {
            "source": "qgis_geojson",
            "stations": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [121.5, 25.042]},
                        "properties": {"id": "station-a", "name": "Station A"},
                    },
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [121.5193, 25.042]},
                        "properties": {"id": "station-b", "name": "Station B"},
                    },
                ],
            },
            "station_access_points": None,
            "lines": {"type": "FeatureCollection", "features": []},
        }
        context = GisRouteContext(
            payload=gis_payload,
            station_coords_by_id={"station-a": (121.5, 25.042), "station-b": (121.5193, 25.042)},
            walk_graph=build_walk_graph(None),
            walk_targets_by_node={},
            station_lookup={
                "station-a": {"id": "station-a", "name": "Station A", "line_ids": ["blue"]},
                "station-b": {"id": "station-b", "name": "Station B", "line_ids": ["blue"]},
            },
        )
        dummy_route = RouteResult(
            total_time_sec=35 * 60,
            walking_time_sec=0,
            transfer_count=0,
            stop_count=1,
            station_ids=["station-a", "station-b"],
            line_sequence=["blue"],
            steps=[
                RouteStep(
                    kind="ride",
                    station_id="station-a",
                    line_id="blue",
                    next_station_id="station-b",
                    duration_sec=35 * 60,
                )
            ],
        )

        class DummyEngine:
            def find_route_through_stations(self, station_ids):
                return dummy_route

        with (
            patch("app.api.routes.get_subway_network", return_value=network),
            patch("app.api.routes.get_gis_route_context", return_value=context),
            patch("app.api.routes.get_route_engine", return_value=DummyEngine()),
        ):
            body = await get_gis_route_for_points(
                GisPointRouteRequest(
                    start_lon=121.5,
                    start_lat=25.042,
                    end_lon=121.5193,
                    end_lat=25.042,
                    walking_m_per_sec=1.1,
                )
            )

        self.assertEqual(body["journey_mode"], "subway")
        self.assertEqual(body["route_diagnostics"]["mode_decision"], "metro_selected")
        self.assertGreater(body["route_diagnostics"]["walk_only_time_sec"], 20 * 60)
        self.assertEqual(body["route_diagnostics"]["metro_allowed_slower_sec"], 25 * 60)
        self.assertEqual(body["total_journey_time_sec"], 35 * 60)

    def test_route_candidate_score_prefers_less_walking_when_times_are_close(self):
        long_walk_short_ride = RouteResult(
            total_time_sec=5 * 60,
            walking_time_sec=0,
            transfer_count=0,
            stop_count=1,
            station_ids=["near-end-a", "near-end-b"],
            line_sequence=["blue"],
            steps=[],
        )
        short_walk_loop_ride = RouteResult(
            total_time_sec=40 * 60,
            walking_time_sec=0,
            transfer_count=0,
            stop_count=8,
            station_ids=["near-start-a", "near-start-b"],
            line_sequence=["blue"],
            steps=[],
        )
        long_walk_candidate = SimpleNamespace(
            station_id="near-end-a",
            distance_m=1800.0,
            path_coordinates=[],
            access_point_coordinate=None,
        )
        long_egress_candidate = SimpleNamespace(
            station_id="near-end-b",
            distance_m=900.0,
            path_coordinates=[],
            access_point_coordinate=None,
        )
        short_walk_candidate = SimpleNamespace(
            station_id="near-start-a",
            distance_m=200.0,
            path_coordinates=[],
            access_point_coordinate=None,
        )
        short_egress_candidate = SimpleNamespace(
            station_id="near-start-b",
            distance_m=200.0,
            path_coordinates=[],
            access_point_coordinate=None,
        )

        long_walk_eval = _route_candidate_evaluation(
            long_walk_short_ride,
            long_walk_candidate,
            long_egress_candidate,
            1.1,
            [],
            candidate_set="test",
        )
        short_walk_eval = _route_candidate_evaluation(
            short_walk_loop_ride,
            short_walk_candidate,
            short_egress_candidate,
            1.1,
            [],
            candidate_set="test",
        )

        self.assertGreater(short_walk_eval["actual_time_sec"], long_walk_eval["actual_time_sec"])
        self.assertLess(short_walk_eval["total_walk_time_sec"], long_walk_eval["total_walk_time_sec"])
        self.assertLess(short_walk_eval["selection_cost_sec"], long_walk_eval["selection_cost_sec"])

    async def test_gis_route_points_can_route_from_shipai_to_gongguan(self):
        body = await get_gis_route_for_points(
            GisPointRouteRequest(
                start_lon=121.52581,
                start_lat=25.11988,
                end_lon=121.54110,
                end_lat=25.01823,
                walking_m_per_sec=1.1,
            )
        )

        self.assertIn(body["selected_start_station"]["id"], {"shipai", "mingde"})
        self.assertEqual(body["selected_end_station"]["id"], "gongguan")
        self.assertEqual(body["journey_mode"], "subway")
        self.assertIn(body["selected_start_station"]["id"], body["route"]["station_ids"])
        self.assertIn("gongguan", body["route"]["station_ids"])
        self.assertGreaterEqual(len(body["route"]["line_sequence"]), 2)

    async def test_gis_route_points_prefers_walk_access_path_over_air_distance(self):
        walk_network = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [1.0, 1.0],
                            [1.0, 0.0],
                            [0.0, 0.0],
                        ],
                    },
                    "properties": {},
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [1.0, 1.0],
                            [2.0, 1.0],
                        ],
                    },
                    "properties": {},
                },
            ],
        }
        gis_payload = {
            "source": "qgis_geojson",
            "bounds": [0.0, 0.0, 2.0, 1.0],
            "stations": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [1.05, 1.0]},
                        "properties": {"id": "station-a", "name": "Station A"},
                    },
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [2.0, 1.0]},
                        "properties": {"id": "station-b", "name": "Station B"},
                    },
                ],
            },
            "lines": {"type": "FeatureCollection", "features": []},
            "station_access_points": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
                        "properties": {"station_id": "station-a", "name": "A Exit"},
                    },
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [2.0, 1.0]},
                        "properties": {"station_id": "station-b", "name": "B Exit"},
                    },
                ],
            },
            "walk_network": walk_network,
        }
        network = SubwayNetwork(
            stations={
                "station-a": Station(id="station-a", name="Station A", x=0, y=0),
                "station-b": Station(id="station-b", name="Station B", x=0, y=0),
            },
            lines={"blue": Line(id="blue", name="Blue Line", color="#007ec7")},
            station_lines=[],
            segments=[],
            transfers=[],
            station_to_lines={"station-a": {"blue"}, "station-b": {"blue"}},
        )
        dummy_route = RouteResult(
            total_time_sec=120,
            walking_time_sec=0,
            transfer_count=0,
            stop_count=1,
            station_ids=["station-b"],
            line_sequence=["blue"],
            steps=[],
        )

        class DummyEngine:
            def find_route_through_stations(self, station_ids):
                self.station_ids = station_ids
                return dummy_route

        engine = DummyEngine()

        with (
            patch("app.api.routes.get_subway_network", return_value=network),
            patch("app.api.routes.get_route_engine", return_value=engine),
            patch("app.api.routes.build_gis_payload", return_value=gis_payload),
        ):
            body = await get_gis_route_for_points(
                GisPointRouteRequest(
                    start_lon=1.0,
                    start_lat=1.0,
                    end_lon=2.0,
                    end_lat=1.0,
                    walking_m_per_sec=1.1,
                    via_station_ids=["station-b"],
                )
            )

        self.assertEqual(body["selected_start_station"]["id"], "station-b")
        self.assertEqual(body["selected_start_access_point"]["name"], "B Exit")
        self.assertEqual(
            body["access_walk_path"]["coordinates"],
            [[1.0, 1.0], [2.0, 1.0]],
        )
        self.assertEqual(engine.station_ids, ["station-b", "station-b", "station-b"])

    async def test_gis_route_points_returns_ride_path_features_from_gis_lines(self):
        gis_payload = {
            "source": "qgis_geojson",
            "bounds": [0.0, 0.0, 1.0, 1.0],
            "stations": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
                        "properties": {"id": "station-a", "name": "Station A", "line_ids": ["c2"]},
                    },
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [1.0, 1.0]},
                        "properties": {"id": "station-b", "name": "Station B", "line_ids": ["c2"]},
                    },
                ],
            },
            "lines": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "MultiLineString",
                            "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]],
                        },
                        "properties": {"line_name": "Red Line", "line_color": "#ff0000"},
                    }
                ],
            },
            "station_access_points": None,
            "walk_network": None,
        }
        network = SubwayNetwork(
            stations={
                "station-a": Station(id="station-a", name="Station A", x=0, y=0),
                "station-b": Station(id="station-b", name="Station B", x=0, y=0),
            },
            lines={"c2": Line(id="c2", name="Line C2", color="#e3002d")},
            station_lines=[],
            segments=[],
            transfers=[],
            station_to_lines={"station-a": {"c2"}, "station-b": {"c2"}},
        )
        dummy_route = RouteResult(
            total_time_sec=60,
            walking_time_sec=0,
            transfer_count=0,
            stop_count=1,
            station_ids=["station-a", "station-b"],
            line_sequence=["c2"],
            steps=[
                RouteStep(
                    kind="ride",
                    station_id="station-a",
                    line_id="c2",
                    next_station_id="station-b",
                    duration_sec=60,
                )
            ],
        )

        class DummyEngine:
            def find_route_through_stations(self, station_ids):
                self.station_ids = station_ids
                return dummy_route

        engine = DummyEngine()

        with (
            patch("app.api.routes.get_subway_network", return_value=network),
            patch("app.api.routes.get_route_engine", return_value=engine),
            patch("app.api.routes.build_gis_payload", return_value=gis_payload),
        ):
            body = await get_gis_route_for_points(
                GisPointRouteRequest(
                    start_lon=0.0,
                    start_lat=0.0,
                    end_lon=1.0,
                    end_lat=1.0,
                    walking_m_per_sec=1.1,
                    via_station_ids=["station-a"],
                )
            )

        self.assertEqual(len(body["ride_path_features"]), 1)
        self.assertEqual(
            body["ride_path_features"][0]["geometry"]["coordinates"],
            [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
        )
        self.assertEqual(
            body["ride_path_features"][0]["properties"].get("line_color"),
            "#ff0000",
        )

    async def test_gis_route_points_walk_path_uses_exact_clicked_point_and_access_point(self):
        gis_payload = {
            "source": "qgis_geojson",
            "bounds": [0.0, 0.0, 3.0, 1.0],
            "stations": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [2.1, 0.0]},
                        "properties": {"id": "station-b", "name": "Station B"},
                    }
                ],
            },
            "lines": {"type": "FeatureCollection", "features": []},
            "station_access_points": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [2.1, 0.0]},
                        "properties": {"station_id": "station-b", "name": "B Exit"},
                    }
                ],
            },
            "walk_network": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [1.0, 0.0],
                                [2.0, 0.0],
                            ],
                        },
                        "properties": {},
                    }
                ],
            },
        }
        network = SubwayNetwork(
            stations={
                "station-b": Station(id="station-b", name="Station B", x=0, y=0),
            },
            lines={},
            station_lines=[],
            segments=[],
            transfers=[],
            station_to_lines={"station-b": set()},
        )
        dummy_route = RouteResult(
            total_time_sec=0,
            walking_time_sec=0,
            transfer_count=0,
            stop_count=0,
            station_ids=["station-b"],
            line_sequence=[],
            steps=[],
        )

        class DummyEngine:
            def find_route_through_stations(self, station_ids):
                self.station_ids = station_ids
                return dummy_route

        engine = DummyEngine()

        with (
            patch("app.api.routes.get_subway_network", return_value=network),
            patch("app.api.routes.get_route_engine", return_value=engine),
            patch("app.api.routes.build_gis_payload", return_value=gis_payload),
        ):
            body = await get_gis_route_for_points(
                GisPointRouteRequest(
                    start_lon=0.0,
                    start_lat=0.0,
                    end_lon=2.1,
                    end_lat=0.0,
                    walking_m_per_sec=1.1,
                    via_station_ids=["station-b"],
                )
            )

        self.assertEqual(body["selected_start_access_point"]["name"], "B Exit")
        self.assertEqual(body["selected_start_access_point"]["lon"], 2.1)
        self.assertEqual(body["selected_start_access_point"]["lat"], 0.0)
        self.assertEqual(
            body["access_walk_path"]["coordinates"],
            [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [2.1, 0.0]],
        )

    async def test_legacy_api_endpoints_are_gone(self):
        builder_request = BuilderNetworkSaveRequest(
            stations=[
                BuilderStationPayload(id="S1", name="Alpha", x=100, y=200),
            ],
            lines=[
                BuilderLinePayload(id="red", name="Red Line", color="#d94f4f"),
            ],
            station_lines=[
                BuilderStationLinePayload(station_id="S1", line_id="red", seq=1),
            ],
        )
        calibration_request = CalibrationSaveRequest(
            stations=[
                {"id": "X1", "x": 1200, "y": 2800},
            ]
        )
        legacy_calls = [
            ("network", lambda: get_network()),
            (
                "route",
                lambda: get_route(
                    RouteRequest(start_station_id="ximen", end_station_id="liuzhangli"),
                ),
            ),
            (
                "route_points",
                lambda: get_route_for_points(
                    PointRouteRequest(start_x=0, start_y=0, end_x=1, end_y=1),
                ),
            ),
            ("builder_network", lambda: get_builder_network()),
            ("save_builder_network", lambda: save_builder_network(builder_request)),
            ("save_calibration", lambda: save_calibration(calibration_request)),
        ]

        for name, factory in legacy_calls:
            with self.subTest(endpoint=name):
                with self.assertRaises(HTTPException) as context:
                    await factory()
                self.assertEqual(context.exception.status_code, 410)

    async def test_save_gis_stations_updates_station_geojson_coordinates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gis_dir = Path(tmpdir)
            stations_path = gis_dir / "stations.geojson"
            stations_path.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "geometry": {"type": "Point", "coordinates": [121.5, 25.0]},
                                "properties": {"id": "station-a", "name": "Station A"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            request = GisStationSaveRequest(
                stations=[
                    GisStationPositionPayload(id="station-a", lon=121.5123456, lat=25.0123456),
                ]
            )
            patched_settings = replace(get_settings(), qgis_geojson_dir=gis_dir)

            with patch("app.api.routes.settings", patched_settings):
                body = await save_gis_stations(request)

            self.assertEqual(body["updated_count"], 1)
            saved_payload = json.loads(stations_path.read_text(encoding="utf-8"))
            self.assertEqual(
                saved_payload["features"][0]["geometry"]["coordinates"],
                [121.5123456, 25.0123456],
            )

    async def test_save_gis_stations_rejects_unknown_station_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gis_dir = Path(tmpdir)
            stations_path = gis_dir / "stations.geojson"
            stations_path.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "geometry": {"type": "Point", "coordinates": [121.5, 25.0]},
                                "properties": {"id": "station-a", "name": "Station A"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            request = GisStationSaveRequest(
                stations=[
                    GisStationPositionPayload(id="station-missing", lon=121.6, lat=25.1),
                ]
            )
            patched_settings = replace(get_settings(), qgis_geojson_dir=gis_dir)

            with (
                patch("app.api.routes.settings", patched_settings),
                self.assertRaises(HTTPException) as context,
            ):
                await save_gis_stations(request)

            self.assertEqual(context.exception.status_code, 400)
            self.assertIn("Unknown GIS station id", context.exception.detail)

    async def test_save_gis_stations_marks_station_deleted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gis_dir = Path(tmpdir)
            stations_path = gis_dir / "stations.geojson"
            stations_path.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "geometry": {"type": "Point", "coordinates": [121.5, 25.0]},
                                "properties": {"id": "station-a", "name": "Station A"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            request = GisStationSaveRequest(
                stations=[
                    GisStationPositionPayload(id="station-a", lon=121.5, lat=25.0, deleted=True),
                ]
            )
            patched_settings = replace(get_settings(), qgis_geojson_dir=gis_dir)

            with patch("app.api.routes.settings", patched_settings):
                body = await save_gis_stations(request)

            self.assertEqual(body["updated_count"], 1)
            saved_payload = json.loads(stations_path.read_text(encoding="utf-8"))
            self.assertTrue(saved_payload["features"][0]["properties"]["deleted"])

    async def test_delete_gis_station_marks_station_deleted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gis_dir = Path(tmpdir)
            stations_path = gis_dir / "stations.geojson"
            stations_path.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "geometry": {"type": "Point", "coordinates": [121.5, 25.0]},
                                "properties": {"id": "station-a", "name": "Station A"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            patched_settings = replace(get_settings(), qgis_geojson_dir=gis_dir)

            with patch("app.api.routes.settings", patched_settings):
                body = await delete_gis_station("station-a")

            self.assertEqual(body["updated_count"], 1)
            saved_payload = json.loads(stations_path.read_text(encoding="utf-8"))
            self.assertTrue(saved_payload["features"][0]["properties"]["deleted"])


if __name__ == "__main__":
    unittest.main()
