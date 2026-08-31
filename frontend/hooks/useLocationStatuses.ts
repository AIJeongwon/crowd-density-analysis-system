'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchLocationStatuses } from '@/lib/api';
import {
  ENABLE_DEMO_FALLBACK,
  REFRESH_INTERVAL_MS,
  STATUS_WINDOW_SECONDS,
} from '@/lib/config';
import { getDemoLocationsResponse } from '@/lib/demoData';
import type { LocationsStatusResponse } from '@/types/api';

export type DataPhase = 'loading' | 'live' | 'demo' | 'stale' | 'error';

export interface LocationStatusesState {
  snapshot: LocationsStatusResponse | null;
  phase: DataPhase;
  message: string | null;
  isRefreshing: boolean;
  refresh: () => void;
}

export const useLocationStatuses = (): LocationStatusesState => {
  const [snapshot, setSnapshot] =
    useState<LocationsStatusResponse | null>(null);
  const [phase, setPhase] = useState<DataPhase>('loading');
  const [message, setMessage] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const hasLiveData = useRef(false);

  const refresh = useCallback(() => {
    setRefreshKey((value) => value + 1);
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;

    const clearTimer = () => {
      if (timer) clearTimeout(timer);
      timer = null;
    };

    const schedule = () => {
      clearTimer();
      if (!disposed && document.visibilityState === 'visible') {
        timer = setTimeout(run, REFRESH_INTERVAL_MS);
      }
    };

    const run = async () => {
      clearTimer();
      controller?.abort();
      controller = new AbortController();
      setIsRefreshing(true);

      try {
        const response = await fetchLocationStatuses(controller.signal);
        if (disposed) return;
        hasLiveData.current = true;
        setSnapshot(response);
        setPhase('live');
        setMessage(null);
      } catch (error) {
        if (disposed) return;
        if (error instanceof DOMException && error.name === 'AbortError') {
          return;
        }

        const errorMessage =
          error instanceof Error
            ? error.message
            : '혼잡도 데이터를 불러오지 못했습니다.';

        if (hasLiveData.current) {
          setPhase('stale');
          setMessage('업데이트가 지연되고 있습니다. 마지막 데이터를 표시합니다.');
        } else if (ENABLE_DEMO_FALLBACK) {
          setSnapshot(getDemoLocationsResponse(STATUS_WINDOW_SECONDS));
          setPhase('demo');
          setMessage('백엔드 연결 전이라 예시 데이터를 표시하고 있습니다.');
        } else {
          setPhase('error');
          setMessage(errorMessage);
        }
      } finally {
        if (!disposed) {
          setIsRefreshing(false);
          schedule();
        }
      }
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        clearTimer();
        controller?.abort();
        return;
      }
      void run();
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    void run();

    return () => {
      disposed = true;
      clearTimer();
      controller?.abort();
      document.removeEventListener(
        'visibilitychange',
        handleVisibilityChange,
      );
    };
  }, [refreshKey]);

  return {
    snapshot,
    phase,
    message,
    isRefreshing,
    refresh,
  };
};
