const FEED_LIMIT = 7;
const ADMIN_SCENARIO_STORAGE_KEY = 'mrt_admin_scenarios';
const LEGACY_ADMIN_SCENARIO_STORAGE_KEY = 'mrt_admin_scenarios_backup';
const DEFAULT_VIEWPORT_BOUNDS = [121.44, 24.97, 121.62, 25.13];
const MAX_FOCUS_LON_SPAN = 0.22;
const MAX_FOCUS_LAT_SPAN = 0.16;
const MIN_FOCUS_LON_SPAN = 0.12;
const MIN_FOCUS_LAT_SPAN = 0.09;
const MAP_SOURCE_IDS = {
  basemapLines: 'admin-metro-lines',
  basemapStations: 'admin-metro-stations',
  rainZones: 'admin-rain-zones',
  blockSegments: 'admin-block-segments',
  temporaryPoint: 'admin-temporary-point',
  bannedStations: 'admin-banned-stations',
};

const state = {
  network: null,
  gis: null,
  map: null,
  lineById: new Map(),
  stationById: new Map(),
  stationCoordsById: new Map(),
  mapBounds: [121.45, 24.95, 121.65, 25.15],
  mode: 'rain',
  rainSeverity: 'moderate',
  rainZones: [],
  blockSegments: [],
  temporaryPoint: null,
  bannedStationIds: new Set(),
  activityFeed: [],
};

const elements = {
  modeRain: document.getElementById('modeRain'),
  modeBlock: document.getElementById('modeBlock'),
  clearAll: document.getElementById('clearAll'),
  exportRules: document.getElementById('exportRules'),
  zoomInBtn: document.getElementById('zoomInBtn'),
  zoomOutBtn: document.getElementById('zoomOutBtn'),
  zoomResetBtn: document.getElementById('zoomResetBtn'),
  toggleControls: document.getElementById('toggleControls'),
  quickToggleControls: document.getElementById('quickToggleControls'),
  statusText: document.getElementById('statusText'),
  modeLabel: document.getElementById('modeLabel'),
  modeHint: document.getElementById('modeHint'),
  networkSourceLabel: document.getElementById('networkSourceLabel'),
  totalRuleCount: document.getElementById('totalRuleCount'),
  lineCount: document.getElementById('lineCount'),
  stationCount: document.getElementById('stationCount'),
  segmentCount: document.getElementById('segmentCount'),
  bannedCount: document.getElementById('bannedCount'),
  rainCount: document.getElementById('rainCount'),
  blockCount: document.getElementById('blockCount'),
  selectedBannedCount: document.getElementById('selectedBannedCount'),
  bannedStations: document.getElementById('bannedStations'),
  rulesSummary: document.getElementById('rulesSummary'),
  activityFeed: document.getElementById('activityFeed'),
  activeRulesList: document.getElementById('activeRulesList'),
  severityButtons: document.querySelectorAll('[data-severity]'),
  mapHelper: document.getElementById('mapHelper'),
  floatingHeader: document.querySelector('.floating-header'),
  toolsPanel: document.querySelector('.tools-panel'),
  toggleHeader: document.getElementById('toggleHeader'),
  showHeader: document.getElementById('showHeader'),
  toggleInspector: document.getElementById('toggleInspector'),
  quickToggleInspector: document.getElementById('quickToggleInspector'),
  inspectorPanel: document.getElementById('inspectorPanel'),
  logoutButton: document.getElementById('logoutButton'),
  quickLogoutButton: document.getElementById('quickLogoutButton'),
};

async function init() {
  if (sessionStorage.getItem('mrt_admin_authenticated') !== 'true') {
    window.location.replace('/login?next=/admin');
    return;
  }

  if (!window.maplibregl) {
    setStatus('MapLibre failed to load. Check CDN or network access.');
    return;
  }

  try {
    setStatus('Loading GIS network...');
    const gisResponse = await fetch('/api/gis/network');
    state.gis = await gisResponse.json();
    state.network = buildNetworkCatalog(state.gis);

    if (!gisResponse.ok) {
      throw new Error(state.gis?.detail || 'Failed to load GIS payload for admin.');
    }

    state.lineById = new Map((state.network.lines || []).map((line) => [line.id, line]));
    state.stationById = new Map((state.network.stations || []).map((station) => [station.id, station]));
    state.mapBounds = state.gis.basemap?.bounds || state.gis.bounds || state.mapBounds;

    buildStationCoordinateLookup();
    await hydrateScenarioState();
    bindEvents();
    initBannedStationSelector();
    applyHydratedSelections();
    applyModeUi();
    renderActivityFeed();
    initializeMap();
    render();

    addFeed(
      'Admin map ready',
      `Loaded ${state.network.stations.length} stations and ${state.network.lines.length} lines on GIS map.`
    );
    setStatus('Admin studio is ready. Use the GIS map to draw rain zones or block segments.');
  } catch (error) {
    console.error(error);
    setStatus(`Initialization error: ${error.message}`);
    elements.rulesSummary.textContent = JSON.stringify({ error: error.message }, null, 2);
  }
}

