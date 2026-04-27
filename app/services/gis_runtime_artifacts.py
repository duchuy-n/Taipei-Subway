from __future__ import annotations

import hashlib
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.gis_loader import get_cached_walk_graph
from app.services.gis_route_geometry import _extract_line_colors_from_geojson
from app.services.gis_route_geometry import load_or_build_geojson_segment_index
from app.services.walk_network import build_walk_targets_by_node


@dataclass(frozen=True)
class GisRuntimeArtifacts:
    walk_graph: object
    walk_targets_by_node: dict
    station_lookup: dict[str, dict]
    geojson_segment_index: dict[tuple[str, str, str], list[tuple[float, float]]]
    geojson_line_colors: dict[str, str]


def load_or_build_gis_runtime_artifacts(
    *,
    project_root: Path,
    qgis_geojson_dir: Path,
    gis_payload: dict[str, Any],
    station_coords_by_id: dict[str, tuple[float, float]],
    signature: str,
) -> GisRuntimeArtifacts:
    cache_path = _runtime_artifact_cache_path(project_root, signature)
    cached_artifacts = _load_persisted_runtime_artifacts(cache_path, signature)
    if cached_artifacts is not None:
        return cached_artifacts

    walk_graph = get_cached_walk_graph(qgis_geojson_dir)
    lines_geojson = gis_payload.get("lines")
    stations_geojson = gis_payload.get("stations")
    artifacts = GisRuntimeArtifacts(
        walk_graph=walk_graph,
        walk_targets_by_node=build_walk_targets_by_node(
            walk_graph,
            gis_payload.get("station_access_points"),
            station_coords_by_id,
        ),
        station_lookup=_build_station_lookup_from_geojson(stations_geojson),
        geojson_segment_index=load_or_build_geojson_segment_index(
            stations_geojson,
            lines_geojson,
            project_root / "cache",
            signature,
        ),
        geojson_line_colors=_extract_line_colors_from_geojson(
            (lines_geojson or {}).get("features", [])
            if isinstance(lines_geojson, dict)
            else []
        ),
    )
    _persist_runtime_artifacts(cache_path, signature, artifacts)
    return artifacts


def describe_runtime_artifact_path(project_root: Path, signature: str) -> Path:
    return _runtime_artifact_cache_path(project_root, signature)


def _build_station_lookup_from_geojson(stations_geojson: dict[str, Any] | None) -> dict[str, dict]:
    if not isinstance(stations_geojson, dict):
        return {}

    lookup: dict[str, dict] = {}
    for feature in stations_geojson.get("features", []):
        properties = feature.get("properties", {}) or {}
        station_id = properties.get("id")
        coordinates = feature.get("geometry", {}).get("coordinates", [None, None])
        if not station_id:
            continue
        lookup[str(station_id)] = {
            "id": str(station_id),
            "name": properties.get("name"),
            "line_ids": list(properties.get("line_ids") or []),
            "lon": coordinates[0],
            "lat": coordinates[1],
        }
    return lookup


def _runtime_artifact_cache_path(project_root: Path, signature: str) -> Path:
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    return project_root / "cache" / f"gis_runtime_artifacts_{digest}.pickle"


def _load_persisted_runtime_artifacts(
    cache_path: Path,
    signature: str,
) -> GisRuntimeArtifacts | None:
    if not cache_path.exists():
        return None
    try:
        with cache_path.open("rb") as handle:
            payload = pickle.load(handle)
    except (OSError, pickle.PickleError, EOFError):
        return None

    if not isinstance(payload, dict) or payload.get("signature") != signature:
        return None
    artifacts = payload.get("artifacts")
    return artifacts if isinstance(artifacts, GisRuntimeArtifacts) else None


def _persist_runtime_artifacts(
    cache_path: Path,
    signature: str,
    artifacts: GisRuntimeArtifacts,
) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("wb") as handle:
            pickle.dump(
                {"signature": signature, "artifacts": artifacts},
                handle,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
    except OSError:
        return
