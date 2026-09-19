import type { LocationStatus } from '../../types/api.ts';
import type { InferenceResult, LocationRow } from './models.ts';

export function buildLocationStatus(
  locationId: string,
  location: LocationRow | null,
  latest: InferenceResult | null,
  windowSeconds: number,
  now: number,
): LocationStatus {
  const empty: LocationStatus = {
    location_id: locationId,
    display_name: location ? (location.display_name || locationId) : null,
    latitude: location?.latitude ?? null,
    longitude: location?.longitude ?? null,
    area_m2: location?.area_m2 ?? null,
    capacity: location?.capacity ?? null,
    status: location ? 'NO_DATA' : 'LOCATION_NOT_CONFIGURED',
    node_id: null, measured_at: null, received_at: null, people_count: null,
    density_per_m2: null, occupancy_ratio: null, congestion_score: null,
    congestion_level: 'UNKNOWN', confidence: 0, window_seconds: windowSeconds,
  };
  if (!location || !latest || latest.received_at < now - windowSeconds * 1000) return empty;
  const occupancy = latest.people_count / location.capacity;
  const score = Math.round(Math.min(occupancy * 100, 100) * 10) / 10;
  return {
    ...empty,
    status: 'OK',
    node_id: latest.node_id,
    measured_at: latest.timestamp,
    received_at: new Date(latest.received_at).toISOString(),
    people_count: latest.people_count,
    density_per_m2: Math.round(latest.people_count / location.area_m2 * 10000) / 10000,
    occupancy_ratio: Math.round(occupancy * 10000) / 10000,
    congestion_score: score,
    congestion_level: score < 35 ? 'LOW' : score < 70 ? 'MEDIUM' : 'HIGH',
    confidence: latest.confidence,
  };
}
