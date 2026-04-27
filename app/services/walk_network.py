import os
import pickle
import heapq
import math
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
import logging

logger = logging.getLogger(__name__)


from app.services.geo_utils import haversine_distance_m


Coordinate = tuple[float, float]
CellId = tuple[int, int]
DEFAULT_GRID_TARGET_NODES_PER_CELL = 64
MIN_GRID_CELL_SIZE_DEG = 1e-4


@dataclass(frozen=True)
class StationAccessPoint:
    station_id: str
    coordinate: Coordinate
    name: str | None = None


@dataclass(frozen=True)
class WalkPathResult:
    station_id: str
    distance_m: float
    path_coordinates: list[Coordinate]
    access_point_coordinate: Coordinate
    snapped_start_coordinate: Coordinate
    access_point_name: str | None = None


@dataclass
class WalkGraph:
    adjacency: dict[Coordinate, list[tuple[Coordinate, float]]]
    snapped_node_cache: dict[Coordinate, Coordinate] = field(default_factory=dict)
    spatial_index: dict[CellId, list[Coordinate]] = field(default_factory=dict)
    edge_geometry: dict[tuple[Coordinate, Coordinate], list[Coordinate]] = field(default_factory=dict)
    grid_origin: Coordinate = (0.0, 0.0)
    grid_cell_size: Coordinate = (MIN_GRID_CELL_SIZE_DEG, MIN_GRID_CELL_SIZE_DEG)
    grid_bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    is_simplified: bool = False

    def __post_init__(self) -> None:
        if self.adjacency and not self.spatial_index:
            (
                self.spatial_index,
                self.grid_origin,
                self.grid_cell_size,
                self.grid_bounds,
            ) = _build_spatial_index(self.adjacency)

    @property
    def nodes(self) -> tuple[Coordinate, ...]:
        return tuple(self.adjacency.keys())

    def nearest_node(self, lon: float, lat: float) -> Coordinate:
        if not self.adjacency:
            raise ValueError("Walk graph has no nodes")

        point = (float(lon), float(lat))
        if point in self.adjacency:
            return point

        cached_node = self.snapped_node_cache.get(point)
        if cached_node is not None:
            return cached_node

        query_cell = self._cell_id(*point)
        search_center_cell = self._clamp_cell_to_bounds(query_cell)
        min_cell_x, max_cell_x, min_cell_y, max_cell_y = self.grid_bounds
        max_ring = max(
            abs(search_center_cell[0] - min_cell_x),
            abs(search_center_cell[0] - max_cell_x),
            abs(search_center_cell[1] - min_cell_y),
            abs(search_center_cell[1] - max_cell_y),
        )

        best_node: Coordinate | None = None
        best_distance_m = float("inf")
        for ring in range(max_ring + 1):
            for cell_id in self._iter_cells_for_ring(search_center_cell, ring):
                for node_lon, node_lat in self.spatial_index.get(cell_id, []):
                    distance_m = haversine_distance_m(lat, lon, node_lat, node_lon)
                    if distance_m < best_distance_m:
                        best_distance_m = distance_m
                        best_node = (node_lon, node_lat)

            if best_node is None:
                continue
            if ring == max_ring:
                break
            if best_distance_m <= self._min_outside_ring_distance_m(*point, search_center_cell, ring):
                break

        self.snapped_node_cache[point] = best_node
        return best_node

    def nearest_nodes(self, lon: float, lat: float, k: int = 5) -> list[tuple[Coordinate, float]]:
        """Returns the k nearest nodes in the graph with their distances."""
        if not self.adjacency:
            return []

        point = (float(lon), float(lat))
        query_cell = self._cell_id(*point)
        search_center_cell = self._clamp_cell_to_bounds(query_cell)
        
        candidates: list[tuple[float, Coordinate]] = []
        
        # Search roughly 3 rings out to find enough candidates
        for ring in range(4):
            for cell_id in self._iter_cells_for_ring(search_center_cell, ring):
                for node_lon, node_lat in self.spatial_index.get(cell_id, []):
                    distance_m = haversine_distance_m(lat, lon, node_lat, node_lon)
                    heapq.heappush(candidates, (distance_m, (node_lon, node_lat)))
            
            if len(candidates) >= k and ring >= 1:
                break
                
        results: list[tuple[Coordinate, float]] = []
        seen = set()
        while candidates and len(results) < k:
            dist, node = heapq.heappop(candidates)
            if node not in seen:
                results.append((node, dist))
                seen.add(node)
                
        return results

    def load_geometry(self, settings: Any) -> None:
        """Lazily load detailed edge geometry from the split cache."""
        if self.edge_geometry:
            return
            
        _, cache_geom_path = _get_cache_paths(settings)
        if cache_geom_path.exists():
            try:
                with open(cache_geom_path, "rb") as f:
                    self.edge_geometry = pickle.load(f)
                    logger.info(f"Loaded edge geometry from {cache_geom_path}")
            except Exception as e:
                logger.warning(f"Failed to load edge geometry cache: {e}")

    def _cell_id(self, lon: float, lat: float) -> CellId:
        origin_lon, origin_lat = self.grid_origin
        cell_size_lon, cell_size_lat = self.grid_cell_size
        return (
            math.floor((float(lon) - origin_lon) / cell_size_lon),
            math.floor((float(lat) - origin_lat) / cell_size_lat),
        )

    def _iter_cells_for_ring(self, center_cell: CellId, ring: int):
        center_x, center_y = center_cell
        if ring == 0:
            yield center_cell
            return

        min_x = center_x - ring
        max_x = center_x + ring
        min_y = center_y - ring
        max_y = center_y + ring

        for cell_x in range(min_x, max_x + 1):
            yield (cell_x, min_y)
            yield (cell_x, max_y)
        for cell_y in range(min_y + 1, max_y):
            yield (min_x, cell_y)
            yield (max_x, cell_y)

    def _clamp_cell_to_bounds(self, cell_id: CellId) -> CellId:
        cell_x, cell_y = cell_id
        min_cell_x, max_cell_x, min_cell_y, max_cell_y = self.grid_bounds
        return (
            min(max(cell_x, min_cell_x), max_cell_x),
            min(max(cell_y, min_cell_y), max_cell_y),
        )

    def _min_outside_ring_distance_m(
        self,
        lon: float,
        lat: float,
        center_cell: CellId,
        ring: int,
    ) -> float:
        center_x, center_y = center_cell
        origin_lon, origin_lat = self.grid_origin
        cell_size_lon, cell_size_lat = self.grid_cell_size
        min_cell_x, max_cell_x, min_cell_y, max_cell_y = self.grid_bounds

        west_boundary_lon = origin_lon + (center_x - ring) * cell_size_lon
        east_boundary_lon = origin_lon + (center_x + ring + 1) * cell_size_lon
        south_boundary_lat = origin_lat + (center_y - ring) * cell_size_lat
        north_boundary_lat = origin_lat + (center_y + ring + 1) * cell_size_lat

        candidate_bounds_m: list[float] = []
        if center_x - ring > min_cell_x:
            candidate_bounds_m.append(haversine_distance_m(lat, lon, lat, west_boundary_lon))
        if center_x + ring < max_cell_x:
            candidate_bounds_m.append(haversine_distance_m(lat, lon, lat, east_boundary_lon))
        if center_y - ring > min_cell_y:
            candidate_bounds_m.append(haversine_distance_m(lat, lon, south_boundary_lat, lon))
        if center_y + ring < max_cell_y:
            candidate_bounds_m.append(haversine_distance_m(lat, lon, north_boundary_lat, lon))

        if not candidate_bounds_m:
            return float("inf")
        return min(candidate_bounds_m)


