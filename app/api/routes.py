from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from pydantic import BaseModel
from pydantic import Field

from fastapi import APIRouter
from fastapi import HTTPException

from app.services.route_engine import RouteEngine

from app.config import get_settings
from app.services.geo_utils import haversine_distance_m
from app.services.gis_loader import build_gis_payload
from app.services.gis_loader import get_cached_walk_graph
from app.services.gis_station_store import delete_gis_station as delete_gis_station_in_store
from app.services.gis_station_store import save_gis_station_positions
from app.services.gis_route import extract_station_coordinates
from app.services.gis_route_geometry import build_route_geometry_features
from app.services.gis_runtime_artifacts import load_or_build_gis_runtime_artifacts
from app.services.geo_utils import walking_time_sec
from app.services.travel_defaults import DEFAULT_WALKING_M_PER_SEC
from app.services.walk_network import (
    build_walk_graph,
    build_walk_targets_by_node,
    find_candidate_stations_by_walk,
    find_walk_path,
)
from app.services.runtime import (
    get_network as get_subway_network,
    get_route_engine,
    refresh_runtime_caches,
)
from app.services.admin_scenarios import (
    load_admin_scenarios,
    default_admin_scenarios,
    normalize_admin_scenarios,
    build_admin_scenario_effects,
    apply_admin_scenarios_to_network,
)

router = APIRouter(prefix="/api", tags=["subway"])
settings = get_settings()
logger = logging.getLogger(__name__)
WALK_DISCOMFORT_FACTOR = 1.5
TRANSFER_COMFORT_PENALTY_SEC = 2 * 60
WALK_COMPARE_TIME_SEC = 20 * 60
METRO_MIN_SHORT_WALK_SAVING_SEC = 7 * 60
METRO_ALLOWED_SLOWER_SEC = 25 * 60
NORMAL_MAX_ACCESS_WALK_M = 1200.0
STRATEGIC_MAX_ACCESS_WALK_M = 3000.0
_GIS_ROUTE_CONTEXT_CACHE: dict[str, GisRouteContext] = {}
_GIS_ROUTE_CONTEXT_CACHE_MAXSIZE = 4
RAIN_SEVERITY_RULES = {
    "light": {
        "label": "Light",
        "walking_multiplier": 1.5,
        "station_access_penalty_sec": 45,
    },
    "moderate": {
        "label": "Moderate",
        "walking_multiplier": 2.5,
        "station_access_penalty_sec": 120,
    },
    "heavy": {
        "label": "Heavy",
        "walking_multiplier": 4.0,
        "station_access_penalty_sec": 240,
    },
}


class RouteRequest(BaseModel):
    start_station_id: str
    end_station_id: str
    via_station_ids: list[str] = Field(default_factory=list)


class PointRouteRequest(BaseModel):
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    walking_seconds_per_pixel: float = 1.0
    candidate_limit: int | None = None
    max_station_walk_sec: int | None = None
    start_preferred_line_ids: list[str] = Field(default_factory=list)
    end_preferred_line_ids: list[str] = Field(default_factory=list)
    via_station_ids: list[str] = Field(default_factory=list)


class GisPointRouteRequest(BaseModel):
    start_lon: float
    start_lat: float
    end_lon: float
    end_lat: float
    walking_m_per_sec: float = DEFAULT_WALKING_M_PER_SEC
    via_station_ids: list[str] = Field(default_factory=list)
    admin_scenarios: dict | None = None


class GisStationPositionPayload(BaseModel):
    id: str
    lon: float
    lat: float
    deleted: bool = False


class GisStationSaveRequest(BaseModel):
    stations: list[GisStationPositionPayload]


class CalibrationStationPayload(BaseModel):
    id: str
    x: float
    y: float


class CalibrationSaveRequest(BaseModel):
    stations: list[CalibrationStationPayload]


class BuilderStationPayload(BaseModel):
    id: str
    name: str
    x: float
    y: float


class BuilderLinePayload(BaseModel):
    id: str
    name: str
    color: str


class BuilderStationLinePayload(BaseModel):
    station_id: str
    line_id: str
    seq: int


class BuilderNetworkSaveRequest(BaseModel):
    stations: list[BuilderStationPayload]
    lines: list[BuilderLinePayload]
    station_lines: list[BuilderStationLinePayload]
    default_travel_sec: int = 90
    default_transfer_sec: int = 180


class AdminScenarioSaveRequest(BaseModel):
    source: str = "client"
    ui_mode: str = "rain"
    rain_zones: list[dict] = Field(default_factory=list)
    block_segments: list[dict] = Field(default_factory=list)
    banned_stations: list[dict] = Field(default_factory=list)
    generated_at: str | None = None
    map_bounds: dict | None = None


@dataclass(frozen=True)
class GisRouteContext:
    payload: dict
    station_coords_by_id: dict[str, tuple[float, float]]
    walk_graph: object
    walk_targets_by_node: dict
    station_lookup: dict[str, dict] | None = None
    geojson_segment_index: dict | None = None
    geojson_line_colors: dict | None = None


def _raise_legacy_api_removed() -> None:
    raise HTTPException(
        status_code=410,
        detail="Legacy studio API has been removed. Use /api/gis/* instead.",
    )


def _network_payload() -> dict:
    network = get_subway_network()
    return {
        "map": {
            "image_url": f"/map/{settings.map_image_name}",
            "width": settings.map_width,
            "height": settings.map_height,
            "raster_width": settings.map_width,
            "raster_height": settings.map_height,
            "is_vector": settings.map_is_vector,
            "supports_line_hints": settings.map_supports_line_hints,
            "max_zoom": settings.map_max_zoom,
            "title": "Taipei vector map background",
        },
        "diagram": {
            "svg_url": f"/map/{settings.diagram_svg_name}",
            "width": settings.diagram_width,
            "height": settings.diagram_height,
            "raster_width": settings.diagram_raster_width,
            "raster_height": settings.diagram_raster_height,
            "is_vector": settings.diagram_is_vector,
            "max_zoom": settings.diagram_max_zoom,
            "title": "Taipei MRT semantic SVG diagram",
        },
        "stations": [
            {
                "id": station.id,
                "name": station.name,
                "x": station.x,
                "y": station.y,
                "diagram_x": station.diagram_x,
                "diagram_y": station.diagram_y,
                "line_ids": sorted(network.station_to_lines.get(station.id, set())),
            }
            for station in sorted(network.stations.values(), key=lambda item: item.name)
        ],
        "lines": [
            {"id": line.id, "name": line.name, "color": line.color}
            for line in network.lines.values()
        ],
        "segments": [
            {
                "line_id": segment.line_id,
                "from_station_id": segment.from_station_id,
                "to_station_id": segment.to_station_id,
                "travel_sec": segment.travel_sec,
            }
            for segment in network.segments
        ],
        "source": network.metadata.get("source_kind", "json"),
    }


def _station_lookup_payload() -> dict[str, dict]:
    return {
        station["id"]: station
        for station in _network_payload()["stations"]
    }


