const state = {
  network: null,
  gis: null,
  map: null,
  pickMode: "start",
  startPoint: null,
  endPoint: null,
  viaStationIds: [],
  routeResult: null,
  stationCoordsById: new Map(),
  stationById: new Map(),
  lineById: new Map(),
  suppressNextMapClick: false,
  sidebarVisible: true,
  sourceDataKeys: {
    pickedPoints: null,
    selectedStations: null,
    route: null,
  },
};

const elements = {
  gisSourceBadge: document.getElementById("gisSourceBadge"),
  pickStartBtn: document.getElementById("pickStartBtn"),
  pickEndBtn: document.getElementById("pickEndBtn"),
  pickViaBtn: document.getElementById("pickViaBtn"),
  clearViaBtn: document.getElementById("clearViaBtn"),
  findRouteBtn: document.getElementById("findRouteBtn"),
  resetBtn: document.getElementById("resetBtn"),
  toggleSidebarBtn: document.getElementById("toggleSidebarBtn"),
  openSidebarBtn: document.getElementById("openSidebarBtn"),
  statusText: document.getElementById("statusText"),
  selectionCard: document.getElementById("selectionCard"),
  summaryCard: document.getElementById("summaryCard"),
  stepsList: document.getElementById("stepsList"),
};

const SOURCE_IDS = {
  lines: "metro-lines",
  stations: "metro-stations",
  pickedPoints: "picked-points",
  selectedStations: "selected-stations",
  route: "route-lines",
};
const SIDEBAR_TRANSITION_MS = 280;
const DEFAULT_VIEWPORT_BOUNDS = [121.44, 24.97, 121.62, 25.13];
const MAX_FOCUS_LON_SPAN = 0.22;
const MAX_FOCUS_LAT_SPAN = 0.16;
const MIN_FOCUS_LON_SPAN = 0.12;
const MIN_FOCUS_LAT_SPAN = 0.09;
const ROUTE_WALK_COLOR = "#14b8a6";
const ROUTE_SELECTED_MAIN_COLOR = "#58DE1B";
const ROUTE_SELECTED_GLOW_COLOR = "rgba(88, 222, 27, 0.42)";
const ROUTE_SELECTED_CORE_COLOR = "#d8f8c6";
const VIA_STATION_COLOR = "#d97706";
const VIA_STATION_TEXT_COLOR = "#7a4d00";
const PICKED_POINT_COLOR_MATCH = [
  "match",
  ["get", "role"],
  "start_point",
  "#16a34a",
  "#dc2626",
];

async function init() {
  if (!window.maplibregl) {
    setStatus("MapLibre failed to load. Check internet/CDN access.");
    return;
  }

  try {
    const gisResponse = await fetch("/api/gis/network");
    state.gis = await gisResponse.json();

    if (!gisResponse.ok) {
      throw new Error("Failed to load GIS API payload.");
    }

    buildMetadataLookup();
    buildStationCoordinateLookup();
    bindEvents();
    applySidebarState();
    initializeMap();
    renderAll();

    const networkSourceLabel = state.gis.source?.startsWith("qgis_geojson")
      ? "QGIS GeoJSON"
      : "Fallback Projection";
    const baseMapLabel = state.gis.basemap?.enabled
      ? "Local Raster"
      : "OSM Raster";
    elements.gisSourceBadge.textContent = `${networkSourceLabel} + ${baseMapLabel}`;
    setStatus(
      state.gis.source?.startsWith("qgis_geojson")
        ? state.gis.basemap?.enabled
          ? "GIS source loaded from QGIS export with local raster basemap."
          : "GIS source loaded from QGIS export with OSM fallback basemap."
        : state.gis.basemap?.enabled
          ? "Projected GIS topology fallback is active with local raster basemap."
          : "Projected GIS topology fallback is active. Put QGIS exports into app/data/gis for true geospatial accuracy.",
    );
  } catch (error) {
    console.error(error);
    setStatus(error.message || "Unable to initialize GIS studio.");
  }
}

function buildMetadataLookup() {
  state.lineById = new Map((state.gis?.line_catalog || []).map((line) => [line.id, line]));
  state.stationById = new Map((state.gis?.station_catalog || []).map((station) => [station.id, station]));

  (state.gis?.lines?.features || []).forEach((feature) => {
    const properties = feature?.properties || {};
    const lineId = properties.line_id;
    if (!lineId || state.lineById.has(lineId)) {
      return;
    }
    state.lineById.set(lineId, {
      id: lineId,
      name: properties.line_name || lineId,
      color: properties.line_color || "#7b8794",
    });
  });

  (state.gis?.stations?.features || []).forEach((feature) => {
    const properties = feature?.properties || {};
    const stationId = properties.id;
    if (!stationId || state.stationById.has(stationId)) {
      return;
    }
    state.stationById.set(stationId, {
      id: stationId,
      name: properties.name || stationId,
      line_ids: Array.isArray(properties.line_ids) ? properties.line_ids : [],
    });
  });
}

function buildStationCoordinateLookup() {
  state.stationCoordsById.clear();

  (state.gis.stations?.features || []).forEach((feature) => {
    const stationId = feature?.properties?.id;
    const coordinates = feature?.geometry?.coordinates;
    if (!stationId || !Array.isArray(coordinates) || coordinates.length < 2) {
      return;
    }
    state.stationCoordsById.set(stationId, [Number(coordinates[0]), Number(coordinates[1])]);
  });
}

function computeFeatureBounds(featureCollection) {
  const features = featureCollection?.features;
  if (!Array.isArray(features) || !features.length) {
    return null;
  }

  let minLon = Infinity;
  let minLat = Infinity;
  let maxLon = -Infinity;
  let maxLat = -Infinity;

  features.forEach((feature) => {
    const coordinates = feature?.geometry?.coordinates;
    if (!Array.isArray(coordinates) || coordinates.length < 2) {
      return;
    }
    const [lon, lat] = coordinates;
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) {
      return;
    }
    minLon = Math.min(minLon, lon);
    minLat = Math.min(minLat, lat);
    maxLon = Math.max(maxLon, lon);
    maxLat = Math.max(maxLat, lat);
  });

  if (!Number.isFinite(minLon)) {
    return null;
  }
  return [minLon, minLat, maxLon, maxLat];
}

