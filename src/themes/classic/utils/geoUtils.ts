import * as mapboxPolyline from '@mapbox/polyline';
import gcoord from 'gcoord';
import { WebMercatorViewport } from '@math.gl/web-mercator';
import type { FeatureCollection, LineString, Feature } from 'geojson';
import type { GeoJsonProperties } from 'geojson';
import type { RPGeometry } from '../static/run_countries';
import worldGeoJsonUrl from '../static/world.zh.json?url';
import { getMapThemeFromCurrentTheme } from '../hooks/useTheme';
import {
  CYCLING_COLOR,
  getMapTileVendorStyles,
  getRuntimeRunColor,
  HIKING_COLOR,
  INDOOR_COLOR,
  MAIN_COLOR,
  MAP_TILE_STYLE_DARK,
  MAP_TILE_STYLES,
  NEED_FIX_MAP,
  RUN_TRAIL_COLOR,
  SWIMMING_COLOR,
  WALKING_COLOR,
} from './const';
import type { Activity } from './utils';
import { locationForRun } from './utils';

export type Coordinate = [number, number];

export interface IViewState {
  longitude?: number;
  latitude?: number;
  zoom?: number;
}

export const pathForRun = (run: Activity): Coordinate[] => {
  try {
    if (!run.summary_polyline) {
      return [];
    }
    const c = mapboxPolyline.decode(run.summary_polyline);
    // reverse lat long for mapbox
    c.forEach((arr) => {
      [arr[0], arr[1]] = !NEED_FIX_MAP
        ? [arr[1], arr[0]]
        : gcoord.transform([arr[1], arr[0]], gcoord.GCJ02, gcoord.WGS84);
    });
    // try to use location city coordinate instead, if runpath is incomplete
    if (c.length === 2 && String(c[0]) === String(c[1])) {
      const { coordinate } = locationForRun(run);
      if (coordinate?.[0] && coordinate?.[1]) {
        return [coordinate, coordinate];
      }
    }
    return c;
  } catch (_err) {
    return [];
  }
};

const colorForRun = (run: Activity): string => {
  const dynamicRunColor = getRuntimeRunColor();

  switch (run.type) {
    case 'Run': {
      if (run.subtype === 'indoor' || run.subtype === 'treadmill') {
        return INDOOR_COLOR;
      }
      if (run.subtype === 'trail') {
        return RUN_TRAIL_COLOR;
      } else if (run.subtype === 'generic') {
        return dynamicRunColor;
      }
      return dynamicRunColor;
    }
    case 'cycling':
    case 'Ride':
      return CYCLING_COLOR;
    case 'hiking':
    case 'Hike':
      return HIKING_COLOR;
    case 'walking':
    case 'Walk':
      return WALKING_COLOR;
    case 'swimming':
    case 'Swim':
      return SWIMMING_COLOR;
    default:
      return MAIN_COLOR;
  }
};

export const geoJsonForRuns = (
  runs: Activity[]
): FeatureCollection<LineString> => ({
  type: 'FeatureCollection',
  features: runs.map((run) => {
    const points = pathForRun(run);
    const color = colorForRun(run);
    return {
      type: 'Feature',
      properties: {
        color: color,
        indoor: run.subtype === 'indoor' || run.subtype === 'treadmill',
      },
      geometry: {
        type: 'LineString',
        coordinates: points,
      },
    };
  }),
});

let worldGeoJsonPromise: Promise<FeatureCollection<RPGeometry>> | undefined;

const loadWorldGeoJson = () => {
  worldGeoJsonPromise ??= fetch(worldGeoJsonUrl).then((response) => {
    if (!response.ok) {
      throw new Error(`Failed to load world GeoJSON: ${response.status}`);
    }
    return response.json() as Promise<FeatureCollection<RPGeometry>>;
  });
  return worldGeoJsonPromise;
};

export const geoJsonForMap = async (): Promise<
  FeatureCollection<RPGeometry>
> => {
  const [{ chinaGeojson }, worldGeoJson] = await Promise.all([
    import('../static/run_countries'),
    loadWorldGeoJson(),
  ]);

  return {
    type: 'FeatureCollection',
    features: [...worldGeoJson.features, ...chinaGeojson.features] as Feature<
      RPGeometry,
      GeoJsonProperties
    >[],
  };
};

export const getBoundsForGeoData = (
  geoData: FeatureCollection<LineString>
): IViewState => {
  let minLongitude = Infinity;
  let minLatitude = Infinity;
  let maxLongitude = -Infinity;
  let maxLatitude = -Infinity;

  // Include every recorded route so an older GPS fix cannot hide other cities.
  for (const feature of geoData.features) {
    for (const [longitude, latitude] of feature.geometry.coordinates) {
      if (
        !Number.isFinite(longitude) ||
        !Number.isFinite(latitude) ||
        Math.abs(longitude) > 180 ||
        Math.abs(latitude) > 90
      ) {
        continue;
      }
      minLongitude = Math.min(minLongitude, longitude);
      minLatitude = Math.min(minLatitude, latitude);
      maxLongitude = Math.max(maxLongitude, longitude);
      maxLatitude = Math.max(maxLatitude, latitude);
    }
  }

  if (!Number.isFinite(minLongitude)) {
    return { longitude: 20, latitude: 20, zoom: 3 };
  }
  if (minLongitude === maxLongitude && minLatitude === maxLatitude) {
    return { longitude: minLongitude, latitude: minLatitude, zoom: 9 };
  }
  const cornersLongLat: [Coordinate, Coordinate] = [
    [minLongitude, minLatitude],
    [maxLongitude, maxLatitude],
  ];
  const viewState = new WebMercatorViewport({
    width: 800,
    height: 600,
  }).fitBounds(cornersLongLat, { padding: 200 });
  const { longitude, latitude, zoom } = viewState;
  return { longitude, latitude, zoom };
};

export const getMapStyle = (
  vendor: string,
  styleName: string,
  token: string
) => {
  const style = getMapTileVendorStyles(vendor)?.[styleName];
  if (!style) {
    return MAP_TILE_STYLES.default;
  }
  if (vendor === 'maptiler' || vendor === 'stadiamaps') {
    return style + token;
  }
  return style;
};

export const isTouchDevice = () => {
  if (typeof window === 'undefined') return false;
  return (
    'ontouchstart' in window ||
    navigator.maxTouchPoints > 0 ||
    window.innerWidth <= 768
  );
};

export const getMapTheme = (): string => {
  if (typeof window === 'undefined') return MAP_TILE_STYLE_DARK;

  const dataTheme = document.documentElement.getAttribute('data-theme') as
    'light' | 'dark' | null;
  const savedTheme = localStorage.getItem('theme') as 'light' | 'dark' | null;

  if (dataTheme) {
    return getMapThemeFromCurrentTheme(dataTheme);
  }
  if (savedTheme) {
    return getMapThemeFromCurrentTheme(savedTheme);
  }
  return getMapThemeFromCurrentTheme('dark');
};