def _build_network_payload_from_builder(request: BuilderNetworkSaveRequest) -> dict:
    runtime_network = get_subway_network()
    existing_station_lookup = {
        station.id: {
            "x": station.x,
            "y": station.y,
        }
        for station in runtime_network.stations.values()
    }

    station_ids = [station.id for station in request.stations]
    line_ids = [line.id for line in request.lines]

    if not station_ids:
        # Safety Guard: Prevent accidental wipe of the entire network
        raise HTTPException(
            status_code=400, 
            detail="Cannot save an empty network. If you intended to clear everything, please add at least one dummy station."
        )

    if len(station_ids) != len(set(station_ids)):
        raise HTTPException(status_code=400, detail="Duplicate station id detected")
    if len(line_ids) != len(set(line_ids)):
        raise HTTPException(status_code=400, detail="Duplicate line id detected")
    if request.default_travel_sec <= 0:
        raise HTTPException(status_code=400, detail="default_travel_sec must be > 0")
    if request.default_transfer_sec <= 0:
        raise HTTPException(status_code=400, detail="default_transfer_sec must be > 0")

    known_station_ids = set(station_ids)
    known_line_ids = set(line_ids)

    line_membership: dict[str, list[BuilderStationLinePayload]] = {}
    for station_line in request.station_lines:
        if station_line.station_id not in known_station_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown station_id in station_lines: {station_line.station_id}",
            )
        if station_line.line_id not in known_line_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown line_id in station_lines: {station_line.line_id}",
            )
        if station_line.seq <= 0:
            raise HTTPException(status_code=400, detail="station_lines seq must be > 0")

        line_membership.setdefault(station_line.line_id, []).append(station_line)

    segments: list[dict] = []
    station_to_lines: dict[str, set[str]] = {}

    for line_id, station_lines in line_membership.items():
        ordered = sorted(station_lines, key=lambda item: (item.seq, item.station_id))
        seen_station_ids: set[str] = set()
        ordered_station_ids: list[str] = []

        for station_line in ordered:
            if station_line.station_id in seen_station_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"Duplicate station {station_line.station_id} on line {line_id}",
                )
            seen_station_ids.add(station_line.station_id)
            ordered_station_ids.append(station_line.station_id)
            station_to_lines.setdefault(station_line.station_id, set()).add(line_id)

        for from_station_id, to_station_id in zip(ordered_station_ids, ordered_station_ids[1:], strict=False):
            segments.append(
                {
                    "line_id": line_id,
                    "from_station_id": from_station_id,
                    "to_station_id": to_station_id,
                    "travel_sec": request.default_travel_sec,
                }
            )

    transfers: list[dict] = []
    for station_id, station_line_ids in sorted(station_to_lines.items()):
        ordered_line_ids = sorted(station_line_ids)
        for from_line_id in ordered_line_ids:
            for to_line_id in ordered_line_ids:
                if from_line_id == to_line_id:
                    continue
                transfers.append(
                    {
                        "station_id": station_id,
                        "from_line_id": from_line_id,
                        "to_line_id": to_line_id,
                        "transfer_sec": request.default_transfer_sec,
                    }
                )

    return {
        "stations": [
            {
                "id": station.id,
                "name": station.name,
                "x": existing_station_lookup.get(station.id, {}).get("x", station.x),
                "y": existing_station_lookup.get(station.id, {}).get("y", station.y),
                "diagram_x": station.x,
                "diagram_y": station.y,
            }
            for station in request.stations
        ],
        "lines": [
            {
                "id": line.id,
                "name": line.name,
                "color": line.color,
            }
            for line in request.lines
        ],
        "station_lines": [
            {
                "station_id": station_line.station_id,
                "line_id": station_line.line_id,
                "seq": station_line.seq,
            }
            for station_line in sorted(
                request.station_lines,
                key=lambda item: (item.line_id, item.seq, item.station_id),
            )
        ],
        "segments": segments,
        "transfers": transfers,
        "metadata": {
            "source_kind": "builder",
        },
    }


def _feature_station_id(feature: dict) -> str | None:
    properties = feature.get("properties", {}) or {}
    station_id = properties.get("id") or properties.get("station_id")
    return str(station_id) if station_id else None


def _filter_geojson_features_by_ids(payload: dict, valid_ids: set[str]) -> dict:
    features = payload.get("features", [])
    filtered_features = [
        feature
        for feature in features
        if (feature_id := _feature_station_id(feature)) and feature_id in valid_ids
    ]
    return {
        **payload,
        "features": filtered_features,
    }


def _build_station_catalog_from_geojson(stations_geojson: dict, valid_ids: set[str]) -> list[dict]:
    catalog: list[dict] = []
    for feature in stations_geojson.get("features", []):
        station_id = _feature_station_id(feature)
        if not station_id or station_id not in valid_ids:
            continue
        coordinates = feature.get("geometry", {}).get("coordinates", [None, None])
        properties = feature.get("properties", {}) or {}
        catalog.append(
            {
                "id": station_id,
                "name": properties.get("name"),
                "line_ids": list(properties.get("line_ids") or []),
                "lon": coordinates[0],
                "lat": coordinates[1],
            }
        )
    return catalog


def _build_line_catalog_from_network(network) -> list[dict]:
    return [
        {"id": line.id, "name": line.name, "color": line.color}
        for line in network.lines.values()
    ]


def _build_route_object_fallback(
    station_ids: list[str],
) -> dict:
    compact_station_ids: list[str] = []
    for station_id in station_ids:
        if compact_station_ids and compact_station_ids[-1] == station_id:
            continue
        compact_station_ids.append(station_id)
    if not compact_station_ids:
        return {}
    return {
        "total_time_sec": 0,
        "walking_time_sec": 0,
        "transfer_count": 0,
        "stop_count": max(0, len(compact_station_ids) - 1),
        "station_ids": compact_station_ids,
        "line_sequence": [],
        "steps": [],
    }


def _filter_reasonable_walk_candidates(
    candidates: list,
    *,
    max_ratio: float = 2.0,
    max_extra_m: float = 300.0,
    max_distance_m: float | None = None,
    fallback_count: int = 1,
) -> list:
    if not candidates:
        return []
    best_distance_m = candidates[0].distance_m
    threshold_m = max(best_distance_m * max_ratio, best_distance_m + max_extra_m)
    filtered = [
        candidate
        for candidate in candidates
        if candidate.distance_m <= threshold_m
    ]
    if max_distance_m is None:
        return filtered
    capped = [
        candidate
        for candidate in filtered
        if candidate.distance_m <= max_distance_m
    ]
    return capped or filtered[:max(1, fallback_count)]


def _exclude_station_candidates(candidates: list, rejected_station_ids: set[str]) -> list:
    if not rejected_station_ids:
        return candidates
    return [
        candidate
        for candidate in candidates
        if candidate.station_id not in rejected_station_ids
    ]


def _rain_zones_from_effects(admin_effects: dict | None) -> list[dict]:
    scenarios = (admin_effects or {}).get("scenarios") or {}
    rain_zones = scenarios.get("rain_zones") or []
    return rain_zones if isinstance(rain_zones, list) else []