async function hydrateScenarioState() {
  let localPayload = null;

  try {
    const stored =
      localStorage.getItem(ADMIN_SCENARIO_STORAGE_KEY) ||
      localStorage.getItem(LEGACY_ADMIN_SCENARIO_STORAGE_KEY);
    if (stored) {
      localPayload = JSON.parse(stored);
      localStorage.setItem(ADMIN_SCENARIO_STORAGE_KEY, JSON.stringify(localPayload));
      localStorage.removeItem(LEGACY_ADMIN_SCENARIO_STORAGE_KEY);
    }
  } catch (error) {
    console.warn('Unable to load from localStorage', error);
  }

  applyScenarioPayload(localPayload || {});
}

function saveToLocalStorage(payload) {
  try {
    localStorage.setItem(ADMIN_SCENARIO_STORAGE_KEY, JSON.stringify(payload));
  } catch (error) {
    console.warn('Failed to save to localStorage', error);
  }
}

function applyScenarioPayload(payload) {
  state.mode = ['rain', 'block'].includes(payload?.ui_mode) ? payload.ui_mode : 'rain';
  state.rainZones = Array.isArray(payload?.rain_zones)
    ? payload.rain_zones
        .map((zone) => ({
          id: zone.id || `rain-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          center: {
            lon: Number(zone.center?.lon),
            lat: Number(zone.center?.lat),
          },
          radius_m: Number(zone.radius_m || 0),
          severity: normalizeRainSeverity(zone.severity),
        }))
        .filter((zone) => Number.isFinite(zone.center.lon) && Number.isFinite(zone.center.lat))
    : [];

  state.blockSegments = Array.isArray(payload?.block_segments)
    ? payload.block_segments
        .map((segment) => ({
          id: segment.id || `block-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          kind: segment.kind === 'point' ? 'point' : 'line',
          from: {
            lon: Number(segment.from?.lon),
            lat: Number(segment.from?.lat),
          },
          to: {
            lon: Number(segment.to?.lon),
            lat: Number(segment.to?.lat),
          },
        }))
        .filter(
          (segment) =>
            Number.isFinite(segment.from.lon) &&
            Number.isFinite(segment.from.lat) &&
            Number.isFinite(segment.to.lon) &&
            Number.isFinite(segment.to.lat)
        )
    : [];

  state.bannedStationIds = new Set(
    Array.isArray(payload?.banned_stations) ? payload.banned_stations.map((item) => item.id) : []
  );
}

async function saveScenarioState() {
  const payload = buildPayloadPreview();
  saveToLocalStorage(payload);
  renderMetrics();
  updateRulesSummary();
}

function buildNetworkCatalog(gisPayload) {
  const stationCatalog = Array.isArray(gisPayload?.station_catalog) ? gisPayload.station_catalog : [];
  const lineCatalog = Array.isArray(gisPayload?.line_catalog) ? gisPayload.line_catalog : [];
  const stationFeatures = Array.isArray(gisPayload?.stations?.features) ? gisPayload.stations.features : [];
  const lineFeatures = Array.isArray(gisPayload?.lines?.features) ? gisPayload.lines.features : [];

  const stations = stationCatalog.length
    ? stationCatalog.map((station) => ({
        id: station.id,
        name: station.name || station.id,
        line_ids: Array.isArray(station.line_ids) ? station.line_ids : [],
      }))
    : stationFeatures
        .map((feature) => {
          const properties = feature?.properties || {};
          if (!properties.id) {
            return null;
          }
          return {
            id: properties.id,
            name: properties.name || properties.id,
            line_ids: Array.isArray(properties.line_ids) ? properties.line_ids : [],
          };
        })
        .filter(Boolean);

  const lines = lineCatalog.length
    ? lineCatalog.map((line) => ({
        id: line.id,
        name: line.name || line.id,
        color: line.color || '#64748b',
      }))
    : [...new Map(
        lineFeatures
          .map((feature) => {
            const properties = feature?.properties || {};
            if (!properties.line_id) {
              return null;
            }
            return [
              properties.line_id,
              {
                id: properties.line_id,
                name: properties.line_name || properties.line_id,
                color: properties.line_color || '#64748b',
              },
            ];
          })
          .filter(Boolean)
      ).values()];

  const segments = lineFeatures
    .map((feature) => {
      const properties = feature?.properties || {};
      if (!properties.line_id || !properties.from_station_id || !properties.to_station_id) {
        return null;
      }
      return {
        line_id: properties.line_id,
        from_station_id: properties.from_station_id,
        to_station_id: properties.to_station_id,
      };
    })
    .filter(Boolean);

  return { stations, lines, segments };
}