function clampBounds(innerBounds, outerBounds) {
  if (!Array.isArray(innerBounds) || !Array.isArray(outerBounds)) {
    return innerBounds;
  }

  const [outerMinLon, outerMinLat, outerMaxLon, outerMaxLat] = outerBounds;
  const width = innerBounds[2] - innerBounds[0];
  const height = innerBounds[3] - innerBounds[1];
  const clampedMinLon = Math.min(Math.max(innerBounds[0], outerMinLon), outerMaxLon - width);
  const clampedMinLat = Math.min(Math.max(innerBounds[1], outerMinLat), outerMaxLat - height);
  return [
    clampedMinLon,
    clampedMinLat,
    clampedMinLon + width,
    clampedMinLat + height,
  ];
}

function expandBounds(bounds, ratio) {
  const width = bounds[2] - bounds[0];
  const height = bounds[3] - bounds[1];
  const padLon = width * ratio;
  const padLat = height * ratio;
  return [
    bounds[0] - padLon,
    bounds[1] - padLat,
    bounds[2] + padLon,
    bounds[3] + padLat,
  ];
}

function resolveViewportBounds() {
  const stationBounds = computeFeatureBounds(state.gis?.stations);
  const outerBounds = state.gis?.basemap?.bounds || state.gis?.bounds || DEFAULT_VIEWPORT_BOUNDS;
  if (!stationBounds) {
    return DEFAULT_VIEWPORT_BOUNDS;
  }

  const centerLon = (stationBounds[0] + stationBounds[2]) / 2;
  const centerLat = (stationBounds[1] + stationBounds[3]) / 2;
  const lonSpan = Math.min(
    Math.max((stationBounds[2] - stationBounds[0]) * 0.72, MIN_FOCUS_LON_SPAN),
    MAX_FOCUS_LON_SPAN,
  );
  const latSpan = Math.min(
    Math.max((stationBounds[3] - stationBounds[1]) * 0.72, MIN_FOCUS_LAT_SPAN),
    MAX_FOCUS_LAT_SPAN,
  );

  return clampBounds(
    [
      centerLon - lonSpan / 2,
      centerLat - latSpan / 2,
      centerLon + lonSpan / 2,
      centerLat + latSpan / 2,
    ],
    outerBounds,
  );
}

function resolvePanBounds() {
  const viewportBounds = resolveViewportBounds();
  const outerBounds = state.gis?.basemap?.bounds || state.gis?.bounds || DEFAULT_VIEWPORT_BOUNDS;
  return clampBounds(expandBounds(viewportBounds, 0.18), outerBounds);
}

function bindEvents() {
  elements.pickStartBtn.addEventListener("click", () => setPickMode("start"));
  elements.pickEndBtn.addEventListener("click", () => setPickMode("end"));
  elements.pickViaBtn.addEventListener("click", () => setPickMode("via"));
  elements.clearViaBtn.addEventListener("click", clearViaStations);
  elements.findRouteBtn.addEventListener("click", findRouteForPoints);
  elements.resetBtn.addEventListener("click", resetAll);
  elements.toggleSidebarBtn?.addEventListener("click", toggleSidebar);
  elements.openSidebarBtn?.addEventListener("click", showSidebar);
  window.addEventListener("resize", () => {
    resizeMapAfterLayout(0);
  });
}

function buildBasemapSource() {
  const basemap = state.gis?.basemap;
  if (basemap?.enabled && basemap.tiles_url) {
    return {
      type: "raster",
      tiles: [basemap.tiles_url],
      tileSize: Number(basemap.tile_size || 256),
      minzoom: Number(basemap.minzoom || 0),
      maxzoom: Number(basemap.maxzoom || 22),
      attribution: basemap.name || "Local raster",
    };
  }

  return {
    type: "raster",
    tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
    tileSize: 256,
    attribution: "(c) OpenStreetMap contributors",
  };
}

function initializeMap() {
  const basemapSource = buildBasemapSource();
  state.map = new maplibregl.Map({
    container: "gisMap",
    style: {
      version: 8,
      glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
      sources: {
        basemap: basemapSource,
      },
      layers: [
        {
          id: "basemap-background",
          type: "background",
          paint: {
            "background-color": "#f4f1e8",
          },
        },
        {
          id: "basemap-raster",
          type: "raster",
          source: "basemap",
        },
      ],
    },
    center: [121.54, 25.05],
    zoom: 11.2,
    attributionControl: true,
  });

  state.map.addControl(new maplibregl.NavigationControl(), "top-right");
  state.map.on("load", handleMapLoad);
}

