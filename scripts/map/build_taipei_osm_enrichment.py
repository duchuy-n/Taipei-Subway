from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path

import networkx as nx
import numpy as np
import osmnx as ox


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NETWORK_PATH = PROJECT_ROOT / "app" / "data" / "subway_network.json"
DEFAULT_GRAPH_PATH = PROJECT_ROOT / "map" / "geography" / "taipei_streets.graphml"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "app" / "data" / "subway_osm_enrichment.json"
DEFAULT_CACHE_PATH = PROJECT_ROOT / "cache" / "taipei_station_geocode_cache.json"
TAIPEI_BOUNDS = {
    "lat_min": 24.85,
    "lat_max": 25.25,
    "lon_min": 121.15,
    "lon_max": 121.65,
}
AREA_HINTS = ("Taipei", "New Taipei", "Taoyuan")
MANUAL_QUERY_NAMES = {
    "fuxingguang": "Fuxinggang",
    "tamsui-fishermans-wharf": "Tamsui Fisherman's Wharf",
    "zhinan-temple-station": "Zhinan Temple",
}


@dataclass(frozen=True)
class GeocodeCandidate:
    query: str
    latitude: float
    longitude: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build OSM enrichment for the Taipei subway network.")
    parser.add_argument("--network", type=Path, default=DEFAULT_NETWORK_PATH)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--max-snap-meters", type=float, default=400.0)
    parser.add_argument("--max-walk-straight-meters", type=float, default=650.0)
    parser.add_argument("--max-walk-path-meters", type=float, default=900.0)
    parser.add_argument("--walk-speed-mps", type=float, default=1.35)
    parser.add_argument("--residual-threshold-meters", type=float, default=15000.0)
    return parser.parse_args()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def load_cache(path: Path) -> dict[str, list[float | None]]:
    if not path.exists():
        return {}
    return load_json(path)