function bindEvents() {
  elements.modeRain.addEventListener('click', () => setMode('rain'));
  elements.modeBlock.addEventListener('click', () => setMode('block'));
  elements.severityButtons.forEach((button) => {
    button.addEventListener('click', () => setRainSeverity(button.dataset.severity));
  });
  elements.clearAll.addEventListener('click', resetAll);
  elements.exportRules.addEventListener('click', exportRules);
  elements.zoomInBtn.addEventListener('click', () => state.map?.zoomIn());
  elements.zoomOutBtn.addEventListener('click', () => state.map?.zoomOut());
  elements.zoomResetBtn.addEventListener('click', resetMapView);
  elements.toggleControls?.addEventListener('click', toggleControlsPanel);
  elements.quickToggleControls?.addEventListener('click', toggleControlsPanel);
  elements.bannedStations.addEventListener('change', () => {
    const selected = Array.from(elements.bannedStations.selectedOptions).map((option) => option.value);
    setBannedStations(selected);
  });
  elements.activeRulesList?.addEventListener('click', handleActiveRuleAction);
  elements.toggleInspector?.addEventListener('click', () => {
    elements.inspectorPanel?.classList.toggle('collapsed');
  });
  elements.quickToggleInspector?.addEventListener('click', () => {
    elements.inspectorPanel?.classList.toggle('collapsed');
  });
  elements.toggleHeader?.addEventListener('click', () => {
    elements.floatingHeader?.classList.add('collapsed');
  });
  elements.showHeader?.addEventListener('click', () => {
    elements.floatingHeader?.classList.remove('collapsed');
  });
  const logout = () => {
    sessionStorage.removeItem('mrt_admin_authenticated');
    window.location.href = '/login';
  };
  elements.logoutButton?.addEventListener('click', logout);
  elements.quickLogoutButton?.addEventListener('click', logout);
  window.addEventListener('resize', () => state.map?.resize());
}

function toggleControlsPanel() {
  const isCollapsed = elements.toolsPanel?.classList.toggle('collapsed') || false;
  const label = isCollapsed ? 'Show controls' : 'Hide controls';
  if (elements.toggleControls) {
    elements.toggleControls.textContent = label;
  }
  if (elements.quickToggleControls) {
    elements.quickToggleControls.textContent = label;
  }
}

function initializeMap() {
  const basemapSource = buildBasemapSource();

  state.map = new maplibregl.Map({
    container: 'adminMap',
    style: {
      version: 8,
      glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
      sources: {
        basemap: basemapSource,
      },
      layers: [
        {
          id: 'admin-basemap-background',
          type: 'background',
          paint: {
            'background-color': '#f6f4ec',
          },
        },
        {
          id: 'admin-basemap-raster',
          type: 'raster',
          source: 'basemap',
        },
      ],
    },
    center: [121.54, 25.05],
    zoom: 11.2,
    attributionControl: true,
  });

  state.map.addControl(new maplibregl.NavigationControl(), 'top-right');
  state.map.on('load', handleMapLoad);
}

function buildBasemapSource() {
  const basemap = state.gis?.basemap;
  if (basemap?.enabled && basemap.tiles_url) {
    return {
      type: 'raster',
      tiles: [basemap.tiles_url],
      tileSize: Number(basemap.tile_size || 256),
      minzoom: Number(basemap.minzoom || 0),
      maxzoom: Number(basemap.maxzoom || 22),
      attribution: basemap.name || 'Local raster',
    };
  }

  return {
    type: 'raster',
    tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
    tileSize: 256,
    attribution: '(c) OpenStreetMap contributors',
  };
}

