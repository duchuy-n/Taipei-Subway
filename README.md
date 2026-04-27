# IT3160 Subway Web

Web application for route finding on the Taipei MRT network. It combines a GIS map, walking access and egress paths, and an admin console for simulating operational scenarios such as blocked segments, banned stations, and rain zones.

## Highlights

- **GIS Route Studio**: users pick start and end points directly on the map.
- **A\* Route Engine**: routes are calculated on an expanded MRT graph with `(station, line)` state.
- **Walking access and egress**: the system estimates walking legs from the selected point to the entry station and from the exit station to the destination, using a default walking speed of `1.3 m/s`.
- **Metro speed model**: train travel time is interpolated from distance with an `80 km/h` speed model when segment-level `travel_sec` data is unavailable.
- **Admin Scenario**: admins can create rain zones, blocked segments, or banned stations; runtime routing updates automatically from the active scenario.

## Setup

Requires Python 3.12+.

```bash
python -m pip install fastapi uvicorn pydantic
```

## Run The App

On Windows:

```powershell
.\start_web.ps1
```

Or run directly:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

## Screens

- `http://127.0.0.1:8010/` or `/gis`: GIS Route Studio.
- `http://127.0.0.1:8010/login`: demo login.
- `http://127.0.0.1:8010/admin`: admin scenario console.
- `/calibrate` and `/builder`: legacy routes that currently redirect to `/`.

## Main API

- `GET /health`: checks server health.
- `GET /api/gis/network`: returns station data, line data, GeoJSON, and map metadata.
- `POST /api/gis/route/points`: calculates a route from two map coordinates.
- `GET /api/admin/scenarios`: reads the current admin scenario.
- `PUT /api/admin/scenarios`: saves a new admin scenario.
- `DELETE /api/admin/scenarios`: resets the admin scenario.

Legacy APIs such as `/api/route`, `/api/network`, and `/api/builder/network` currently return `410 Gone`; the demo flow uses the newer GIS API.

## Algorithm

The routing core uses A\* on an expanded MRT graph:

- Ride edge: train travel between two adjacent stations on the same line.
- Transfer edge: line transfer inside the same station.
- Walk-transfer edge: walking between nearby stations, or walking around blocked segments in an admin scenario.

Route comparison uses generalized cost:

```text
cost = ride/transfer time + walking_penalty * walking_time
```

The default `walking_penalty = 5.0`, so the system supports walking while still preferring reasonable metro journeys.

## Tests

After dependencies are installed:

```bash
python -m unittest discover -s tests -v
```