function handleMapLoad() {
  state.map.addSource(SOURCE_IDS.lines, {
    type: "geojson",
    data: state.gis.lines,
  });
  state.map.addSource(SOURCE_IDS.stations, {
    type: "geojson",
    data: state.gis.stations,
  });
  state.map.addSource(SOURCE_IDS.pickedPoints, {
    type: "geojson",
    data: emptyFeatureCollection(),
  });
  state.map.addSource(SOURCE_IDS.selectedStations, {
    type: "geojson",
    data: emptyFeatureCollection(),
  });
  state.map.addSource(SOURCE_IDS.route, {
    type: "geojson",
    data: emptyFeatureCollection(),
  });

  state.map.addLayer({
    id: "metro-lines-casing",
    type: "line",
    source: SOURCE_IDS.lines,
    layout: {
      "line-cap": "round",
      "line-join": "round",
    },
    paint: {
      "line-color": "rgba(15,23,42,0.2)",
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 3.6, 13, 7.6],
      "line-opacity": 0.72,
    },
  });

  state.map.addLayer({
    id: "metro-lines-base",
    type: "line",
    source: SOURCE_IDS.lines,
    layout: {
      "line-cap": "round",
      "line-join": "round",
    },
    paint: {
      "line-color": ["coalesce", ["get", "line_color"], "#637081"],
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 2.6, 13, 5.8],
      "line-opacity": 0.84,
    },
  });

  state.map.addLayer({
    id: "route-lines-halo",
    type: "line",
    source: SOURCE_IDS.route,
    filter: ["==", ["get", "kind"], "ride"],
    layout: {
      "line-cap": "round",
      "line-join": "round",
    },
    paint: {
      "line-color": "rgba(255,255,255,0.96)",
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 7, 13, 12],
      "line-opacity": 0.82,
    },
  });

  state.map.addLayer({
    id: "route-lines-ride",
    type: "line",
    source: SOURCE_IDS.route,
    filter: ["==", ["get", "kind"], "ride"],
    layout: {
      "line-cap": "round",
      "line-join": "round",
    },
    paint: {
      "line-color": ROUTE_SELECTED_MAIN_COLOR,
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 4.8, 13, 9.1],
      "line-opacity": 1,
    },
  });

  state.map.addLayer({
    id: "route-lines-ride-highlight",
    type: "line",
    source: SOURCE_IDS.route,
    filter: ["==", ["get", "kind"], "ride"],
    layout: {
      "line-cap": "round",
      "line-join": "round",
    },
    paint: {
      "line-color": ROUTE_SELECTED_GLOW_COLOR,
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 6.2, 13, 11.4],
      "line-opacity": 0.9,
    },
  });

  state.map.addLayer({
    id: "route-lines-ride-core",
    type: "line",
    source: SOURCE_IDS.route,
    filter: ["==", ["get", "kind"], "ride"],
    layout: {
      "line-cap": "round",
      "line-join": "round",
    },
    paint: {
      "line-color": ROUTE_SELECTED_CORE_COLOR,
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.85, 13, 1.8],
      "line-opacity": 0.9,
    },
  });

  state.map.addLayer({
    id: "route-lines-walk-halo",
    type: "line",
    source: SOURCE_IDS.route,
    filter: ["==", ["get", "kind"], "walk"],
    layout: {
      "line-cap": "round",
      "line-join": "round",
    },
    paint: {
      "line-color": "rgba(255,255,255,0.96)",
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 5.2, 13, 9.2],
      "line-opacity": 0.84,
    },
  });

  state.map.addLayer({
    id: "route-lines-walk-underlay",
    type: "line",
    source: SOURCE_IDS.route,
    filter: ["==", ["get", "kind"], "walk"],
    layout: {
      "line-cap": "round",
      "line-join": "round",
    },
    paint: {
      "line-color": ROUTE_WALK_COLOR,
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 3.1, 13, 5.2],
      "line-opacity": 0.68,
    },
  });

  state.map.addLayer({
    id: "route-lines-walk",
    type: "line",
    source: SOURCE_IDS.route,
    filter: ["==", ["get", "kind"], "walk"],
    layout: {
      "line-cap": "round",
      "line-join": "round",
    },
    paint: {
      "line-color": ROUTE_WALK_COLOR,
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 3.4, 13, 6.4],
      "line-opacity": 1,
      "line-dasharray": [2.2, 0.95],
    },
  });

  state.map.addLayer({
    id: "route-transfer-marker-halo",
    type: "circle",
    source: SOURCE_IDS.route,
    filter: ["==", ["get", "kind"], "transfer_marker"],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 7, 13, 11],
      "circle-color": "#ffffff",
      "circle-stroke-color": ROUTE_WALK_COLOR,
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 9, 2.2, 13, 3.4],
      "circle-opacity": 0.98,
    },
  });

  state.map.addLayer({
    id: "route-transfer-marker-core",
    type: "circle",
    source: SOURCE_IDS.route,
    filter: ["==", ["get", "kind"], "transfer_marker"],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.7, 13, 4.8],
      "circle-color": ROUTE_WALK_COLOR,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 1.2,
    },
  });

  state.map.addLayer({
    id: "route-transfer-marker-label",
    type: "symbol",
    source: SOURCE_IDS.route,
    filter: ["==", ["get", "kind"], "transfer_marker"],
    minzoom: 12.5,
    layout: {
      "text-field": "Transfer",
      "text-font": ["Noto Sans Bold"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 12.5, 10, 15, 12],
      "text-offset": [0.9, -0.9],
      "text-anchor": "left",
      "text-allow-overlap": true,
    },
    paint: {
      "text-color": ROUTE_WALK_COLOR,
      "text-halo-color": "#ffffff",
      "text-halo-width": 1.8,
    },
  });

  state.map.addLayer({
    id: "metro-stations-circle",
    type: "circle",
    source: SOURCE_IDS.stations,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 3, 13, 6.1],
      "circle-color": "#0f172a",
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 1.6,
      "circle-opacity": 0.9,
    },
  });

  state.map.addLayer({
    id: "selected-stations-circle",
    type: "circle",
    source: SOURCE_IDS.selectedStations,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 5.8, 13, 10.8],
      "circle-color": "#ffffff",
      "circle-stroke-color": VIA_STATION_COLOR,
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 9, 2.4, 13, 3.2],
      "circle-opacity": 0.98,
    },
  });

  state.map.addLayer({
    id: "selected-stations-core",
    type: "circle",
    source: SOURCE_IDS.selectedStations,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.4, 13, 4.8],
      "circle-color": VIA_STATION_COLOR,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 1.2,
      "circle-opacity": 1,
    },
  });

  state.map.addLayer({
    id: "selected-stations-label",
    type: "symbol",
    source: SOURCE_IDS.selectedStations,
    layout: {
      "text-field": ["get", "name"],
      "text-font": ["Noto Sans Bold"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 10, 11, 14, 15],
      "text-offset": [0.95, -0.7],
      "text-anchor": "left",
      "text-allow-overlap": true,
      "text-ignore-placement": true,
    },
    paint: {
      "text-color": VIA_STATION_TEXT_COLOR,
      "text-halo-color": "#ffffff",
      "text-halo-width": 2,
      "text-opacity": 1,
    },
  });

  state.map.addLayer({
    id: "picked-points-circle",
    type: "circle",
    source: SOURCE_IDS.pickedPoints,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 4.8, 13, 8.8],
      "circle-color": "#ffffff",
      "circle-stroke-color": PICKED_POINT_COLOR_MATCH,
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 9, 2.2, 13, 3],
      "circle-opacity": 0.98,
    },
  });

  state.map.addLayer({
    id: "picked-points-core",
    type: "circle",
    source: SOURCE_IDS.pickedPoints,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.1, 13, 3.9],
      "circle-color": PICKED_POINT_COLOR_MATCH,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 1.1,
      "circle-opacity": 1,
    },
  });

  state.map.addLayer({
    id: "picked-points-label",
    type: "symbol",
    source: SOURCE_IDS.pickedPoints,
    layout: {
      "text-field": ["get", "label"],
      "text-font": ["Noto Sans Bold"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 10, 10, 14, 13],
      "text-offset": [0, 1.25],
      "text-anchor": "top",
      "text-allow-overlap": true,
    },
    paint: {
      "text-color": "#0f172a",
      "text-halo-color": "#ffffff",
      "text-halo-width": 1.6,
      "text-opacity": 0.98,
    },
  });

  state.map.addLayer({
    id: "metro-stations-label",
    type: "symbol",
    source: SOURCE_IDS.stations,
    minzoom: 11.8,
    layout: {
      "text-field": ["get", "name"],
      "text-font": ["Noto Sans Regular"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 11.8, 10, 14, 13.5],
      "text-offset": [0.75, -0.65],
      "text-anchor": "left",
      "text-allow-overlap": true,
      "text-ignore-placement": false,
    },
    paint: {
      "text-color": "#0f172a",
      "text-halo-color": "#ffffff",
      "text-halo-width": 1.5,
      "text-opacity": ["interpolate", ["linear"], ["zoom"], 11.8, 0, 12.6, 0.96],
    },
  });

  state.map.on("mouseenter", "metro-stations-circle", () => {
    state.map.getCanvas().style.cursor = "pointer";
  });
  state.map.on("mouseleave", "metro-stations-circle", () => {
    state.map.getCanvas().style.cursor = "";
  });

  state.map.on("click", "metro-stations-circle", (event) => {
    const feature = event.features?.[0];
    const stationId = feature?.properties?.id;
    if (!stationId) {
      return;
    }

    if (state.pickMode === "via") {
      toggleViaStation(stationId);
      state.suppressNextMapClick = true;
      return;
    }

    const coordinates = feature.geometry?.coordinates;
    if (!Array.isArray(coordinates) || coordinates.length < 2) {
      return;
    }
    if (state.pickMode === "start") {
      state.startPoint = { lon: Number(coordinates[0]), lat: Number(coordinates[1]) };
    } else if (state.pickMode === "end") {
      state.endPoint = { lon: Number(coordinates[0]), lat: Number(coordinates[1]) };
    }
    state.routeResult = null;
    state.suppressNextMapClick = true;
    updatePickedPointsSource();
    updateSelectedStationsSource();
    updateRouteSource(emptyFeatureCollection());
    renderAll();
    setStatus("Point updated. Click Find Route to calculate.");
  });

  state.map.on("click", (event) => {
    if (state.suppressNextMapClick) {
      state.suppressNextMapClick = false;
      return;
    }
    if (state.pickMode === "via") {
      setStatus("Via mode: click on a station to add/remove stopover.");
      return;
    }

    const point = {
      lon: roundTo6(event.lngLat.lng),
      lat: roundTo6(event.lngLat.lat),
    };
    if (state.pickMode === "start") {
      state.startPoint = point;
    } else {
      state.endPoint = point;
    }
    state.routeResult = null;
    updatePickedPointsSource();
    updateSelectedStationsSource();
    updateRouteSource(emptyFeatureCollection());
    renderAll();
    setStatus("Point updated. Click Find Route to calculate.");
  });

  const bounds = resolveViewportBounds();
  const maxBounds = resolvePanBounds();
  state.map.fitBounds(
    [
      [bounds[0], bounds[1]],
      [bounds[2], bounds[3]],
    ],
    { padding: 40, duration: 0 },
  );
  state.map.setMaxBounds([
    [maxBounds[0], maxBounds[1]],
    [maxBounds[2], maxBounds[3]],
  ]);

  updatePickedPointsSource();
  updateSelectedStationsSource();
  updateRouteSource(emptyFeatureCollection());
  resizeMapAfterLayout(0);
}