function handleMapLoad() {
  state.map.addSource(MAP_SOURCE_IDS.basemapLines, {
    type: 'geojson',
    data: state.gis.lines,
  });
  state.map.addSource(MAP_SOURCE_IDS.basemapStations, {
    type: 'geojson',
    data: state.gis.stations,
  });
  state.map.addSource(MAP_SOURCE_IDS.rainZones, {
    type: 'geojson',
    data: emptyFeatureCollection(),
  });
  state.map.addSource(MAP_SOURCE_IDS.blockSegments, {
    type: 'geojson',
    data: emptyFeatureCollection(),
  });
  state.map.addSource(MAP_SOURCE_IDS.temporaryPoint, {
    type: 'geojson',
    data: emptyFeatureCollection(),
  });
  state.map.addSource(MAP_SOURCE_IDS.bannedStations, {
    type: 'geojson',
    data: emptyFeatureCollection(),
  });

  state.map.addLayer({
    id: 'admin-lines-casing',
    type: 'line',
    source: MAP_SOURCE_IDS.basemapLines,
    layout: {
      'line-cap': 'round',
      'line-join': 'round',
    },
    paint: {
      'line-color': 'rgba(15,23,42,0.18)',
      'line-width': ['interpolate', ['linear'], ['zoom'], 9, 3.8, 13, 7.6],
      'line-opacity': 0.7,
    },
  });

  state.map.addLayer({
    id: 'admin-lines-base',
    type: 'line',
    source: MAP_SOURCE_IDS.basemapLines,
    layout: {
      'line-cap': 'round',
      'line-join': 'round',
    },
    paint: {
      'line-color': ['coalesce', ['get', 'line_color'], '#64748b'],
      'line-width': ['interpolate', ['linear'], ['zoom'], 9, 2.6, 13, 5.8],
      'line-opacity': 0.82,
    },
  });

  state.map.addLayer({
    id: 'admin-rain-fill',
    type: 'fill',
    source: MAP_SOURCE_IDS.rainZones,
    paint: {
      'fill-color': [
        'match',
        ['get', 'severity'],
        'light',
        '#38bdf8',
        'heavy',
        '#1d4ed8',
        '#2563eb',
      ],
      'fill-opacity': 0.16,
    },
  });

  state.map.addLayer({
    id: 'admin-rain-outline',
    type: 'line',
    source: MAP_SOURCE_IDS.rainZones,
    paint: {
      'line-color': [
        'match',
        ['get', 'severity'],
        'light',
        '#0284c7',
        'heavy',
        '#1e3a8a',
        '#1d4ed8',
      ],
      'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1.4, 13, 2.6],
      'line-opacity': 0.94,
    },
  });

  state.map.addLayer({
    id: 'admin-block-lines',
    type: 'line',
    source: MAP_SOURCE_IDS.blockSegments,
    filter: ['==', ['get', 'kind'], 'line'],
    layout: {
      'line-cap': 'round',
      'line-join': 'round',
    },
    paint: {
      'line-color': '#dc2626',
      'line-width': ['interpolate', ['linear'], ['zoom'], 9, 4.6, 13, 8.2],
      'line-opacity': 0.96,
    },
  });

  state.map.addLayer({
    id: 'admin-block-points',
    type: 'circle',
    source: MAP_SOURCE_IDS.blockSegments,
    filter: ['==', ['get', 'kind'], 'point'],
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 5.6, 13, 10.8],
      'circle-color': '#dc2626',
      'circle-stroke-color': '#fee2e2',
      'circle-stroke-width': 2.2,
      'circle-opacity': 0.96,
    },
  });

  state.map.addLayer({
    id: 'admin-stations-base',
    type: 'circle',
    source: MAP_SOURCE_IDS.basemapStations,
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 2.8, 13, 5.8],
      'circle-color': '#111827',
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 1.5,
      'circle-opacity': 0.86,
    },
  });

  state.map.addLayer({
    id: 'admin-banned-stations',
    type: 'circle',
    source: MAP_SOURCE_IDS.bannedStations,
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 5.2, 13, 10.6],
      'circle-color': '#b45309',
      'circle-stroke-color': '#fff7ed',
      'circle-stroke-width': 2.3,
      'circle-opacity': 0.98,
    },
  });

  state.map.addLayer({
    id: 'admin-banned-labels',
    type: 'symbol',
    source: MAP_SOURCE_IDS.bannedStations,
    minzoom: 11,
    layout: {
      'text-field': ['get', 'name'],
      'text-font': ['Noto Sans Bold'],
      'text-size': ['interpolate', ['linear'], ['zoom'], 11, 10, 14, 13],
      'text-offset': [0.85, -0.7],
      'text-anchor': 'left',
      'text-allow-overlap': true,
    },
    paint: {
      'text-color': '#7c2d12',
      'text-halo-color': '#ffffff',
      'text-halo-width': 1.7,
      'text-opacity': 0.98,
    },
  });

  state.map.addLayer({
    id: 'admin-temp-point',
    type: 'circle',
    source: MAP_SOURCE_IDS.temporaryPoint,
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 6, 13, 11],
      'circle-color': '#f59e0b',
      'circle-stroke-color': '#fef3c7',
      'circle-stroke-width': 2.4,
      'circle-opacity': 0.96,
    },
  });

  state.map.on('mouseenter', 'admin-stations-base', () => {
    state.map.getCanvas().style.cursor = 'pointer';
  });
  state.map.on('mouseleave', 'admin-stations-base', () => {
    state.map.getCanvas().style.cursor = '';
  });
  state.map.on('click', 'admin-stations-base', (event) => {
    const feature = event.features?.[0];
    if (!feature) {
      return;
    }
    const stationId = feature.properties?.id;
    const station = state.stationById.get(stationId);
    const coordinates = feature.geometry?.coordinates;
    if (!station || !Array.isArray(coordinates) || coordinates.length < 2) {
      return;
    }

    new maplibregl.Popup({ closeButton: false, closeOnClick: true, offset: 10 })
      .setLngLat([coordinates[0], coordinates[1]])
      .setHTML(
        `<div class="station-popup"><strong>${escapeHtml(station.name)}</strong><span>${escapeHtml(station.id)}</span></div>`
      )
      .addTo(state.map);
  });

  state.map.on('click', (event) => {
    handleMapClick({
      lon: roundTo6(event.lngLat.lng),
      lat: roundTo6(event.lngLat.lat),
    });
  });

  resetMapView();
  applyMapBoundsConstraint();
  updateMapSources();
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

