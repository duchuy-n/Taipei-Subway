from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    x: float
    y: float
    diagram_x: float | None = None
    diagram_y: float | None = None


@dataclass(frozen=True)
class Stop:
    id: str
    station_id: str
    name: str
    latitude: float
    longitude: float
    line_id: str | None = None


@dataclass(frozen=True)
class Line:
    id: str
    name: str
    color: str


@dataclass(frozen=True)
class StationLine:
    station_id: str
    line_id: str
    seq: int


@dataclass(frozen=True)
class Segment:
    line_id: str
    from_station_id: str
    to_station_id: str
    travel_sec: int


@dataclass(frozen=True)
class Transfer:
    station_id: str
    from_line_id: str
    to_line_id: str
    transfer_sec: int


@dataclass(frozen=True)
class WalkTransfer:
    from_station_id: str
    to_station_id: str
    duration_sec: int


@dataclass
class SubwayNetwork:
    stations: dict[str, Station]
    lines: dict[str, Line]
    station_lines: list[StationLine]
    segments: list[Segment]
    transfers: list[Transfer]
    stops: dict[str, Stop] = field(default_factory=dict)
    walk_transfers: list[WalkTransfer] = field(default_factory=list)
    station_to_lines: dict[str, set[str]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteStep:
    kind: str
    station_id: str
    line_id: str
    next_station_id: str | None = None
    duration_sec: int = 0
    coordinates: list[tuple[float, float]] | None = None


@dataclass
class RouteResult:
    total_time_sec: int
    walking_time_sec: int
    transfer_count: int
    stop_count: int
    station_ids: list[str]
    line_sequence: list[str]
    steps: list[RouteStep]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_time_sec": self.total_time_sec,
            "walking_time_sec": self.walking_time_sec,
            "transfer_count": self.transfer_count,
            "stop_count": self.stop_count,
            "station_ids": self.station_ids,
            "line_sequence": self.line_sequence,
            "steps": [
                {
                    "kind": step.kind,
                    "station_id": step.station_id,
                    "line_id": step.line_id,
                    "next_station_id": step.next_station_id,
                    "duration_sec": step.duration_sec,
                    "coordinates": step.coordinates,
                }
                for step in self.steps
            ],
        }