def _rain_severity_rule(zone: dict) -> dict:
    severity = str(zone.get("severity") or "moderate").lower()
    return RAIN_SEVERITY_RULES.get(severity, RAIN_SEVERITY_RULES["moderate"])


def _rain_zone_contains_point(point: tuple[float, float], zone: dict) -> bool:
    center = zone.get("center") or {}
    center_lon = center.get("lon")
    center_lat = center.get("lat")
    radius_m = zone.get("radius_m") or 0
    if center_lon is None or center_lat is None or radius_m <= 0:
        return False
    return haversine_distance_m(point[1], point[0], center_lat, center_lon) <= radius_m


def _strongest_rain_rule_for_points(points: list[tuple[float, float]], rain_zones: list[dict]) -> tuple[str | None, dict | None]:
    strongest_severity = None
    strongest_rule = None
    strongest_multiplier = 1.0
    for zone in rain_zones:
        if not any(_rain_zone_contains_point(point, zone) for point in points):
            continue
        rule = _rain_severity_rule(zone)
        if rule["walking_multiplier"] > strongest_multiplier:
            strongest_severity = str(zone.get("severity") or "moderate").lower()
            strongest_rule = rule
            strongest_multiplier = rule["walking_multiplier"]
    return strongest_severity, strongest_rule


def _rain_penalty_for_path(
    path_coordinates: list[tuple[float, float]],
    rain_zones: list[dict],
    walking_m_per_sec: float,
    *,
    access_point_coordinate: tuple[float, float] | None = None,
    include_station_access_penalty: bool = False,
) -> dict:
    if not rain_zones or walking_m_per_sec <= 0:
        return {
            "penalty_sec": 0,
            "affected_distance_m": 0.0,
            "severity": None,
            "walking_multiplier": 1.0,
            "station_access_penalty_sec": 0,
        }

    extra_time_sec = 0.0
    affected_distance_m = 0.0
    strongest_severity = None
    strongest_multiplier = 1.0

    # Overlapping rain zones should not stack. Each walking segment uses the
    # strongest zone touching that segment, then moves on to the next segment.
    for start, end in zip(path_coordinates, path_coordinates[1:], strict=False):
        midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        severity, rule = _strongest_rain_rule_for_points([start, midpoint, end], rain_zones)
        if rule is None:
            continue
        distance_m = haversine_distance_m(start[1], start[0], end[1], end[0])
        affected_distance_m += distance_m
        multiplier = rule["walking_multiplier"]
        extra_time_sec += (distance_m / walking_m_per_sec) * (multiplier - 1.0)
        if multiplier > strongest_multiplier:
            strongest_multiplier = multiplier
            strongest_severity = severity

    station_access_penalty_sec = 0
    access_rule = None
    if include_station_access_penalty and access_point_coordinate is not None:
        severity, access_rule = _strongest_rain_rule_for_points([access_point_coordinate], rain_zones)
    if access_rule is not None:
        station_access_penalty_sec = access_rule["station_access_penalty_sec"]
        if access_rule["walking_multiplier"] > strongest_multiplier:
            strongest_multiplier = access_rule["walking_multiplier"]
            strongest_severity = severity

    penalty_sec = int(round(extra_time_sec + station_access_penalty_sec))
    return {
        "penalty_sec": penalty_sec,
        "affected_distance_m": round(affected_distance_m, 1),
        "severity": strongest_severity,
        "walking_multiplier": strongest_multiplier,
        "station_access_penalty_sec": station_access_penalty_sec,
    }


def _rain_penalty_for_walk(candidate, rain_zones: list[dict], walking_m_per_sec: float) -> dict:
    return _rain_penalty_for_path(
        list(candidate.path_coordinates or []),
        rain_zones,
        walking_m_per_sec,
        access_point_coordinate=candidate.access_point_coordinate,
        include_station_access_penalty=True,
    )


def _line_distance_m(coordinates: list[tuple[float, float]]) -> float:
    distance_m = 0.0
    for start, end in zip(coordinates, coordinates[1:], strict=False):
        distance_m += haversine_distance_m(start[1], start[0], end[1], end[0])
    return distance_m


def _build_walk_only_option(
    *,
    start_lon: float,
    start_lat: float,
    end_lon: float,
    end_lat: float,
    walk_graph,
    walking_m_per_sec: float,
    rain_zones: list[dict] | None = None,
) -> dict:
    path_coordinates = find_walk_path(
        start_lon,
        start_lat,
        end_lon,
        end_lat,
        walk_graph,
        settings=settings,
    )
    if path_coordinates:
        distance_m = _line_distance_m(path_coordinates)
        distance_source = "walk_graph"
    else:
        distance_m = haversine_distance_m(start_lat, start_lon, end_lat, end_lon) * 1.25
        distance_source = "geo_fallback"
        path_coordinates = [(start_lon, start_lat), (end_lon, end_lat)]

    walk_time_sec = walking_time_sec(distance_m, walking_m_per_sec)
    rain_penalty = _rain_penalty_for_path(
        list(path_coordinates or []),
        rain_zones or [],
        walking_m_per_sec,
    )
    total_time_sec = walk_time_sec + rain_penalty["penalty_sec"]
    return {
        "path_coordinates": path_coordinates,
        "distance_m": distance_m,
        "distance_source": distance_source,
        "walk_time_sec": walk_time_sec,
        "rain_penalty": rain_penalty,
        "total_time_sec": total_time_sec,
        "has_road_path": bool(path_coordinates),
    }


def _build_walk_only_response(
    *,
    gis_payload: dict,
    request: GisPointRouteRequest,
    walk_option: dict,
    warnings: list[str] | None = None,
    selection_reason: str = "walk_only",
) -> dict:
    path_coordinates = walk_option.get("path_coordinates") or []
    return {
        "source": gis_payload["source"],
        "journey_mode": "walk_fallback",
        "route_selection_reason": selection_reason,
        "start_point": {"lon": request.start_lon, "lat": request.start_lat},
        "end_point": {"lon": request.end_lon, "lat": request.end_lat},
        "total_journey_time_sec": walk_option["total_time_sec"],
        "base_journey_time_sec": walk_option["walk_time_sec"],
        "access_walk_path": {
            "type": "LineString",
            "coordinates": [[lon, lat] for lon, lat in path_coordinates],
        },
        "access_walk_distance_m": round(walk_option["distance_m"], 1),
        "access_walk_time_sec": walk_option["walk_time_sec"],
        "rain_penalty_sec": walk_option["rain_penalty"]["penalty_sec"],
        "rain_walk_details": {
            "direct": walk_option["rain_penalty"],
        },
        "route_diagnostics": {
            "subway_time_sec": 0,
            "walking_time_sec": walk_option["walk_time_sec"],
            "rain_penalty_sec": walk_option["rain_penalty"]["penalty_sec"],
            "transfer_count": 0,
            "scenario_mode": "soft_penalty" if walk_option["rain_penalty"]["penalty_sec"] else "normal",
            "mode_decision": selection_reason,
            "walk_only_distance_m": round(walk_option["distance_m"], 1),
            "walk_only_time_sec": walk_option["walk_time_sec"],
            "walk_only_distance_source": walk_option["distance_source"],
            "walk_compare_time_sec": WALK_COMPARE_TIME_SEC,
            "metro_min_short_walk_saving_sec": METRO_MIN_SHORT_WALK_SAVING_SEC,
            "metro_allowed_slower_sec": METRO_ALLOWED_SLOWER_SEC,
        },
        "route": {
            "total_time_sec": walk_option["walk_time_sec"],
            "walking_time_sec": walk_option["walk_time_sec"],
            "transfer_count": 0,
            "stop_count": 0,
            "station_ids": [],
            "line_sequence": [],
            "steps": [
                {
                    "kind": "walk",
                    "station_id": None,
                    "next_station_id": None,
                    "duration_sec": walk_option["walk_time_sec"],
                    "coordinates": path_coordinates,
                }
            ],
        },
        "warnings": sorted(set(warnings or [])),
    }


