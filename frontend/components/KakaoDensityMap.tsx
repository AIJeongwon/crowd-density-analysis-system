'use client';

import { Minus, Plus } from 'lucide-react';
import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { getCongestionPresentation } from '@/lib/congestion';
import {
  DEFAULT_MAP_CENTER,
  KAKAO_MAP_APP_KEY,
} from '@/lib/config';
import { loadKakaoMapsSdk } from '@/lib/loadKakaoMaps';
import type { LocationStatus } from '@/types/api';

interface KakaoDensityMapProps {
  locations: LocationStatus[];
  selectedId: string | null;
  onSelect: (locationId: string) => void;
}

interface OverlayRecord {
  overlay: KakaoCustomOverlay;
  element: HTMLButtonElement;
  handleClick: () => void;
}

type MapMode = 'loading' | 'ready' | 'fallback' | 'error';

const hasCoordinates = (
  location: LocationStatus,
): location is LocationStatus & {
  latitude: number;
  longitude: number;
} =>
  typeof location.latitude === 'number' &&
  typeof location.longitude === 'number';

const markerLabel = (location: LocationStatus) =>
  location.display_name || location.location_id;

const markerCount = (location: LocationStatus) =>
  location.people_count === null ? '–' : String(location.people_count);

const FallbackMap = ({
  locations,
  selectedId,
  onSelect,
}: KakaoDensityMapProps) => {
  const positioned = useMemo(() => {
    const candidates = locations.filter(hasCoordinates);
    if (!candidates.length) return [];

    const latitudes = candidates.map((location) => location.latitude);
    const longitudes = candidates.map((location) => location.longitude);
    const minLatitude = Math.min(...latitudes);
    const maxLatitude = Math.max(...latitudes);
    const minLongitude = Math.min(...longitudes);
    const maxLongitude = Math.max(...longitudes);
    const latitudeSpan = maxLatitude - minLatitude || 0.01;
    const longitudeSpan = maxLongitude - minLongitude || 0.01;

    return candidates.map((location) => ({
      location,
      left:
        22 +
        ((location.longitude - minLongitude) / longitudeSpan) * 56,
      top:
        24 +
        ((maxLatitude - location.latitude) / latitudeSpan) * 48,
    }));
  }, [locations]);

  return (
    <div className="fallback-map" aria-hidden="true">
      <div className="fallback-blocks" />
      <span className="fallback-road road-a" />
      <span className="fallback-road road-b" />
      <span className="fallback-road road-c" />
      <span className="fallback-river" />
      {positioned.map(({ location, left, top }) => {
        const presentation = getCongestionPresentation(
          location.congestion_level,
        );
        return (
          <button
            className={
              'map-marker ' +
              presentation.className +
              (selectedId === location.location_id ? ' selected' : '')
            }
            key={location.location_id}
            onClick={() => onSelect(location.location_id)}
            style={{ left: left + '%', top: top + '%' }}
            type="button"
            tabIndex={-1}
          >
            <span className="map-marker-pulse" />
            <span className="map-marker-count">{markerCount(location)}</span>
            <span className="map-marker-label">{markerLabel(location)}</span>
          </button>
        );
      })}
    </div>
  );
};

