import {
  API_BASE_URL,
  STATUS_WINDOW_SECONDS,
} from '@/lib/config';
import type { LocationsStatusResponse } from '@/types/api';

export class CrowdApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = 'CrowdApiError';
  }
}

const isLocationsResponse = (
  value: unknown,
): value is LocationsStatusResponse => {
  if (!value || typeof value !== 'object') return false;
  const response = value as Partial<LocationsStatusResponse>;
  return (
    typeof response.generated_at === 'string' &&
    typeof response.window_seconds === 'number' &&
    Array.isArray(response.locations)
  );
};

export const fetchLocationStatuses = async (
  signal?: AbortSignal,
): Promise<LocationsStatusResponse> => {
  const endpoint = new URL(
    API_BASE_URL + '/api/locations/statuses',
    window.location.origin,
  );
  endpoint.searchParams.set(
    'window_seconds',
    String(STATUS_WINDOW_SECONDS),
  );

  let response: Response;
  try {
    response = await fetch(endpoint, {
      signal,
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error;
    }
    throw new CrowdApiError('혼잡도 서버에 연결할 수 없습니다.');
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new CrowdApiError(
      '서버 응답을 읽을 수 없습니다.',
      response.status,
    );
  }

  if (!response.ok) {
    throw new CrowdApiError(
      '혼잡도 데이터를 불러오지 못했습니다.',
      response.status,
    );
  }
  if (!isLocationsResponse(body)) {
    throw new CrowdApiError('서버 응답 형식이 올바르지 않습니다.');
  }
  return body;
};