def _is_long_same_station_walk_pair(start_candidate, end_candidate) -> bool:
    return (
        start_candidate.station_id == end_candidate.station_id
        and max(start_candidate.distance_m, end_candidate.distance_m) > 300.0
    )


def _route_candidate_evaluation(
    route_result,
    start_candidate,
    end_candidate,
    walking_m_per_sec: float,
    rain_zones: list[dict] | None = None,
    *,
    candidate_set: str,
) -> dict:
    access_walk_time = walking_time_sec(start_candidate.distance_m, walking_m_per_sec)
    egress_walk_time = walking_time_sec(end_candidate.distance_m, walking_m_per_sec)
    access_rain = _rain_penalty_for_walk(start_candidate, rain_zones or [], walking_m_per_sec)
    egress_rain = _rain_penalty_for_walk(end_candidate, rain_zones or [], walking_m_per_sec)
    rain_penalty_sec = access_rain["penalty_sec"] + egress_rain["penalty_sec"]
    route_walk_time = route_result.walking_time_sec
    ride_and_transfer_time = route_result.total_time_sec - route_walk_time
    point_walk_time = access_walk_time + egress_walk_time
    total_walk_time = route_walk_time + point_walk_time
    walking_discomfort_cost = int(round(total_walk_time * WALK_DISCOMFORT_FACTOR))
    transfer_comfort_cost = route_result.transfer_count * TRANSFER_COMFORT_PENALTY_SEC
    actual_time = route_result.total_time_sec + point_walk_time + rain_penalty_sec
    selection_cost = actual_time + walking_discomfort_cost + transfer_comfort_cost
    return {
        "candidate_set": candidate_set,
        "start_station_id": start_candidate.station_id,
        "end_station_id": end_candidate.station_id,
        "access_walk_m": round(start_candidate.distance_m, 1),
        "egress_walk_m": round(end_candidate.distance_m, 1),
        "access_walk_time_sec": access_walk_time,
        "egress_walk_time_sec": egress_walk_time,
        "subway_time_sec": route_result.total_time_sec,
        "ride_and_transfer_time_sec": ride_and_transfer_time,
        "route_walk_time_sec": route_walk_time,
        "point_walk_time_sec": point_walk_time,
        "total_walk_time_sec": total_walk_time,
        "transfer_count": route_result.transfer_count,
        "stop_count": route_result.stop_count,
        "walk_discomfort_factor": WALK_DISCOMFORT_FACTOR,
        "walking_discomfort_cost_sec": walking_discomfort_cost,
        "transfer_comfort_penalty_sec": transfer_comfort_cost,
        "rain_penalty_sec": rain_penalty_sec,
        "actual_time_sec": actual_time,
        "selection_cost_sec": selection_cost,
        "status": "candidate",
    }


def _append_candidate_diagnostic(
    diagnostics: list[dict] | None,
    item: dict,
) -> None:
    if diagnostics is not None:
        diagnostics.append(item)


def _summarize_candidate_diagnostics(
    diagnostics: list[dict],
    selected_start_station_id: str | None,
    selected_end_station_id: str | None,
    selected_candidate_set: str | None,
    *,
    limit: int = 12,
) -> list[dict]:
    summarized: list[dict] = []
    for item in diagnostics:
        copy = dict(item)
        is_selected = (
            selected_start_station_id is not None
            and selected_end_station_id is not None
            and copy.get("start_station_id") == selected_start_station_id
            and copy.get("end_station_id") == selected_end_station_id
            and copy.get("candidate_set") == selected_candidate_set
        )
        if is_selected:
            copy["selected"] = True
            copy["status"] = "selected"
            copy["reject_reason"] = None
        elif copy.get("status") == "candidate":
            copy["selected"] = False
            copy["reject_reason"] = "higher_score"
        summarized.append(copy)

    summarized.sort(
        key=lambda item: (
            0 if item.get("selected") else 1,
            item.get("selection_cost_sec", float("inf")),
            item.get("access_walk_m", float("inf")) + item.get("egress_walk_m", float("inf")),
            item.get("candidate_set") or "",
            item.get("start_station_id") or "",
            item.get("end_station_id") or "",
        )
    )
    return summarized[:limit]


def _find_best_candidate_route(
    *,
    engine,
    start_candidates: list,
    end_candidates: list,
    via_station_ids: list[str],
    walking_m_per_sec: float,
    rain_zones: list[dict] | None = None,
    diagnostics: list[dict] | None = None,
    candidate_set: str = "strategic",
) -> tuple[object | None, object | None, object | None, float]:
    best_candidate_pair = None
    best_route_result = None
    best_total_cost = float("inf")

    for s_cand in start_candidates:
        for e_cand in end_candidates:
            if (
                _is_long_same_station_walk_pair(s_cand, e_cand)
                and (len(start_candidates) > 1 or len(end_candidates) > 1)
            ):
                _append_candidate_diagnostic(
                    diagnostics,
                    {
                        "candidate_set": candidate_set,
                        "start_station_id": s_cand.station_id,
                        "end_station_id": e_cand.station_id,
                        "access_walk_m": round(s_cand.distance_m, 1),
                        "egress_walk_m": round(e_cand.distance_m, 1),
                        "status": "rejected",
                        "reject_reason": "same_station_requires_long_walk",
                    },
                )
                continue
            try:
                route_result = engine.find_route_through_stations(
                    [
                        s_cand.station_id,
                        *via_station_ids,
                        e_cand.station_id,
                    ]
                )
            except ValueError:
                _append_candidate_diagnostic(
                    diagnostics,
                    {
                        "candidate_set": candidate_set,
                        "start_station_id": s_cand.station_id,
                        "end_station_id": e_cand.station_id,
                        "access_walk_m": round(s_cand.distance_m, 1),
                        "egress_walk_m": round(e_cand.distance_m, 1),
                        "status": "rejected",
                        "reject_reason": "no_subway_path",
                    },
                )
                continue

            evaluation = _route_candidate_evaluation(
                route_result,
                s_cand,
                e_cand,
                walking_m_per_sec,
                rain_zones,
                candidate_set=candidate_set,
            )
            _append_candidate_diagnostic(diagnostics, evaluation)
            total_cost = evaluation["selection_cost_sec"]
            if total_cost < best_total_cost:
                best_candidate_pair = (s_cand, e_cand)
                best_route_result = route_result
                best_total_cost = total_cost

    if best_candidate_pair is None:
        return None, None, None, float("inf")
    return best_candidate_pair[0], best_candidate_pair[1], best_route_result, best_total_cost