function setPickMode(mode) {
  state.pickMode = mode;
  elements.pickStartBtn.classList.toggle("active", mode === "start");
  elements.pickEndBtn.classList.toggle("active", mode === "end");
  elements.pickViaBtn.classList.toggle("active", mode === "via");
  setStatus(
    mode === "start"
      ? "Click anywhere on map to set START point."
      : mode === "end"
        ? "Click anywhere on map to set END point."
        : "Via mode: click station circles to add/remove stopovers.",
  );
}

function toggleSidebar() {
  state.sidebarVisible = !state.sidebarVisible;
  applySidebarState();
}

function showSidebar() {
  if (state.sidebarVisible) {
    return;
  }
  state.sidebarVisible = true;
  applySidebarState();
}

function applySidebarState() {
  const shell = document.querySelector(".gis-shell");
  if (!shell) {
    return;
  }

  shell.classList.toggle("is-sidebar-hidden", !state.sidebarVisible);
  if (elements.toggleSidebarBtn) {
    elements.toggleSidebarBtn.textContent = state.sidebarVisible ? "Hide Sidebar" : "Show Sidebar";
    elements.toggleSidebarBtn.setAttribute("aria-expanded", state.sidebarVisible ? "true" : "false");
  }
  if (elements.openSidebarBtn) {
    elements.openSidebarBtn.textContent = state.sidebarVisible ? "Menu" : "Show Menu";
    elements.openSidebarBtn.setAttribute("aria-expanded", state.sidebarVisible ? "true" : "false");
  }
  resizeMapAfterLayout(SIDEBAR_TRANSITION_MS);
}

function resizeMapAfterLayout(delayMs) {
  if (!state.map) {
    return;
  }
  requestAnimationFrame(() => state.map.resize());
  if (delayMs > 0) {
    window.setTimeout(() => state.map.resize(), delayMs);
    window.setTimeout(() => state.map.resize(), delayMs + 120);
  }
}

function toggleViaStation(stationId) {
  if (state.viaStationIds.includes(stationId)) {
    state.viaStationIds = state.viaStationIds.filter((id) => id !== stationId);
  } else {
    state.viaStationIds.push(stationId);
  }
  state.routeResult = null;
  updateSelectedStationsSource();
  updateRouteSource(emptyFeatureCollection());
  renderAll();
}

function clearViaStations() {
  state.viaStationIds = [];
  state.routeResult = null;
  updateSelectedStationsSource();
  updateRouteSource(emptyFeatureCollection());
  renderAll();
  setStatus("VIA stations cleared.");
}

