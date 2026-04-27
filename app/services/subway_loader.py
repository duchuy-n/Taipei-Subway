from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from dataclasses import replace
from pathlib import Path

from app.domain.models import Line
from app.domain.models import Segment
from app.domain.models import Station
from app.domain.models import StationLine
from app.domain.models import Stop
from app.domain.models import SubwayNetwork
from app.domain.models import Transfer
from app.domain.models import WalkTransfer
from app.services.travel_defaults import DEFAULT_DIAGRAM_WALK_SECONDS_PER_UNIT
from app.services.travel_defaults import SUBWAY_SPEED_M_PER_SEC


MAX_REPAIRED_SEGMENT_DISTANCE_M = 3_000.0


@dataclass(frozen=True)
class NetworkBuildOptions:
    station_positions: dict[str, tuple[float, float]] = field(default_factory=dict)
    default_transfer_sec: int = 30
    line_switch_penalty: float = 180.0
    auto_walk_transfer_radius: float = 1500.0
    auto_walk_seconds_per_unit: float = DEFAULT_DIAGRAM_WALK_SECONDS_PER_UNIT
    repair_missing_segments: bool = False


def load_json_file(path: str | Path | None) -> dict:
    if path is None:
        return {}

    file_path = Path(path)
    if not file_path.exists():
        return {}

    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_station_positions_file(path: str | Path | None) -> dict[str, tuple[float, float]]:
    payload = load_json_file(path)
    if not payload:
        return {}
    if isinstance(payload, dict) and "stations" in payload:
        return {
            station["id"]: (float(station["x"]), float(station["y"]))
            for station in payload.get("stations", [])
            if "x" in station and "y" in station
        }
    if isinstance(payload, dict):
        return {
            station_id: (float(coords["x"]), float(coords["y"]))
            for station_id, coords in payload.items()
            if isinstance(coords, dict) and "x" in coords and "y" in coords
        }
    return {}


def merge_network_enrichment(raw: dict, enrichment: dict | None) -> dict:
    if not enrichment:
        return dict(raw)

    merged = dict(raw)
    merged["stops"] = _merge_stops(raw.get("stops", []), enrichment.get("stops", []))
    merged["walk_transfers"] = _merge_walk_transfer_dicts(
        raw.get("walk_transfers", []),
        enrichment.get("walk_transfers", []),
    )

    metadata = dict(raw.get("metadata", {}))
    metadata.update(enrichment.get("metadata", {}))
    if metadata:
        merged["metadata"] = metadata

    return merged


def build_station_transfers(
    station_to_lines: dict[str, set[str]],
    explicit_transfers: list[Transfer],
    default_transfer_sec: int,
) -> list[Transfer]:
    merged = {
        (transfer.station_id, transfer.from_line_id, transfer.to_line_id): transfer
        for transfer in explicit_transfers
    }

    for station_id, line_ids in station_to_lines.items():
        ordered_line_ids = sorted(line_ids)
        for from_line_id in ordered_line_ids:
            for to_line_id in ordered_line_ids:
                if from_line_id == to_line_id:
                    continue
                key = (station_id, from_line_id, to_line_id)
                if key in merged:
                    continue
                merged[key] = Transfer(
                    station_id=station_id,
                    from_line_id=from_line_id,
                    to_line_id=to_line_id,
                    transfer_sec=default_transfer_sec,
                )

    return list(merged.values())


def dedupe_walk_transfers(walk_transfers: list[WalkTransfer]) -> list[WalkTransfer]:
    deduped: dict[tuple[str, str], WalkTransfer] = {}

    for transfer in walk_transfers:
        key = (transfer.from_station_id, transfer.to_station_id)
        existing = deduped.get(key)
        if existing is None or transfer.duration_sec < existing.duration_sec:
            deduped[key] = transfer

    return list(deduped.values())


