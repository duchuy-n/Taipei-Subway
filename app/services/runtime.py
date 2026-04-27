from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.services.route_engine import RouteEngine
from app.services.subway_loader import NetworkBuildOptions
from app.services.subway_loader import load_json_file
from app.services.subway_loader import load_network_from_dict
from app.services.subway_loader import load_station_positions_file
from app.services.subway_loader import merge_network_enrichment
from app.services.admin_scenarios import (
    load_admin_scenarios,
    build_admin_scenario_effects,
    apply_admin_scenarios_to_network,
)



def get_network():
    settings = get_settings()
    source_path = settings.data_file
    positions_path = settings.station_positions_file if settings.station_positions_file.exists() else None
    enrichment_path = settings.osm_enrichment_file if settings.osm_enrichment_file.exists() else None
    signature = _build_signature(
        source_path,
        positions_path,
        enrichment_path,
        settings.qgis_geojson_dir,
    )

    return _load_network_cached(
        str(source_path),
        str(positions_path) if positions_path else "",
        str(enrichment_path) if enrichment_path else "",
        str(settings.qgis_geojson_dir),
        str(settings.admin_scenarios_file),
        settings.map_width,
        settings.map_height,
        (
            settings.fallback_min_lon,
            settings.fallback_min_lat,
            settings.fallback_max_lon,
            settings.fallback_max_lat,
        ),
        settings.default_transfer_sec,

        settings.auto_walk_transfer_radius,
        settings.auto_walk_seconds_per_unit,
        settings.line_switch_penalty,
        signature,
    )



@lru_cache(maxsize=4)
def _load_network_cached(
    source_path: str,
    positions_path: str,
    enrichment_path: str,
    qgis_geojson_dir: str,
    admin_scenarios_path: str,
    map_width: float,
    map_height: float,
    fallback_bounds: tuple[float, float, float, float],
    default_transfer_sec: int,

    auto_walk_transfer_radius: float,
    auto_walk_seconds_per_unit: float,
    line_switch_penalty: float,
    signature: str,
):

    del signature
    options = NetworkBuildOptions(
        station_positions=load_station_positions_file(positions_path or None),
        default_transfer_sec=default_transfer_sec,
        line_switch_penalty=line_switch_penalty,
        auto_walk_transfer_radius=auto_walk_transfer_radius,
        auto_walk_seconds_per_unit=auto_walk_seconds_per_unit,
        repair_missing_segments=Path(source_path).name == "network_topology.json",
    )
    raw_network = load_json_file(source_path)
    enrichment = load_json_file(enrichment_path or None)
    network = load_network_from_dict(
        merge_network_enrichment(raw_network, enrichment),
        options=options,
    )

    # Use specialized GIS loader to calculate impacts of blocked stations/zones
    from app.services.gis_loader import build_gis_payload
    gis_payload = build_gis_payload(
        network=network,
        qgis_geojson_dir=Path(qgis_geojson_dir),
        map_width=map_width,
        map_height=map_height,
        fallback_bounds=fallback_bounds,
        include_walk_network=False,
    )

    scenarios = load_admin_scenarios(admin_scenarios_path)
    effects = build_admin_scenario_effects(
        network=network,
        gis_payload=gis_payload,
        scenarios=scenarios,
    )
    network = apply_admin_scenarios_to_network(network, effects)

    return network


def get_gis_payload(include_walk_network: bool = True):
    """Optimized getter for the GIS payload used by the map UI."""
    settings = get_settings()
    source_path = settings.data_file
    positions_path = settings.station_positions_file if settings.station_positions_file.exists() else None
    enrichment_path = settings.osm_enrichment_file if settings.osm_enrichment_file.exists() else None
    
    signature = _build_signature(
        source_path,
        positions_path,
        enrichment_path,
        settings.qgis_geojson_dir,
    )

    return _load_gis_payload_cached(
        signature,
        settings.map_width,
        settings.map_height,
        (
            settings.fallback_min_lon,
            settings.fallback_min_lat,
            settings.fallback_max_lon,
            settings.fallback_max_lat,
        ),
        include_walk_network,
        str(settings.qgis_geojson_dir),
        str(settings.admin_scenarios_file),
    )