def save_cache(path: Path, cache: dict[str, list[float | None]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=True, indent=2, sort_keys=True)


def build_station_to_lines(raw: dict) -> dict[str, set[str]]:
    station_to_lines: dict[str, set[str]] = defaultdict(set)
    for station_line in raw["station_lines"]:
        station_to_lines[station_line["station_id"]].add(station_line["line_id"])
    return dict(station_to_lines)


def build_query_names(station: dict) -> list[str]:
    names = [
        MANUAL_QUERY_NAMES.get(station["id"]),
        normalize_name(station["name"]),
        normalize_name(title_from_slug(station["id"])),
    ]
    return [name for index, name in enumerate(names) if name and name not in names[:index]]


def normalize_name(value: str) -> str:
    normalized = value.strip()
    normalized = normalized.replace("DIngxi", "Dingxi")
    normalized = normalized.replace("Fishermans", "Fisherman's")
    normalized = normalized.replace("Rd.", "Road")
    normalized = normalized.replace("W.", "West")
    return " ".join(normalized.split())


def title_from_slug(slug: str) -> str:
    parts = slug.replace("-", " ").split()
    title_parts = []
    for part in parts:
        lowered = part.lower()
        if lowered == "ntu":
            title_parts.append("NTU")
            continue
        title_parts.append(part.title())
    return " ".join(title_parts)


def geocode_station(
    station: dict,
    cache: dict[str, list[float | None]],
    include_area_hints: bool,
) -> list[GeocodeCandidate]:
    queries = build_queries(station, include_area_hints)
    candidates: list[GeocodeCandidate] = []

    for query in queries:
        candidate = geocode_query(query, cache)
        if candidate is None:
            continue
        if candidate not in candidates:
            candidates.append(candidate)

    return candidates


def build_queries(station: dict, include_area_hints: bool) -> list[str]:
    queries: list[str] = []
    for name in build_query_names(station):
        queries.extend(
            [
                f"{name} Station, Taiwan",
                f"{name}, Taiwan",
            ]
        )
        if not include_area_hints:
            continue
        for area_hint in AREA_HINTS:
            queries.extend(
                [
                    f"{name} Station, {area_hint}, Taiwan",
                    f"{name}, {area_hint}, Taiwan",
                ]
            )

    return dedupe_strings(queries)


def geocode_query(query: str, cache: dict[str, list[float | None]]) -> GeocodeCandidate | None:
    if query in cache:
        latitude, longitude = cache[query]
        if latitude is None or longitude is None:
            return None
        return GeocodeCandidate(query=query, latitude=latitude, longitude=longitude)

    try:
        latitude, longitude = ox.geocode(query)
    except Exception:
        cache[query] = [None, None]
        return None

    cache[query] = [latitude, longitude]
    return GeocodeCandidate(query=query, latitude=latitude, longitude=longitude)


def resolve_station_candidates(
    stations: list[dict],
    cache: dict[str, list[float | None]],
    residual_threshold_meters: float,
) -> dict[str, GeocodeCandidate]:
    station_by_id = {station["id"]: station for station in stations}
    station_candidates = {
        station["id"]: geocode_station(station, cache, include_area_hints=False)
        for station in stations
    }

    chosen = {
        station_id: next(
            (candidate for candidate in candidates if is_in_taipei_bounds(candidate.latitude, candidate.longitude)),
            candidates[0] if candidates else None,
        )
        for station_id, candidates in station_candidates.items()
    }
    chosen = {station_id: candidate for station_id, candidate in chosen.items() if candidate is not None}

    model = fit_affine_model(station_by_id, chosen)
    for station in stations:
        current = chosen.get(station["id"])
        residual = candidate_residual_meters(station, current, model)
        if current is not None and is_in_taipei_bounds(current.latitude, current.longitude) and residual <= residual_threshold_meters:
            continue
        station_candidates[station["id"]] = geocode_station(station, cache, include_area_hints=True)

    for _ in range(3):
        model = fit_affine_model(station_by_id, chosen)
        updated: dict[str, GeocodeCandidate] = {}
        for station in stations:
            best_candidate = choose_best_candidate(
                station=station,
                candidates=station_candidates.get(station["id"], []),
                model=model,
            )
            if best_candidate is not None:
                updated[station["id"]] = best_candidate
        if updated == chosen:
            break
        chosen = updated

    return chosen


def fit_affine_model(
    station_by_id: dict[str, dict],
    chosen_candidates: dict[str, GeocodeCandidate],
) -> tuple[np.ndarray, np.ndarray] | None:
    rows = []
    latitudes = []
    longitudes = []

    for station_id, candidate in chosen_candidates.items():
        if not is_in_taipei_bounds(candidate.latitude, candidate.longitude):
            continue
        station = station_by_id[station_id]
        rows.append([float(station["x"]), float(station["y"]), 1.0])
        latitudes.append(candidate.latitude)
        longitudes.append(candidate.longitude)

    if len(rows) < 3:
        return None

    matrix = np.asarray(rows, dtype=float)
    latitude_coeffs, _, _, _ = np.linalg.lstsq(matrix, np.asarray(latitudes, dtype=float), rcond=None)
    longitude_coeffs, _, _, _ = np.linalg.lstsq(matrix, np.asarray(longitudes, dtype=float), rcond=None)
    return latitude_coeffs, longitude_coeffs


def choose_best_candidate(
    station: dict,
    candidates: list[GeocodeCandidate],
    model: tuple[np.ndarray, np.ndarray] | None,
) -> GeocodeCandidate | None:
    if not candidates:
        return None

    predicted = predict_coordinate(station, model)
    scored = []
    for candidate in candidates:
        in_bounds = is_in_taipei_bounds(candidate.latitude, candidate.longitude)
        score = 0.0 if in_bounds else 1_000_000.0
        if predicted is not None:
            score += haversine_meters(
                predicted[0],
                predicted[1],
                candidate.latitude,
                candidate.longitude,
            )
        scored.append((score, candidate))

    scored.sort(key=lambda item: item[0])
    return scored[0][1]


def predict_coordinate(
    station: dict,
    model: tuple[np.ndarray, np.ndarray] | None,
) -> tuple[float, float] | None:
    if model is None:
        return None

    latitude_coeffs, longitude_coeffs = model
    vector = np.asarray([float(station["x"]), float(station["y"]), 1.0], dtype=float)
    latitude = float(vector @ latitude_coeffs)
    longitude = float(vector @ longitude_coeffs)
    return latitude, longitude


def candidate_residual_meters(
    station: dict,
    candidate: GeocodeCandidate | None,
    model: tuple[np.ndarray, np.ndarray] | None,
) -> float:
    predicted = predict_coordinate(station, model)
    if candidate is None or predicted is None:
        return math.inf
    return haversine_meters(
        predicted[0],
        predicted[1],
        candidate.latitude,
        candidate.longitude,
    )


def build_stops_payload(
    stations: list[dict],
    resolved_candidates: dict[str, GeocodeCandidate],
) -> list[dict]:
    stops = []
    for station in stations:
        candidate = resolved_candidates.get(station["id"])
        if candidate is None:
            continue
        stops.append(
            {
                "id": station["id"],
                "station_id": station["id"],
                "name": station["name"],
                "latitude": round(candidate.latitude, 7),
                "longitude": round(candidate.longitude, 7),
            }
        )
    return stops


def snap_station_nodes(
    graph: nx.MultiDiGraph,
    stations: list[dict],
    resolved_candidates: dict[str, GeocodeCandidate],
    max_snap_meters: float,
) -> dict[str, dict]:
    snapped: dict[str, dict] = {}
    node_lookup = build_node_lookup(graph)

    for station in stations:
        candidate = resolved_candidates.get(station["id"])
        if candidate is None:
            continue
        nearest_node = find_nearest_node_id(
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            node_lookup=node_lookup,
        )
        node_data = graph.nodes[nearest_node]
        snap_distance_m = haversine_meters(
            candidate.latitude,
            candidate.longitude,
            float(node_data["y"]),
            float(node_data["x"]),
        )
        if snap_distance_m > max_snap_meters:
            continue
        snapped[station["id"]] = {
            "osm_node_id": nearest_node,
            "node_latitude": float(node_data["y"]),
            "node_longitude": float(node_data["x"]),
            "snap_distance_m": round(snap_distance_m, 1),
            "query": candidate.query,
        }

    return snapped


def build_node_lookup(graph: nx.MultiDiGraph) -> dict[str, np.ndarray]:
    node_ids = np.asarray(list(graph.nodes), dtype=object)
    node_latitudes = np.asarray([float(graph.nodes[node_id]["y"]) for node_id in node_ids], dtype=float)
    node_longitudes = np.asarray([float(graph.nodes[node_id]["x"]) for node_id in node_ids], dtype=float)
    return {
        "ids": node_ids,
        "latitudes": node_latitudes,
        "longitudes": node_longitudes,
    }


def find_nearest_node_id(
    latitude: float,
    longitude: float,
    node_lookup: dict[str, np.ndarray],
) -> int | str:
    latitude_scale = math.cos(math.radians(latitude))
    distance_sq = (
        ((node_lookup["longitudes"] - longitude) * latitude_scale) ** 2
        + (node_lookup["latitudes"] - latitude) ** 2
    )
    nearest_index = int(np.argmin(distance_sq))
    return node_lookup["ids"][nearest_index]


def build_walk_transfers(
    graph: nx.MultiGraph,
    stations: list[dict],
    resolved_candidates: dict[str, GeocodeCandidate],
    snapped_nodes: dict[str, dict],
    station_to_lines: dict[str, set[str]],
    max_walk_straight_meters: float,
    max_walk_path_meters: float,
    walk_speed_mps: float,
) -> list[dict]:
    walk_transfers: list[dict] = []
    station_ids = [station["id"] for station in stations if station["id"] in snapped_nodes]

    for index, from_station_id in enumerate(station_ids):
        from_candidate = resolved_candidates[from_station_id]
        from_node_id = snapped_nodes[from_station_id]["osm_node_id"]
        for to_station_id in station_ids[index + 1 :]:
            if station_to_lines.get(from_station_id, set()) & station_to_lines.get(to_station_id, set()):
                continue

            to_candidate = resolved_candidates[to_station_id]
            straight_distance_m = haversine_meters(
                from_candidate.latitude,
                from_candidate.longitude,
                to_candidate.latitude,
                to_candidate.longitude,
            )
            if straight_distance_m > max_walk_straight_meters:
                continue

            to_node_id = snapped_nodes[to_station_id]["osm_node_id"]
            try:
                path_length_m = nx.shortest_path_length(
                    graph,
                    from_node_id,
                    to_node_id,
                    weight="length",
                )
            except nx.NetworkXNoPath:
                continue

            if path_length_m > max_walk_path_meters:
                continue

            duration_sec = max(1, int(round(path_length_m / walk_speed_mps)))
            walk_transfers.extend(
                [
                    {
                        "from_station_id": from_station_id,
                        "to_station_id": to_station_id,
                        "duration_sec": duration_sec,
                    },
                    {
                        "from_station_id": to_station_id,
                        "to_station_id": from_station_id,
                        "duration_sec": duration_sec,
                    },
                ]
            )

    walk_transfers.sort(key=lambda item: (item["from_station_id"], item["to_station_id"]))
    return walk_transfers


def build_metadata(
    graph_path: Path,
    resolved_candidates: dict[str, GeocodeCandidate],
    snapped_nodes: dict[str, dict],
    walk_transfers: list[dict],
    stations: list[dict],
) -> dict:
    unresolved_station_ids = sorted(
        station["id"]
        for station in stations
        if station["id"] not in resolved_candidates
    )
    unsnapped_station_ids = sorted(
        station["id"]
        for station in stations
        if station["id"] in resolved_candidates and station["id"] not in snapped_nodes
    )

    station_osm_mapping = {}
    for station in stations:
        station_id = station["id"]
        candidate = resolved_candidates.get(station_id)
        if candidate is None:
            continue
        mapping_entry = {
            "latitude": round(candidate.latitude, 7),
            "longitude": round(candidate.longitude, 7),
            "query": candidate.query,
        }
        if station_id in snapped_nodes:
            mapping_entry.update(snapped_nodes[station_id])
        station_osm_mapping[station_id] = mapping_entry

    return {
        "osm_enrichment": {
            "generated_at": datetime.now(UTC).isoformat(),
            "street_graph_file": str(graph_path),
            "resolved_station_count": len(resolved_candidates),
            "snapped_station_count": len(snapped_nodes),
            "walk_transfer_count": len(walk_transfers),
            "unresolved_station_ids": unresolved_station_ids,
            "unsnapped_station_ids": unsnapped_station_ids,
        },
        "station_osm_mapping": station_osm_mapping,
    }


def is_in_taipei_bounds(latitude: float, longitude: float) -> bool:
    return (
        TAIPEI_BOUNDS["lat_min"] <= latitude <= TAIPEI_BOUNDS["lat_max"]
        and TAIPEI_BOUNDS["lon_min"] <= longitude <= TAIPEI_BOUNDS["lon_max"]
    )


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius_m * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def dedupe_strings(values: list[str]) -> list[str]:
    return [value for index, value in enumerate(values) if value not in values[:index]]


def main() -> None:
    args = parse_args()

    raw_network = load_json(args.network)
    stations = raw_network["stations"]
    station_to_lines = build_station_to_lines(raw_network)

    cache = load_cache(args.cache)
    resolved_candidates = resolve_station_candidates(
        stations=stations,
        cache=cache,
        residual_threshold_meters=args.residual_threshold_meters,
    )
    save_cache(args.cache, cache)

    graph = ox.load_graphml(args.graph)
    walk_graph = nx.MultiGraph(graph)
    snapped_nodes = snap_station_nodes(
        graph=graph,
        stations=stations,
        resolved_candidates=resolved_candidates,
        max_snap_meters=args.max_snap_meters,
    )
    walk_transfers = build_walk_transfers(
        graph=walk_graph,
        stations=stations,
        resolved_candidates=resolved_candidates,
        snapped_nodes=snapped_nodes,
        station_to_lines=station_to_lines,
        max_walk_straight_meters=args.max_walk_straight_meters,
        max_walk_path_meters=args.max_walk_path_meters,
        walk_speed_mps=args.walk_speed_mps,
    )

    payload = {
        "stops": build_stops_payload(stations, resolved_candidates),
        "walk_transfers": walk_transfers,
        "metadata": build_metadata(
            graph_path=args.graph,
            resolved_candidates=resolved_candidates,
            snapped_nodes=snapped_nodes,
            walk_transfers=walk_transfers,
            stations=stations,
        ),
    }
    save_json(args.output, payload)

    summary = payload["metadata"]["osm_enrichment"]
    print(f"Resolved stations: {summary['resolved_station_count']}")
    print(f"Snapped stations: {summary['snapped_station_count']}")
    print(f"Walk transfers: {summary['walk_transfer_count']}")
    print(f"Unresolved stations: {len(summary['unresolved_station_ids'])}")
    print(f"Unsnapped stations: {len(summary['unsnapped_station_ids'])}")
    print(f"Wrote enrichment: {args.output}")


if __name__ == "__main__":
    main()