def build_walk_transfers(
    stations: dict[str, Station],
    station_to_lines: dict[str, set[str]],
    existing_walk_transfers: list[WalkTransfer],
    radius: float,
    seconds_per_unit: float,
) -> list[WalkTransfer]:
    if radius <= 0:
        return dedupe_walk_transfers(existing_walk_transfers)

    walk_transfers = list(existing_walk_transfers)
    existing_pairs = {
        (transfer.from_station_id, transfer.to_station_id)
        for transfer in existing_walk_transfers
    }
    station_ids = sorted(stations)

    for index, from_station_id in enumerate(station_ids):
        for to_station_id in station_ids[index + 1 :]:
            if (station_to_lines.get(from_station_id, set()) & 
                station_to_lines.get(to_station_id, set())):
                continue

            distance = math.hypot(
                stations[from_station_id].x - stations[to_station_id].x,
                stations[from_station_id].y - stations[to_station_id].y,
            )
            if distance <= 0:
                continue
            if distance > radius:
                continue

            duration_sec = max(1, int(round(distance * seconds_per_unit)))
            for source_station_id, target_station_id in (
                (from_station_id, to_station_id),
                (to_station_id, from_station_id),
            ):
                if (source_station_id, target_station_id) in existing_pairs:
                    continue
                walk_transfers.append(
                    WalkTransfer(
                        from_station_id=source_station_id,
                        to_station_id=target_station_id,
                        duration_sec=duration_sec,
                    )
                )

    return dedupe_walk_transfers(walk_transfers)


def _station_distance(stations: dict[str, Station], from_station_id: str, to_station_id: str, is_geographic: bool) -> float:
    from_station = stations[from_station_id]
    to_station = stations[to_station_id]
    if is_geographic:
        return _haversine_distance_m(
            from_station.y,
            from_station.x,
            to_station.y,
            to_station.x,
        )
    return math.hypot(from_station.x - to_station.x, from_station.y - to_station.y)


def _haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_m = 6_371_000.0
    rad_lat1 = math.radians(lat1)
    rad_lon1 = math.radians(lon1)
    rad_lat2 = math.radians(lat2)
    rad_lon2 = math.radians(lon2)
    delta_lat = rad_lat2 - rad_lat1
    delta_lon = rad_lon2 - rad_lon1
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(rad_lat1) * math.cos(rad_lat2) * (math.sin(delta_lon / 2) ** 2)
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(max(1e-12, 1 - a)))
    return earth_radius_m * c


def _line_component_index(segments: list[Segment]) -> dict[tuple[str, str], int]:
    adjacency: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for segment in segments:
        source = (segment.line_id, segment.from_station_id)
        target = (segment.line_id, segment.to_station_id)
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)

    component_by_node: dict[tuple[str, str], int] = {}
    component_id = 0
    for node in sorted(adjacency):
        if node in component_by_node:
            continue
        stack = [node]
        component_by_node[node] = component_id
        while stack:
            current = stack.pop()
            for neighbor in adjacency.get(current, set()):
                if neighbor in component_by_node:
                    continue
                component_by_node[neighbor] = component_id
                stack.append(neighbor)
        component_id += 1
    return component_by_node