def build_walk_graph(
    walk_network_geojson: dict[str, Any] | None,
    settings: Any | None = None,
) -> WalkGraph:
    # Try loading from cache first if settings are provided
    if settings:
        cached_graph = load_cache(settings)
        if cached_graph:
            return cached_graph

    adjacency: dict[Coordinate, list[tuple[Coordinate, float]]] = {}
    if not _is_valid_geojson(walk_network_geojson):
        return WalkGraph(adjacency={})

    for feature in walk_network_geojson.get("features", []):
        geometry = feature.get("geometry", {}) or {}
        for line in _iter_line_strings(geometry):
            for start, end in zip(line, line[1:], strict=False):
                if start == end:
                    continue
                distance_m = haversine_distance_m(start[1], start[0], end[1], end[0])
                adjacency.setdefault(start, []).append((end, distance_m))
                adjacency.setdefault(end, []).append((start, distance_m))

    graph = WalkGraph(adjacency=adjacency)

    # Simplification collapses degree-2 road nodes. That is good for speed but
    # can make snapping choose a farther endpoint instead of the nearest road
    # vertex, so keep the full walking graph unless explicitly enabled.
    if settings and _walk_graph_simplification_enabled(settings):
        graph = _simplify_graph(graph)

    # Save to cache if possible
    if settings:
        save_cache(settings, graph)
        
    return graph


def _walk_graph_simplification_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "enable_walk_graph_simplification", False))


