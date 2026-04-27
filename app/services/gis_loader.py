from __future__ import annotations

import hashlib
import json
import pickle
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.domain.models import SubwayNetwork
from app.services.walk_network import WalkGraph
from app.services.walk_network import build_walk_graph
from app.services.geo_utils import (
    BLOCK_LINE_SEGMENT_THRESHOLD_M,
    is_line_near_geometry,
)


def build_gis_payload(
    network: SubwayNetwork,
    qgis_geojson_dir: Path,
    map_width: float,
    map_height: float,
    fallback_bounds: tuple[float, float, float, float],
    include_station_access_points: bool = True,
    include_walk_network: bool = True,
    merge_missing_stations: bool = True,
    closed_segment_keys: set[str] | None = None,
    block_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    stations_path = qgis_geojson_dir / "stations.geojson"
    lines_path = qgis_geojson_dir / "lines.geojson"
    station_access_points_path = qgis_geojson_dir / "station_access_points.geojson"
    walk_network_path = qgis_geojson_dir / "walk_network.geojson"

    qgis_stations = _load_geojson(stations_path)
    qgis_lines = _load_geojson(lines_path)
    qgis_station_access_points = (
        _load_geojson(station_access_points_path)
        if include_station_access_points
        else None
    )
    qgis_walk_network = _load_geojson(walk_network_path) if include_walk_network else None

    fallback_stations_geojson, fallback_lines_geojson = _build_fallback_geojson(
        network,
        map_width,
        map_height,
        fallback_bounds,
        closed_segment_keys=closed_segment_keys,
    )

    # Perform Block Tagging on QGIS lines if they exist
    if _is_valid_geojson(qgis_lines) and block_segments:
        _tag_geojson_blocks_spatially(qgis_lines, block_segments)
    
    # Also tag fallback lines if they were built
    if fallback_lines_geojson and block_segments:
        _tag_geojson_blocks_spatially(fallback_lines_geojson, block_segments)

    qgis_stations_are_complete = _is_valid_station_geojson(qgis_stations, network)

    if _is_valid_geojson(qgis_stations):
        stations_geojson = (
            _merge_station_geojson(
                qgis_stations,
                fallback_stations_geojson,
                network,
            )
            if merge_missing_stations
            else qgis_stations
        )
        lines_geojson = qgis_lines if _is_valid_geojson(qgis_lines) else fallback_lines_geojson
        source = (
            "qgis_geojson"
            if qgis_stations_are_complete and _is_valid_geojson(qgis_lines)
            else "qgis_geojson_merged"
            if merge_missing_stations
            else "qgis_geojson_partial"
        )
    else:
        source = "fallback_projected"
        stations_geojson = fallback_stations_geojson
        lines_geojson = fallback_lines_geojson

    bounds = _compute_geojson_bounds(stations_geojson)
    payload = {
        "source": source,
        "bounds": bounds,
        "stations": stations_geojson,
        "lines": lines_geojson,
        "station_catalog": _build_station_catalog(stations_geojson, set(network.stations)),
        "line_catalog": _build_line_catalog(network),
    }

    if include_station_access_points:
        payload["station_access_points"] = _resolve_station_access_points(
            qgis_station_access_points,
            stations_geojson,
        )
    if include_walk_network:
        payload["walk_network"] = qgis_walk_network if _is_valid_geojson(qgis_walk_network) else None
    return payload


def _build_station_catalog(stations_geojson: dict[str, Any], valid_ids: set[str]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for feature in stations_geojson.get("features", []):
        properties = feature.get("properties", {}) or {}
        station_id = properties.get("id") or properties.get("station_id")
        if not station_id or station_id not in valid_ids:
            continue
        coordinates = feature.get("geometry", {}).get("coordinates", [None, None])
        catalog.append(
            {
                "id": station_id,
                "name": properties.get("name") or station_id,
                "line_ids": list(properties.get("line_ids") or []),
                "lon": coordinates[0],
                "lat": coordinates[1],
            }
        )
    return catalog


def _build_line_catalog(network: SubwayNetwork) -> list[dict[str, Any]]:
    return [
        {"id": line.id, "name": line.name, "color": line.color}
        for line in network.lines.values()
    ]


def _load_geojson(path: Path) -> dict[str, Any] | None:
    signature = _path_signature(path)
    return _load_geojson_cached(str(path), signature)


def get_cached_walk_graph(qgis_geojson_dir: Path) -> WalkGraph:
    walk_network_path = qgis_geojson_dir / "walk_network.geojson"
    signature = _path_signature(walk_network_path)
    return _load_walk_graph_cached(str(walk_network_path), signature)


@lru_cache(maxsize=16)
def _load_geojson_cached(path_str: str, signature: str) -> dict[str, Any] | None:
    del signature
    path = Path(path_str)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


@lru_cache(maxsize=4)
def _load_walk_graph_cached(path_str: str, signature: str) -> WalkGraph:
    cached_graph = _load_persisted_walk_graph(path_str, signature)
    if cached_graph is not None:
        return cached_graph

    walk_graph = build_walk_graph(_load_geojson_cached(path_str, signature))
    _persist_walk_graph(path_str, signature, walk_graph)
    return walk_graph


def _is_valid_geojson(payload: dict[str, Any] | None) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("type") == "FeatureCollection"
        and isinstance(payload.get("features"), list)
    )


def _is_valid_station_geojson(payload: dict[str, Any] | None, network: SubwayNetwork) -> bool:
    if not _is_valid_geojson(payload):
        return False

    available_station_ids = {
        feature.get("properties", {}).get("id")
        for feature in payload.get("features", [])
    }
    required_station_ids = set(network.stations.keys())
    return required_station_ids.issubset(available_station_ids)


def _merge_station_geojson(
    qgis_stations: dict[str, Any],
    fallback_stations_geojson: dict[str, Any],
    network: SubwayNetwork,
) -> dict[str, Any]:
    del network
    merged_features: list[dict[str, Any]] = []
    existing_station_ids: set[str] = set()

    for feature in qgis_stations.get("features", []):
        station_id = feature.get("properties", {}).get("id")
        if station_id:
            existing_station_ids.add(str(station_id))
        merged_features.append(feature)

    for feature in fallback_stations_geojson.get("features", []):
        station_id = feature.get("properties", {}).get("id")
        if not station_id or str(station_id) in existing_station_ids:
            continue
        merged_features.append(feature)

    return {"type": "FeatureCollection", "features": merged_features}


def _build_fallback_geojson(
    network: SubwayNetwork,
    map_width: float,
    map_height: float,
    fallback_bounds: tuple[float, float, float, float],
    closed_segment_keys: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    stations_features = []
    for station in network.stations.values():
        # Detect if station already has GIS coords
        if abs(station.y) <= 90 and abs(station.x) <= 180 and (abs(station.y) > 0.001 or abs(station.x) > 0.001):
            lon, lat = station.x, station.y
        else:
            lon, lat = _pixel_to_lonlat(
                station.x,
                station.y,
                map_width,
                map_height,
                fallback_bounds,
            )
        stations_features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "id": station.id,
                    "name": station.name,
                    "line_ids": sorted(network.station_to_lines.get(station.id, set())),
                },
            }
        )

    lines_features = []
    for segment in network.segments:
        from_station = network.stations.get(segment.from_station_id)
        to_station = network.stations.get(segment.to_station_id)
        line = network.lines.get(segment.line_id)
        if from_station is None or to_station is None:
            continue

        # Detect if from_station already has GIS coords
        if abs(from_station.y) <= 90 and abs(from_station.x) <= 180 and (abs(from_station.y) > 0.001 or abs(from_station.x) > 0.001):
            from_lon, from_lat = from_station.x, from_station.y
        else:
            from_lon, from_lat = _pixel_to_lonlat(
                from_station.x,
                from_station.y,
                map_width,
                map_height,
                fallback_bounds,
            )

        # Detect if to_station already has GIS coords
        if abs(to_station.y) <= 90 and abs(to_station.x) <= 180 and (abs(to_station.y) > 0.001 or abs(to_station.x) > 0.001):
            to_lon, to_lat = to_station.x, to_station.y
        else:
            to_lon, to_lat = _pixel_to_lonlat(
                to_station.x,
                to_station.y,
                map_width,
                map_height,
                fallback_bounds,
            )
        is_blocked = False
        if closed_segment_keys:
            # Use canonical key format: line_id:left_station_id:right_station_id (sorted)
            s1, s2 = sorted((segment.from_station_id, segment.to_station_id))
            key = f"{segment.line_id}:{s1}:{s2}"
            if key in closed_segment_keys:
                is_blocked = True

        lines_features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[from_lon, from_lat], [to_lon, to_lat]],
                },
                "properties": {
                    "line_id": segment.line_id,
                    "line_name": line.name if line else segment.line_id,
                    "line_color": line.color if line else "#7b8794",
                    "from_station_id": segment.from_station_id,
                    "to_station_id": segment.to_station_id,
                    "travel_sec": segment.travel_sec,
                    "is_blocked": is_blocked,
                },
            }
        )

    return (
        {"type": "FeatureCollection", "features": stations_features},
        {"type": "FeatureCollection", "features": lines_features},
    )


