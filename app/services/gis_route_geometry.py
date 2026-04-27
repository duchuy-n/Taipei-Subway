from __future__ import annotations

import hashlib
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.walk_network import haversine_distance_m


Coordinate = tuple[float, float]
LINE_MATCH_THRESHOLD_M = 350.0
PRECOMPUTED_SEGMENT_MAX_STATION_DISTANCE_M = 180.0
STEP_MAX_ENDPOINT_SNAP_THRESHOLD_M = 450.0
RUN_MAX_SNAP_THRESHOLD_M = 120.0
RUN_AVERAGE_SNAP_THRESHOLD_M = 450.0
MULTILINE_STITCH_ENDPOINT_TOLERANCE_M = 30.0
ANCHOR_ENDPOINT_THRESHOLD_M = 80.0


@dataclass(frozen=True)
class SnapPoint:
    point: Coordinate
    distance_m: float
    segment_index: int
    segment_offset: float


def build_route_geometry_features(
    route_steps: list[dict[str, Any]],
    station_coords_by_id: dict[str, Coordinate],
    stations_geojson: dict[str, Any] | None,
    lines_geojson: dict[str, Any] | None,
    precomputed_segment_index: dict[tuple[str, str, str], list[Coordinate]] | None = None,
    geojson_line_colors: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    explicit_precomputed_segment_index = precomputed_segment_index is not None
    if precomputed_segment_index is None:
        if not _is_valid_geojson(lines_geojson):
            return []
        all_line_features = lines_geojson.get("features", [])
        if geojson_line_colors is None:
            geojson_line_colors = _extract_line_colors_from_geojson(all_line_features)
        precomputed_segment_index = _build_geojson_segment_index(
            stations_geojson,
            lines_geojson,
        )
    else:
        all_line_features = (
            lines_geojson.get("features", [])
            if _is_valid_geojson(lines_geojson)
            else []
        )
        if geojson_line_colors is None:
            geojson_line_colors = _extract_line_colors_from_geojson(all_line_features)

    features: list[dict[str, Any]] = []
    
    # Process contiguous rides
    for ride_group in _group_contiguous_ride_steps(route_steps):
        line_id = ride_group[0].get("line_id")
        candidate_features: list[dict[str, Any]] = []
        coordinates = _build_precomputed_path_coordinates(
            ride_group,
            line_id,
            precomputed_segment_index,
        )

        if not coordinates:
            station_sequence = _build_station_sequence_for_group(
                ride_group,
                station_coords_by_id,
            )
            if len(station_sequence) < 2:
                continue

            candidate_features = _match_line_features_to_station_sequence(
                station_sequence,
                _filter_line_features_by_line_id(line_id, all_line_features),
            )
            coordinates = _build_run_path_coordinates(
                station_sequence,
                candidate_features,
            )

        if not coordinates:
            continue

        line_color = (
            geojson_line_colors.get(line_id)
            if line_id
            else None
        ) or _extract_first_line_color(candidate_features or _filter_line_features_by_line_id(line_id, all_line_features))

        properties: dict[str, Any] = {"kind": "ride", "line_id": line_id}
        if line_color:
            properties["line_color"] = line_color

        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for lon, lat in coordinates],
                },
                "properties": properties,
            }
        )

    # Process transfers and walks to bridge gaps
    for step in route_steps:
        if step.get("kind") in ("transfer", "walk"):
            start_id = step.get("station_id")
            end_id = step.get("next_station_id")
            if not start_id or not end_id:
                continue
            
            # Use detailed coordinates if available (road-following path)
            step_coords = step.get("coordinates")
            if step_coords and len(step_coords) >= 2:
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[c[0], c[1]] for c in step_coords]
                    },
                    "properties": {
                        "kind": "walk",
                        "station_id": start_id,
                        "next_station_id": end_id
                    }
                })
                continue

            # Fallback to straight line
            start_coord = station_coords_by_id.get(start_id)
            end_coord = station_coords_by_id.get(end_id)
            
            if start_coord and end_coord and not _coordinates_equal(start_coord, end_coord):
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [start_coord[0], start_coord[1]],
                            [end_coord[0], end_coord[1]]
                        ]
                    },
                    "properties": {
                        "kind": "walk",
                        "station_id": start_id,
                        "next_station_id": end_id
                    }
                })

    return features