def _find_first_candidate_route(
    *,
    engine,
    start_candidates: list,
    end_candidates: list,
    via_station_ids: list[str],
    walking_m_per_sec: float,
    rain_zones: list[dict] | None = None,
    diagnostics: list[dict] | None = None,
    candidate_set: str = "normal",
) -> tuple[object | None, object | None, object | None, float]:
    best_candidate_pair = None
    best_route_result = None
    best_total_cost = float("inf")

    for s_cand in start_candidates:
        for e_cand in end_candidates:
            if (
                _is_long_same_station_walk_pair(s_cand, e_cand)
                and (len(start_candidates) > 1 or len(end_candidates) > 1)
            ):
                _append_candidate_diagnostic(
                    diagnostics,
                    {
                        "candidate_set": candidate_set,
                        "start_station_id": s_cand.station_id,
                        "end_station_id": e_cand.station_id,
                        "access_walk_m": round(s_cand.distance_m, 1),
                        "egress_walk_m": round(e_cand.distance_m, 1),
                        "status": "rejected",
                        "reject_reason": "same_station_requires_long_walk",
                    },
                )
                continue
            try:
                route_result = engine.find_route_through_stations(
                    [
                        s_cand.station_id,
                        *via_station_ids,
                        e_cand.station_id,
                    ]
                )
            except ValueError:
                _append_candidate_diagnostic(
                    diagnostics,
                    {
                        "candidate_set": candidate_set,
                        "start_station_id": s_cand.station_id,
                        "end_station_id": e_cand.station_id,
                        "access_walk_m": round(s_cand.distance_m, 1),
                        "egress_walk_m": round(e_cand.distance_m, 1),
                        "status": "rejected",
                        "reject_reason": "no_subway_path",
                    },
                )
                continue
            evaluation = _route_candidate_evaluation(
                route_result,
                s_cand,
                e_cand,
                walking_m_per_sec,
                rain_zones,
                candidate_set=candidate_set,
            )
            _append_candidate_diagnostic(diagnostics, evaluation)
            total_cost = evaluation["selection_cost_sec"]
            if total_cost < best_total_cost:
                best_candidate_pair = (s_cand, e_cand)
                best_route_result = route_result
                best_total_cost = total_cost
    if best_candidate_pair is None:
        return None, None, None, float("inf")
    return best_candidate_pair[0], best_candidate_pair[1], best_route_result, best_total_cost


def _build_station_attempts(
    candidates: list,
    selected_station_id: str,
    rejected_station_ids: list[str],
    rain_zones: list[dict] | None = None,
    walking_m_per_sec: float = DEFAULT_WALKING_M_PER_SEC,
) -> list[dict]:
    attempts = [
        {"station_id": station_id, "status": "rejected", "reason": "station_closed"}
        for station_id in rejected_station_ids
    ]
    seen = set(rejected_station_ids)
    for candidate in candidates:
        if candidate.station_id in seen:
            continue
        item = {
            "station_id": candidate.station_id,
            "status": "selected" if candidate.station_id == selected_station_id else "candidate",
            "distance_m": round(candidate.distance_m, 1),
        }
        rain_penalty = _rain_penalty_for_walk(candidate, rain_zones or [], walking_m_per_sec)
        if rain_penalty["penalty_sec"] > 0:
            item["rain_penalty_sec"] = rain_penalty["penalty_sec"]
            item["rain_severity"] = rain_penalty["severity"]
            item["rain_affected_distance_m"] = rain_penalty["affected_distance_m"]
        attempts.append(item)
        seen.add(candidate.station_id)
    return attempts


def _build_fallback_ride_path_features(
    route_station_ids: list[str],
    station_coords_by_id: dict[str, tuple[float, float]],
) -> list[dict]:
    coordinates = [
        station_coords_by_id.get(station_id)
        for station_id in route_station_ids
    ]
    filtered = [item for item in coordinates if item is not None]
    deduped: list[tuple[float, float]] = []
    for lon, lat in filtered:
        coordinate = (float(lon), float(lat))
        if deduped and deduped[-1] == coordinate:
            continue
        deduped.append(coordinate)
    if len(deduped) < 2:
        return []
    return [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[lon, lat] for lon, lat in deduped],
            },
            "properties": {"kind": "ride"},
        }
    ]


def _build_gis_route_context(network: object, signature: str) -> GisRouteContext:
    fallback_bounds = (
        settings.fallback_min_lon,
        settings.fallback_min_lat,
        settings.fallback_max_lon,
        settings.fallback_max_lat,
    )
    admin_effects = network.metadata.get("admin_effects", {})
    gis_payload = build_gis_payload(
        network=network,
        qgis_geojson_dir=settings.qgis_geojson_dir,
        map_width=settings.map_width,
        map_height=settings.map_height,
        fallback_bounds=fallback_bounds,
        include_station_access_points=True,
        include_walk_network=False,
        closed_segment_keys=set(admin_effects.get("closed_segment_keys", [])),
        block_segments=admin_effects.get("block_segments", []),
    )
    station_coords_by_id = extract_station_coordinates(gis_payload["stations"])
    walk_graph = (
        build_walk_graph(gis_payload.get("walk_network"))
        if gis_payload.get("walk_network") is not None
        else get_cached_walk_graph(settings.qgis_geojson_dir)
    )
    runtime_artifacts = load_or_build_gis_runtime_artifacts(
        project_root=settings.project_root,
        qgis_geojson_dir=settings.qgis_geojson_dir,
        gis_payload=gis_payload,
        station_coords_by_id=station_coords_by_id,
        signature=signature,
    )
    return GisRouteContext(
        payload=gis_payload,
        station_coords_by_id=station_coords_by_id,
        walk_graph=walk_graph,
        walk_targets_by_node=(
            build_walk_targets_by_node(
                walk_graph,
                gis_payload.get("station_access_points"),
                station_coords_by_id,
            )
            if walk_graph is not None
            else {}
        ),
        station_lookup=runtime_artifacts.station_lookup,
        geojson_segment_index=runtime_artifacts.geojson_segment_index,
        geojson_line_colors=runtime_artifacts.geojson_line_colors,
    )


def _path_signature(path: Path) -> str:
    if not path.exists():
        return f"{path}:missing"
    stat = path.stat()
    return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"


