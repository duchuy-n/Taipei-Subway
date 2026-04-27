from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from app.domain.models import SubwayNetwork
from app.domain.models import WalkTransfer
from app.services.gis_route import extract_station_coordinates
from app.services.gis_route_geometry import _build_geojson_segment_index


from app.services.geo_utils import (
    BLOCK_POINT_SEGMENT_THRESHOLD_M,
    BLOCK_LINE_SEGMENT_THRESHOLD_M,
    haversine_distance_m,
    is_line_near_geometry,
)
from app.services.travel_defaults import DEFAULT_WALKING_M_PER_SEC


ADMIN_BLOCK_BYPASS_MAX_WALK_M = 1700.0
ADMIN_BLOCK_BYPASS_WALK_M_PER_SEC = DEFAULT_WALKING_M_PER_SEC
RAIN_SEVERITIES = {"light", "moderate", "heavy"}


def default_admin_scenarios() -> dict[str, Any]:
    return {
        "source": "server",
        "generated_at": None,
        "ui_mode": "rain",
        "map_bounds": None,
        "rain_zones": [],
        "block_segments": [],
        "banned_stations": [],
    }


def load_admin_scenarios(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return default_admin_scenarios()

    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default_admin_scenarios()

    return normalize_admin_scenarios(payload)


def save_admin_scenarios(path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_admin_scenarios(payload)
    temp_path = file_path.with_name(f".{file_path.name}.tmp")
    temp_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(file_path)
    return normalized


def normalize_admin_scenarios(payload: dict[str, Any] | None) -> dict[str, Any]:
    normalized = default_admin_scenarios()
    if not isinstance(payload, dict):
        return normalized

    normalized["source"] = str(payload.get("source", "server"))
    normalized["generated_at"] = payload.get("generated_at")
    normalized["ui_mode"] = str(payload.get("ui_mode", "rain"))
    normalized["map_bounds"] = payload.get("map_bounds")
    normalized["rain_zones"] = _normalize_rain_zones(payload.get("rain_zones"))
    normalized["block_segments"] = _normalize_block_segments(payload.get("block_segments"))
    normalized["banned_stations"] = _normalize_banned_stations(payload.get("banned_stations"))
    return normalized


def build_admin_scenario_effects(
    network: SubwayNetwork,
    gis_payload: dict[str, Any],
    scenarios: dict[str, Any],
) -> dict[str, Any]:
    scenarios = normalize_admin_scenarios(scenarios)
    station_coords_by_id = extract_station_coordinates(gis_payload.get("stations") or {})
    segment_index = _build_geojson_segment_index(
        gis_payload.get("stations"),
        gis_payload.get("lines"),
    )

    banned_station_ids = {
        station["id"]
        for station in scenarios.get("banned_stations", [])
        if station.get("id") in network.stations
    }
    rain_station_ids = _collect_rain_station_ids(
        station_coords_by_id,
        scenarios.get("rain_zones", []),
    )
    blocked_segment_keys = _collect_blocked_segment_keys(
        segment_index,
        scenarios.get("block_segments", []),
    )
    closed_station_ids = set(banned_station_ids)

    return {
        "has_active_incidents": bool(
            closed_station_ids
            or blocked_segment_keys
            or scenarios.get("rain_zones")
            or scenarios.get("block_segments")
        ),
        "closed_station_ids": sorted(closed_station_ids),
        "explicit_banned_station_ids": sorted(banned_station_ids),
        "rain_station_ids": sorted(rain_station_ids),
        "closed_segment_keys": sorted(blocked_segment_keys),
        "rain_zone_count": len(scenarios.get("rain_zones", [])),
        "block_segment_count": len(scenarios.get("block_segments", [])),
        "scenarios": scenarios,  # Preserve the original scenarios for metadata
    }


def apply_admin_scenarios_to_network(
    network: SubwayNetwork,
    effects: dict[str, Any],
) -> SubwayNetwork:
    closed_station_ids = set(effects.get("closed_station_ids", []))
    closed_segment_keys = set(effects.get("closed_segment_keys", []))

    stations = {
        station_id: station
        for station_id, station in network.stations.items()
        if station_id not in closed_station_ids
    }
    station_lines = [
        station_line
        for station_line in network.station_lines
        if station_line.station_id in stations
    ]

    line_ids_in_use = {station_line.line_id for station_line in station_lines}
    lines = {
        line_id: line
        for line_id, line in network.lines.items()
        if line_id in line_ids_in_use
    }
    station_lines = [
        station_line
        for station_line in station_lines
        if station_line.line_id in lines
    ]

    segments = [
        segment
        for segment in network.segments
        if segment.from_station_id in stations
        and segment.to_station_id in stations
        and segment.line_id in lines
        and _canonical_segment_key(
            segment.line_id,
            segment.from_station_id,
            segment.to_station_id,
        )
        not in closed_segment_keys
    ]

    station_line_pairs = {
        (station_line.station_id, station_line.line_id)
        for station_line in station_lines
    }
    transfers = [
        transfer
        for transfer in network.transfers
        if transfer.station_id in stations
        and transfer.from_line_id in lines
        and transfer.to_line_id in lines
        and (transfer.station_id, transfer.from_line_id) in station_line_pairs
        and (transfer.station_id, transfer.to_line_id) in station_line_pairs
    ]
    walk_transfers = [
        transfer
        for transfer in network.walk_transfers
        if transfer.from_station_id in stations
        and transfer.to_station_id in stations
    ]
    existing_walk_pairs = {
        (transfer.from_station_id, transfer.to_station_id)
        for transfer in walk_transfers
    }
    admin_walk_bypass_pairs: list[list[str]] = []
    for segment_key in closed_segment_keys:
        parts = segment_key.split(":")
        if len(parts) != 3:
            continue
        _, from_station_id, to_station_id = parts
        if from_station_id not in stations or to_station_id not in stations:
            continue
        distance_m = _station_distance_m(stations[from_station_id], stations[to_station_id])
        if distance_m <= 0 or distance_m > ADMIN_BLOCK_BYPASS_MAX_WALK_M:
            continue
        duration_sec = max(1, int(round(distance_m / ADMIN_BLOCK_BYPASS_WALK_M_PER_SEC)))
        for source_station_id, target_station_id in (
            (from_station_id, to_station_id),
            (to_station_id, from_station_id),
        ):
            if (source_station_id, target_station_id) in existing_walk_pairs:
                continue
            walk_transfers.append(
                WalkTransfer(
                    from_station_id=source_station_id,
                    to_station_id=target_station_id,
                    duration_sec=duration_sec,
                )
            )
            existing_walk_pairs.add((source_station_id, target_station_id))
            admin_walk_bypass_pairs.append([source_station_id, target_station_id])
    stops = {
        stop_id: stop
        for stop_id, stop in network.stops.items()
        if stop.station_id in stations
    }

    station_to_lines: dict[str, set[str]] = {}
    for station_line in station_lines:
        station_to_lines.setdefault(station_line.station_id, set()).add(station_line.line_id)

    return SubwayNetwork(
        stations=stations,
        lines=lines,
        station_lines=station_lines,
        segments=segments,
        transfers=transfers,
        stops=stops,
        walk_transfers=walk_transfers,
        station_to_lines=station_to_lines,
        metadata={
            **network.metadata,
            "admin_effects": {
                "closed_station_ids": sorted(closed_station_ids),
                "closed_segment_keys": sorted(closed_segment_keys),
                "block_segments": effects.get("scenarios", {}).get("block_segments", []),
                "walk_bypass_pairs": admin_walk_bypass_pairs,
            },
        },
    )


def _station_distance_m(station_a: Any, station_b: Any) -> float:
    if abs(station_a.y) <= 90 and abs(station_a.x) <= 180 and abs(station_b.y) <= 90 and abs(station_b.x) <= 180:
        return haversine_distance_m(station_a.y, station_a.x, station_b.y, station_b.x)
    return math.hypot(station_a.x - station_b.x, station_a.y - station_b.y)


def _normalize_rain_zones(raw_items: Any) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    if not isinstance(raw_items, list):
        return zones

    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        center = item.get("center")
        if not isinstance(center, dict):
            continue
        lon = _safe_float(center.get("lon"))
        lat = _safe_float(center.get("lat"))
        radius_m = int(round(_safe_float(item.get("radius_m"), 0.0)))
        if lon is None or lat is None or radius_m <= 0:
            continue
        zones.append(
            {
                "id": str(item.get("id") or f"rain-{index}"),
                "center": {"lon": round(lon, 6), "lat": round(lat, 6)},
                "radius_m": radius_m,
                "severity": _normalize_rain_severity(item.get("severity")),
            }
        )
    return zones


def _normalize_rain_severity(value: Any) -> str:
    severity = str(value or "moderate").strip().lower()
    if severity not in RAIN_SEVERITIES:
        return "moderate"
    return severity


def _normalize_block_segments(raw_items: Any) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    if not isinstance(raw_items, list):
        return segments

    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        from_point = _normalize_point(item.get("from"))
        to_point = _normalize_point(item.get("to"))
        if from_point is None or to_point is None:
            continue
        kind = "point" if str(item.get("kind")) == "point" else "line"
        segments.append(
            {
                "id": str(item.get("id") or f"block-{index}"),
                "kind": kind,
                "from": from_point,
                "to": to_point,
            }
        )
    return segments


def _normalize_banned_stations(raw_items: Any) -> list[dict[str, Any]]:
    banned: list[dict[str, Any]] = []
    if not isinstance(raw_items, list):
        return banned

    seen_ids: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        station_id = item.get("id")
        if not station_id:
            continue
        station_id = str(station_id)
        if station_id in seen_ids:
            continue
        seen_ids.add(station_id)
        banned.append({"id": station_id, "name": item.get("name")})
    return banned


def _normalize_point(raw_point: Any) -> dict[str, float] | None:
    if not isinstance(raw_point, dict):
        return None
    lon = _safe_float(raw_point.get("lon"))
    lat = _safe_float(raw_point.get("lat"))
    if lon is None or lat is None:
        return None
    return {"lon": round(lon, 6), "lat": round(lat, 6)}


def _collect_rain_station_ids(
    station_coords_by_id: dict[str, Coordinate],
    rain_zones: list[dict[str, Any]],
) -> set[str]:
    impacted: set[str] = set()
    for station_id, coordinate in station_coords_by_id.items():
        if any(_point_in_rain_zone(coordinate, zone) for zone in rain_zones):
            impacted.add(station_id)
    return impacted


def _collect_blocked_segment_keys(
    segment_index: dict[tuple[str, str, str], list[Coordinate]],
    block_segments: list[dict[str, Any]],
) -> set[str]:
    blocked: set[str] = set()

    for line_id, from_station_id, to_station_id in segment_index:
        coordinates = segment_index[(line_id, from_station_id, to_station_id)]
        canonical_key = _canonical_segment_key(line_id, from_station_id, to_station_id)
        if any(_polyline_matches_admin_block(coordinates, block) for block in block_segments):
            blocked.add(canonical_key)

    return blocked


def _point_in_rain_zone(point: Coordinate, zone: dict[str, Any]) -> bool:
    center = zone.get("center") or {}
    center_lon = _safe_float(center.get("lon"))
    center_lat = _safe_float(center.get("lat"))
    radius_m = _safe_float(zone.get("radius_m"), 0.0) or 0.0
    if center_lon is None or center_lat is None or radius_m <= 0:
        return False
    return haversine_distance_m(point[1], point[0], center_lat, center_lon) <= radius_m


def _polyline_matches_admin_block(
    coordinates: list[Coordinate],
    block: dict[str, Any],
) -> bool:
    from_point = block.get("from") or {}
    to_point = block.get("to") or {}
    from_coord = (_safe_float(from_point.get("lon")), _safe_float(from_point.get("lat")))
    to_coord = (_safe_float(to_point.get("lon")), _safe_float(to_point.get("lat")))
    if None in from_coord or None in to_coord:
        return False

    block_start = (float(from_coord[0]), float(from_coord[1]))
    block_end = (float(to_coord[0]), float(to_coord[1]))
    block_kind = str(block.get("kind") or "line")

    if block_kind == "point":
        from app.services.geo_utils import point_to_segment_distance_m
        if any(
            haversine_distance_m(point[1], point[0], block_start[1], block_start[0])
            <= BLOCK_POINT_SEGMENT_THRESHOLD_M
            for point in coordinates
        ):
            return True
        for start, end in zip(coordinates, coordinates[1:], strict=False):
            if point_to_segment_distance_m(block_start, start, end) <= BLOCK_POINT_SEGMENT_THRESHOLD_M:
                return True
        return False

    return _is_line_near_segment(block_start, block_end, coordinates)


def _is_line_near_segment(
    block_start: tuple[float, float],
    block_end: tuple[float, float],
    coordinates: list[tuple[float, float]],
) -> bool:
    return is_line_near_geometry(block_start, block_end, coordinates, BLOCK_LINE_SEGMENT_THRESHOLD_M)


def _canonical_segment_key(line_id: str, from_station_id: str, to_station_id: str) -> str:
    left_station_id, right_station_id = sorted((from_station_id, to_station_id))
    return f"{line_id}:{left_station_id}:{right_station_id}"


def _safe_float(raw_value: Any, default: float | None = None) -> float | None:
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return default