def _resolve_station_access_points(
    station_access_points_geojson: dict[str, Any] | None,
    stations_geojson: dict[str, Any],
) -> dict[str, Any]:
    if _is_valid_geojson(station_access_points_geojson):
        return station_access_points_geojson

    fallback_features: list[dict[str, Any]] = []
    for feature in stations_geojson.get("features", []):
        station_id = feature.get("properties", {}).get("id")
        coordinates = feature.get("geometry", {}).get("coordinates")
        if (
            not station_id
            or not isinstance(coordinates, list)
            or len(coordinates) < 2
        ):
            continue
        fallback_features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(coordinates[0]), float(coordinates[1])],
                },
                "properties": {
                    "station_id": str(station_id),
                    "name": feature.get("properties", {}).get("name"),
                },
            }
        )

    return {"type": "FeatureCollection", "features": fallback_features}


def _tag_geojson_blocks_spatially(
    geojson: dict[str, Any],
    block_segments: list[dict[str, Any]],
) -> None:
    """Tag GeoJSON features with is_blocked=True if they are near any block_segments."""
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry", {})
        coords = []
        if geometry.get("type") == "LineString":
            coords = geometry.get("coordinates", [])
        elif geometry.get("type") == "MultiLineString":
            # Just flatten for simple intersection check
            for part in geometry.get("coordinates", []):
                coords.extend(part)
        
        if not coords:
            continue
            
        # Convert coordinates to tuples for the helper
        line_coords = [(float(c[0]), float(c[1])) for c in coords]
        
        for block in block_segments:
            block_start = (block["from"]["lon"], block["from"]["lat"])
            block_end = (block["to"]["lon"], block["to"]["lat"])
            
            if is_line_near_geometry(block_start, block_end, line_coords, BLOCK_LINE_SEGMENT_THRESHOLD_M):
                feature.setdefault("properties", {})["is_blocked"] = True
                break