def _build_gis_route_context_signature() -> str:
    positions_path = settings.station_positions_file if settings.station_positions_file.exists() else None
    enrichment_path = settings.osm_enrichment_file if settings.osm_enrichment_file.exists() else None
    qgis_geojson_dir = settings.qgis_geojson_dir
    parts = [
        _path_signature(settings.data_file),
        _path_signature(qgis_geojson_dir / "stations.geojson"),
        _path_signature(qgis_geojson_dir / "lines.geojson"),
        _path_signature(qgis_geojson_dir / "station_access_points.geojson"),
        _path_signature(qgis_geojson_dir / "walk_network.geojson"),
    ]
    if positions_path is not None:
        parts.append(_path_signature(positions_path))
    if enrichment_path is not None:
        parts.append(_path_signature(enrichment_path))
    parts.append(_path_signature(settings.admin_scenarios_file))
    return "|".join(parts)



def get_gis_route_context(network: object) -> GisRouteContext:
    signature = f"{_build_gis_route_context_signature()}|network:{id(network)}"
    cached = _GIS_ROUTE_CONTEXT_CACHE.get(signature)
    if cached is not None:
        return cached

    context = _build_gis_route_context(network, signature)
    _GIS_ROUTE_CONTEXT_CACHE[signature] = context
    if len(_GIS_ROUTE_CONTEXT_CACHE) > _GIS_ROUTE_CONTEXT_CACHE_MAXSIZE:
        _GIS_ROUTE_CONTEXT_CACHE.pop(next(iter(_GIS_ROUTE_CONTEXT_CACHE)))
    return context


def _enrich_route_payload(route_payload: dict, context: GisRouteContext, network) -> dict:
    station_lookup = context.station_lookup or _station_lookup_payload()
    route_payload["stations"] = []
    station_ids = route_payload.get("station_ids", [])
    
    for station_id in station_ids:
        station = station_lookup.get(station_id)
        if station is None:
            station = {
                "id": station_id,
                "name": network.stations.get(station_id).name if station_id in network.stations else station_id,
                "line_ids": sorted(network.station_to_lines.get(station_id, set())),
                "lon": None,
                "lat": None,
            }
        route_payload["stations"].append(station)
    
    route_payload["line_labels"] = [
        network.lines[line_id].name if line_id in network.lines else line_id
        for line_id in route_payload["line_sequence"]
    ]

    # Post-process steps to add road-following coordinates for transfers/walks
    for step in route_payload.get("steps", []):
        if step.get("kind") in ("walk", "transfer"):
            start_id = step.get("station_id")
            next_id = step.get("next_station_id")
            if start_id and next_id and start_id in context.station_coords_by_id and next_id in context.station_coords_by_id:
                # If coordinates are missing, calculate them using the walk graph
                if not step.get("coordinates") and context.walk_graph:
                    start_pos = context.station_coords_by_id[start_id]
                    next_pos = context.station_coords_by_id[next_id]
                    step["coordinates"] = find_walk_path(
                        start_pos[0], start_pos[1],
                        next_pos[0], next_pos[1],
                        context.walk_graph,
                        settings=settings
                    )
                    
    return route_payload


def _refresh_runtime_after_gis_write() -> None:
    refresh_runtime_caches()
    _GIS_ROUTE_CONTEXT_CACHE.clear()


@router.get("/network")
async def get_network():
    _raise_legacy_api_removed()


@router.get("/gis/network")
async def get_gis_network(include_walk_network: bool = False):
    fallback_bounds = (
        settings.fallback_min_lon,
        settings.fallback_min_lat,
        settings.fallback_max_lon,
        settings.fallback_max_lat,
    )
    network = get_subway_network()
    payload = build_gis_payload(
        network=network,
        qgis_geojson_dir=settings.qgis_geojson_dir,
        map_width=settings.map_width,
        map_height=settings.map_height,
        fallback_bounds=fallback_bounds,
        include_station_access_points=include_walk_network,
        include_walk_network=include_walk_network,
        merge_missing_stations=False,
    )
    payload["station_catalog"] = [
        {
            "id": station.id,
            "name": station.name,
            "line_ids": sorted(network.station_to_lines.get(station.id, set())),
            "lon": station.x,
            "lat": station.y,
        }
        for station in sorted(network.stations.values(), key=lambda item: item.name)
    ]
    payload["line_catalog"] = [
        {"id": line.id, "name": line.name, "color": line.color}
        for line in network.lines.values()
    ]
    
    # Basemap fallback (MBTiles support removed in cleanup)
    payload["basemap"] = {
        "enabled": False,
        "type": "osm_raster_fallback",
        "bounds": payload["bounds"],
    }
    return payload