async function findRouteForPoints() {
  if (!state.startPoint || !state.endPoint) {
    setStatus("Please pick both START and END points first.");
    return;
  }

  try {
    setStatus("Calculating route...");
    const response = await fetch("/api/gis/route/points", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start_lon: state.startPoint.lon,
        start_lat: state.startPoint.lat,
        end_lon: state.endPoint.lon,
        end_lat: state.endPoint.lat,
        via_station_ids: state.viaStationIds,
        walking_m_per_sec: 1.1,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Unable to calculate route.");
    }

    state.routeResult = payload;
    updateSelectedStationsSource();
    updateRouteSource(buildRouteGeoJson(payload));
    renderAll();
    setStatus("Route ready with highlighted travel path.");
  } catch (error) {
    console.error(error);
    setStatus(error.message || "Route calculation failed.");
  }
}

function buildRouteGeoJson(resultPayload) {
  const features = [];
  const route = resultPayload.route || {};
  const ridePathFeatures = Array.isArray(resultPayload.ride_path_features)
    ? resultPayload.ride_path_features
    : [];

  (route.steps || []).forEach((step) => {
    // Skip walk/transfer steps in this loop because we rely on ridePathFeatures (road-following)
    // or access_walk_path/egress_walk_path for these. 
    // This loop now only serves as a fallback for the "ride" part of the journey if ridePathFeatures is empty.
    if (step.kind === "walk" || step.kind === "transfer" || !step.next_station_id) {
      return;
    }
    
    // If we have detailed ride features, we don't need to draw the straight subway segments either.
    if (step.kind === "ride" && ridePathFeatures.length) {
      return;
    }

    const from = state.stationCoordsById.get(step.station_id);
    const to = state.stationCoordsById.get(step.next_station_id);
    if (!from || !to) {
      return;
    }
    features.push({
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [from, to],
      },
      properties: {
        kind: step.kind === "ride" ? "ride" : "walk",
      },
    });
  });

  ridePathFeatures.forEach((feature) => {
    const coordinates = feature?.geometry?.coordinates;
    if (!Array.isArray(coordinates) || coordinates.length < 2) {
      return;
    }
    features.push(feature);
  });

  (route.steps || []).forEach((step) => {
    if (step.kind !== "walk" && step.kind !== "transfer") {
      return;
    }
    if (step.kind === "transfer") {
      addTransferMarker(features, step);
    }
    if (!step.next_station_id || step.station_id === step.next_station_id || hasStepWalkFeature(features, step)) {
      return;
    }
    addWalkingFeature(
      features,
      { coordinates: step.coordinates },
      [stationCoordinate(step.station_id), stationCoordinate(step.next_station_id)],
      step.kind,
    );
  });

  if (resultPayload.journey_mode === "walk_fallback") {
    addDirectFallbackWalkFeature(features, resultPayload);
    return {
      type: "FeatureCollection",
      features,
    };
  }

  addWalkingFeature(
    features,
    resultPayload.access_walk_path,
    [
      pointToCoordinate(state.startPoint),
      accessPointCoordinate(resultPayload.selected_start_access_point),
      stationCoordinate(resultPayload.selected_start_station?.id),
    ],
    "access",
  );

  addWalkingFeature(
    features,
    resultPayload.egress_walk_path,
    [
      stationCoordinate(resultPayload.selected_end_station?.id),
      accessPointCoordinate(resultPayload.selected_end_access_point),
      pointToCoordinate(state.endPoint),
    ],
    "egress",
  );

  return {
    type: "FeatureCollection",
    features,
  };
}

function addWalkingFeature(features, geometry, fallbackCoordinates, phase) {
  const coordinates = normalizeLineStringCoordinates(geometry?.coordinates);
  const fallback = fallbackCoordinates.filter(Boolean);
  const resolvedCoordinates = coordinates.length >= 2
    ? stitchWalkingEndpoints(coordinates, fallback)
    : fallback;
  if (resolvedCoordinates.length < 2) {
    return;
  }
  features.push({
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: resolvedCoordinates,
    },
    properties: { kind: "walk", phase },
  });
}

function addDirectFallbackWalkFeature(features, resultPayload) {
  const coordinates = normalizeLineStringCoordinates(resultPayload.access_walk_path?.coordinates);
  if (coordinates.length >= 2) {
    addWalkingFeature(features, resultPayload.access_walk_path, [], "direct");
    return;
  }

  const start = pointToCoordinate(state.startPoint);
  const end = pointToCoordinate(state.endPoint);
  if (!start || !end) {
    return;
  }

  if (coordinateDistanceM(start, end) > 1200) {
    return;
  }

  addWalkingFeature(features, null, [start, end], "direct");
}

function addTransferMarker(features, step) {
  const coordinate = stationCoordinate(step.station_id);
  if (!coordinate) {
    return;
  }
  features.push({
    type: "Feature",
    geometry: {
      type: "Point",
      coordinates: coordinate,
    },
    properties: {
      kind: "transfer_marker",
      station_id: step.station_id,
      line_id: step.line_id || "",
      duration_sec: step.duration_sec || 0,
    },
  });
}

function hasStepWalkFeature(features, step) {
  return features.some((feature) => {
    if (feature?.properties?.kind !== "walk") {
      return false;
    }
    const stationId = feature.properties.station_id;
    const nextStationId = feature.properties.next_station_id;
    return (
      (stationId === step.station_id && nextStationId === step.next_station_id) ||
      (stationId === step.next_station_id && nextStationId === step.station_id)
    );
  });
}

function stitchWalkingEndpoints(coordinates, requiredCoordinates) {
  if (!Array.isArray(requiredCoordinates) || !requiredCoordinates.length) {
    return coordinates;
  }
  const stitched = [...coordinates];
  requiredCoordinates.forEach((coordinate) => {
    if (!coordinate || pathContainsCoordinate(stitched, coordinate, 6)) {
      return;
    }
    const prependDistance = coordinateDistanceM(coordinate, stitched[0]);
    const appendDistance = coordinateDistanceM(coordinate, stitched[stitched.length - 1]);
    if (prependDistance <= appendDistance) {
      stitched.unshift(coordinate);
    } else {
      stitched.push(coordinate);
    }
  });
  return stitched;
}

function pathContainsCoordinate(coordinates, target, thresholdM) {
  return coordinates.some((coordinate) => coordinateDistanceM(coordinate, target) <= thresholdM);
}

function coordinateDistanceM(a, b) {
  if (!a || !b) {
    return Number.POSITIVE_INFINITY;
  }
  return haversineDistanceM(a[1], a[0], b[1], b[0]);
}

