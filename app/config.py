from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.services.travel_defaults import DEFAULT_DIAGRAM_WALK_SECONDS_PER_UNIT


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_FILE = PROJECT_ROOT / "app" / "data" / "gis" / "network_topology.json"
DEFAULT_POSITION_FILE = (
    PROJECT_ROOT / "app" / "data" / "station_positions_taipei_vector_map_2022.json"
)
DEFAULT_OSM_ENRICHMENT_FILE = PROJECT_ROOT / "app" / "data" / "subway_osm_enrichment.json"
DEFAULT_ADMIN_SCENARIOS_FILE = PROJECT_ROOT / "app" / "data" / "admin_scenarios.json"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = "IT3160 Subway Web"
    project_root: Path = PROJECT_ROOT
    data_file: Path = Path(os.getenv("SUBWAY_JSON_DATA_FILE", str(DEFAULT_DATA_FILE)))
    station_positions_file: Path = Path(
        os.getenv("SUBWAY_STATION_POSITIONS_FILE", str(DEFAULT_POSITION_FILE))
    )
    osm_enrichment_file: Path = Path(
        os.getenv("SUBWAY_OSM_ENRICHMENT_FILE", str(DEFAULT_OSM_ENRICHMENT_FILE))
    )
    static_dir: Path = PROJECT_ROOT / "app" / "static"
    map_dir: Path = PROJECT_ROOT / "map"
    map_image_name: str = "geography/taipei-vector-map-2022.svg"
    map_width: int = 3507
    map_height: int = 2480
    map_is_vector: bool = True
    map_supports_line_hints: bool = _env_flag("SUBWAY_MAP_SUPPORTS_LINE_HINTS", False)
    map_max_zoom: int = 10
    diagram_svg_name: str = "diagram/taipei_mrt_interactive.svg"
    diagram_width: float = 160.0
    diagram_height: float = 160.0
    diagram_raster_width: int = 4096
    diagram_raster_height: int = 4096
    diagram_is_vector: bool = True
    diagram_max_zoom: int = 14
    default_transfer_sec: int = int(os.getenv("SUBWAY_DEFAULT_TRANSFER_SEC", "30"))
    line_switch_penalty: float = float(os.getenv("SUBWAY_LINE_SWITCH_PENALTY", "180.0"))
    auto_walk_transfer_radius: float = float(
        os.getenv("SUBWAY_AUTO_WALK_TRANSFER_RADIUS", "150.0")
    )
    auto_walk_seconds_per_unit: float = float(
        os.getenv("SUBWAY_AUTO_WALK_SECONDS_PER_UNIT", str(DEFAULT_DIAGRAM_WALK_SECONDS_PER_UNIT))
    )
    point_route_max_station_walk_sec: int = int(
        os.getenv("SUBWAY_POINT_ROUTE_MAX_WALK_SEC", "60")
    )
    qgis_geojson_dir: Path = Path(
        os.getenv("SUBWAY_QGIS_GEOJSON_DIR", str(PROJECT_ROOT / "app" / "data" / "gis"))
    )
    gis_line_simplify_tolerance_m: float = float(
        os.getenv("SUBWAY_GIS_LINE_SIMPLIFY_TOLERANCE_M", "8.0")
    )
    admin_scenarios_file: Path = Path(
        os.getenv("SUBWAY_ADMIN_SCENARIOS_FILE", str(DEFAULT_ADMIN_SCENARIOS_FILE))
    )
    fallback_min_lon: float = float(os.getenv("SUBWAY_FALLBACK_MIN_LON", "121.36"))
    fallback_min_lat: float = float(os.getenv("SUBWAY_FALLBACK_MIN_LAT", "24.90"))
    fallback_max_lon: float = float(os.getenv("SUBWAY_FALLBACK_MAX_LON", "121.72"))
    fallback_max_lat: float = float(os.getenv("SUBWAY_FALLBACK_MAX_LAT", "25.24"))


def get_settings() -> Settings:
    return Settings()