def _simplify_graph(graph: WalkGraph) -> WalkGraph:
    """Collapses degree-2 nodes to reduce unpickling time and routing complexity."""
    adj = graph.adjacency
    degrees = {node: len(neighbors) for node, neighbors in adj.items()}
    critical_nodes = {node for node, deg in degrees.items() if deg != 2}
    
    logger.info(f"Simplifying graph: {len(adj)} nodes -> target critical {len(critical_nodes)}")
    
    new_adj: dict[Coordinate, list[tuple[Coordinate, float]]] = {node: [] for node in critical_nodes}
    edge_geometry: dict[tuple[Coordinate, Coordinate], list[Coordinate]] = {}
    visited_segments = set()

    for start_node in critical_nodes:
        for neighbor, initial_dist in adj[start_node]:
            if (start_node, neighbor) in visited_segments:
                continue

            path = [start_node, neighbor]
            current_dist = initial_dist
            prev = start_node
            curr = neighbor
            
            while curr not in critical_nodes:
                next_neighbors = [n for n, d in adj[curr] if n != prev]
                if not next_neighbors: break
                
                # Find neighbor entry
                next_node = None
                dist_to_next = 0.0
                for n, d in adj[curr]:
                    if n == next_neighbors[0]:
                        next_node, dist_to_next = n, d
                        break
                
                if next_node is None: break
                
                path.append(next_node)
                current_dist += dist_to_next
                prev, curr = curr, next_node
            
            # Safety: Ensure curr is in new_adj
            if curr not in new_adj:
                new_adj[curr] = []
            if start_node not in new_adj:
                new_adj[start_node] = []

            new_adj[start_node].append((curr, current_dist))
            new_adj[curr].append((start_node, current_dist))
            
            # Store full intermediate geometry for both directions
            edge_geometry[(start_node, curr)] = path
            edge_geometry[(curr, start_node)] = list(reversed(path))
            
            # Map all segments in this path to visited
            for i in range(len(path) - 1):
                p_v, c_v = path[i], path[i+1]
                visited_segments.add((p_v, c_v))
                visited_segments.add((c_v, p_v))

    # Construct the simplified graph
    simplified = WalkGraph(
        adjacency=new_adj,
        edge_geometry=edge_geometry,
        snapped_node_cache=graph.snapped_node_cache,
        # Rebuild spatial_index automatically in __post_init__
        is_simplified=True
    )
    logger.info(f"Graph simplified to {len(simplified.adjacency)} nodes.")
    return simplified


def _get_cache_paths(settings: Any) -> tuple[Path, Path]:
    cache_dir = Path(settings.qgis_geojson_dir) / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "walk_graph.pkl", cache_dir / "walk_graph_geom.pkl"


def _get_source_mtime(settings: Any) -> float:
    geojson_dir = Path(settings.qgis_geojson_dir)
    mtimes = [0.0]
    if geojson_dir.exists():
        for f in geojson_dir.glob("*.json"):
            mtimes.append(f.stat().st_mtime)
        for f in geojson_dir.glob("*.geojson"):
            mtimes.append(f.stat().st_mtime)
    return max(mtimes)


def load_cache(settings: Any) -> WalkGraph | None:
    cache_topo_path, _ = _get_cache_paths(settings)
    try:
        if not cache_topo_path.exists():
            return None
            
        source_mtime = _get_source_mtime(settings)
        cache_mtime = os.path.getmtime(cache_topo_path)
        
        if cache_mtime < source_mtime:
            logger.info("WalkGraph cache is stale, rebuilding...")
            return None
            
        import gc
        gc_enabled = gc.isenabled()
        gc.disable()
        try:
            with open(cache_topo_path, "rb") as f:
                graph = pickle.load(f)
                if getattr(graph, "is_simplified", False) and not _walk_graph_simplification_enabled(settings):
                    logger.info("Ignoring simplified WalkGraph cache; rebuilding full graph for accurate snapping.")
                    return None
                logger.info(f"Loaded WalkGraph Topology from cache: {cache_topo_path}")
                # Geometry is not loaded yet (Lazy loading)
                return graph
        finally:
            if gc_enabled:
                gc.enable()
    except Exception as e:
        logger.warning(f"Failed to load WalkGraph cache: {e}")
        return None


def save_cache(settings: Any, graph: WalkGraph) -> None:
    cache_topo_path, cache_geom_path = _get_cache_paths(settings)
    try:
        # Save Topology (Heavy list counts, but small coordinates)
        # We strip geometry before pickling to cache_topo_path
        geometry_backup = graph.edge_geometry
        graph.edge_geometry = {} 
        try:
            with open(cache_topo_path, "wb") as f:
                pickle.dump(graph, f, protocol=5)
            
            # Save Geometry separately
            with open(cache_geom_path, "wb") as f:
                pickle.dump(geometry_backup, f, protocol=5)
            
            logger.info(f"Saved split WalkGraph cache to {cache_topo_path}")
        finally:
            graph.edge_geometry = geometry_backup
    except Exception as e:
        logger.warning(f"Failed to save WalkGraph cache: {e}")