def build_ride_path_features(
    route_steps: list[dict[str, Any]],
    station_coords_by_id: dict[str, Coordinate],
    stations_geojson: dict[str, Any] | None,
    lines_geojson: dict[str, Any] | None,
    precomputed_segment_index: dict[tuple[str, str, str], list[Coordinate]] | None = None,
    geojson_line_colors: dict[str, str] | None = None,
    fallback_to_station_sequence: bool = True,
) -> list[dict[str, Any]]:
    """Compatibility wrapper for callers that only need ride geometries."""
    if precomputed_segment_index is not None:
        all_line_features = (
            lines_geojson.get("features", [])
            if _is_valid_geojson(lines_geojson)
            else []
        )
        if geojson_line_colors is None:
            geojson_line_colors = _extract_line_colors_from_geojson(all_line_features)
        return _build_ride_path_features_from_precomputed_segments(
            route_steps=route_steps,
            station_coords_by_id=station_coords_by_id,
            precomputed_segment_index=precomputed_segment_index,
            geojson_line_colors=geojson_line_colors,
            all_line_features=all_line_features,
            fallback_to_station_sequence=fallback_to_station_sequence,
        )

    return [
        feature
        for feature in build_route_geometry_features(
            route_steps=route_steps,
            station_coords_by_id=station_coords_by_id,
            stations_geojson=stations_geojson,
            lines_geojson=lines_geojson,
            precomputed_segment_index=None,
            geojson_line_colors=geojson_line_colors,
        )
        if feature.get("properties", {}).get("kind") == "ride"
    ]


def _build_ride_path_features_from_precomputed_segments(
    *,
    route_steps: list[dict[str, Any]],
    station_coords_by_id: dict[str, Coordinate],
    precomputed_segment_index: dict[tuple[str, str, str], list[Coordinate]],
    geojson_line_colors: dict[str, str],
    all_line_features: list[dict[str, Any]],
    fallback_to_station_sequence: bool,
) -> list[dict[str, Any]]:
    ride_features: list[dict[str, Any]] = []

    for ride_group in _group_contiguous_ride_steps(route_steps):
        line_id = ride_group[0].get("line_id")
        coordinates = _build_precomputed_path_coordinates(
            ride_group,
            line_id,
            precomputed_segment_index,
        )
        if not coordinates and fallback_to_station_sequence:
            coordinates = _build_station_sequence_for_group(
                ride_group,
                station_coords_by_id,
            )
        if len(coordinates) < 2:
            continue

        line_color = (
            geojson_line_colors.get(line_id)
            if line_id
            else None
        ) or _extract_first_line_color(_filter_line_features_by_line_id(line_id, all_line_features))

        properties: dict[str, Any] = {"kind": "ride", "line_id": line_id}
        if line_color:
            properties["line_color"] = line_color

        ride_features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for lon, lat in coordinates],
                },
                "properties": properties,
            }
        )

    return ride_features


def _build_precomputed_path_coordinates(
    ride_group: list[dict[str, Any]],
    line_id: str | None,
    segment_index: dict[tuple[str, str, str], list[Coordinate]],
) -> list[Coordinate]:
    if not line_id:
        return []

    station_id_sequence = _build_station_id_sequence_for_group(ride_group)
    if len(station_id_sequence) < 2:
        return []

    merged_coordinates: list[Coordinate] = []
    for start_station_id, end_station_id in zip(station_id_sequence, station_id_sequence[1:], strict=False):
        path_coordinates = segment_index.get((line_id, start_station_id, end_station_id))
        if not path_coordinates:
            return []
        if not merged_coordinates:
            merged_coordinates.extend(path_coordinates)
            continue
        merged_coordinates.extend(path_coordinates[1:] if len(path_coordinates) > 1 else path_coordinates)

    return _dedupe_coordinates(merged_coordinates)