@lru_cache(maxsize=8)
def _load_gis_payload_cached(
    signature: str,
    map_width: float,
    map_height: float,
    fallback_bounds: tuple[float, float, float, float],
    include_walk_network: bool,
    qgis_geojson_dir: str,
    admin_scenarios_path: str,
):
    del signature
    # Get base network (already cached)
    network = get_network() 
    
    from app.services.gis_loader import build_gis_payload
    from app.services.admin_scenarios import load_admin_scenarios

    scenarios = load_admin_scenarios(admin_scenarios_path)
    # Highlight blocked segments in the map UI payload
    block_segments = scenarios.get("block_segments", [])
    
    return build_gis_payload(
        network=network,
        qgis_geojson_dir=Path(qgis_geojson_dir),
        map_width=map_width,
        map_height=map_height,
        fallback_bounds=fallback_bounds,
        include_walk_network=include_walk_network,
        block_segments=block_segments,
    )


def get_route_engine() -> RouteEngine:
    settings = get_settings()
    source_path = settings.data_file
    positions_path = settings.station_positions_file if settings.station_positions_file.exists() else None
    enrichment_path = settings.osm_enrichment_file if settings.osm_enrichment_file.exists() else None
    signature = _build_signature(
        source_path,
        positions_path,
        enrichment_path,
        settings.qgis_geojson_dir,
    )

    return _load_route_engine_cached(
        str(source_path),
        str(positions_path) if positions_path else "",
        str(enrichment_path) if enrichment_path else "",
        str(settings.qgis_geojson_dir),
        str(settings.admin_scenarios_file),
        settings.map_width,
        settings.map_height,
        (
            settings.fallback_min_lon,
            settings.fallback_min_lat,
            settings.fallback_max_lon,
            settings.fallback_max_lat,
        ),
        settings.default_transfer_sec,

        settings.auto_walk_transfer_radius,
        settings.auto_walk_seconds_per_unit,
        settings.line_switch_penalty,
        signature,
    )



@lru_cache(maxsize=4)
def _load_route_engine_cached(
    source_path: str,
    positions_path: str,
    enrichment_path: str,
    qgis_geojson_dir: str,
    admin_scenarios_path: str,
    map_width: float,
    map_height: float,
    fallback_bounds: tuple[float, float, float, float],
    default_transfer_sec: int,

    auto_walk_transfer_radius: float,
    auto_walk_seconds_per_unit: float,
    line_switch_penalty: float,
    signature: str,
) -> RouteEngine:

    network = _load_network_cached(
        source_path,
        positions_path,
        enrichment_path,
        qgis_geojson_dir,
        admin_scenarios_path,
        map_width,
        map_height,
        fallback_bounds,
        default_transfer_sec,

        auto_walk_transfer_radius,
        auto_walk_seconds_per_unit,
        line_switch_penalty,
        signature,
    )

    return RouteEngine(network)


def refresh_runtime_caches() -> None:
    _load_network_cached.cache_clear()
    _load_route_engine_cached.cache_clear()
    _load_gis_payload_cached.cache_clear()


def _build_signature(
    source_path: Path,
    positions_path: Path | None,
    enrichment_path: Path | None,
    qgis_geojson_dir: Path,
) -> str:
    parts = [_path_signature(source_path)]
    if positions_path is not None:
        parts.append(_path_signature(positions_path))
    if enrichment_path is not None:
        parts.append(_path_signature(enrichment_path))
        
    # Standard GeoJSON files from QGIS directory
    for filename in ["stations.geojson", "lines.geojson", "walk_network.geojson", "station_access_points.geojson"]:
        parts.append(_path_signature(qgis_geojson_dir / filename))
    
    settings = get_settings()
    parts.append(_path_signature(settings.admin_scenarios_file))

    return "|".join(parts)



def _path_signature(path: Path) -> str:
    if not path.exists():
        return f"{path}:missing"
    stat = path.stat()
    return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
