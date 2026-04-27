# QGIS GIS Export Folder

Place QGIS exports in this folder to enable production-accurate GIS mode:

- `network_topology.json`
- `stations.geojson`
- `lines.geojson`
- `station_access_points.geojson`

Requirements:

- CRS: `EPSG:4326` (WGS84 lon/lat)
- `stations.geojson` features must contain `properties.id` matching station ids in `network_topology.json`
- `lines.geojson` should contain `LineString` or `MultiLineString` features

Runtime notes:

- The app now runs in GIS-only mode.
- Backend routing topology loads from `network_topology.json` in this folder instead of the removed legacy default database path.

Manual editing notes:

- `stations.geojson` is the main file to edit when you want to move or disable a GIS station node directly.
- To hide/delete a station node safely, set `properties.deleted` to `true` on that station feature instead of removing the whole feature block.
- `station_access_points.geojson` is optional and can be edited directly when you want to move or remove station entrances/exits.
- `walk_network.geojson` is machine-generated path data and is not intended for hand editing.

All human-edited GIS GeoJSON files in this folder are formatted with indentation so they are easier to review and modify directly.

When these files are missing, `/api/gis/network` automatically falls back to projected coordinates from the GIS topology dataset.