function resetMapView() {
  if (!state.map) {
    return;
  }
  const bounds = resolveViewportBounds();
  state.map.fitBounds(
    [
      [bounds[0], bounds[1]],
      [bounds[2], bounds[3]],
    ],
    { padding: 42, duration: 400 }
  );
}

function applyMapBoundsConstraint() {
  if (!state.map) {
    return;
  }
  const bounds = resolvePanBounds();
  state.map.setMaxBounds([
    [bounds[0], bounds[1]],
    [bounds[2], bounds[3]],
  ]);
}

async function setMode(mode) {
  state.mode = ['rain', 'block'].includes(mode) ? mode : 'rain';
  state.temporaryPoint = null;
  applyModeUi();
  setStatus(getModeStatusText(state.mode));
  updateMapSources();
  updateMapHelper();
  await saveScenarioState();
}

function applyModeUi() {
  elements.modeRain.classList.toggle('active', state.mode === 'rain');
  elements.modeBlock.classList.toggle('active', state.mode === 'block');
  document.body.classList.toggle('is-block-mode', state.mode === 'block');
  elements.modeLabel.textContent = state.mode === 'rain' ? 'Rain Zone' : 'Block Segment';
  elements.modeHint.textContent = getModeHintText(state.mode);
  elements.severityButtons.forEach((button) => {
    button.classList.toggle('active', normalizeRainSeverity(button.dataset.severity) === state.rainSeverity);
  });
}

function getModeStatusText(mode) {
  if (mode === 'block') {
    return 'Block mode is active. Click one point or two points on the GIS map.';
  }
  return 'Rain mode is active. Click center first, then click again to set radius.';
}

function getModeHintText(mode) {
  if (mode === 'block') {
    return 'Create a blocked line or a blocked point';
  }
  return 'Create a soft walking penalty with 2 map clicks';
}

async function setRainSeverity(severity) {
  state.rainSeverity = normalizeRainSeverity(severity);
  applyModeUi();
  setStatus(`${formatSeverityLabel(state.rainSeverity)} rain will add a walking penalty without closing stations.`);
  await saveScenarioState();
}

async function handleMapClick(point) {
  if (state.mode === 'rain') {
    if (!state.temporaryPoint) {
      state.temporaryPoint = point;
      setStatus('Rain center selected. Click a second point to define the radius.');
      updateMapSources();
      updateMapHelper();
      return;
    }
    await addRainZone(state.temporaryPoint, point);
    return;
  }

  if (!state.temporaryPoint) {
    state.temporaryPoint = point;
    setStatus('Block start point selected. Click again to create a segment or near the same point for a blocked point.');
    updateMapSources();
    updateMapHelper();
    return;
  }

  const distanceM = haversineDistanceM(
    state.temporaryPoint.lat,
    state.temporaryPoint.lon,
    point.lat,
    point.lon
  );
  if (distanceM < 70) {
    await addBlockPoint(point);
    return;
  }

  await addBlockSegment(state.temporaryPoint, point);
}

async function addRainZone(centerPoint, edgePoint) {
  const radiusM = haversineDistanceM(centerPoint.lat, centerPoint.lon, edgePoint.lat, edgePoint.lon);
  state.rainZones.push({
    id: `rain-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    center: centerPoint,
    radius_m: Math.max(30, Math.round(radiusM)),
    severity: state.rainSeverity,
  });
  state.temporaryPoint = null;
  addFeed(
    'Rain zone added',
    `${formatSeverityLabel(state.rainSeverity)} rain at ${formatLonLat(centerPoint)} with radius ${Math.round(radiusM)} m.`
  );
  setStatus('A new rain zone was added to the GIS map.');
  render();
  await saveScenarioState();
}

async function addBlockSegment(fromPoint, toPoint) {
  state.blockSegments.push({
    id: `block-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    kind: 'line',
    from: fromPoint,
    to: toPoint,
  });
  state.temporaryPoint = null;
  addFeed(
    'Block segment added',
    `${formatLonLat(fromPoint)} -> ${formatLonLat(toPoint)}`
  );
  setStatus('A new blocked segment was added.');
  render();
  await saveScenarioState();
}