def _pixel_to_lonlat(
    x: float,
    y: float,
    map_width: float,
    map_height: float,
    fallback_bounds: tuple[float, float, float, float],
) -> tuple[float, float]:
    min_lon, min_lat, max_lon, max_lat = fallback_bounds
    lon = min_lon + (float(x) / float(map_width)) * (max_lon - min_lon)
    lat = max_lat - (float(y) / float(map_height)) * (max_lat - min_lat)
    return round(lon, 7), round(lat, 7)


def _compute_geojson_bounds(payload: dict[str, Any]) -> list[float]:
    min_lon = float("inf")
    min_lat = float("inf")
    max_lon = float("-inf")
    max_lat = float("-inf")

    for feature in payload.get("features", []):
        coordinates = feature.get("geometry", {}).get("coordinates")
        for lon, lat in _iter_coordinates(coordinates):
            min_lon = min(min_lon, lon)
            min_lat = min(min_lat, lat)
            max_lon = max(max_lon, lon)
            max_lat = max(max_lat, lat)

    if min_lon == float("inf"):
        return [121.45, 24.95, 121.65, 25.15]
    return [min_lon, min_lat, max_lon, max_lat]


def _iter_coordinates(node: Any):
    if not isinstance(node, list):
        return
    if len(node) >= 2 and isinstance(node[0], (int, float)) and isinstance(node[1], (int, float)):
        yield float(node[0]), float(node[1])
        return
    for item in node:
        yield from _iter_coordinates(item)


def _path_signature(path: Path) -> str:
    if not path.exists():
        return f"{path}:missing"
    stat = path.stat()
    return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"


def _walk_graph_cache_path(path_str: str, signature: str) -> Path:
    source_path = Path(path_str)
    cache_dir = source_path.parent / ".runtime-cache"
    digest = hashlib.sha256(f"{source_path.name}|{signature}".encode("utf-8")).hexdigest()
    return cache_dir / f"walk_graph_{digest}.pickle"


def _load_persisted_walk_graph(path_str: str, signature: str) -> WalkGraph | None:
    cache_path = _walk_graph_cache_path(path_str, signature)
    if not cache_path.exists():
        return None
    try:
        with cache_path.open("rb") as handle:
            payload = pickle.load(handle)
    except (OSError, pickle.PickleError, EOFError):
        return None

    if not isinstance(payload, dict) or payload.get("signature") != signature:
        return None

    walk_graph = payload.get("walk_graph")
    return walk_graph if isinstance(walk_graph, WalkGraph) else None


def _persist_walk_graph(path_str: str, signature: str, walk_graph: WalkGraph) -> None:
    cache_path = _walk_graph_cache_path(path_str, signature)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("wb") as handle:
            pickle.dump({"signature": signature, "walk_graph": walk_graph}, handle, protocol=pickle.HIGHEST_PROTOCOL)
    except OSError:
        return