function haversineDistanceM(lat1, lon1, lat2, lon2) {
  const earthRadiusM = 6371000;
  const toRad = (value) => (value * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(1e-12, 1 - a)));
  return earthRadiusM * c;
}

function normalizeLineStringCoordinates(coordinates) {
  if (!Array.isArray(coordinates)) {
    return [];
  }
  return coordinates
    .map((coordinate) => {
      if (!Array.isArray(coordinate) || coordinate.length < 2) {
        return null;
      }
      const lon = Number(coordinate[0]);
      const lat = Number(coordinate[1]);
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) {
        return null;
      }
      return [lon, lat];
    })
    .filter(Boolean);
}

function pointToCoordinate(point) {
  if (!point) {
    return null;
  }
  const lon = Number(point.lon);
  const lat = Number(point.lat);
  return Number.isFinite(lon) && Number.isFinite(lat) ? [lon, lat] : null;
}

function stationCoordinate(stationId) {
  return state.stationCoordsById.get(stationId) || null;
}

function accessPointCoordinate(accessPoint) {
  if (!accessPoint) {
    return null;
  }
  const lon = Number(accessPoint.lon);
  const lat = Number(accessPoint.lat);
  return Number.isFinite(lon) && Number.isFinite(lat) ? [lon, lat] : null;
}

function updatePickedPointsSource() {
  const features = [];
  if (state.startPoint) {
    features.push({
      type: "Feature",
      geometry: {
        type: "Point",
        coordinates: [state.startPoint.lon, state.startPoint.lat],
      },
      properties: { role: "start_point", label: "Start point" },
    });
  }
  if (state.endPoint) {
    features.push({
      type: "Feature",
      geometry: {
        type: "Point",
        coordinates: [state.endPoint.lon, state.endPoint.lat],
      },
      properties: { role: "end_point", label: "End point" },
    });
  }
  setGeoJsonSourceDataIfChanged(SOURCE_IDS.pickedPoints, {
    type: "FeatureCollection",
    features,
  }, "pickedPoints");
}

function updateSelectedStationsSource() {
  const features = [];
  state.viaStationIds.forEach((stationId) => {
    features.push(buildStationFeature(stationId));
  });
  setGeoJsonSourceDataIfChanged(SOURCE_IDS.selectedStations, {
    type: "FeatureCollection",
    features: features.filter(Boolean),
  }, "selectedStations");
}

function buildStationFeature(stationId) {
  const coordinates = state.stationCoordsById.get(stationId);
  if (!coordinates) {
    return null;
  }
  const station = state.stationById.get(stationId);
  return {
    type: "Feature",
    geometry: {
      type: "Point",
      coordinates,
    },
    properties: {
      id: stationId,
      name: station?.name || stationId,
    },
  };
}

function updateRouteSource(featureCollection) {
  setGeoJsonSourceDataIfChanged(SOURCE_IDS.route, featureCollection, "route");
}

function setGeoJsonSourceDataIfChanged(sourceId, featureCollection, cacheKey) {
  const source = state.map?.getSource(sourceId);
  if (!source) {
    return;
  }
  const nextKey = JSON.stringify(featureCollection);
  if (state.sourceDataKeys[cacheKey] === nextKey) {
    return;
  }
  state.sourceDataKeys[cacheKey] = nextKey;
  source.setData(featureCollection);
}

function renderAll() {
  renderSelectionCard();
  renderSummary();
  renderSteps();
}

function renderSelectionCard() {
  const cards = [];

  if (state.startPoint) {
    cards.push(renderMetricCard("Start Point", formatLonLat(state.startPoint)));
  }
  if (state.endPoint) {
    cards.push(renderMetricCard("End Point", formatLonLat(state.endPoint)));
  }
  if (state.viaStationIds.length) {
    cards.push(
      renderMetricCard(
        "VIA Stations",
        state.viaStationIds
          .map((stationId) => state.stationById.get(stationId)?.name || stationId)
          .join(" -> "),
      ),
    );
  }
  if (state.routeResult) {
    cards.push(
      renderMetricCard(
        "Snap Start Station",
        formatStation(state.routeResult.selected_start_station?.id),
      ),
    );
    cards.push(
      renderMetricCard(
        "Snap End Station",
        formatStation(state.routeResult.selected_end_station?.id),
      ),
    );
  }

  if (!cards.length) {
    elements.selectionCard.classList.add("empty");
    elements.selectionCard.textContent = "Click anywhere on map for start/end points.";
    return;
  }
  elements.selectionCard.classList.remove("empty");
  elements.selectionCard.innerHTML = cards.join("");
}

function renderSummary() {
  if (!state.routeResult) {
    elements.summaryCard.classList.add("empty");
    elements.summaryCard.textContent = "No route yet.";
    return;
  }

  const route = state.routeResult.route || {};
  const diagnostics = state.routeResult.route_diagnostics || {};
  const lineLabels = (route.line_labels || []).join(" -> ") || "No ride";
  const isWalkFallback = state.routeResult.journey_mode === "walk_fallback";

  elements.summaryCard.classList.remove("empty");
  elements.summaryCard.innerHTML = [
    renderMetricCard("Total Journey", formatDuration(state.routeResult.total_journey_time_sec || 0)),
    renderMetricCard("Subway Time", isWalkFallback ? "No ride" : formatDuration(diagnostics.subway_time_sec || route.total_time_sec || 0)),
    renderMetricCard(
      "Walking",
      formatDuration(
        isWalkFallback
          ? state.routeResult.access_walk_time_sec || 0
          : diagnostics.walking_time_sec ||
              (state.routeResult.access_walk_time_sec || 0) + (state.routeResult.egress_walk_time_sec || 0),
      ),
    ),
    renderMetricCard("Rain Penalty", formatSignedDuration(diagnostics.rain_penalty_sec || state.routeResult.rain_penalty_sec || 0)),
    renderMetricCard("Transfers", String(diagnostics.transfer_count || route.transfer_count || 0)),
    renderMetricCard(
      "Walk Dist.",
      `${Math.round((state.routeResult.access_walk_distance_m || 0) + (state.routeResult.egress_walk_distance_m || 0))} m`,
    ),
    renderMetricCard("Line Sequence", lineLabels),
  ].join("");
}