def _build_spatial_index(
    adjacency: dict[Coordinate, list[tuple[Coordinate, float]]],
) -> tuple[dict[CellId, list[Coordinate]], Coordinate, Coordinate, tuple[int, int, int, int]]:
    nodes = tuple(adjacency.keys())
    min_lon = min(node[0] for node in nodes)
    max_lon = max(node[0] for node in nodes)
    min_lat = min(node[1] for node in nodes)
    max_lat = max(node[1] for node in nodes)

    axis_cell_count = max(
        1,
        math.ceil(math.sqrt(len(nodes) / DEFAULT_GRID_TARGET_NODES_PER_CELL)),
    )
    cell_size_lon = max((max_lon - min_lon) / axis_cell_count, MIN_GRID_CELL_SIZE_DEG)
    cell_size_lat = max((max_lat - min_lat) / axis_cell_count, MIN_GRID_CELL_SIZE_DEG)
    origin = (min_lon, min_lat)
    cell_size = (cell_size_lon, cell_size_lat)

    spatial_index: dict[CellId, list[Coordinate]] = {}
    min_cell_x = max_cell_x = min_cell_y = max_cell_y = 0
    for index, node in enumerate(nodes):
        cell_x = math.floor((node[0] - origin[0]) / cell_size_lon)
        cell_y = math.floor((node[1] - origin[1]) / cell_size_lat)
        spatial_index.setdefault((cell_x, cell_y), []).append(node)
        if index == 0:
            min_cell_x = max_cell_x = cell_x
            min_cell_y = max_cell_y = cell_y
            continue
        min_cell_x = min(min_cell_x, cell_x)
        max_cell_x = max(max_cell_x, cell_x)
        min_cell_y = min(min_cell_y, cell_y)
        max_cell_y = max(max_cell_y, cell_y)

    return spatial_index, origin, cell_size, (min_cell_x, max_cell_x, min_cell_y, max_cell_y)


def extract_station_access_points(
    station_access_points_geojson: dict[str, Any] | None,
    station_coords_by_id: dict[str, Coordinate],
) -> list[StationAccessPoint]:
    if _is_valid_geojson(station_access_points_geojson):
        points: list[StationAccessPoint] = []
        for feature in station_access_points_geojson.get("features", []):
            station_id = feature.get("properties", {}).get("station_id")
            coordinates = feature.get("geometry", {}).get("coordinates")
            if (
                not station_id
                or station_id not in station_coords_by_id
                or not isinstance(coordinates, list)
                or len(coordinates) < 2
            ):
                continue
            points.append(
                StationAccessPoint(
                    station_id=str(station_id),
                    coordinate=(float(coordinates[0]), float(coordinates[1])),
                    name=feature.get("properties", {}).get("name"),
                )
            )
        if points:
            return points

    return [
        StationAccessPoint(
            station_id=station_id,
            coordinate=(station_lon, station_lat),
        )
        for station_id, (station_lon, station_lat) in station_coords_by_id.items()
    ]


def find_nearest_station_by_walk(
    lon: float,
    lat: float,
    station_coords_by_id: dict[str, Coordinate],
    station_access_points_geojson: dict[str, Any] | None,
    walk_network_geojson: dict[str, Any] | None,
    walk_graph: WalkGraph | None = None,
    targets_by_node: dict[Coordinate, list[StationAccessPoint]] | None = None,
    settings: Any | None = None,
) -> WalkPathResult:
    candidates = find_candidate_stations_by_walk(
        lon=lon,
        lat=lat,
        station_coords_by_id=station_coords_by_id,
        station_access_points_geojson=station_access_points_geojson,
        walk_network_geojson=walk_network_geojson,
        walk_graph=walk_graph,
        limit=1,
        targets_by_node=targets_by_node,
        settings=settings,
    )
    if not candidates:
        raise ValueError("No GIS stations available")
    return candidates[0]