def load_network_from_dict(
    raw: dict,
    options: NetworkBuildOptions | None = None,
) -> SubwayNetwork:
    options = options or NetworkBuildOptions()

    valid_stations = {}
    for item in raw["stations"]:
        station = _build_station(item, options.station_positions)
        valid_stations[station.id] = station

    stations = valid_stations
    stops = {
        item["id"]: Stop(
            id=item["id"],
            station_id=item.get("station_id", item["id"]),
            name=item["name"],
            latitude=float(item.get("latitude", item.get("y", 0.0))),
            longitude=float(item.get("longitude", item.get("x", 0.0))),
            line_id=item.get("line_id"),
        )
        for item in raw.get("stops", [])
        if "id" in item and "name" in item
    }
    
    # Detect if positions are geographic
    is_geographic = False
    # Check stations first
    for st in stations.values():
        if abs(st.y) <= 90 and abs(st.x) <= 180 and (abs(st.y) > 0.001 or abs(st.x) > 0.001):
            is_geographic = True
            break
    
    # If not detected yet, check if we have multiple valid GPS stops
    if not is_geographic and len(stops) > 5:
        # Check a few stops to be sure
        valid_gps_stops = 0
        for stop in list(stops.values())[:20]:
            if abs(stop.latitude) <= 90 and abs(stop.longitude) <= 180 and (abs(stop.latitude) > 0.001 or abs(stop.longitude) > 0.001):
                valid_gps_stops += 1
        if valid_gps_stops >= 3:
            is_geographic = True
    lines = {
        item["id"]: Line(
            id=item["id"],
            name=item["name"],
            color=item["color"],
        )
        for item in raw["lines"]
    }
    station_lines = []
    for item in raw["station_lines"]:
        sid = item["station_id"]
        if sid in stations:
            station_lines.append(
                StationLine(
                    station_id=sid,
                    line_id=item["line_id"],
                    seq=item["seq"],
                )
            )
        else:
            print(f"INTEGRITY: Skipping station_line for missing station {sid}")
    # Use canonical IDs from the topology data
    segments = []
    for item in raw["segments"]:
        f_id = str(item["from_station_id"])
        t_id = str(item["to_station_id"])
        
        f_st = stations.get(f_id)
        t_st = stations.get(t_id)

        if not f_st or not t_st:
            continue

        segments.append(
            Segment(
                line_id=item["line_id"],
                from_station_id=f_id,
                to_station_id=t_id,
                travel_sec=item["travel_sec"],
            )
        )
    explicit_transfers = []
    for item in raw["transfers"]:
        sid = item["station_id"]
        if sid in stations:
            explicit_transfers.append(
                Transfer(
                    station_id=sid,
                    from_line_id=item["from_line_id"],
                    to_line_id=item["to_line_id"],
                    transfer_sec=item["transfer_sec"],
                )
            )
        else:
            print(f"INTEGRITY: Skipping transfer for missing station {sid}")
    explicit_walk_transfers = []
    for item in raw.get("walk_transfers", []):
        f_id = item["from_station_id"]
        t_id = item["to_station_id"]
        if f_id in stations and t_id in stations:
            explicit_walk_transfers.append(
                WalkTransfer(
                    from_station_id=f_id,
                    to_station_id=t_id,
                    duration_sec=item["duration_sec"],
                )
            )
        else:
            # print(f"INTEGRITY: Skipping walk_transfer for missing stations {f_id} and/or {t_id}")
            continue

    # 2. Enrich stations with geographic positions if in geographic mode
    if is_geographic:
        for st in stations.values():
            # Try to find a better GPS coord for this station
            match = None
            # Direct match
            if st.id in stops:
                match = stops[st.id]
            else:
                # Fuzzy match
                for stop in stops.values():
                    if stop.station_id == st.id:
                        match = stop
                        break
                if not match:
                    for stop in stops.values():
                        if (stop.id.lower() in st.id.lower() or 
                            st.id.lower() in stop.id.lower() or
                            (st.name and stop.name and st.name.lower() == stop.name.lower())):
                            match = stop
                            break
            
            if match:
                stations[st.id] = replace(
                    st,
                    x=match.longitude,
                    y=match.latitude,
                )

    if options.repair_missing_segments:
        segment_keys = {
            (segment.line_id, frozenset((segment.from_station_id, segment.to_station_id)))
            for segment in segments
        }
        component_by_node = _line_component_index(segments)
        station_lines_by_line: dict[str, list[StationLine]] = {}
        for station_line in station_lines:
            station_lines_by_line.setdefault(station_line.line_id, []).append(station_line)
        for line_id, ordered_station_lines in station_lines_by_line.items():
            ordered_station_lines.sort(key=lambda item: item.seq)
            for current, following in zip(ordered_station_lines, ordered_station_lines[1:], strict=False):
                key = (line_id, frozenset((current.station_id, following.station_id)))
                if key in segment_keys:
                    continue

                current_node = (line_id, current.station_id)
                following_node = (line_id, following.station_id)
                current_component = component_by_node.get(current_node)
                following_component = component_by_node.get(following_node)
                if current_component is not None and current_component == following_component:
                    continue

                distance = _station_distance(stations, current.station_id, following.station_id, is_geographic)
                if is_geographic and distance > MAX_REPAIRED_SEGMENT_DISTANCE_M:
                    continue

                travel_sec = (
                    max(1, int(round(distance / SUBWAY_SPEED_M_PER_SEC)))
                    if is_geographic
                    else 0
                )
                segments.append(
                    Segment(
                        line_id=line_id,
                        from_station_id=current.station_id,
                        to_station_id=following.station_id,
                        travel_sec=travel_sec,
                    )
                )
                segment_keys.add(key)

                if current_component is None and following_component is None:
                    new_component = max(component_by_node.values(), default=-1) + 1
                    component_by_node[current_node] = new_component
                    component_by_node[following_node] = new_component
                elif current_component is None:
                    component_by_node[current_node] = following_component
                elif following_component is None:
                    component_by_node[following_node] = current_component
                else:
                    for node, component in list(component_by_node.items()):
                        if component == following_component:
                            component_by_node[node] = current_component

    station_to_lines: dict[str, set[str]] = {}
    for station_line in station_lines:
        station_to_lines.setdefault(station_line.station_id, set()).add(station_line.line_id)

    transfers = build_station_transfers(
        station_to_lines,
        explicit_transfers,
        options.default_transfer_sec,
    )
    walk_transfers = build_walk_transfers(
        stations,
        station_to_lines,
        explicit_walk_transfers,
        options.auto_walk_transfer_radius,
        options.auto_walk_seconds_per_unit,
    )

    metadata = dict(raw.get("metadata", {}))
    metadata["is_geographic"] = is_geographic
    metadata["options"] = {
        "auto_walk_transfer_radius": options.auto_walk_transfer_radius,
        "auto_walk_seconds_per_unit": options.auto_walk_seconds_per_unit,
        "default_transfer_sec": options.default_transfer_sec,
        "line_switch_penalty": options.line_switch_penalty,
    }

    return SubwayNetwork(
        stations=stations,
        lines=lines,
        station_lines=station_lines,
        segments=segments,
        transfers=transfers,
        stops=stops,
        walk_transfers=walk_transfers,
        station_to_lines=station_to_lines,
        metadata=metadata,
    )