function renderSteps() {
  if (!state.routeResult) {
    elements.stepsList.innerHTML = '<li class="empty">Pick start and end points, then calculate route.</li>';
    return;
  }

  const route = state.routeResult.route || {};
  const items = [];

  if (state.routeResult.journey_mode === "walk_fallback") {
    const modeExplanation = buildModeDecisionExplanation();
    items.push(
      renderStepCard({
        title: "Walk directly to destination",
        description: modeExplanation?.description || "No reachable subway route was found, so the app shows a walking fallback between the selected points.",
        duration: formatDuration(state.routeResult.total_journey_time_sec || 0),
        badge: formatDistance(state.routeResult.access_walk_distance_m || 0),
      }),
    );
    elements.stepsList.innerHTML = items.join("");
    return;
  }

  const startStationName = state.routeResult.selected_start_station?.name || "entry station";
  const endStationName = state.routeResult.selected_end_station?.name || "exit station";
  const startAccess = formatAccessPoint(state.routeResult.selected_start_access_point);
  const endAccess = formatAccessPoint(state.routeResult.selected_end_access_point);
  const modeExplanation = buildModeDecisionExplanation();

  if (modeExplanation) {
    items.push(renderStepCard(modeExplanation));
  }

  items.push(
    renderStepCard({
      title: `Snap start to ${startStationName}`,
      description: `Walk from the selected point${startAccess ? ` via ${startAccess}` : ""} to enter the MRT network.`,
      duration: formatDuration(state.routeResult.access_walk_time_sec || 0),
      badge: formatDistance(state.routeResult.access_walk_distance_m || 0),
    }),
  );

  summarizeRouteSteps(route).forEach((step) => {
    items.push(renderStepCard(step));
  });

  items.push(
    renderStepCard({
      title: `Exit at ${endStationName}`,
      description: `Walk from the MRT exit${endAccess ? ` via ${endAccess}` : ""} to the selected destination point.`,
      duration: formatDuration(state.routeResult.egress_walk_time_sec || 0),
      badge: formatDistance(state.routeResult.egress_walk_distance_m || 0),
    }),
  );

  const rainExplanation = buildRainExplanation();
  if (rainExplanation) {
    items.push(renderStepCard(rainExplanation));
  }

  if (Array.isArray(state.routeResult.warnings) && state.routeResult.warnings.length) {
    items.push(
      renderStepCard({
        title: "Routing note",
        description: state.routeResult.warnings.map(formatWarning).join("; "),
        duration: "",
        badge: "notice",
      }),
    );
  }

  const rejectedStationNames = rejectedAttemptNames(state.routeResult.start_station_attempts)
    .concat(rejectedAttemptNames(state.routeResult.end_station_attempts));
  if (rejectedStationNames.length) {
    items.push(
      renderStepCard({
        title: "Scenario blocked station",
        description: `${dedupe(rejectedStationNames).join(", ")} skipped because the active admin scenario closed the station.`,
        duration: "",
        badge: "scenario",
      }),
    );
  }

  elements.stepsList.innerHTML = items.join("");
}

function summarizeRouteSteps(route) {
  const summaries = [];
  const steps = route?.steps || [];

  for (let index = 0; index < steps.length; index += 1) {
    const step = steps[index];
    const from = state.stationById.get(step.station_id)?.name || step.station_id;
    const to = state.stationById.get(step.next_station_id)?.name || step.next_station_id;
    const lineName = state.lineById.get(step.line_id)?.name || step.line_id;

    if (step.kind === "ride") {
      let totalDuration = step.duration_sec;
      let finalTo = to;
      let stopCount = 1;
      let nextIndex = index + 1;

      while (nextIndex < steps.length) {
        const nextStep = steps[nextIndex];
        if (nextStep.kind !== "ride" || nextStep.line_id !== step.line_id) {
          break;
        }
        totalDuration += nextStep.duration_sec;
        stopCount += 1;
        finalTo = state.stationById.get(nextStep.next_station_id)?.name || nextStep.next_station_id;
        nextIndex += 1;
      }

      summaries.push({
        title: `Ride ${lineName}`,
        description: `Board at ${from} and stay on until ${finalTo}.`,
        duration: formatDuration(totalDuration),
        badge: stopCount === 1 ? "1 stop" : `${stopCount} stops`,
      });
      index = nextIndex - 1;
      continue;
    }

    if (step.kind === "transfer") {
      const nextRide = steps.slice(index + 1).find((candidate) => candidate.kind === "ride");
      const nextLineName = nextRide ? state.lineById.get(nextRide.line_id)?.name || nextRide.line_id : null;
      summaries.push({
        title: `Transfer at ${from}`,
        description: nextLineName
          ? `Switch from ${lineName} to ${nextLineName}.`
          : `Switch line after ${lineName}.`,
        duration: formatDuration(step.duration_sec),
        badge: "transfer",
      });
      continue;
    }

    summaries.push({
      title: `Walk link at ${from}`,
      description: `Use a walking connection from ${from} to ${to}.`,
      duration: formatDuration(step.duration_sec),
      badge: "walk",
    });
  }

  return summaries;
}

function renderStepCard(step) {
  const badgeMarkup = step.badge
    ? `<span class="step-card__badge">${escapeHtml(step.badge)}</span>`
    : "";
  const durationMarkup = step.duration
    ? `<span class="step-card__duration">${escapeHtml(step.duration)}</span>`
    : "";
  return `
    <li class="step-card">
      <div class="step-card__title-row">
        <strong>${escapeHtml(step.title)}</strong>
        ${durationMarkup}
      </div>
      <div class="step-card__meta">
        <span class="step-card__description">${escapeHtml(step.description)}</span>
        ${badgeMarkup}
      </div>
    </li>
  `;
}