def _build_geojson_segment_index(
    stations_geojson: dict[str, Any] | None,
    lines_geojson: dict[str, Any] | None,
) -> dict[tuple[str, str, str], list[Coordinate]]:
    if not _is_valid_geojson(stations_geojson) or not _is_valid_geojson(lines_geojson):
        return {}

    stations_by_line_id: dict[str, list[tuple[str, Coordinate]]] = {}
    for feature in stations_geojson.get("features", []):
        properties = feature.get("properties", {})
        if properties.get("deleted"):
            continue
        station_id = properties.get("id")
        line_ids = properties.get("line_ids")
        coordinate = _extract_point_coordinate(feature.get("geometry", {}))
        if not station_id or not coordinate or not isinstance(line_ids, list):
            continue
        for line_id in line_ids:
            if not line_id:
                continue
            stations_by_line_id.setdefault(str(line_id), []).append((station_id, coordinate))

    best_segments: dict[
        tuple[str, str, str],
        tuple[tuple[float, float, int], list[Coordinate]],
    ] = {}
    all_line_features = lines_geojson.get("features", [])

    for line_id, station_entries in stations_by_line_id.items():
        if len(station_entries) < 2:
            continue
        for feature in _filter_line_features_by_line_id(line_id, all_line_features):
            for line in _iter_line_strings(feature.get("geometry", {})):
                snapped_station_entries = _build_line_station_snap_entries(station_entries, line)
                if len(snapped_station_entries) < 2:
                    continue

                for previous_entry, next_entry in zip(
                    snapped_station_entries,
                    snapped_station_entries[1:],
                    strict=False,
                ):
                    if previous_entry[0] == next_entry[0]:
                        continue
                    _store_segment_candidate(
                        best_segments,
                        line_id,
                        line,
                        previous_entry,
                        next_entry,
                    )

    return {
        key: coordinates
        for key, (_, coordinates) in best_segments.items()
    }


def load_or_build_geojson_segment_index(
    stations_geojson: dict[str, Any] | None,
    lines_geojson: dict[str, Any] | None,
    cache_dir: Path,
    signature: str,
) -> dict[tuple[str, str, str], list[Coordinate]]:
    cache_path = _geojson_segment_index_cache_path(cache_dir, signature)
    cached_index = _load_persisted_geojson_segment_index(cache_path, signature)
    if cached_index is not None:
        return cached_index

    segment_index = _build_geojson_segment_index(stations_geojson, lines_geojson)
    _persist_geojson_segment_index(cache_path, signature, segment_index)
    return segment_index


def _build_line_station_snap_entries(
    station_entries: list[tuple[str, Coordinate]],
    line: list[Coordinate],
) -> list[tuple[str, Coordinate, SnapPoint]]:
    snapped_entries: list[tuple[str, Coordinate, SnapPoint]] = []
    for station_id, station_coordinate in station_entries:
        snap = _snap_point_to_line(station_coordinate, line)
        if snap.distance_m > PRECOMPUTED_SEGMENT_MAX_STATION_DISTANCE_M:
            continue
        snapped_entries.append((station_id, station_coordinate, snap))

    snapped_entries.sort(key=lambda entry: _snap_position_key(entry[2]))
    return snapped_entries


def _store_segment_candidate(
    best_segments: dict[
        tuple[str, str, str],
        tuple[tuple[float, float, int], list[Coordinate]],
    ],
    line_id: str,
    line: list[Coordinate],
    previous_entry: tuple[str, Coordinate, SnapPoint],
    next_entry: tuple[str, Coordinate, SnapPoint],
) -> None:
    previous_station_id, previous_coordinate, previous_snap = previous_entry
    next_station_id, next_coordinate, next_snap = next_entry
    forward_coordinates = _anchor_path_to_station_coordinates(
        _slice_line_between_snaps(line, previous_snap, next_snap),
        previous_coordinate,
        next_coordinate,
        previous_snap.distance_m,
        next_snap.distance_m,
    )
    score = (
        max(previous_snap.distance_m, next_snap.distance_m),
        previous_snap.distance_m + next_snap.distance_m,
        -len(forward_coordinates),
    )
    _set_best_segment_candidate(
        best_segments,
        (line_id, previous_station_id, next_station_id),
        score,
        forward_coordinates,
    )
    _set_best_segment_candidate(
        best_segments,
        (line_id, next_station_id, previous_station_id),
        score,
        list(reversed(forward_coordinates)),
    )


