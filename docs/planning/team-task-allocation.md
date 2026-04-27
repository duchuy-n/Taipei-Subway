# Demo And Defense Task Allocation

Current goal: present the project as a Taipei MRT route-finding system with GIS integration, A* search, walking access and egress, and admin scenarios.

## Team 1 - Backend, Algorithm, API

**Scope**

- `app/api/routes.py`
- `app/services/route_engine.py`
- `app/services/runtime.py`
- `app/services/admin_scenarios.py`
- `tests/test_route_engine.py`
- `tests/test_admin_scenarios.py`

**Key points to finalize**

- Explain that the route engine uses A* on the `(station, line)` graph.
- State that Dijkstra is the baseline and A* is selected because station coordinates provide a useful geographic heuristic.
- Explain the cost model: train time, transfer time, walking time, and walking penalty.
- Verify that admin blocked segments and banned stations can change the calculated route.

## Team 2 - Frontend And Demo Flow

**Scope**

- `app/static/gis-studio/*`
- `app/static/admin/*`
- `app/static/login/*`
- `app/static/shared/*`

**Key points to finalize**

- Demo from `/`: pick A/B points, calculate a route, and read the summary.
- Demo from `/admin`: create a blocked segment or banned station.
- Return to `/`, calculate again, and show that the route changed.
- Prepare one reliable demo point pair.

## Team 3 - Data/GIS And Report

**Scope**

- `app/data/gis/*`
- `app/data/admin_scenarios.json`
- `scripts/map/*`
- `IT3160_Report/*`
- `docs/*`

**Key points to finalize**

- Do not describe Dijkstra as the main algorithm when the code uses A*.
- Do not present Calibration or Builder as primary features because the current UI no longer exposes them as separate screens.
- Describe walking as an access/egress layer that supports the map experience, not as the main routing problem replacing metro travel.
- Keep documentation aligned with the current routes: `/`, `/gis`, `/login`, and `/admin`.

## Short Defense Script

1. "The core problem is route finding on the MRT network."
2. "Users pick points on the map; the system snaps those points to suitable stations through the walk network."
3. "The route engine uses A* on the expanded `(station, line)` state graph."
4. "The cost model penalizes walking so the system does not overuse walking instead of metro travel."
5. "Admins can block a segment or station; the runtime graph changes and the route is recalculated."

## Answer For Dijkstra Versus A*

> Dijkstra is suitable when no directional information is available. In this project, each station has geographic coordinates, so the system uses A* with a heuristic based on distance to the destination divided by maximum train speed. If the heuristic is set to zero, A* becomes Dijkstra, so A* is the more general and more appropriate choice for this AI course project.
