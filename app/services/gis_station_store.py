from __future__ import annotations

import json
from pathlib import Path


def save_gis_station_positions(
    path: str | Path,
    positions: dict[str, dict[str, float | bool]],
) -> int:
    file_path = Path(path)
    if not file_path.exists():
        raise ValueError(f"GIS stations file not found: {file_path}")

    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise ValueError("GIS stations payload must be a GeoJSON FeatureCollection")

    features_by_station_id: dict[str, dict] = {}
    for feature in payload["features"]:
        station_id = feature.get("properties", {}).get("id")
        if station_id:
            features_by_station_id[str(station_id)] = feature

    unknown_station_ids = sorted(
        station_id
        for station_id in positions
        if station_id not in features_by_station_id
    )
    if unknown_station_ids:
        raise ValueError(f"Unknown GIS station id(s): {', '.join(unknown_station_ids)}")

    updated_count = 0
    for station_id, coordinates in positions.items():
        feature = features_by_station_id[station_id]
        if feature.get("geometry", {}).get("type") != "Point":
            raise ValueError(f"GIS station {station_id} does not have Point geometry")

        feature["geometry"]["coordinates"] = [
            round(float(coordinates["lon"]), 7),
            round(float(coordinates["lat"]), 7),
        ]
        feature.setdefault("properties", {})["deleted"] = bool(coordinates.get("deleted", False))
        updated_count += 1

    file_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return updated_count


def delete_gis_station(path: str | Path, station_id: str) -> int:
    return save_gis_station_positions(
        path,
        {
            station_id: {
                "lon": 0.0,
                "lat": 0.0,
                "deleted": True,
            }
        },
    )
