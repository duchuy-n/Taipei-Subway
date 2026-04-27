from __future__ import annotations

import math

BLOCK_POINT_SEGMENT_THRESHOLD_M = 200.0
BLOCK_LINE_SEGMENT_THRESHOLD_M = 20.0


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points 
    on the earth (specified in decimal degrees)
    """
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


def walking_time_sec(distance_m: float, walking_m_per_sec: float) -> int:
    """Calculate walking time in seconds based on distance and speed."""
    if walking_m_per_sec <= 0:
        return 0
    return max(0, int(round(distance_m / walking_m_per_sec)))


def euclidean_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    """Calculate standard Euclidean distance between two points."""
    return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)


def segments_intersect(p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float], p4: tuple[float, float]) -> bool:
    """Check if segment (p1, p2) intersects with (p3, p4). Coordinates are (lon, lat)."""
    def ccw(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
        return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])

    return (ccw(p1, p3, p4) != ccw(p2, p3, p4)) and (ccw(p1, p2, p3) != ccw(p1, p2, p4))


def point_to_segment_distance_m(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    """Calculate distance from point to segment in meters using a local planar approximation."""
    avg_lat_rad = math.radians((point[1] + start[1] + end[1]) / 3.0)
    scale_x = 111_320.0 * math.cos(avg_lat_rad)
    scale_y = 111_320.0

    point_x = point[0] * scale_x
    point_y = point[1] * scale_y
    start_x = start[0] * scale_x
    start_y = start[1] * scale_y
    end_x = end[0] * scale_x
    end_y = end[1] * scale_y

    delta_x = end_x - start_x
    delta_y = end_y - start_y
    denominator = (delta_x * delta_x) + (delta_y * delta_y)

    if denominator <= 1e-12:
        return math.hypot(point_x - start_x, point_y - start_y)

    offset = max(
        0.0,
        min(
            1.0,
            (((point_x - start_x) * delta_x) + ((point_y - start_y) * delta_y)) / denominator,
        ),
    )
    projected_x = start_x + (offset * delta_x)
    projected_y = start_y + (offset * delta_y)
    return math.hypot(point_x - projected_x, point_y - projected_y)


def is_line_near_geometry(
    line_start: tuple[float, float],
    line_end: tuple[float, float],
    geometry_coords: list[tuple[float, float]],
    threshold_m: float,
) -> bool:
    """Check if a line (start, end) is near or intersects a sequence of coordinates."""
    if not geometry_coords or len(geometry_coords) < 2:
        return False

    for start, end in zip(geometry_coords, geometry_coords[1:], strict=False):
        if point_to_segment_distance_m(line_start, start, end) <= threshold_m:
            return True
        if point_to_segment_distance_m(line_end, start, end) <= threshold_m:
            return True
        if segments_intersect(line_start, line_end, start, end):
            return True
    return False