@router.post("/gis/stations")
async def save_gis_stations(request: GisStationSaveRequest):
    if not request.stations:
        raise HTTPException(status_code=400, detail="stations payload must not be empty")

    seen_station_ids: set[str] = set()
    positions = {
        station.id: {
            "lon": station.lon,
            "lat": station.lat,
            "deleted": station.deleted,
        }
        for station in request.stations
    }
    for station in request.stations:
        if station.id in seen_station_ids:
            raise HTTPException(status_code=400, detail=f"Duplicate GIS station id in payload: {station.id}")
        seen_station_ids.add(station.id)
    try:
        updated_count = save_gis_station_positions(
            settings.qgis_geojson_dir / "stations.geojson",
            positions,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    _refresh_runtime_after_gis_write()
    return {
        "message": "GIS station coordinates saved",
        "updated_count": updated_count,
    }


@router.delete("/gis/stations/{station_id}")
async def delete_gis_station(station_id: str):
    try:
        updated_count = delete_gis_station_in_store(
            settings.qgis_geojson_dir / "stations.geojson",
            station_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    _refresh_runtime_after_gis_write()
    return {
        "message": "GIS station marked deleted",
        "updated_count": updated_count,
    }


@router.post("/gis/route/points")
async def get_gis_route_for_points(request: GisPointRouteRequest):
    try:
        if request.walking_m_per_sec <= 0:
            raise HTTPException(status_code=400, detail="walking_m_per_sec must be > 0")

        network = get_subway_network()
        admin_effects_for_response = None
        preview_context = get_gis_route_context(network)
        scenarios = (
            request.admin_scenarios
            if request.admin_scenarios is not None
            else load_admin_scenarios(settings.admin_scenarios_file)
        )
        effects = build_admin_scenario_effects(
            network=network,
            gis_payload=preview_context.payload,
            scenarios=scenarios,
        )
        if effects.get("has_active_incidents"):
            admin_effects_for_response = effects
        if effects.get("closed_station_ids") or effects.get("closed_segment_keys"):
            network = apply_admin_scenarios_to_network(network, effects)
        for via_station_id in request.via_station_ids:
            if via_station_id not in network.stations:
                raise HTTPException(status_code=400, detail=f"Unknown via station: {via_station_id}")

        context = get_gis_route_context(network)
        gis_payload = context.payload
        
        # CRITICAL: Filter station coordinates to only those active in the augmented network
        station_coords_by_id = {
            sid: coords 
            for sid, coords in context.station_coords_by_id.items()
            if sid in network.stations
        }
        
        if not station_coords_by_id:
            raise HTTPException(status_code=500, detail="GIS station coordinates are unavailable")

        warnings: list[str] = []
        route_quality = "normal"
        rejected_start_end_station_ids: set[str] = set()
        rain_zones = _rain_zones_from_effects(admin_effects_for_response)
        if admin_effects_for_response:
            rejected_start_end_station_ids = set(admin_effects_for_response.get("explicit_banned_station_ids", []))

        walk_only_option = _build_walk_only_option(
            start_lon=request.start_lon,
            start_lat=request.start_lat,
            end_lon=request.end_lon,
            end_lat=request.end_lat,
            walk_graph=context.walk_graph,
            walking_m_per_sec=request.walking_m_per_sec,
            rain_zones=rain_zones,
        )
        hard_scenario_active = bool(
            (admin_effects_for_response or {}).get("closed_station_ids")
            or (admin_effects_for_response or {}).get("closed_segment_keys")
        )
        can_choose_walk_only = (
            not request.via_station_ids
            and walk_only_option["has_road_path"]
        )

        raw_start_candidates = find_candidate_stations_by_walk(
            request.start_lon,
            request.start_lat,
            station_coords_by_id,
            gis_payload.get("station_access_points"),
            None,
            walk_graph=context.walk_graph,
            targets_by_node=context.walk_targets_by_node,
            limit=20,
        )
        raw_end_candidates = find_candidate_stations_by_walk(
            request.end_lon,
            request.end_lat,
            station_coords_by_id,
            gis_payload.get("station_access_points"),
            None,
            walk_graph=context.walk_graph,
            targets_by_node=context.walk_targets_by_node,
            limit=10,
        )
        start_candidates = _exclude_station_candidates(raw_start_candidates, rejected_start_end_station_ids)
        end_candidates = _exclude_station_candidates(raw_end_candidates, rejected_start_end_station_ids)
        normal_start_candidates = _filter_reasonable_walk_candidates(
            start_candidates,
            max_distance_m=NORMAL_MAX_ACCESS_WALK_M,
        )
        normal_end_candidates = _filter_reasonable_walk_candidates(
            end_candidates,
            max_distance_m=NORMAL_MAX_ACCESS_WALK_M,
        )
        strategic_start_candidates = _filter_reasonable_walk_candidates(
            start_candidates,
            max_ratio=4.0,
            max_extra_m=2200.0,
            max_distance_m=STRATEGIC_MAX_ACCESS_WALK_M,
            fallback_count=10,
        )
        strategic_end_candidates = _filter_reasonable_walk_candidates(
            end_candidates,
            max_ratio=4.0,
            max_extra_m=2200.0,
            max_distance_m=STRATEGIC_MAX_ACCESS_WALK_M,
            fallback_count=6,
        )

        engine = get_route_engine()
        if getattr(engine, "network", None) is not network and isinstance(engine, RouteEngine):
            engine = RouteEngine(network)
        candidate_diagnostics: list[dict] = []
        normal_start, normal_end, normal_route, normal_cost = _find_first_candidate_route(
            engine=engine,
            start_candidates=normal_start_candidates,
            end_candidates=normal_end_candidates,
            via_station_ids=request.via_station_ids,
            walking_m_per_sec=request.walking_m_per_sec,
            rain_zones=rain_zones,
            diagnostics=candidate_diagnostics,
            candidate_set="normal",
        )
        strategic_start, strategic_end, strategic_route, strategic_cost = _find_best_candidate_route(
            engine=engine,
            start_candidates=strategic_start_candidates,
            end_candidates=strategic_end_candidates,
            via_station_ids=request.via_station_ids,
            walking_m_per_sec=request.walking_m_per_sec,
            rain_zones=rain_zones,
            diagnostics=candidate_diagnostics,
            candidate_set="strategic",
        )

        best_candidate_pair = None
        best_route_result = None
        selected_candidate_set = None
        if strategic_route is not None and (
            normal_route is None
            or strategic_cost < normal_cost
        ):
            best_candidate_pair = (strategic_start, strategic_end)
            best_route_result = strategic_route
            selected_candidate_set = "strategic"
            warnings.append("strategic_walk_access")
        elif normal_route is not None:
            best_candidate_pair = (normal_start, normal_end)
            best_route_result = normal_route
            selected_candidate_set = "normal"

        if best_route_result:
            best_route_result = engine.find_route_through_stations(
                [
                    best_candidate_pair[0].station_id,
                    *request.via_station_ids,
                    best_candidate_pair[1].station_id,
                ]
            )
            route_payload = best_route_result.to_dict()

        # FALLBACK: If no subway-involved route is found, suggest walking the whole way
        if not best_candidate_pair:
            return _build_walk_only_response(
                gis_payload=gis_payload,
                request=request,
                walk_option=walk_only_option,
                warnings=["subway_unreachable_walking_fallback"],
                selection_reason="subway_unreachable_walking_fallback",
            )

        start_walk_result, end_walk_result = best_candidate_pair
        
        selected_start_station_id = start_walk_result.station_id
        selected_end_station_id = end_walk_result.station_id
        access_walk_distance_m = start_walk_result.distance_m
        egress_walk_distance_m = end_walk_result.distance_m

        access_walk_time_sec = walking_time_sec(access_walk_distance_m, request.walking_m_per_sec)
        egress_walk_time_sec = walking_time_sec(egress_walk_distance_m, request.walking_m_per_sec)
        access_rain_penalty = _rain_penalty_for_walk(
            start_walk_result,
            rain_zones,
            request.walking_m_per_sec,
        )
        egress_rain_penalty = _rain_penalty_for_walk(
            end_walk_result,
            rain_zones,
            request.walking_m_per_sec,
        )
        rain_penalty_sec = access_rain_penalty["penalty_sec"] + egress_rain_penalty["penalty_sec"]
        metro_total_time_sec = route_payload["total_time_sec"] + access_walk_time_sec + egress_walk_time_sec + rain_penalty_sec
        if can_choose_walk_only:
            walk_only_time_sec = walk_only_option["total_time_sec"]
            if (
                walk_only_time_sec <= WALK_COMPARE_TIME_SEC
                and metro_total_time_sec > walk_only_time_sec - METRO_MIN_SHORT_WALK_SAVING_SEC
            ):
                return _build_walk_only_response(
                    gis_payload=gis_payload,
                    request=request,
                    walk_option=walk_only_option,
                    warnings=["metro_not_enough_time_saving"],
                    selection_reason="metro_not_enough_time_saving",
                )
            if (
                walk_only_time_sec > WALK_COMPARE_TIME_SEC
                and metro_total_time_sec > walk_only_time_sec + METRO_ALLOWED_SLOWER_SEC
            ):
                return _build_walk_only_response(
                    gis_payload=gis_payload,
                    request=request,
                    walk_option=walk_only_option,
                    warnings=["metro_detour_too_slow"],
                    selection_reason="metro_detour_too_slow",
                )

        station_lookup = context.station_lookup or _station_lookup_payload()
        route_payload = _enrich_route_payload(route_payload, context, network)
        
        route_geometry_features = build_route_geometry_features(
            route_steps=route_payload.get("steps", []),
            station_coords_by_id=station_coords_by_id,
            stations_geojson=gis_payload.get("stations"),
            lines_geojson=gis_payload.get("lines"),
            precomputed_segment_index=context.geojson_segment_index,
            geojson_line_colors=context.geojson_line_colors,
        )
        
        geometry_source: str | None = None
        has_line_features = bool((gis_payload.get("lines") or {}).get("features"))
        if route_geometry_features and not has_line_features:
            geometry_source = "fallback_station_sequence"
            route_quality = "degraded"
            warnings.append("ride_geometry_fallback")
            
        if not route_geometry_features:
            fallback_ride_path_features = _build_fallback_ride_path_features(
                route_payload.get("station_ids", []),
                station_coords_by_id,
            )
            if fallback_ride_path_features:
                route_geometry_features = fallback_ride_path_features
                geometry_source = "fallback_station_sequence"
                route_quality = "degraded"
                warnings.append("ride_geometry_fallback")

        response_payload = {
            "source": gis_payload["source"],
            "journey_mode": "subway",
            "start_point": {"lon": request.start_lon, "lat": request.start_lat},
            "end_point": {"lon": request.end_lon, "lat": request.end_lat},
            "selected_start_station": {
                **station_lookup[selected_start_station_id],
                "lon": station_coords_by_id[selected_start_station_id][0],
                "lat": station_coords_by_id[selected_start_station_id][1],
            },
            "selected_start_access_point": {
                "name": start_walk_result.access_point_name,
                "lon": start_walk_result.access_point_coordinate[0],
                "lat": start_walk_result.access_point_coordinate[1],
            },
            "selected_end_station": {
                **station_lookup[selected_end_station_id],
                "lon": station_coords_by_id[selected_end_station_id][0],
                "lat": station_coords_by_id[selected_end_station_id][1],
            },
            "selected_end_access_point": {
                "name": end_walk_result.access_point_name,
                "lon": end_walk_result.access_point_coordinate[0],
                "lat": end_walk_result.access_point_coordinate[1],
            },
            "via_stations": [
                {
                    **station_lookup[station_id],
                    "lon": station_coords_by_id.get(station_id, (None, None))[0],
                    "lat": station_coords_by_id.get(station_id, (None, None))[1],
                }
                for station_id in request.via_station_ids
            ],
            "access_walk_path": {
                "type": "LineString",
                "coordinates": [
                    [path_lon, path_lat]
                    for path_lon, path_lat in start_walk_result.path_coordinates
                ],
            },
            "egress_walk_path": {
                "type": "LineString",
                "coordinates": [
                    [path_lon, path_lat]
                    for path_lon, path_lat in end_walk_result.path_coordinates
                ],
            },
            "access_walk_distance_m": round(access_walk_distance_m, 1),
            "egress_walk_distance_m": round(egress_walk_distance_m, 1),
            "access_walk_time_sec": access_walk_time_sec,
            "egress_walk_time_sec": egress_walk_time_sec,
            "access_rain_penalty_sec": access_rain_penalty["penalty_sec"],
            "egress_rain_penalty_sec": egress_rain_penalty["penalty_sec"],
            "rain_penalty_sec": rain_penalty_sec,
            "rain_walk_details": {
                "access": access_rain_penalty,
                "egress": egress_rain_penalty,
            },
            "ride_path_features": route_geometry_features,
            "total_journey_time_sec": metro_total_time_sec,
            "base_journey_time_sec": (
                route_payload["total_time_sec"] + access_walk_time_sec + egress_walk_time_sec
            ),
            "route_diagnostics": {
                "subway_time_sec": route_payload["total_time_sec"],
                "walking_time_sec": access_walk_time_sec + egress_walk_time_sec,
                "rain_penalty_sec": rain_penalty_sec,
                "transfer_count": route_payload.get("transfer_count", 0),
                "scenario_mode": "soft_penalty" if rain_zones else "normal",
                "mode_decision": "metro_selected",
                "selected_candidate_set": selected_candidate_set,
                "selection_weighted_cost_sec": normal_cost if selected_candidate_set == "normal" else strategic_cost,
                "normal_selection_cost_sec": None if normal_cost == float("inf") else normal_cost,
                "strategic_selection_cost_sec": None if strategic_cost == float("inf") else strategic_cost,
                "walk_only_distance_m": round(walk_only_option["distance_m"], 1),
                "walk_only_time_sec": walk_only_option["total_time_sec"],
                "walk_compare_time_sec": WALK_COMPARE_TIME_SEC,
                "metro_min_short_walk_saving_sec": METRO_MIN_SHORT_WALK_SAVING_SEC,
                "metro_allowed_slower_sec": METRO_ALLOWED_SLOWER_SEC,
                "candidate_pairs": _summarize_candidate_diagnostics(
                    candidate_diagnostics,
                    selected_start_station_id,
                    selected_end_station_id,
                    selected_candidate_set,
                ),
            },
            "route_quality": route_quality,
            "route": route_payload,
        }
        if admin_effects_for_response:
            response_payload["admin_effects"] = admin_effects_for_response
            rejected_station_ids = sorted(set(admin_effects_for_response.get("explicit_banned_station_ids", [])))
            response_payload["start_station_attempts"] = _build_station_attempts(
                raw_start_candidates,
                selected_start_station_id,
                rejected_station_ids,
                rain_zones,
                request.walking_m_per_sec,
            )
            response_payload["end_station_attempts"] = _build_station_attempts(
                raw_end_candidates,
                selected_end_station_id,
                rejected_station_ids,
                rain_zones,
                request.walking_m_per_sec,
            )
        if warnings:
            response_payload["warnings"] = sorted(set(warnings))
        if geometry_source is not None:
            response_payload["geometry_source"] = geometry_source
        return response_payload
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("GIS point route calculation failed")
        raise HTTPException(
            status_code=500,
            detail="GIS route calculation failed.",
        ) from e


@router.get("/builder/network")
async def get_builder_network():
    _raise_legacy_api_removed()


@router.post("/route")
async def get_route(request: RouteRequest):
    _raise_legacy_api_removed()


@router.post("/route/points")
async def get_route_for_points(request: PointRouteRequest):
    _raise_legacy_api_removed()


@router.post("/calibration/stations")
async def save_calibration(request: CalibrationSaveRequest):
    _raise_legacy_api_removed()


@router.post("/builder/network")
async def save_builder_network(request: BuilderNetworkSaveRequest):
    _raise_legacy_api_removed()


@router.get("/admin/scenarios")
async def get_admin_scenarios():
    scenarios = load_admin_scenarios(settings.admin_scenarios_file)
    return {"status": "ok", "scenarios": scenarios}


@router.put("/admin/scenarios")
async def save_admin_scenarios(request: AdminScenarioSaveRequest):
    scenarios = normalize_admin_scenarios(request.dict())
    return {"status": "ok", "scenarios": scenarios}


@router.delete("/admin/scenarios")
async def reset_admin_scenarios():
    scenarios = default_admin_scenarios()
    return {"status": "ok", "scenarios": scenarios}