def find_candidate_stations_by_walk(
    lon: float,
    lat: float,
    station_coords_by_id: dict[str, Coordinate],
    station_access_points_geojson: dict[str, Any] | None,
    walk_network_geojson: dict[str, Any] | None,
    walk_graph: WalkGraph | None = None,
    targets_by_node: dict[Coordinate, list[StationAccessPoint]] | None = None,
    limit: int | None = None,
    settings: Any | None = None,
) -> list[WalkPathResult]:
    if not station_coords_by_id:
        raise ValueError("No GIS stations available")

    graph = walk_graph or build_walk_graph(walk_network_geojson, settings=settings)
    if not graph.adjacency:
        return _candidate_stations_by_distance(
            lon,
            lat,
            station_coords_by_id,
            limit=limit,
        )
    if targets_by_node is not None:
        start_coordinate = (float(lon), float(lat))
        
        # Smart Snapping: Consider multiple nearby nodes to avoid crossing barriers (like rivers)
        # if another node on the 'correct' side provides a better (though perhaps longer) path.
        snap_candidates: list[tuple[Coordinate, float, float]] = []
        for start_node, snap_dist_m in graph.nearest_nodes(lon, lat, k=5):
            # We apply a heavy penalty to snap distances over 100m to discourage crossing rivers
            # which usually appear as "shortcuts" to nodes on the other side.
            snap_penalty = 1.0
            if snap_dist_m > 100:
                snap_penalty = 5.0 # 5x penalty for far snaps
            snap_candidates.append((start_node, snap_dist_m, snap_penalty))
        
        all_path_candidates = _dijkstra_station_candidates_from_snaps(
            graph=graph,
            start_coordinate=start_coordinate,
            snap_candidates=snap_candidates,
            targets_by_node=targets_by_node,
            limit=limit,
            settings=settings,
        )
        
        # Sort by the penalized distance, then take the top limit
        sorted_candidates = sorted(all_path_candidates, key=lambda x: x.distance_m)
        
        # Deduplicate results by station_id, keeping the best path for each
        final_results: list[WalkPathResult] = []
        seen_station_ids = set()
        for res in sorted_candidates:
            if res.station_id not in seen_station_ids:
                final_results.append(res)
                seen_station_ids.add(res.station_id)

        if not final_results:
            return _candidate_stations_by_distance(
                lon,
                lat,
                station_coords_by_id,
                limit=limit,
            )
        
        if limit and limit > 0:
            return final_results[:limit]
        return final_results


    access_points = extract_station_access_points(
        station_access_points_geojson,
        station_coords_by_id,
    )
    if not access_points:
        return _candidate_stations_by_distance(
            lon,
            lat,
            station_coords_by_id,
            limit=limit,
        )

    start_coordinate = (float(lon), float(lat))
    start_node = graph.nearest_node(lon, lat)
    distances, previous_nodes = _dijkstra_all_nodes(
        graph,
        start_node,
    )
    targets_by_station_id: dict[str, list[tuple[Coordinate, StationAccessPoint]]] = {}
    for access_point in access_points:
        access_node = graph.nearest_node(*access_point.coordinate)
        targets_by_station_id.setdefault(access_point.station_id, []).append((access_node, access_point))

    start_connector_distance_m = _connector_distance_m(start_coordinate, start_node)
    best_result_by_station_id: dict[str, WalkPathResult] = {}
    for station_id, targets in targets_by_station_id.items():
        best_candidate: WalkPathResult | None = None
        best_score: tuple[float, str, float, float] | None = None
        for access_node, access_point in targets:
            graph_distance_m = distances.get(access_node)
            if graph_distance_m is None:
                continue
            # Ensure geometry is loaded if needed
            if graph.is_simplified and not graph.edge_geometry and settings:
                graph.load_geometry(settings)

            path_coordinates = _build_path_coordinates(
                start_coordinate,
                _reconstruct_path(previous_nodes, start_node, access_node, graph=graph),
                access_point.coordinate,
            )
            total_distance_m = (
                start_connector_distance_m
                + graph_distance_m
                + _connector_distance_m(access_node, access_point.coordinate)
            )
            score = (
                total_distance_m,
                station_id,
                access_point.coordinate[0],
                access_point.coordinate[1],
            )
            if best_score is None or score < best_score:
                best_score = score
                best_candidate = WalkPathResult(
                    station_id=station_id,
                    distance_m=total_distance_m,
                    path_coordinates=path_coordinates,
                    access_point_coordinate=access_point.coordinate,
                    snapped_start_coordinate=start_node,
                    access_point_name=access_point.name,
                )
        if best_candidate is not None:
            best_result_by_station_id[station_id] = best_candidate

    if not best_result_by_station_id:
        return _candidate_stations_by_distance(
            lon,
            lat,
            station_coords_by_id,
            limit=limit,
        )

    ordered_results = sorted(
        best_result_by_station_id.values(),
        key=lambda item: (
            item.distance_m,
            item.station_id,
            item.access_point_coordinate[0],
            item.access_point_coordinate[1],
        ),
    )
    if limit is None or limit <= 0:
        return ordered_results
    return ordered_results[:limit]