def _set_best_segment_candidate(
    best_segments: dict[
        tuple[str, str, str],
        tuple[tuple[float, float, int], list[Coordinate]],
    ],
    key: tuple[str, str, str],
    score: tuple[float, float, int],
    coordinates: list[Coordinate],
) -> None:
    existing = best_segments.get(key)
    if existing is None or score < existing[0]:
        best_segments[key] = (score, coordinates)


def _build_station_id_sequence_for_group(ride_group: list[dict[str, Any]]) -> list[str]:
    station_ids: list[str] = []
    for step in ride_group:
        start_station_id = step.get("station_id")
        end_station_id = step.get("next_station_id")
        if not start_station_id or not end_station_id:
            continue
        if not station_ids:
            station_ids.append(start_station_id)
        if station_ids[-1] != end_station_id:
            station_ids.append(end_station_id)
    return station_ids


def _extract_point_coordinate(geometry: dict[str, Any]) -> Coordinate | None:
    if geometry.get("type") != "Point":
        return None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
    return (float(coordinates[0]), float(coordinates[1]))


def _match_line_features_to_station_sequence(
    station_sequence: list[Coordinate],
    line_features: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not station_sequence:
        return line_features

    scored_features: list[tuple[int, float, dict[str, Any]]] = []
    for feature in line_features:
        matched_distances = [
            _distance_to_geometry_m(point, feature.get("geometry", {}))
            for point in station_sequence
        ]
        nearby_distances = [
            distance_m
            for distance_m in matched_distances
            if distance_m <= LINE_MATCH_THRESHOLD_M
        ]
        if not nearby_distances:
            continue
        scored_features.append(
            (
                len(nearby_distances),
                sum(nearby_distances) / len(nearby_distances),
                feature,
            )
        )

    if not scored_features:
        return line_features

    scored_features.sort(key=lambda item: (-item[0], item[1]))
    best_match_count = scored_features[0][0]
    minimum_match_count = max(2, best_match_count - 1)
    matched_features = [
        feature
        for match_count, _, feature in scored_features
        if match_count >= minimum_match_count
    ]
    if not matched_features:
        matched_features = [scored_features[0][2]]
    return matched_features[:6]


def _group_contiguous_ride_steps(route_steps: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current_group: list[dict[str, Any]] = []
    current_line_id: str | None = None

    for step in route_steps:
        if step.get("kind") != "ride" or not step.get("next_station_id"):
            if current_group:
                groups.append(current_group)
                current_group = []
                current_line_id = None
            continue

        step_line_id = str(step.get("line_id"))
        if current_group and step_line_id != current_line_id:
            groups.append(current_group)
            current_group = []

        current_group.append(step)
        current_line_id = step_line_id

    if current_group:
        groups.append(current_group)
    return groups


def _build_station_sequence_for_group(
    ride_group: list[dict[str, Any]],
    station_coords_by_id: dict[str, Coordinate],
) -> list[Coordinate]:
    coordinates: list[Coordinate] = []
    for step in ride_group:
        start_coordinate = station_coords_by_id.get(step.get("station_id"))
        end_coordinate = station_coords_by_id.get(step.get("next_station_id"))
        if not start_coordinate or not end_coordinate:
            continue

        if not coordinates:
            coordinates.append(start_coordinate)
        coordinates.append(end_coordinate)

    deduped: list[Coordinate] = []
    for coordinate in coordinates:
        if deduped and _coordinates_equal(deduped[-1], coordinate):
            continue
        deduped.append(coordinate)
    return deduped


def _build_run_path_coordinates(
    station_sequence: list[Coordinate],
    line_features: list[dict[str, Any]],
) -> list[Coordinate]:
    if len(station_sequence) == 2:
        return _build_step_path_coordinates(
            station_sequence[0],
            station_sequence[1],
            line_features,
        )

    best_candidate: tuple[float, int, list[Coordinate]] | None = None
    for feature in line_features:
        for line in _iter_line_strings(feature.get("geometry", {})):
            snapped_points = [_snap_point_to_line(station_coordinate, line) for station_coordinate in station_sequence]
            if not _snaps_follow_single_direction(snapped_points):
                continue

            average_distance_m = sum(snap.distance_m for snap in snapped_points) / len(snapped_points)
            max_distance_m = max(snap.distance_m for snap in snapped_points)
            path_coordinates = _slice_line_between_snaps(line, snapped_points[0], snapped_points[-1])
            candidate = (
                max_distance_m,
                average_distance_m,
                -len(path_coordinates),
                snapped_points[0].distance_m,
                snapped_points[-1].distance_m,
                path_coordinates,
            )
            if best_candidate is None or candidate < best_candidate:
                best_candidate = candidate

    if (
        best_candidate is not None
        and best_candidate[0] <= RUN_MAX_SNAP_THRESHOLD_M
        and best_candidate[1] <= RUN_AVERAGE_SNAP_THRESHOLD_M
    ):
        return _anchor_path_to_station_coordinates(
            best_candidate[5],
            station_sequence[0],
            station_sequence[-1],
            best_candidate[3],
            best_candidate[4],
        )

    merged_coordinates: list[Coordinate] = []
    for start_coordinate, end_coordinate in zip(station_sequence, station_sequence[1:], strict=False):
        path_coordinates = _build_step_path_coordinates(
            start_coordinate,
            end_coordinate,
            line_features,
        )
        if not merged_coordinates:
            merged_coordinates.extend(path_coordinates)
            continue
        merged_coordinates.extend(path_coordinates[1:] if len(path_coordinates) > 1 else path_coordinates)

    return _dedupe_coordinates(merged_coordinates)


def _build_step_path_coordinates(
    start_coordinate: Coordinate,
    end_coordinate: Coordinate,
    line_features: list[dict[str, Any]],
) -> list[Coordinate]:
    best_candidate: tuple[float, int, list[Coordinate]] | None = None

    for feature in line_features:
        for line in _iter_line_strings(feature.get("geometry", {})):
            start_snap = _snap_point_to_line(start_coordinate, line)
            end_snap = _snap_point_to_line(end_coordinate, line)
            max_endpoint_distance_m = max(start_snap.distance_m, end_snap.distance_m)
            snapped_distance_m = start_snap.distance_m + end_snap.distance_m
            path_coordinates = _slice_line_between_snaps(line, start_snap, end_snap)
            candidate = (
                max_endpoint_distance_m,
                snapped_distance_m,
                -len(path_coordinates),
                start_snap.distance_m,
                end_snap.distance_m,
                path_coordinates,
            )
            if best_candidate is None or candidate < best_candidate:
                best_candidate = candidate

    if best_candidate is None or best_candidate[0] > STEP_MAX_ENDPOINT_SNAP_THRESHOLD_M:
        return [start_coordinate, end_coordinate]
    return _anchor_path_to_station_coordinates(
        best_candidate[5],
        start_coordinate,
        end_coordinate,
        best_candidate[3],
        best_candidate[4],
    )


def _filter_line_features_by_line_id(
    line_id: str | None,
    line_features: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not line_id:
        return line_features
    matching_features = [
        feature
        for feature in line_features
        if feature.get("properties", {}).get("line_id") == line_id
    ]
    return matching_features or line_features


def _extract_line_colors_from_geojson(
    line_features: list[dict[str, Any]],
) -> dict[str, str]:
    colors: dict[str, str] = {}
    for feature in line_features:
        props = feature.get("properties", {})
        line_id = props.get("line_id")
        line_color = props.get("line_color")
        if line_id and line_color and line_id not in colors:
            colors[line_id] = line_color
    return colors


def _extract_first_line_color(
    features: list[dict[str, Any]],
) -> str | None:
    for feature in features:
        color = feature.get("properties", {}).get("line_color")
        if color:
            return color
    return None


def _anchor_path_to_station_coordinates(
    path_coordinates: list[Coordinate],
    start_coordinate: Coordinate,
    end_coordinate: Coordinate,
    start_distance_m: float,
    end_distance_m: float,
) -> list[Coordinate]:
    if not path_coordinates:
        return [start_coordinate, end_coordinate]
    anchored_coordinates = [
        start_coordinate if start_distance_m <= ANCHOR_ENDPOINT_THRESHOLD_M else path_coordinates[0]
    ]
    anchored_coordinates.extend(path_coordinates[1:-1])
    anchored_coordinates.append(
        end_coordinate if end_distance_m <= ANCHOR_ENDPOINT_THRESHOLD_M else path_coordinates[-1]
    )
    return _dedupe_coordinates(anchored_coordinates)


def _distance_to_geometry_m(point: Coordinate, geometry: dict[str, Any]) -> float:
    best_distance_m = float("inf")
    for line in _iter_line_strings(geometry):
        snap = _snap_point_to_line(point, line)
        best_distance_m = min(best_distance_m, snap.distance_m)
    return best_distance_m


def _snap_point_to_line(point: Coordinate, line: list[Coordinate]) -> SnapPoint:
    best_snap: SnapPoint | None = None
    for segment_index, (start, end) in enumerate(zip(line, line[1:], strict=False)):
        candidate = _snap_point_to_segment(point, start, end, segment_index)
        if best_snap is None or candidate.distance_m < best_snap.distance_m:
            best_snap = candidate

    if best_snap is None:
        return SnapPoint(point=point, distance_m=float("inf"), segment_index=0, segment_offset=0.0)
    return best_snap


def _snap_point_to_segment(
    point: Coordinate,
    start: Coordinate,
    end: Coordinate,
    segment_index: int,
) -> SnapPoint:
    start_lon, start_lat = start
    end_lon, end_lat = end
    point_lon, point_lat = point
    delta_lon = end_lon - start_lon
    delta_lat = end_lat - start_lat
    denominator = (delta_lon * delta_lon) + (delta_lat * delta_lat)

    if denominator <= 1e-12:
        snapped_point = start
        segment_offset = 0.0
    else:
        segment_offset = max(
            0.0,
            min(
                1.0,
                (((point_lon - start_lon) * delta_lon) + ((point_lat - start_lat) * delta_lat)) / denominator,
            ),
        )
        snapped_point = (
            start_lon + (segment_offset * delta_lon),
            start_lat + (segment_offset * delta_lat),
        )

    return SnapPoint(
        point=snapped_point,
        distance_m=haversine_distance_m(point_lat, point_lon, snapped_point[1], snapped_point[0]),
        segment_index=segment_index,
        segment_offset=segment_offset,
    )


def _slice_line_between_snaps(
    line: list[Coordinate],
    start_snap: SnapPoint,
    end_snap: SnapPoint,
) -> list[Coordinate]:
    if (
        start_snap.segment_index < end_snap.segment_index
        or (
            start_snap.segment_index == end_snap.segment_index
            and start_snap.segment_offset <= end_snap.segment_offset
        )
    ):
        coordinates = [start_snap.point]
        coordinates.extend(line[start_snap.segment_index + 1 : end_snap.segment_index + 1])
        coordinates.append(end_snap.point)
        return _dedupe_coordinates(coordinates)

    coordinates = [start_snap.point]
    coordinates.extend(reversed(line[end_snap.segment_index + 1 : start_snap.segment_index + 1]))
    coordinates.append(end_snap.point)
    return _dedupe_coordinates(coordinates)


def _dedupe_coordinates(coordinates: list[Coordinate]) -> list[Coordinate]:
    deduped: list[Coordinate] = []
    for lon, lat in coordinates:
        if deduped and abs(deduped[-1][0] - lon) < 1e-12 and abs(deduped[-1][1] - lat) < 1e-12:
            continue
        deduped.append((float(lon), float(lat)))
    return deduped


def _snaps_follow_single_direction(snapped_points: list[SnapPoint]) -> bool:
    if len(snapped_points) < 2:
        return True

    positions = [_snap_position_key(snap) for snap in snapped_points]
    non_decreasing = all(left <= right for left, right in zip(positions, positions[1:], strict=False))
    non_increasing = all(left >= right for left, right in zip(positions, positions[1:], strict=False))
    return non_decreasing or non_increasing


def _snap_position_key(snap: SnapPoint) -> tuple[int, float]:
    return (snap.segment_index, snap.segment_offset)


def _coordinates_equal(left: Coordinate, right: Coordinate) -> bool:
    return abs(left[0] - right[0]) < 1e-12 and abs(left[1] - right[1]) < 1e-12


def _iter_line_strings(geometry: dict[str, Any]) -> list[list[Coordinate]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "LineString" and isinstance(coordinates, list):
        line = _normalize_line(coordinates)
        return [line] if len(line) >= 2 else []
    if geometry_type == "MultiLineString" and isinstance(coordinates, list):
        lines: list[list[Coordinate]] = []
        for candidate in coordinates:
            if not isinstance(candidate, list):
                continue
            line = _normalize_line(candidate)
            if len(line) >= 2:
                lines.append(line)
        return _stitch_connected_lines(lines)
    return []


def _stitch_connected_lines(lines: list[list[Coordinate]]) -> list[list[Coordinate]]:
    if len(lines) <= 1:
        return lines

    stitched_lines: list[list[Coordinate]] = []
    current_line = lines[0]

    for next_line in lines[1:]:
        merged_line = _merge_lines_if_connected(current_line, next_line)
        if merged_line is not None:
            current_line = merged_line
            continue
        stitched_lines.append(_dedupe_coordinates(current_line))
        current_line = next_line

    stitched_lines.append(_dedupe_coordinates(current_line))
    return stitched_lines


def _merge_lines_if_connected(left: list[Coordinate], right: list[Coordinate]) -> list[Coordinate] | None:
    if not left or not right:
        return None

    if _coordinates_within_tolerance(left[-1], right[0]):
        return _dedupe_coordinates([*left, *right[1:]])
    if _coordinates_within_tolerance(left[-1], right[-1]):
        return _dedupe_coordinates([*left, *reversed(right[:-1])])
    if _coordinates_within_tolerance(left[0], right[-1]):
        return _dedupe_coordinates([*right[:-1], *left])
    if _coordinates_within_tolerance(left[0], right[0]):
        return _dedupe_coordinates([*reversed(right[1:]), *left])
    return None


def _coordinates_within_tolerance(left: Coordinate, right: Coordinate) -> bool:
    return (
        haversine_distance_m(left[1], left[0], right[1], right[0])
        <= MULTILINE_STITCH_ENDPOINT_TOLERANCE_M
    )


def _normalize_line(coordinates: list[Any]) -> list[Coordinate]:
    return [
        (float(point[0]), float(point[1]))
        for point in coordinates
        if isinstance(point, list) and len(point) >= 2
    ]


def _is_valid_geojson(payload: dict[str, Any] | None) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("type") == "FeatureCollection"
        and isinstance(payload.get("features"), list)
    )


def _geojson_segment_index_cache_path(cache_dir: Path, signature: str) -> Path:
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    return cache_dir / f"geojson_segment_index_{digest}.pickle"


def _load_persisted_geojson_segment_index(
    cache_path: Path,
    signature: str,
) -> dict[tuple[str, str, str], list[Coordinate]] | None:
    if not cache_path.exists():
        return None
    try:
        with cache_path.open("rb") as handle:
            payload = pickle.load(handle)
    except (OSError, pickle.PickleError, EOFError):
        return None

    if not isinstance(payload, dict) or payload.get("signature") != signature:
        return None
    segment_index = payload.get("segment_index")
    return segment_index if isinstance(segment_index, dict) else None


def _persist_geojson_segment_index(
    cache_path: Path,
    signature: str,
    segment_index: dict[tuple[str, str, str], list[Coordinate]],
) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("wb") as handle:
            pickle.dump(
                {"signature": signature, "segment_index": segment_index},
                handle,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
    except OSError:
        return
