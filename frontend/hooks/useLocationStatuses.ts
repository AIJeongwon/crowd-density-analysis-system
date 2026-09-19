'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchLocationStatuses } from '@/lib/api';
import {
  API_REQUEST_TIMEOUT_MS,
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
      const requestController = new AbortController();
      controller = requestController;
      let timedOut = false;
      const requestTimeout = setTimeout(() => {
        timedOut = true;
        requestController.abort();
      }, API_REQUEST_TIMEOUT_MS);
      setIsRefreshing(true);

      try {
        const response = await fetchLocationStatuses(requestController.signal);
        if (
          disposed || controller !== requestController ||
          requestController.signal.aborted
        ) return;
        hasLiveData.current = true;
        setSnapshot(response);
        setPhase('live');
        setMessage(null);
      } catch (error) {
        if (disposed || controller !== requestController) return;
        if (requestController.signal.aborted && !timedOut) {
          return;
        }

        const errorMessage =
          timedOut
            ? '서버 응답 시간이 초과되었습니다. 잠시 후 다시 시도합니다.'
            : error instanceof Error
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
        clearTimeout(requestTimeout);
        // A cancelled older request must not change a newer request's state/timer.
        if (!disposed && controller === requestController) {
          controller = null;
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