def load_network_from_file(
    path: str | Path,
    options: NetworkBuildOptions | None = None,
) -> SubwayNetwork:
    return load_network_from_dict(load_json_file(path), options=options)


def _build_station(
    raw_station: dict,
    station_positions: dict[str, tuple[float, float]],
) -> Station:
    x, y = station_positions.get(
        raw_station["id"],
        (float(raw_station["x"]), float(raw_station["y"])),
    )
    return Station(
        id=raw_station["id"],
        name=raw_station["name"],
        x=x,
        y=y,
        diagram_x=float(raw_station["diagram_x"]) if "diagram_x" in raw_station else None,
        diagram_y=float(raw_station["diagram_y"]) if "diagram_y" in raw_station else None,
    )


def _merge_stops(base_stops: list[dict], overlay_stops: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {
        item["id"]: dict(item)
        for item in base_stops
        if isinstance(item, dict) and "id" in item
    }

    for item in overlay_stops:
        if not isinstance(item, dict) or "id" not in item:
            continue
        merged[item["id"]] = dict(item)

    return list(merged.values())


def _merge_walk_transfer_dicts(
    base_transfers: list[dict],
    overlay_transfers: list[dict],
) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}

    for item in base_transfers:
        if not _is_walk_transfer_dict(item):
            continue
        merged[(item["from_station_id"], item["to_station_id"])] = dict(item)

    for item in overlay_transfers:
        if not _is_walk_transfer_dict(item):
            continue
        merged[(item["from_station_id"], item["to_station_id"])] = dict(item)

    return list(merged.values())


def _is_walk_transfer_dict(item: dict) -> bool:
    return (
        isinstance(item, dict)
        and "from_station_id" in item
        and "to_station_id" in item
        and "duration_sec" in item
    )