export default function KakaoDensityMap({
  locations,
  selectedId,
  onSelect,
}: KakaoDensityMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<KakaoMap | null>(null);
  const mapsRef = useRef<KakaoMapsNamespace | null>(null);
  const overlaysRef = useRef<Map<string, OverlayRecord>>(new Map());
  const onSelectRef = useRef(onSelect);
  const [mode, setMode] = useState<MapMode>(
    KAKAO_MAP_APP_KEY ? 'loading' : 'fallback',
  );

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    if (!KAKAO_MAP_APP_KEY || !containerRef.current) return;
    let disposed = false;
    const overlays = overlaysRef.current;

    loadKakaoMapsSdk(KAKAO_MAP_APP_KEY)
      .then((maps) => {
        if (disposed || !containerRef.current) return;
        mapsRef.current = maps;
        const center = new maps.LatLng(
          DEFAULT_MAP_CENTER.latitude,
          DEFAULT_MAP_CENTER.longitude,
        );
        mapRef.current = new maps.Map(containerRef.current, {
          center,
          level: 4,
        });
        setMode('ready');
      })
      .catch(() => {
        if (!disposed) setMode('error');
      });

    return () => {
      disposed = true;
      overlays.forEach(({ overlay, element, handleClick }) => {
        element.removeEventListener('click', handleClick);
        overlay.setMap(null);
      });
      overlays.clear();
      mapRef.current = null;
      mapsRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const maps = mapsRef.current;
    if (mode !== 'ready' || !map || !maps) return;

    const activeIds = new Set<string>();
    locations.filter(hasCoordinates).forEach((location) => {
      activeIds.add(location.location_id);
      const presentation = getCongestionPresentation(
        location.congestion_level,
      );
      const position = new maps.LatLng(
        location.latitude,
        location.longitude,
      );
      let record = overlaysRef.current.get(location.location_id);

      if (!record) {
        const element = document.createElement('button');
        element.type = 'button';
        element.innerHTML =
          '<span class="map-marker-pulse"></span>' +
          '<span class="map-marker-count"></span>' +
          '<span class="map-marker-label"></span>';
        const handleClick = () =>
          onSelectRef.current(location.location_id);
        element.addEventListener('click', handleClick);
        const overlay = new maps.CustomOverlay({
          content: element,
          map,
          position,
          yAnchor: 0.5,
          zIndex: 4,
        });
        record = { overlay, element, handleClick };
        overlaysRef.current.set(location.location_id, record);
      }

      record.overlay.setPosition(position);
      record.element.className =
        'map-marker ' +
        presentation.className +
        (selectedId === location.location_id ? ' selected' : '');
      record.element.setAttribute(
        'aria-label',
        markerLabel(location) +
          ', ' +
          presentation.label +
          ', ' +
          markerCount(location) +
          '명',
      );
      const count = record.element.querySelector('.map-marker-count');
      const label = record.element.querySelector('.map-marker-label');
      if (count) count.textContent = markerCount(location);
      if (label) label.textContent = markerLabel(location);
    });

    overlaysRef.current.forEach((record, locationId) => {
      if (activeIds.has(locationId)) return;
      record.element.removeEventListener('click', record.handleClick);
      record.overlay.setMap(null);
      overlaysRef.current.delete(locationId);
    });
  }, [locations, mode, selectedId]);

  useEffect(() => {
    const map = mapRef.current;
    const maps = mapsRef.current;
    const selected = locations.find(
      (location) =>
        location.location_id === selectedId && hasCoordinates(location),
    );
    if (mode === 'ready' && map && maps && selected) {
      map.panTo(new maps.LatLng(selected.latitude!, selected.longitude!));
    }
  }, [locations, mode, selectedId]);

  useEffect(() => {
    const container = containerRef.current;
    const map = mapRef.current;
    if (mode !== 'ready' || !container || !map) return;
    const observer = new ResizeObserver(() => map.relayout());
    observer.observe(container);
    return () => observer.disconnect();
  }, [mode]);

  const changeZoom = (difference: number) => {
    const map = mapRef.current;
    if (!map) return;
    map.setLevel(Math.max(1, map.getLevel() + difference), {
      animate: true,
    });
  };

  return (
    <section
      className="map-surface"
      aria-label="실시간 인구 밀집도 지도"
    >
      <div
        className={'kakao-map' + (mode === 'ready' ? ' ready' : '')}
        ref={containerRef}
      />
      {mode !== 'ready' && (
        <FallbackMap
          locations={locations}
          selectedId={selectedId}
          onSelect={onSelect}
        />
      )}

      <div className="map-tools" aria-label="지도 확대 및 축소">
        <button
          aria-label="지도 확대"
          disabled={mode !== 'ready'}
          onClick={() => changeZoom(-1)}
          type="button"
        >
          <Plus size={18} strokeWidth={2.2} />
        </button>
        <button
          aria-label="지도 축소"
          disabled={mode !== 'ready'}
          onClick={() => changeZoom(1)}
          type="button"
        >
          <Minus size={18} strokeWidth={2.2} />
        </button>
      </div>

      <p className="map-source">
        {mode === 'ready'
          ? 'Kakao Map'
          : mode === 'loading'
            ? '지도를 불러오는 중'
            : mode === 'error'
              ? '지도 로딩 실패 · 간이 배경'
              : '카카오맵 키 연결 전 · 간이 배경'}
      </p>
    </section>
  );
}
