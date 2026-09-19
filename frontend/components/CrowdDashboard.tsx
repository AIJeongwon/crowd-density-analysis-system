'use client';

import {
  Activity,
  ChevronDown,
  ChevronUp,
  Clock3,
  Gauge,
  LocateFixed,
  MapPin,
  RefreshCw,
  Users,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import KakaoDensityMap from '@/components/KakaoDensityMap';
import { useLocationStatuses } from '@/hooks/useLocationStatuses';
import { getCongestionPresentation } from '@/lib/congestion';
import { REFRESH_INTERVAL_MS } from '@/lib/config';
import type { LocationStatus } from '@/types/api';

const formatTime = (value: string | null | undefined) => {
  if (!value) return '수집 대기';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '수집 대기';
  return new Intl.DateTimeFormat('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
};

const LocationRow = ({
  location,
  selected,
  onSelect,
}: {
  location: LocationStatus;
  selected: boolean;
  onSelect: () => void;
}) => {
  const presentation = getCongestionPresentation(
    location.congestion_level,
  );
  const name = location.display_name || location.location_id;

  return (
    <button
      aria-pressed={selected}
      className={
        'location-row ' +
        presentation.className +
        (selected ? ' selected' : '')
      }
      onClick={onSelect}
      type="button"
    >
      <span className="location-status-dot" />
      <span className="location-copy">
        <strong>{name}</strong>
        <small>
          {location.status === 'OK'
            ? '최근 수신 ' + formatTime(location.received_at)
            : '새 데이터 수집 대기'}
        </small>
      </span>
      <span className="location-value">
        <strong>
          {location.people_count === null ? '–' : location.people_count}
          <small>명</small>
        </strong>
        <em>{presentation.label}</em>
      </span>
    </button>
  );
};

export default function CrowdDashboard() {
  const { snapshot, phase, message, isRefreshing, refresh } =
    useLocationStatuses();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const locations = useMemo(
    () => snapshot?.locations || [],
    [snapshot],
  );
  const effectiveSelectedId = locations.some(
    (location) => location.location_id === selectedId,
  )
    ? selectedId
    : locations[0]?.location_id || null;

  const selected = locations.find(
    (location) => location.location_id === effectiveSelectedId,
  );

  const summary = useMemo(
    () =>
      locations.reduce(
        (total, location) => ({
          people:
            total.people +
            (location.status === 'OK' ? location.people_count || 0 : 0),
          active: total.active + (location.status === 'OK' ? 1 : 0),
        }),
        { people: 0, active: 0 },
      ),
    [locations],
  );

  const handleSelect = (locationId: string) => {
    setSelectedId(locationId);
    setMobileOpen(true);
  };

  const isConnectionError = phase === 'stale' || phase === 'error';
  const isWaiting = phase === 'loading' || phase === 'waiting';
  const phaseLabel =
    isConnectionError
      ? 'ERROR'
      : phase === 'live'
        ? 'LIVE'
        : phase === 'demo'
          ? 'DEMO'
          : '대기';
  const phaseDescription = isConnectionError
    ? '자동 재시도 중'
    : phase === 'loading'
      ? '연결 확인 중'
      : phase === 'waiting'
        ? '센서 데이터 대기'
        : phase === 'demo'
          ? '예시 데이터'
          : Math.round(REFRESH_INTERVAL_MS / 1000) + '초 자동 갱신';

  return (
    <main className="dashboard-shell">
      <KakaoDensityMap
        locations={locations}
        onSelect={handleSelect}
        selectedId={effectiveSelectedId}
      />

      <header className="map-header">
        <a className="brand" href="#" aria-label="Crowd Map 홈">
          <span className="brand-mark">
            <Activity size={21} strokeWidth={2.5} />
          </span>
          <span>
            <strong>CROWD MAP</strong>
            <small>실시간 도시 밀집도</small>
          </span>
        </a>

        <div
          aria-live="polite"
          className={'connection-pill phase-' + phase}
          role="status"
        >
          {isConnectionError ? (
            <WifiOff size={14} />
          ) : isWaiting ? (
            <Clock3 size={14} />
          ) : (
            <Wifi size={14} />
          )}
          <strong>{phaseLabel}</strong>
          <span>{phaseDescription}</span>
        </div>
      </header>

      <aside
        className={'dashboard-panel' + (mobileOpen ? ' mobile-open' : '')}
        id="location-panel"
      >
        <button
          aria-controls="panel-content"
          aria-expanded={mobileOpen}
          className="sheet-toggle"
          onClick={() => setMobileOpen((open) => !open)}
          type="button"
        >
          <span className="sheet-handle" />
          <span>
            <small>현재 관측 현황</small>
            <strong>
              {summary.people}명 · {summary.active}개 지점
            </strong>
          </span>
          {mobileOpen ? <ChevronDown size={21} /> : <ChevronUp size={21} />}
        </button>

        <div className="panel-scroll" id="panel-content">
          <section className="panel-intro">
            <p className="eyebrow">CROWD · NOW</p>
            <h1>
              지금, 어디가
              <br />
              붐비고 있나요?
            </h1>
            <p>
              현장 센서가 감지한 최신 인구와 혼잡도를
              <br />
              지도에서 한눈에 확인하세요.
            </p>
          </section>

          {message && (
            <div className={'data-notice phase-' + phase}>
              {phase === 'demo' ? (
                <LocateFixed size={15} />
              ) : (
                <WifiOff size={15} />
              )}
              <span>{message}</span>
            </div>
          )}

          <section className="summary-card" aria-label="전체 관측 요약">
            <div>
              <span>
                <Users size={14} />
                관측 인원
              </span>
              <strong>
                {summary.people}
                <small>명</small>
              </strong>
            </div>
            <div>
              <span>
                <MapPin size={14} />
                정상 지점
              </span>
              <strong>
                {summary.active}
                <small>/ {locations.length}곳</small>
              </strong>
            </div>
          </section>

          {selected && (
            <section className="selected-metrics" aria-label="선택 지점 상세">
              <div>
                <Gauge size={16} />
                <span>
                  점유 혼잡도
                  <strong>
                    {selected.congestion_score === null
                      ? '–'
                      : selected.congestion_score + '%'}
                  </strong>
                </span>
              </div>
              <div>
                <Activity size={16} />
                <span>
                  감지 신뢰도
                  <strong>{Math.round(selected.confidence * 100)}%</strong>
                </span>
              </div>
              <div>
                <Clock3 size={16} />
                <span>
                  마지막 수신
                  <strong>{formatTime(selected.received_at)}</strong>
                </span>
              </div>
            </section>
          )}

          <section className="locations-section">
            <div className="section-heading">
              <div>
                <span>관측 지점</span>
                <small>최근 {snapshot?.window_seconds || 30}초 기준</small>
              </div>
              <button
                aria-label="지금 새로고침"
                className="refresh-button"
                disabled={isRefreshing}
                onClick={refresh}
                type="button"
              >
                <RefreshCw
                  className={isRefreshing ? 'spinning' : ''}
                  size={16}
                />
              </button>
            </div>

            <div className="location-list">
              {locations.map((location) => (
                <LocationRow
                  key={location.location_id}
                  location={location}
                  onSelect={() => handleSelect(location.location_id)}
                  selected={effectiveSelectedId === location.location_id}
                />
              ))}
              {!locations.length && (
                <div className="empty-locations">
                  <MapPin size={20} />
                  <strong>표시할 관측 지점이 없습니다.</strong>
                  <span>백엔드 위치 설정을 확인해 주세요.</span>
                </div>
              )}
            </div>
          </section>

          <footer className="panel-footer">
            <span className="legend-item low"><i />여유</span>
            <span className="legend-item medium"><i />보통</span>
            <span className="legend-item high"><i />혼잡</span>
            <span className="legend-item unknown"><i />수집 대기</span>
          </footer>
        </div>
      </aside>
    </main>
  );
}
