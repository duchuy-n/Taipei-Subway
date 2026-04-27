---
title: "Current Project Structure"
description: "A short reference describing the current IT3160-SubwayWeb state for demo and defense use."
---

# Current Project Structure

## 1. Problem

The project represents the Taipei MRT network on a GIS map and finds a journey from point A to point B. The core problem is still graph-based metro route finding; the walking layer only connects the user's selected map points to suitable entry and exit stations.

The demo flow should present:

1. The user picks start and end points on the map.
2. The system finds suitable entry and exit stations through the walk network and station access points.
3. The route engine runs A* on the expanded MRT graph.
4. The frontend displays walking to the entry station, riding the train, transferring lines, and walking to the destination.
5. The admin blocks a segment or station.
6. The user recalculates and sees that the route changes according to the scenario.

## 2. Screens

| Route | Role |
| --- | --- |
| `/` or `/gis` | GIS Route Studio for end users |
| `/login` | Demo login for the admin area |
| `/admin` | Admin console for operational scenarios |
| `/calibrate`, `/builder` | Legacy routes that currently redirect to `/` |

## 3. Main Directory Tree

```text
IT3160-SubwayWeb/
|-- app/
|   |-- api/                 # FastAPI endpoints
|   |-- data/                # Topology, GeoJSON, scenario JSON
|   |-- domain/              # Core dataclass models
|   |-- services/            # Route engine, GIS loader, runtime cache, scenario logic
|   `-- static/
|       |-- gis-studio/      # Main route-finding UI
|       |-- admin/           # Scenario admin UI
|       |-- login/           # Demo login
|       `-- shared/          # Shared CSS
|-- docs/
|-- map/
|-- scripts/
|-- tests/
|-- README.md
|-- pyproject.toml
|-- start_web.ps1
`-- start_web.bat
```

## 4. Main API

| Endpoint | Role |
| --- | --- |
| `GET /health` | Check server health |
| `GET /api/gis/network` | Provide stations, lines, GeoJSON, and map metadata |
| `POST /api/gis/route/points` | Find a journey from two map coordinates |
| `GET /api/admin/scenarios` | Read the current admin scenario |
| `PUT /api/admin/scenarios` | Save a new scenario |
| `DELETE /api/admin/scenarios` | Reset the scenario |
| `POST /api/gis/stations` | Update coordinates or soft-delete GIS station features |

Legacy endpoints such as `/api/network`, `/api/route`, `/api/route/points`, and `/api/builder/network` currently return `410 Gone`.

## 5. Route Engine

Main file: `app/services/route_engine.py`.

The route engine uses A* on an expanded state graph. A vertex has the form `(station_id, line_id)`, which lets the system distinguish the current station and current line.

Main edge types:

- `ride`: train travel between adjacent stations on the same line.
- `transfer`: line transfer inside the same station.
- `walk`: walking between nearby stations when a valid connection exists.

Current parameters:

- Walking speed: `1.1 m/s`.
- Train speed for GIS routing: `80 km/h`.
- Walking penalty for route comparison: `5.0x`.
- Default line-switch penalty from config/env: `SUBWAY_LINE_SWITCH_PENALTY`, default `180s`.

Defense point:

> Dijkstra is a special case of A* when the heuristic is zero. Because this project's data includes geographic coordinates for stations, the system uses A* to use distance-to-destination as the heuristic, which is more appropriate for this AI course project.

## 6. Admin Scenario

Main files:

- `app/services/admin_scenarios.py`
- `app/data/admin_scenarios.json`
- `app/static/admin/admin.js`

Admins can create:

- rain zones,
- blocked segments,
- banned stations.

The backend converts scenarios into runtime-network effects, such as removing stations or edges or marking affected segments. After a scenario is saved, the runtime cache is refreshed so later route queries use the new state.

## 7. Remaining Notes

- The UI depends on CDN assets and OSM tiles for MapLibre, so demos need internet access or a prepared fallback.
- Login is a demo UI only; it does not enforce real authentication.
- Tests and docs should stay aligned with the current direction: GIS Studio + Admin + A*, without presenting Builder or Calibration as primary screens.