async function addBlockPoint(point) {
  state.blockSegments.push({
    id: `block-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    kind: 'point',
    from: point,
    to: point,
  });
  state.temporaryPoint = null;
  addFeed('Blocked point added', formatLonLat(point));
  setStatus('A blocked point was added.');
  render();
  await saveScenarioState();
}

async function setBannedStations(stationIds) {
  state.bannedStationIds = new Set(stationIds);
  addFeed('Banned stations updated', `${state.bannedStationIds.size} stations are blocked.`);
  render();
  await saveScenarioState();
}

async function resetAll() {
  state.rainZones = [];
  state.blockSegments = [];
  state.temporaryPoint = null;
  state.bannedStationIds.clear();
  Array.from(elements.bannedStations.options).forEach((option) => {
    option.selected = false;
  });
  addFeed('Rules cleared', 'Rain zones, blocked segments, and banned stations were reset.');
  setStatus('All admin rules were cleared.');
  render();
  await saveScenarioState();
}

function render() {
  renderMetrics();
  updateRulesSummary();
  renderActiveRules();
  updateMapSources();
  updateMapHelper();
}

function renderActiveRules() {
  if (!elements.activeRulesList) {
    return;
  }

  const items = [];

  state.rainZones.forEach((zone, index) => {
    const severity = normalizeRainSeverity(zone.severity);
    items.push(`
      <article class="active-rule">
        <div class="active-rule__body">
          <span class="active-rule__tag active-rule__tag--rain">Soft Penalty</span>
          <strong class="active-rule__title">Rain zone ${index + 1}</strong>
          <span class="active-rule__meta">${escapeHtml(formatSeverityLabel(severity))} rain - ${escapeHtml(formatLonLat(zone.center))} - radius ${Math.round(zone.radius_m)} m</span>
        </div>
        <button
          class="active-rule__remove"
          type="button"
          data-action="remove-rain"
          data-id="${zone.id}"
          aria-label="Remove rain zone ${index + 1}"
        >
          x
        </button>
      </article>
    `);
  });

  state.blockSegments.forEach((segment, index) => {
    const detail =
      segment.kind === 'point'
        ? `${formatLonLat(segment.from)}`
        : `${formatLonLat(segment.from)} -> ${formatLonLat(segment.to)}`;

    items.push(`
      <article class="active-rule">
        <div class="active-rule__body">
          <span class="active-rule__tag active-rule__tag--block">Hard Block</span>
          <strong class="active-rule__title">Blocked ${segment.kind === 'point' ? 'point' : 'segment'} ${index + 1}</strong>
          <span class="active-rule__meta">${escapeHtml(detail)}</span>
        </div>
        <button
          class="active-rule__remove"
          type="button"
          data-action="remove-block"
          data-id="${segment.id}"
          aria-label="Remove blocked segment ${index + 1}"
        >
          x
        </button>
      </article>
    `);
  });

  [...state.bannedStationIds].forEach((stationId) => {
    const station = state.stationById.get(stationId);
    items.push(`
      <article class="active-rule">
        <div class="active-rule__body">
          <span class="active-rule__tag active-rule__tag--station">Station Closed</span>
          <strong class="active-rule__title">${escapeHtml(station?.name || stationId)}</strong>
          <span class="active-rule__meta">${escapeHtml(stationId)}</span>
        </div>
        <button
          class="active-rule__remove"
          type="button"
          data-action="remove-station"
          data-station-id="${escapeHtml(stationId)}"
          aria-label="Remove banned station ${escapeHtml(stationId)}"
        >
          x
        </button>
      </article>
    `);
  });

  if (!items.length) {
    elements.activeRulesList.innerHTML = `
      <article class="active-rule active-rule--empty">
        <strong>No active rules</strong>
        <span>Add a rain zone, block, or banned station to see it here.</span>
      </article>
    `;
    return;
  }

  elements.activeRulesList.innerHTML = items.join('');
}

async function handleActiveRuleAction(event) {
  const button = event.target.closest('[data-action]');
  if (!button) {
    return;
  }

  const action = button.dataset.action;
  if (action === 'remove-rain') {
    const id = button.dataset.id;
    if (id) {
      state.rainZones = state.rainZones.filter((z) => z.id !== id);
      addFeed('Rain zone removed', `Removed rain zone.`);
      render();
      await saveScenarioState();
    }
    return;
  }

  if (action === 'remove-block') {
    const id = button.dataset.id;
    if (id) {
      state.blockSegments = state.blockSegments.filter((s) => s.id !== id);
      addFeed('Blocked segment removed', `Removed blocked segment.`);
      render();
      await saveScenarioState();
    }
    return;
  }

  if (action === 'remove-station') {
    const stationId = button.dataset.stationId;
    if (!stationId) {
      return;
    }
    state.bannedStationIds.delete(stationId);
    applyHydratedSelections();
    addFeed('Banned station removed', `Removed ${stationId} from banned stations.`);
    render();
    await saveScenarioState();
  }
}

function updateMapSources() {
  updateGeoJsonSource(MAP_SOURCE_IDS.rainZones, buildRainZoneFeatureCollection());
  updateGeoJsonSource(MAP_SOURCE_IDS.blockSegments, buildBlockFeatureCollection());
  updateGeoJsonSource(MAP_SOURCE_IDS.temporaryPoint, buildTemporaryPointFeatureCollection());
  updateGeoJsonSource(MAP_SOURCE_IDS.bannedStations, buildBannedStationFeatureCollection());
}

function updateGeoJsonSource(sourceId, data) {
  const source = state.map?.getSource(sourceId);
  if (!source) {
    return;
  }
  source.setData(data);
}

function buildRainZoneFeatureCollection() {
  return {
    type: 'FeatureCollection',
    features: state.rainZones.map((zone, index) => {
      const polygonCoordinates = buildCirclePolygon(zone.center.lon, zone.center.lat, zone.radius_m, 48);
      return {
        type: 'Feature',
        geometry: {
          type: 'Polygon',
          coordinates: [polygonCoordinates],
        },
        properties: {
          id: `rain-${index + 1}`,
          radius_m: zone.radius_m,
          severity: normalizeRainSeverity(zone.severity),
        },
      };
    }),
  };
}

function buildBlockFeatureCollection() {
  const features = [];
  state.blockSegments.forEach((segment, index) => {
    if (segment.kind === 'point') {
      features.push({
        type: 'Feature',
        geometry: {
          type: 'Point',
          coordinates: [segment.from.lon, segment.from.lat],
        },
        properties: {
          id: segment.id,
          kind: 'point',
        },
      });
      return;
    }

    features.push({
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [
          [segment.from.lon, segment.from.lat],
          [segment.to.lon, segment.to.lat],
        ],
      },
      properties: {
        id: segment.id,
        kind: 'line',
      },
    });
  });
  return {
    type: 'FeatureCollection',
    features,
  };
}

function buildTemporaryPointFeatureCollection() {
  if (!state.temporaryPoint) {
    return emptyFeatureCollection();
  }
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: {
          type: 'Point',
          coordinates: [state.temporaryPoint.lon, state.temporaryPoint.lat],
        },
        properties: {
          role: 'temporary',
        },
      },
    ],
  };
}

function buildBannedStationFeatureCollection() {
  const features = [...state.bannedStationIds]
    .map((stationId) => {
      const coordinates = state.stationCoordsById.get(stationId);
      const station = state.stationById.get(stationId);
      if (!coordinates || !station) {
        return null;
      }
      return {
        type: 'Feature',
        geometry: {
          type: 'Point',
          coordinates,
        },
        properties: {
          id: station.id,
          name: station.name,
        },
      };
    })
    .filter(Boolean);

  return {
    type: 'FeatureCollection',
    features,
  };
}

function renderMetrics() {
  const totalRules = state.rainZones.length + state.blockSegments.length + state.bannedStationIds.size;
  if (elements.totalRuleCount) elements.totalRuleCount.textContent = String(totalRules);
  if (elements.rainCount) elements.rainCount.textContent = String(state.rainZones.length);
  if (elements.blockCount) elements.blockCount.textContent = String(state.blockSegments.length);
  if (elements.selectedBannedCount) elements.selectedBannedCount.textContent = String(state.bannedStationIds.size);
  if (elements.bannedCount) elements.bannedCount.textContent = String(state.bannedStationIds.size);
  if (elements.lineCount) elements.lineCount.textContent = String(state.network?.lines?.length || 0);
  if (elements.stationCount) elements.stationCount.textContent = String(state.network?.stations?.length || 0);
  if (elements.segmentCount) elements.segmentCount.textContent = String(state.network?.segments?.length || 0);

  const sourceLabel = state.gis?.source?.startsWith('qgis_geojson')
    ? 'QGIS GeoJSON'
    : 'Fallback projection';
  const basemapLabel = state.gis?.basemap?.enabled ? 'Local raster' : 'OSM raster';
  if (elements.networkSourceLabel) elements.networkSourceLabel.textContent = `${sourceLabel} + ${basemapLabel}`;
}

function updateMapHelper() {
  if (!elements.mapHelper) {
    return;
  }
  if (!state.temporaryPoint) {
    elements.mapHelper.textContent =
      state.mode === 'rain'
        ? 'Rain mode: click center, then click again to define radius.'
        : 'Block mode: click one point for a blocked point, or click two points for a blocked segment.';
    return;
  }

  elements.mapHelper.textContent =
    state.mode === 'rain'
      ? `Rain center locked at ${formatLonLat(state.temporaryPoint)}. Click again to finish the zone.`
      : `Block start locked at ${formatLonLat(state.temporaryPoint)}. Click again to finish or near the same point to drop a blocked point.`;
}

function buildPayloadPreview() {
  return {
    source: state.gis?.source || null,
    generated_at: new Date().toISOString(),
    ui_mode: state.mode,
    map_bounds: {
      min_lon: roundTo6(state.mapBounds[0]),
      min_lat: roundTo6(state.mapBounds[1]),
      max_lon: roundTo6(state.mapBounds[2]),
      max_lat: roundTo6(state.mapBounds[3]),
    },
    rain_zones: state.rainZones.map((zone, index) => ({
      id: zone.id,
      center: {
        lon: roundTo6(zone.center.lon),
        lat: roundTo6(zone.center.lat),
        normalized: toNormalized(zone.center),
      },
      radius_m: zone.radius_m,
      severity: normalizeRainSeverity(zone.severity),
    })),
    block_segments: state.blockSegments.map((segment, index) => ({
      id: segment.id,
      kind: segment.kind,
      from: {
        lon: roundTo6(segment.from.lon),
        lat: roundTo6(segment.from.lat),
        normalized: toNormalized(segment.from),
      },
      to: {
        lon: roundTo6(segment.to.lon),
        lat: roundTo6(segment.to.lat),
        normalized: toNormalized(segment.to),
      },
    })),
    banned_stations: [...state.bannedStationIds].map((stationId) => {
      const station = state.stationById.get(stationId);
      const coordinates = state.stationCoordsById.get(stationId);
      return {
        id: stationId,
        name: station?.name || stationId,
        lon: coordinates ? roundTo6(coordinates[0]) : null,
        lat: coordinates ? roundTo6(coordinates[1]) : null,
      };
    }),
  };
}

function updateRulesSummary() {
  const payload = buildPayloadPreview();
  elements.rulesSummary.textContent = JSON.stringify(payload, null, 2);
}

function renderActivityFeed() {
  if (!state.activityFeed.length) {
    elements.activityFeed.innerHTML = `
      <article class="feed-item empty-feed">
        <strong>No actions yet</strong>
        <span>Add the first rule to start the session log.</span>
      </article>
    `;
    return;
  }

  elements.activityFeed.innerHTML = state.activityFeed
    .map(
      (item) => `
        <article class="feed-item">
          <strong>${escapeHtml(item.title)} - ${escapeHtml(item.createdAt)}</strong>
          <span>${escapeHtml(item.detail)}</span>
        </article>
      `
    )
    .join('');
}

function addFeed(title, detail) {
  state.activityFeed.unshift({
    title,
    detail,
    createdAt: new Date().toLocaleTimeString('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
    }),
  });
  state.activityFeed = state.activityFeed.slice(0, FEED_LIMIT);
  renderActivityFeed();
}

function initBannedStationSelector() {
  elements.bannedStations.innerHTML = '';
  for (const station of state.network.stations) {
    const option = document.createElement('option');
    option.value = station.id;
    option.textContent = `${station.name} (${station.id})`;
    elements.bannedStations.appendChild(option);
  }
}

function applyHydratedSelections() {
  for (const option of elements.bannedStations.options) {
    option.selected = state.bannedStationIds.has(option.value);
  }
}

async function exportRules() {
  const payload = JSON.stringify(buildPayloadPreview(), null, 2);
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(payload);
      addFeed('JSON exported', 'Rule payload copied to clipboard.');
      setStatus('Rule payload copied to clipboard.');
      return;
    }
  } catch (error) {
    console.warn('Clipboard export failed', error);
  }

  addFeed('JSON export fallback', 'Clipboard is unavailable. Use the preview panel instead.');
  setStatus('Clipboard is unavailable. Use the payload preview panel.');
}

function toNormalized(point) {
  const [minLon, minLat, maxLon, maxLat] = state.mapBounds;
  const lonSpan = maxLon - minLon || 1;
  const latSpan = maxLat - minLat || 1;
  return {
    x: roundTo6((point.lon - minLon) / lonSpan),
    y: roundTo6((maxLat - point.lat) / latSpan),
  };
}

function buildCirclePolygon(centerLon, centerLat, radiusM, steps) {
  const coordinates = [];
  const latRadius = radiusM / 111320;
  const lonRadius = radiusM / (111320 * Math.cos((centerLat * Math.PI) / 180) || 1);

  for (let step = 0; step <= steps; step += 1) {
    const theta = (step / steps) * Math.PI * 2;
    coordinates.push([
      roundTo6(centerLon + lonRadius * Math.cos(theta)),
      roundTo6(centerLat + latRadius * Math.sin(theta)),
    ]);
  }

  return coordinates;
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

function formatLonLat(point) {
  return `${point.lon.toFixed(5)}, ${point.lat.toFixed(5)}`;
}

function normalizeRainSeverity(value) {
  const severity = String(value || 'moderate').toLowerCase();
  return ['light', 'moderate', 'heavy'].includes(severity) ? severity : 'moderate';
}

function formatSeverityLabel(value) {
  const labels = {
    light: 'Light',
    moderate: 'Moderate',
    heavy: 'Heavy',
  };
  return labels[normalizeRainSeverity(value)];
}

function setStatus(text) {
  elements.statusText.textContent = text;
}

function roundTo6(value) {
  return Math.round(value * 1000000) / 1000000;
}

function emptyFeatureCollection() {
  return { type: 'FeatureCollection', features: [] };
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

init();