def build_walk_targets_by_node(
    walk_graph: WalkGraph,
    station_access_points_geojson: dict[str, Any] | None,
    station_coords_by_id: dict[str, Coordinate],
) -> dict[Coordinate, list[StationAccessPoint]]:
    access_points = extract_station_access_points(
        station_access_points_geojson,
        station_coords_by_id,
    )
    targets_by_node: dict[Coordinate, list[StationAccessPoint]] = {}
    for access_point in access_points:
        if walk_graph.adjacency:
            access_node = walk_graph.nearest_node(*access_point.coordinate)
        else:
            access_node = access_point.coordinate
        targets_by_node.setdefault(access_node, []).append(access_point)
    return targets_by_node


def _dijkstra_to_best_access_point(
    graph: WalkGraph,
    start_node: Coordinate,
    targets_by_node: dict[Coordinate, list[StationAccessPoint]],
) -> tuple[float, dict[Coordinate, Coordinate], Coordinate | None, StationAccessPoint | None]:
    if start_node in targets_by_node:
        chosen_access, _ = _choose_best_access_point(start_node, targets_by_node[start_node])
        return 0.0, {}, start_node, chosen_access

    distances: dict[Coordinate, float] = {start_node: 0.0}
    previous_nodes: dict[Coordinate, Coordinate] = {}
    queue: list[tuple[float, Coordinate]] = [(0.0, start_node)]
    best_total_distance_m = float("inf")
    best_target_node: Coordinate | None = None
    best_access: StationAccessPoint | None = None

    while queue:
        current_distance, current_node = heapq.heappop(queue)
        if current_distance > distances.get(current_node, float("inf")):
            continue
        if current_distance > best_total_distance_m:
            break

        if current_node in targets_by_node:
            candidate_access, connector_distance_m = _choose_best_access_point(
                current_node,
                targets_by_node[current_node],
            )
            candidate_total_distance_m = current_distance + connector_distance_m
            if candidate_total_distance_m < best_total_distance_m:
                best_total_distance_m = candidate_total_distance_m
                best_target_node = current_node
                best_access = candidate_access

        for neighbor_node, edge_distance_m in graph.adjacency.get(current_node, []):
            candidate_distance = current_distance + edge_distance_m
            if candidate_distance >= distances.get(neighbor_node, float("inf")):
                continue
            distances[neighbor_node] = candidate_distance
            previous_nodes[neighbor_node] = current_node
            heapq.heappush(queue, (candidate_distance, neighbor_node))

    if best_target_node is None or best_access is None:
        return float("inf"), previous_nodes, None, None
    return distances[best_target_node], previous_nodes, best_target_node, best_access


def _dijkstra_all_nodes(
    graph: WalkGraph,
    start_node: Coordinate,
) -> tuple[dict[Coordinate, float], dict[Coordinate, Coordinate]]:
    distances: dict[Coordinate, float] = {start_node: 0.0}
    previous_nodes: dict[Coordinate, Coordinate] = {}
    queue: list[tuple[float, Coordinate]] = [(0.0, start_node)]

    while queue:
        current_distance, current_node = heapq.heappop(queue)
        if current_distance > distances.get(current_node, float("inf")):
            continue

        for neighbor_node, edge_distance_m in graph.adjacency.get(current_node, []):
            candidate_distance = current_distance + edge_distance_m
            if candidate_distance >= distances.get(neighbor_node, float("inf")):
                continue
            distances[neighbor_node] = candidate_distance
            previous_nodes[neighbor_node] = current_node
            heapq.heappush(queue, (candidate_distance, neighbor_node))

    return distances, previous_nodes


