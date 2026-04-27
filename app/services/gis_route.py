from __future__ import annotations

from typing import Any

from app.domain.models import RouteResult
from app.services.walk_network import WalkGraph, find_walk_path


def extract_station_coordinates(stations_geojson: dict[str, Any]) -> dict[str, tuple[float, float]]:
    lookup: dict[str, tuple[float, float]] = {}
    for feature in stations_geojson.get("features", []):
        properties = feature.get("properties", {})
        if properties.get("deleted"):
            continue
        station_id = properties.get("id")
        coordinates = feature.get("geometry", {}).get("coordinates")
        if (
            not station_id
            or not isinstance(coordinates, list)
            or len(coordinates) < 2
        ):
            continue
        lookup[str(station_id)] = (float(coordinates[0]), float(coordinates[1]))
    return lookup


def enrich_route_with_walk_paths(
    route_result: RouteResult,
    walk_graph: WalkGraph,
    station_coords_by_id: dict[str, tuple[float, float]],
    settings: Any | None = None,
) -> None:
    """Enriches any 'walk' or 'transfer' steps (between different stations) with actual road-following coordinates."""
    if not walk_graph.adjacency:
        return

    for step in route_result.steps:
        if step.kind in ("walk", "transfer") and step.next_station_id:
            if step.station_id == step.next_station_id:
                continue
                
            start_coord = station_coords_by_id.get(step.station_id)
            end_coord = station_coords_by_id.get(step.next_station_id)
            
            if start_coord and end_coord:
                try:
                    step.coordinates = find_walk_path(
                        start_coord[0],
                        start_coord[1],
                        end_coord[0],
                        end_coord[1],
                        walk_graph,
                        settings=settings,
                    )
                except Exception:
                    # Fallback to straight line is implicit by leaving coordinates as None
                    # or adding them explicitly if we want to guarantee consistency
                    pass
