const parsePositiveInteger = (
  value: string | undefined,
  fallback: number,
  minimum: number,
) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= minimum
    ? Math.round(parsed)
    : fallback;
};

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? ''
).trim().replace(/\/+$/, '');

export const KAKAO_MAP_APP_KEY =
  process.env.NEXT_PUBLIC_KAKAO_MAP_APP_KEY?.trim() || '';

export const REFRESH_INTERVAL_MS = parsePositiveInteger(
  process.env.NEXT_PUBLIC_STATUS_POLL_INTERVAL_MS,
  5_000,
  1_000,
);

export const API_REQUEST_TIMEOUT_MS = parsePositiveInteger(
  process.env.NEXT_PUBLIC_API_TIMEOUT_MS,
  10_000,
  1_000,
);

export const STATUS_WINDOW_SECONDS = parsePositiveInteger(
  process.env.NEXT_PUBLIC_STATUS_WINDOW_SECONDS,
  30,
  1,
);

export const ENABLE_DEMO_FALLBACK =
  (process.env.NEXT_PUBLIC_ENABLE_DEMO_FALLBACK || 'false').toLowerCase() ===
  'true';

export const DEFAULT_MAP_CENTER = {
  latitude: 37.429325616,
  longitude: 127.126600316,
};
