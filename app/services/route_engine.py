from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from app.domain.models import RouteResult
from app.domain.models import RouteStep
from app.domain.models import SubwayNetwork
from app.services.geo_utils import haversine_distance_m, euclidean_distance
from app.services.travel_defaults import DEFAULT_DIAGRAM_WALK_SECONDS_PER_UNIT
from app.services.travel_defaults import DEFAULT_WALKING_M_PER_SEC
from app.services.travel_defaults import SUBWAY_SPEED_M_PER_SEC

MAX_AUTO_WALK_DISTANCE_M = 1500.0
WALKING_SPEED_M_PER_SEC = DEFAULT_WALKING_M_PER_SEC
WALK_COST_PENALTY_FACTOR = 5.0
EXPLICIT_WALK_COST_PENALTY_FACTOR = 2.0


State = tuple[str, str]
Cost = tuple[int, int, int, int]


@dataclass(frozen=True)
class Edge:
    target: State
    cost: Cost
    kind: str
    duration_sec: int


class RouteEngine:
    def __init__(self, network: SubwayNetwork):
        self.network = network
        self.graph: dict[State, list[Edge]] = {}
        
        # Determine if we are using geographic coordinates (lat/lon) or diagram units (pixels)
        # Prioritize stops (GPS) if they exist.
        self.is_geographic = len(self.network.stops) > 0
        
        # If stops are missing, fallback to checking station coordinates
        if not self.is_geographic:
            self.is_geographic = True
            for st in self.network.stations.values():
                if abs(st.y) > 90 or abs(st.x) > 180:
                    self.is_geographic = False
                    break
        
        # For diagram coordinates (Pixels), assume units are roughly proportional to meters/seconds
        self.units_per_meter = 1.0

        # 1. Coordinate Loading Logic
        # We need to support both Diagram (Pixels) and GIS (GPS) coordinates.
        self.station_positions: dict[str, tuple[float, float]] = {}
        
        # Priority 1: Raw station x, y (could be pixels OR lat/lon)
        for st in self.network.stations.values():
            self.station_positions[st.id] = (st.y, st.x)

        # Priority 2: In geographic mode, try to find better GPS coords from 'stops'
        if self.is_geographic:
            # Fuzzy match stations to stops to get high-precision GPS
            for st_id in self.network.stations:
                # Try to find a stop that belongs to this station
                match = None
                # First try direct station_id match
                for stop in self.network.stops.values():
                    if stop.station_id == st_id:
                        match = stop
                        break
                # Then try fuzzy ID/name match if no direct match found
                if not match:
                    st_obj = self.network.stations[st_id]
                    for stop in self.network.stops.values():
                        if (stop.id.lower() in st_id.lower() or 
                            st_id.lower() in stop.id.lower() or
                            (st_obj.name and stop.name and st_obj.name.lower() == stop.name.lower())):
                            match = stop
                            break
                
                if match:
                    self.station_positions[st_id] = (match.latitude, match.longitude)

        self._build_graph()
        self._build_spatial_index()
        self.states = tuple(sorted(self.graph.keys()))
        self.state_to_index = {state: i for i, state in enumerate(self.states)}

    def _build_spatial_index(self) -> None:
        """Create a grid-based index for fast station spatial lookup."""
        # Diagram mode uses pixels (approx 100px grid), Geographic uses degrees (0.01 deg approx 1km)
        self.grid_size = 0.01 if self.is_geographic else 100.0
        self.grid: dict[tuple[int, int], list[str]] = {}
        
        for station_id, coords in self.station_positions.items():
            cell = (int(coords[0] / self.grid_size), int(coords[1] / self.grid_size))
            self.grid.setdefault(cell, []).append(station_id)

    def _get_distance(self, s1_id: str, s2_id: str) -> float:
        c1 = self.station_positions.get(s1_id)
        c2 = self.station_positions.get(s2_id)
        if not c1 or not c2:
            return 0.0
        
        if self.is_geographic:
            return haversine_distance_m(c1[0], c1[1], c2[0], c2[1])
        else:
            # Diagram mode: Euclidean distance (y is 0, x is 1)
            return math.sqrt((c1[1] - c2[1])**2 + (c1[0] - c2[0])**2)


    def _build_graph(self) -> None:
        """Construct the routing graph with ride, transfer, and walking edges."""
        opts = self.network.metadata.get("options", {})
        line_switch_penalty = opts.get("line_switch_penalty") or 0.0
        
        # Ensure all states exist in the graph
        for station_line in self.network.station_lines:
            self.graph.setdefault((station_line.station_id, station_line.line_id), [])

        # 1. Ride Edges (Subway segments)
        ride_speed_m_per_s = SUBWAY_SPEED_M_PER_SEC if self.is_geographic else 1.0

        for segment in self.network.segments:
            s1, s2 = segment.from_station_id, segment.to_station_id
            if self.is_geographic:
                dist = self._get_distance(s1, s2)
                travel_sec = max(1, int(round(dist / ride_speed_m_per_s)))
            else:
                travel_sec = segment.travel_sec if segment.travel_sec > 0 else 1

            source = (s1, segment.line_id)
            target = (s2, segment.line_id)
            
            if source in self.graph and target in self.graph:
                ride_cost = (travel_sec, 0, 0, 1) # Cost, WalkTime, Transfers, Stops
                self.graph[source].append(Edge(target, ride_cost, "ride", travel_sec))
                self.graph[target].append(Edge(source, ride_cost, "ride", travel_sec))

        # 2. Transfer Edges
        # 2.1 Explicit Transfers from topology data
        for transfer in self.network.transfers:
            source = (transfer.station_id, transfer.from_line_id)
            target = (transfer.station_id, transfer.to_line_id)
            if source in self.graph and target in self.graph:
                # Add penalty if changing lines at the station
                cost_val = int(transfer.transfer_sec + line_switch_penalty)
                cost = (cost_val, 0, 1, 0) # Cost, WalkTime, Transfers, Stops
                self.graph[source].append(
                    Edge(target, cost, "transfer", transfer.transfer_sec)
                )

        # 2.2 Implicit Auto-Transfers (Connecting lines at the same station)
        default_transfer_sec = opts.get("default_transfer_sec") or 30
        for station_id, line_ids in self.network.station_to_lines.items():
            if len(line_ids) > 1:
                sorted_lines = sorted(line_ids)
                for i, l1 in enumerate(sorted_lines):
                    for j, l2 in enumerate(sorted_lines):
                        if i == j: continue
                        source = (station_id, l1)
                        target = (station_id, l2)
                        
                        if source in self.graph and target in self.graph:
                            # Only add if not already present from explicit data
                            exists = any(e.target == target and e.kind == "transfer" for e in self.graph[source])
                            if not exists:
                                # Every auto-transfer is a line switch
                                trans_cost = int(default_transfer_sec + line_switch_penalty)
                                cost = (trans_cost, 0, 1, 0)
                                self.graph[source].append(
                                    Edge(target, cost, "transfer", default_transfer_sec)
                                )

        # 3. Inter-State Walking (Proximity-based transfers)
        admin_walk_bypass_pairs = {
            tuple(pair)
            for pair in self.network.metadata.get("admin_effects", {}).get("walk_bypass_pairs", [])
            if isinstance(pair, list) and len(pair) == 2
        }
        for transfer in self.network.walk_transfers:
            if (transfer.from_station_id, transfer.to_station_id) not in admin_walk_bypass_pairs:
                continue
            from_lines = self.network.station_to_lines.get(transfer.from_station_id, set())
            to_lines = self.network.station_to_lines.get(transfer.to_station_id, set())
            for from_line_id in from_lines:
                for to_line_id in to_lines:
                    source = (transfer.from_station_id, from_line_id)
                    target = (transfer.to_station_id, to_line_id)
                    if source not in self.graph or target not in self.graph:
                        continue
                    penalty = line_switch_penalty if from_line_id != to_line_id else 0.0
                    cost = (
                        int(transfer.duration_sec * EXPLICIT_WALK_COST_PENALTY_FACTOR + penalty),
                        transfer.duration_sec,
                        0,
                        0,
                    )
                    self.graph[source].append(Edge(target, cost, "walk", transfer.duration_sec))

        if self.is_geographic:
            walk_speed_m_per_s = WALKING_SPEED_M_PER_SEC
            radius = opts.get("auto_walk_transfer_radius") or 1500.0
        else:
            sec_per_unit = opts.get("auto_walk_seconds_per_unit") or DEFAULT_DIAGRAM_WALK_SECONDS_PER_UNIT
            walk_speed_m_per_s = 1.0 / sec_per_unit
            radius = opts.get("auto_walk_transfer_radius") or 25.0
        
        # Build set of existing connections to avoid redundant walk edges
        existing_conns = set()
        for src, edges in self.graph.items():
            for e in edges: existing_conns.add((src, e.target))

        station_ids = sorted([sid for sid in self.station_positions if sid in self.network.station_to_lines])
        for i, s1_id in enumerate(station_ids):
            s1_lines = self.network.station_to_lines[s1_id]
            for j in range(i + 1, len(station_ids)):
                s2_id = station_ids[j]
                dist = self._get_distance(s1_id, s2_id)
                if dist <= 0:
                    continue
                if dist > radius: continue
                
                s2_lines = self.network.station_to_lines[s2_id]
                walk_sec = max(1, int(round(dist / walk_speed_m_per_s)))
                
                for l1 in s1_lines:
                    for l2 in s2_lines:
                        src, tgt = (s1_id, l1), (s2_id, l2)
                        if src in self.graph and tgt in self.graph and (src, tgt) not in existing_conns:
                            # Apply line switch penalty if lines are different
                            penalty = line_switch_penalty if l1 != l2 else 0.0
                            walk_cost_val = int(walk_sec * WALK_COST_PENALTY_FACTOR + penalty)
                            walk_cost = (walk_cost_val, walk_sec, 0, 0)
                            
                            self.graph[src].append(Edge(tgt, walk_cost, "walk", walk_sec))
                            self.graph[tgt].append(Edge(src, walk_cost, "walk", walk_sec))
                            existing_conns.add((src, tgt))
                            existing_conns.add((tgt, src))

        # Deterministic sorting for consistency
        for edges in self.graph.values():
            edges.sort(key=lambda e: (e.target[0], e.target[1], e.kind, e.duration_sec))

        # Keep deterministic traversal order, but sort once at build-time
        # instead of sorting on every routing query.
        for edges in self.graph.values():
            edges.sort(
                key=lambda item: (
                    item.target[0],
                    item.target[1],
                    item.kind,
                    item.duration_sec,
                )
            )

    def _heuristic(self, state: State, goal_station_id: str) -> Cost:
        """Calculate heuristic cost using pre-computed station positions."""
        current_station_id = state[0]
        if current_station_id == goal_station_id:
            return (0, 0, 0, 0)
            
        c1 = self.station_positions.get(current_station_id)
        c2 = self.station_positions.get(goal_station_id)

        if c1 and c2:
            if self.is_geographic:
                dist_m = haversine_distance_m(c1[0], c1[1], c2[0], c2[1])
                # Fastest possible speed is subway (80 km/h)
                h_time = int(round(dist_m / SUBWAY_SPEED_M_PER_SEC))
            else:
                dist = euclidean_distance(c1[1], c1[0], c2[1], c2[0])
                h_time = int(round(dist)) # In diagram pixels, 1 pixel = 1 sec approx
            
            return (h_time, 0, 0, 0)
        
        return (0, 0, 0, 0)

    def find_route(self, start_station_id: str, end_station_id: str) -> RouteResult:
        if start_station_id not in self.network.stations:
            raise ValueError(f"Unknown start station: {start_station_id}")
        if end_station_id not in self.network.stations:
            raise ValueError(f"Unknown end station: {end_station_id}")
            
        if start_station_id == end_station_id:
            return RouteResult(
                total_time_sec=0,
                walking_time_sec=0,
                transfer_count=0,
                stop_count=0,
                station_ids=[start_station_id],
                line_sequence=[],
                steps=[],
            )

        # A* algorithm
        start_states = [
            (start_station_id, line_id)
            for line_id in sorted(self.network.station_to_lines.get(start_station_id, set()))
        ]
        
        # Priority queue: (estimated_total_cost, current_cost, current_state, path_so_far)
        # We'll also track parent for reconstruction
        pq: list[tuple[Cost, Cost, State]] = []
        for state in start_states:
            h = self._heuristic(state, end_station_id)
            heapq.heappush(pq, (h, (0, 0, 0, 0), state))

        distances: dict[State, Cost] = {state: (0, 0, 0, 0) for state in start_states}
        parents: dict[State, tuple[State | None, Edge | None]] = {state: (None, None) for state in start_states}

        best_goal_state: State | None = None

        while pq:
            _, curr_cost, curr_state = heapq.heappop(pq)

            if curr_state[0] == end_station_id:
                best_goal_state = curr_state
                break

            if curr_cost > distances.get(curr_state, (float('inf'), 0, 0, 0)):
                continue

            for edge in self.graph.get(curr_state, []):
                next_state = edge.target
                new_cost = self._add_cost(curr_cost, edge.cost)
                
                if next_state not in distances or new_cost < distances[next_state]:
                    distances[next_state] = new_cost
                    parents[next_state] = (curr_state, edge)
                    h = self._heuristic(next_state, end_station_id)
                    estimated_total = self._add_cost(new_cost, h)
                    heapq.heappush(pq, (estimated_total, new_cost, next_state))

        if best_goal_state is None:
            raise ValueError(f"No route found between {start_station_id} and {end_station_id}")

        return self._build_result(best_goal_state, distances[best_goal_state], parents)

    def find_route_through_stations(self, station_ids: list[str]) -> RouteResult:
        if len(station_ids) < 2:
            raise ValueError("At least two station ids are required")

        normalized_station_ids: list[str] = []
        for station_id in station_ids:
            if station_id not in self.network.stations:
                raise ValueError(f"Unknown station: {station_id}")
            if normalized_station_ids and station_id == normalized_station_ids[-1]:
                continue
            normalized_station_ids.append(station_id)

        if len(normalized_station_ids) == 1:
            station_id = normalized_station_ids[0]
            return RouteResult(
                total_time_sec=0,
                walking_time_sec=0,
                transfer_count=0,
                stop_count=0,
                station_ids=[station_id],
                line_sequence=[],
                steps=[],
            )

        legs: list[RouteResult] = []
        for start_station_id, end_station_id in zip(
            normalized_station_ids,
            normalized_station_ids[1:],
            strict=False,
        ):
            legs.append(self.find_route(start_station_id, end_station_id))

        return self._merge_leg_results(legs)

    def find_best_route_for_points(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        walking_seconds_per_pixel: float = 1.0,
        candidate_limit: int | None = None,
        max_station_walk_sec: int | None = 60,
        start_preferred_line_ids: list[str] | None = None,
        end_preferred_line_ids: list[str] | None = None,
        via_station_ids: list[str] | None = None,
    ) -> dict:
        ordered_via_station_ids = list(via_station_ids or [])
        for via_station_id in ordered_via_station_ids:
            if via_station_id not in self.network.stations:
                raise ValueError(f"Unknown via station: {via_station_id}")

        start_candidates = self._candidate_stations(
            start_x,
            start_y,
            walking_seconds_per_pixel,
            candidate_limit or 3,
            max_station_walk_sec,
            prefer_nearest=False,
            preferred_line_ids=set(start_preferred_line_ids or []),
        )
        end_candidates = self._candidate_stations(
            end_x,
            end_y,
            walking_seconds_per_pixel,
            candidate_limit or 3,
            max_station_walk_sec,
            prefer_nearest=False,
            preferred_line_ids=set(end_preferred_line_ids or []),
        )

        best_result: dict | None = None
        best_score: tuple[int, int, int, int] | None = None

        for start_station_id, start_distance in start_candidates:
            for end_station_id, end_distance in end_candidates:
                try:
                    route = self.find_route_through_stations(
                        [start_station_id, *ordered_via_station_ids, end_station_id]
                    )
                except ValueError:
                    continue

                if not any(step.kind == "ride" for step in route.steps):
                    continue

                access_time_sec = int(round(start_distance * walking_seconds_per_pixel))
                egress_time_sec = int(round(end_distance * walking_seconds_per_pixel))
                point_walking_time_sec = access_time_sec + egress_time_sec
                
                # Actual journey time (unpenalized)
                total_journey_time_sec = route.total_time_sec + point_walking_time_sec
                
                # Penalized score for comparison (walking is expensive).
                # route.walking_time_sec is already part of route.total_time_sec;
                # mimic the graph edge cost so access/egress walks are comparable.
                journey_score_time = (
                    (route.total_time_sec - route.walking_time_sec)
                    + int(route.walking_time_sec * WALK_COST_PENALTY_FACTOR)
                    + int(point_walking_time_sec * WALK_COST_PENALTY_FACTOR)
                )
                
                score = (
                    journey_score_time,
                    route.walking_time_sec + point_walking_time_sec,
                    route.transfer_count,
                    route.stop_count,
                )

                if best_score is None or score < best_score:
                    best_score = score
                    best_result = {
                        "start_point": {"x": start_x, "y": start_y},
                        "end_point": {"x": end_x, "y": end_y},
                        "selected_start_station": self._station_payload(start_station_id),
                        "selected_end_station": self._station_payload(end_station_id),
                        "via_stations": [
                            self._station_payload(station_id)
                            for station_id in ordered_via_station_ids
                        ],
                        "access_walk_distance_px": round(start_distance, 2),
                        "egress_walk_distance_px": round(end_distance, 2),
                        "access_walk_time_sec": access_time_sec,
                        "egress_walk_time_sec": egress_time_sec,
                        "total_journey_time_sec": total_journey_time_sec,
                        "route": route.to_dict(),
                    }

        if best_result is None:
            raise ValueError("No route found for the selected points")

        return best_result

    @staticmethod
    def _merge_leg_results(legs: list[RouteResult]) -> RouteResult:
        if not legs:
            raise ValueError("No route legs to merge")

        total_time_sec = 0
        walking_time_sec = 0
        transfer_count = 0
        stop_count = 0
        station_ids: list[str] = []
        line_sequence: list[str] = []
        steps: list[RouteStep] = []

        for index, leg in enumerate(legs):
            total_time_sec += leg.total_time_sec
            walking_time_sec += leg.walking_time_sec
            transfer_count += leg.transfer_count
            stop_count += leg.stop_count
            steps.extend(leg.steps)

            if index == 0:
                station_ids.extend(leg.station_ids)
            else:
                station_ids.extend(leg.station_ids[1:])

            for line_id in leg.line_sequence:
                if not line_sequence or line_sequence[-1] != line_id:
                    line_sequence.append(line_id)

        return RouteResult(
            total_time_sec=total_time_sec,
            walking_time_sec=walking_time_sec,
            transfer_count=transfer_count,
            stop_count=stop_count,
            station_ids=station_ids,
            line_sequence=line_sequence,
            steps=steps,
        )

    @staticmethod
    def _add_cost(left: Cost, right: Cost) -> Cost:
        return (
            left[0] + right[0],
            left[1] + right[1],
            left[2] + right[2],
            left[3] + right[3],
        )


    def _candidate_stations(
        self,
        x: float,
        y: float,
        walking_seconds_per_pixel: float,
        candidate_limit: int | None,
        max_station_walk_sec: int | None,
        prefer_nearest: bool,
        preferred_line_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        # Use spatial grid for fast filtering
        target_lat, target_lon = (y, x) if self.is_geographic else (y, x) # Standardize
        # Wait, self.station_positions stores (latitude, longitude) or (y, x).
        # In diagram mode, station.x, station.y are pixels.
        
        # Grid lookup
        lat_grid = int(target_lat / self.grid_size)
        lon_grid = int(target_lon / self.grid_size)
        
        candidate_ids = set()
        for dy in [-1, 0, 1]:
            for dx in [-1, 0, 1]:
                cell = (lat_grid + dy, lon_grid + dx)
                candidate_ids.update(self.grid.get(cell, []))
        
        if not candidate_ids:
            # Fallback to all stations if grid lookup failed to find anything nearby
            candidate_ids = set(self.network.stations.keys())

        candidates = []
        for sid in candidate_ids:
            st = self.network.stations[sid]
            dist = euclidean_distance(x, y, st.x, st.y)
            
            # If preferred line requested, prioritize stations on those lines
            if preferred_line_ids and not (self.network.station_to_lines[sid] & preferred_line_ids):
                continue
            
            candidates.append((sid, dist))

        # Apply distance filter IF it doesn't empty the list (soft limit behavior)
        if max_station_walk_sec is not None:
            filtered = [
                c for c in candidates 
                if int(round(c[1] * walking_seconds_per_pixel)) <= max_station_walk_sec
            ]
            if filtered:
                candidates = filtered

        if not candidates and preferred_line_ids:
            # Fallback: ignore the grid and search all stations for the preferred line
            all_stations = self.network.stations.values()
            all_candidates = []
            for station in all_stations:
                if self.network.station_to_lines[station.id] & preferred_line_ids:
                    dist = euclidean_distance(x, y, station.x, station.y)
                    all_candidates.append((station.id, dist))
            
            if max_station_walk_sec is not None:
                filtered = [
                    c for c in all_candidates 
                    if int(round(c[1] * walking_seconds_per_pixel)) <= max_station_walk_sec
                ]
                if filtered:
                    all_candidates = filtered
            
            candidates = all_candidates
            
            # If still no candidates found, fallback to all stations ignoring line preference
            if not candidates:
                return self._candidate_stations(
                    x, y, walking_seconds_per_pixel, candidate_limit, 
                    max_station_walk_sec, prefer_nearest, preferred_line_ids=None
                )

        candidates.sort(key=lambda item: (item[1], item[0]))
        
        if prefer_nearest and candidates:
            return [candidates[0]]

        if candidate_limit is None or candidate_limit <= 0 or candidate_limit >= len(candidates):
            return candidates
        return candidates[:candidate_limit]

    def _station_payload(self, station_id: str) -> dict:
        station = self.network.stations[station_id]
        return {
            "id": station.id,
            "name": station.name,
            "x": station.x,
            "y": station.y,
            "line_ids": sorted(self.network.station_to_lines[station.id]),
        }


    def _build_result(
        self,
        goal_state: State,
        total_cost: Cost,
        parents: dict[State, tuple[State | None, Edge | None]],
    ) -> RouteResult:
        states: list[State] = []
        steps: list[RouteStep] = []
        current: State | None = goal_state

        while current is not None:
            previous, edge = parents[current]
            states.append(current)
            if previous is not None and edge is not None:
                steps.append(
                    RouteStep(
                        kind=edge.kind,
                        station_id=previous[0],
                        line_id=previous[1],
                        next_station_id=current[0],
                        duration_sec=edge.duration_sec,
                    )
                )
            current = previous

        states.reverse()
        steps.reverse()

        station_ids = [states[0][0]]
        for step in steps:
            if step.next_station_id and step.next_station_id != station_ids[-1]:
                station_ids.append(step.next_station_id)

        return RouteResult(
            total_time_sec=sum(step.duration_sec for step in steps),
            walking_time_sec=total_cost[1],
            transfer_count=total_cost[2],
            stop_count=total_cost[3],
            station_ids=station_ids,
            line_sequence=self._extract_line_sequence(states, steps),
            steps=steps,
        )


    def _find_edge(self, source: State, target: State) -> Edge | None:
        for edge in self.graph.get(source, []):
            if edge.target == target:
                return edge
        return None

    @staticmethod
    def _extract_line_sequence(states: list[State], steps: list[RouteStep]) -> list[str]:
        sequence: list[str] = []
        current_line: str | None = None

        for state, step in zip(states, steps, strict=False):
            if step.kind != "ride":
                continue
            if state[1] != current_line:
                sequence.append(state[1])
                current_line = state[1]

        if steps and steps[-1].kind == "ride":
            last_line = states[-1][1]
            if not sequence or sequence[-1] != last_line:
                sequence.append(last_line)

        return sequence