def _dijkstra_station_candidates_from_snaps(
    *,
    graph: WalkGraph,
    start_coordinate: Coordinate,
    snap_candidates: list[tuple[Coordinate, float, float]],
    targets_by_node: dict[Coordinate, list[StationAccessPoint]],
    limit: int | None,
    settings: Any | None = None,
) -> list[WalkPathResult]:
    target_limit = max(1, limit or 10)
    collection_limit = max(target_limit * 2, target_limit + 4)

    distances: dict[Coordinate, float] = {}
    previous_nodes: dict[Coordinate, Coordinate] = {}
    source_by_node: dict[Coordinate, Coordinate] = {}
    queue: list[tuple[float, Coordinate]] = []
    best_by_station_id: dict[
        str,
        tuple[float, Coordinate, Coordinate, StationAccessPoint],
    ] = {}

    for start_node, snap_dist_m, snap_penalty in snap_candidates:
        initial_distance = snap_dist_m * snap_penalty
        if initial_distance >= distances.get(start_node, float("inf")):
            continue
        distances[start_node] = initial_distance
        source_by_node[start_node] = start_node
        heapq.heappush(queue, (initial_distance, start_node))

    while queue:
        current_distance, current_node = heapq.heappop(queue)
        if current_distance > distances.get(current_node, float("inf")):
            continue

        if len(best_by_station_id) >= collection_limit:
            worst_candidate_distance = max(
                candidate[0]
                for candidate in best_by_station_id.values()
            )
            if current_distance > worst_candidate_distance:
                break

        for access_point in targets_by_node.get(current_node, []):
            total_distance_m = (
                current_distance
                + _connector_distance_m(current_node, access_point.coordinate)
            )
            current_best = best_by_station_id.get(access_point.station_id)
            candidate_score = (
                total_distance_m,
                access_point.station_id,
                access_point.coordinate[0],
                access_point.coordinate[1],
            )
            current_score = (
                current_best[0],
                access_point.station_id,
                current_best[3].coordinate[0],
                current_best[3].coordinate[1],
            ) if current_best else None
            if current_score is None or candidate_score < current_score:
                best_by_station_id[access_point.station_id] = (
                    total_distance_m,
                    source_by_node[current_node],
                    current_node,
                    access_point,
                )

        for neighbor_node, edge_distance_m in graph.adjacency.get(current_node, []):
            candidate_distance = current_distance + edge_distance_m
            if candidate_distance >= distances.get(neighbor_node, float("inf")):
                continue
            distances[neighbor_node] = candidate_distance
            previous_nodes[neighbor_node] = current_node
            source_by_node[neighbor_node] = source_by_node[current_node]
            heapq.heappush(queue, (candidate_distance, neighbor_node))

    if graph.is_simplified and not graph.edge_geometry and settings:
        graph.load_geometry(settings)

    ordered_candidates = sorted(
        best_by_station_id.items(),
        key=lambda item: (
            item[1][0],
            item[0],
            item[1][3].coordinate[0],
            item[1][3].coordinate[1],
        ),
    )

    results: list[WalkPathResult] = []
    for station_id, (distance_m, source_node, target_node, access_point) in ordered_candidates[:collection_limit]:
        graph_path = _reconstruct_path(
            previous_nodes,
            source_node,
            target_node,
            graph=graph,
        )
        results.append(
            WalkPathResult(
                station_id=station_id,
                distance_m=distance_m,
                path_coordinates=_build_path_coordinates(
                    start_coordinate,
                    graph_path,
                    access_point.coordinate,
                ),
                access_point_coordinate=access_point.coordinate,
                snapped_start_coordinate=source_node,
                access_point_name=access_point.name,
            )
        )
    return results


def _choose_best_access_point(
    graph_node: Coordinate,
    access_points: list[StationAccessPoint],
) -> tuple[StationAccessPoint, float]:
    def connector_distance(access_point: StationAccessPoint) -> float:
        return _connector_distance_m(graph_node, access_point.coordinate)

    chosen_access = min(
        access_points,
        key=lambda item: (
            connector_distance(item),
            item.station_id,
            item.coordinate[0],
            item.coordinate[1],
        ),
    )
    return chosen_access, connector_distance(chosen_access)


def _reconstruct_path(
    previous_nodes: dict[Coordinate, Coordinate],
    start_node: Coordinate,
    target_node: Coordinate,
    graph: WalkGraph | None = None,
) -> list[Coordinate]:
    simplified_path = [target_node]
    cursor = target_node
    while cursor != start_node:
        cursor = previous_nodes[cursor]
        simplified_path.append(cursor)
    simplified_path.reverse()

    if graph is None or not graph.is_simplified:
        return simplified_path

    # If the graph is simplified, expand edges using edge_geometry
    full_path: list[Coordinate] = []
    for i in range(len(simplified_path) - 1):
        u, v = simplified_path[i], simplified_path[i+1]
        geom = graph.edge_geometry.get((u, v))
        if not geom:
            geom = graph.edge_geometry.get((v, u))
            if geom:
                geom = list(reversed(geom))
        
        if geom:
            # Avoid duplicating the last point of the previous segment
            if full_path:
                full_path.extend(geom[1:])
            else:
                full_path.extend(geom)
        else:
            # Fallback to straight line if geometry missing
            if full_path:
                full_path.append(v)
            else:
                full_path.extend([u, v])
    
    return full_path if full_path else simplified_path


def _build_path_coordinates(
    start_coordinate: Coordinate,
    graph_path: list[Coordinate],
    access_point_coordinate: Coordinate,
) -> list[Coordinate]:
    coordinates = [start_coordinate, *graph_path, access_point_coordinate]
    normalized: list[Coordinate] = []
    for coordinate in coordinates:
        if normalized and normalized[-1] == coordinate:
            continue
        normalized.append(coordinate)
    return normalized