function renderMetricCard(label, value) {
  return `
    <div class="summary-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

function resetAll() {
  state.startPoint = null;
  state.endPoint = null;
  state.viaStationIds = [];
  state.routeResult = null;
  updatePickedPointsSource();
  updateSelectedStationsSource();
  updateRouteSource(emptyFeatureCollection());
  renderAll();
  setPickMode("start");
  setStatus("Selection reset.");
}

function formatStation(stationId) {
  if (!stationId) {
    return "N/A";
  }
  const station = state.stationById.get(stationId);
  if (!station) {
    return stationId;
  }
  return `${station.name} (${station.id})`;
}

function formatLonLat(point) {
  return `${point.lon.toFixed(5)}, ${point.lat.toFixed(5)}`;
}

function formatDuration(totalSec) {
  const safeSec = Math.max(0, Math.round(Number(totalSec) || 0));
  const minutes = Math.floor(safeSec / 60);
  const seconds = safeSec % 60;
  return `${minutes}m ${seconds}s`;
}

function formatSignedDuration(totalSec) {
  const seconds = Math.round(Number(totalSec) || 0);
  return seconds > 0 ? `+${formatDuration(seconds)}` : "0m 0s";
}

function formatDistance(distanceM) {
  const meters = Math.max(0, Math.round(Number(distanceM) || 0));
  return meters >= 1000 ? `${(meters / 1000).toFixed(1)} km` : `${meters} m`;
}

function formatAccessPoint(accessPoint) {
  const name = accessPoint?.name;
  if (!name || name === "null") {
    return "";
  }
  return String(name);
}

function formatWarning(warning) {
  const messages = {
    ride_geometry_fallback: "Some ride geometry used station-sequence fallback",
    strategic_walk_access: "A farther station was selected because it produced a better total route",
    subway_unreachable_walking_fallback: "Subway was unreachable for this pair of points",
    walk_not_slower_than_metro: "Walking directly is no slower than the MRT route",
    metro_not_enough_time_saving: "MRT does not save enough time over direct walking",
    metro_detour_too_slow: "The MRT route is too much slower than walking directly",
  };
  return messages[warning] || String(warning).replaceAll("_", " ");
}

function buildModeDecisionExplanation() {
  if (!state.routeResult) {
    return null;
  }

  const diagnostics = state.routeResult.route_diagnostics || {};
  const mode = diagnostics.mode_decision || state.routeResult.route_selection_reason;
  const walkOnlySec = Number(diagnostics.walk_only_time_sec || state.routeResult.access_walk_time_sec || 0);
  const compareSec = Number(diagnostics.walk_compare_time_sec || 20 * 60);
  const minSavingSec = Number(diagnostics.metro_min_short_walk_saving_sec || 7 * 60);
  const metroSec = Number(state.routeResult.total_journey_time_sec || 0);

  if (state.routeResult.journey_mode === "walk_fallback") {
    if (mode === "walk_not_slower_than_metro" || mode === "metro_not_enough_time_saving") {
      return {
        title: "Route choice",
        description: `Direct walking is ${formatDuration(walkOnlySec)}. Under ${formatDuration(compareSec)}, MRT must save at least ${formatDuration(minSavingSec)} to be selected.`,
        duration: "",
        badge: "walk",
      };
    }
    if (mode === "metro_detour_too_slow") {
      return {
        title: "Route choice",
        description: `Direct walking is ${formatDuration(walkOnlySec)}, while the MRT detour takes too much extra time.`,
        duration: "",
        badge: "walk",
      };
    }
    return null;
  }

  if (mode !== "metro_selected") {
    return null;
  }

  if (walkOnlySec > compareSec) {
    return {
      title: "Route choice",
      description: `MRT is selected because direct walking is ${formatDuration(walkOnlySec)}, over the ${formatDuration(compareSec)} walking comfort threshold.`,
      duration: "",
      badge: "MRT",
    };
  }

  if (metroSec > 0 && walkOnlySec > 0 && metroSec < walkOnlySec) {
    return {
      title: "Route choice",
      description: `MRT is selected because it is faster than direct walking (${formatDuration(metroSec)} vs ${formatDuration(walkOnlySec)}).`,
      duration: "",
      badge: "MRT",
    };
  }

  return {
    title: "Route choice",
    description: "MRT is selected because it gives the best route after station access and scenario costs.",
    duration: "",
    badge: "MRT",
  };
}

function rejectedAttemptNames(attempts) {
  if (!Array.isArray(attempts)) {
    return [];
  }
  return attempts
    .filter((attempt) => attempt.status === "rejected")
    .map((attempt) => state.stationById.get(attempt.station_id)?.name || attempt.station_id);
}

function buildRainExplanation() {
  const diagnostics = state.routeResult?.route_diagnostics || {};
  const penaltySec = Number(diagnostics.rain_penalty_sec || state.routeResult?.rain_penalty_sec || 0);
  const impactedAttempts = rainImpactedAttempts(state.routeResult?.start_station_attempts)
    .concat(rainImpactedAttempts(state.routeResult?.end_station_attempts));
  const impactedNames = dedupe(impactedAttempts.map((attempt) => attempt.name));
  const severity = strongestRainSeverity(impactedAttempts);

  if (penaltySec > 0) {
    return {
      title: "Rain-aware routing",
      description: `Rain remains passable. This route adds ${formatDuration(penaltySec)} of walking cost${impactedNames.length ? ` near ${impactedNames.join(", ")}` : ""}.`,
      duration: formatSignedDuration(penaltySec),
      badge: severity ? `${severity} rain` : "soft penalty",
    };
  }

  if (impactedAttempts.length) {
    return {
      title: "Rain-aware routing",
      description: `Rain stations remain available, but this route avoids extra rain walking cost.`,
      duration: "",
      badge: "soft penalty",
    };
  }

  return null;
}

function rainImpactedAttempts(attempts) {
  if (!Array.isArray(attempts)) {
    return [];
  }
  return attempts
    .filter((attempt) => Number(attempt.rain_penalty_sec || 0) > 0)
    .map((attempt) => ({
      name: state.stationById.get(attempt.station_id)?.name || attempt.station_id,
      severity: attempt.rain_severity,
      penaltySec: Number(attempt.rain_penalty_sec || 0),
    }));
}

function strongestRainSeverity(attempts) {
  const order = { light: 1, moderate: 2, heavy: 3 };
  let strongest = null;
  for (const attempt of attempts) {
    const severity = String(attempt.severity || "").toLowerCase();
    if (!strongest || (order[severity] || 0) > (order[strongest] || 0)) {
      strongest = severity || strongest;
    }
  }
  return strongest;
}

function dedupe(items) {
  return [...new Set(items.filter(Boolean))];
}

function setStatus(message) {
  elements.statusText.textContent = message;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function roundTo6(value) {
  return Math.round(value * 1_000_000) / 1_000_000;
}

function emptyFeatureCollection() {
  return { type: "FeatureCollection", features: [] };
}

init();