def _connector_distance_m(start_coordinate: Coordinate, end_coordinate: Coordinate) -> float:
    if start_coordinate == end_coordinate:
        return 0.0
    return haversine_distance_m(
        start_coordinate[1],
        start_coordinate[0],
        end_coordinate[1],
        end_coordinate[0],
    )


def _iter_line_strings(geometry: dict[str, Any]) -> list[list[Coordinate]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "LineString" and isinstance(coordinates, list):
        line = [
            (float(point[0]), float(point[1]))
            for point in coordinates
            if isinstance(point, list) and len(point) >= 2
        ]
        return [line] if len(line) >= 2 else []
    if geometry_type == "MultiLineString" and isinstance(coordinates, list):
        lines: list[list[Coordinate]] = []
        for line in coordinates:
            if not isinstance(line, list):
                continue
            parsed_line = [
                (float(point[0]), float(point[1]))
                for point in line
                if isinstance(point, list) and len(point) >= 2
            ]
            if len(parsed_line) >= 2:
                lines.append(parsed_line)
        return lines
    return []


def _is_valid_geojson(payload: dict[str, Any] | None) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("type") == "FeatureCollection"
        and isinstance(payload.get("features"), list)
    )


def _nearest_station_by_distance(
    lon: float,
    lat: float,
    station_coords_by_id: dict[str, Coordinate],
) -> tuple[str, float]:
    best_station_id: str | None = None
    best_distance_m = float("inf")

    for station_id, (station_lon, station_lat) in station_coords_by_id.items():
        distance_m = haversine_distance_m(lat, lon, station_lat, station_lon)
        if distance_m < best_distance_m:
            best_distance_m = distance_m
            best_station_id = station_id

    if best_station_id is None:
        raise ValueError("No GIS stations available")
    return best_station_id, best_distance_m


def _candidate_stations_by_distance(
    lon: float,
    lat: float,
    station_coords_by_id: dict[str, Coordinate],
    limit: int | None = None,
) -> list[WalkPathResult]:
    ordered_candidates = sorted(
        (
            (
                station_id,
                haversine_distance_m(lat, lon, station_lat, station_lon),
                (station_lon, station_lat),
            )
            for station_id, (station_lon, station_lat) in station_coords_by_id.items()
        ),
        key=lambda item: (item[1], item[0]),
    )

    results = [
        WalkPathResult(
            station_id=station_id,
            distance_m=distance_m,
            path_coordinates=[(lon, lat), coordinate],
            access_point_coordinate=coordinate,
            snapped_start_coordinate=(lon, lat),
        )
        for station_id, distance_m, coordinate in ordered_candidates
    ]
    if limit is None or limit <= 0:
        return results
    return results[:limit]


def find_walk_path(
    start_lon: float,
    start_lat: float,
    target_lon: float,
    target_lat: float,
    walk_graph: WalkGraph,
    settings: Any | None = None,
) -> list[Coordinate]:
    if not walk_graph.adjacency:
        return [(start_lon, start_lat), (target_lon, target_lat)]

    start_coord = (float(start_lon), float(start_lat))
    target_coord = (float(target_lon), float(target_lat))
    
    start_node = walk_graph.nearest_node(*start_coord)
    target_node = walk_graph.nearest_node(*target_coord)

    if start_node == target_node:
        return [start_coord, start_node, target_coord]

    distances: dict[Coordinate, float] = {start_node: 0.0}
    previous_nodes: dict[Coordinate, Coordinate] = {}
    queue: list[tuple[float, Coordinate]] = [(0.0, start_node)]

    found = False
    while queue:
        current_distance, current_node = heapq.heappop(queue)
        if current_node == target_node:
            found = True
            break
            
        if current_distance > distances.get(current_node, float("inf")):
            continue

        for neighbor_node, edge_distance_m in walk_graph.adjacency.get(current_node, []):
            candidate_distance = current_distance + edge_distance_m
            if candidate_distance >= distances.get(neighbor_node, float("inf")):
                continue
            distances[neighbor_node] = candidate_distance
            previous_nodes[neighbor_node] = current_node
            heapq.heappush(queue, (candidate_distance, neighbor_node))

    if not found:
        # Distance-based guard for straight-line fallback
        dist = haversine_distance_m(start_lat, start_lon, target_lat, target_lon)
        if dist < 300: # Stay within 300m for "unmapped" walk paths
            return [start_coord, target_coord]
        return []

    # Load geometry if simplified
    if walk_graph.is_simplified and not walk_graph.edge_geometry and settings:
        walk_graph.load_geometry(settings)

    graph_path = _reconstruct_path(previous_nodes, start_node, target_node, graph=walk_graph)
    return _build_path_coordinates(start_coord, graph_path, target_coord)
