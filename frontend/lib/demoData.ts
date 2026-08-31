import type { LocationStatus, LocationsStatusResponse } from '@/types/api';

const DEMO_LOCATIONS: Omit<
  LocationStatus,
  'measured_at' | 'received_at' | 'window_seconds'
>[] = [
  {
    location_id: 'moran-market-gate-1',
    display_name: '모란민속5일장',
    latitude: 37.429325616,
    longitude: 127.126600316,
    status: 'OK',
    node_id: 'demo-node-01',
    people_count: 42,
    area_m2: 100,
    capacity: 60,
    density_per_m2: 0.42,
    occupancy_ratio: 0.7,
    congestion_score: 70,
    congestion_level: 'HIGH',
    confidence: 0.91,
  },
  {
    location_id: 'moran-station-plaza',
    display_name: '모란역 광장',
    latitude: 37.43118,
    longitude: 127.12912,
    status: 'OK',
    node_id: 'demo-node-02',
    people_count: 18,
    area_m2: 120,
    capacity: 70,
    density_per_m2: 0.15,
    occupancy_ratio: 0.2571,
    congestion_score: 25.7,
    congestion_level: 'LOW',
    confidence: 0.88,
  },
  {
    location_id: 'seongnam-food-street',
    display_name: '성남동 먹거리 구역',
    latitude: 37.4277,
    longitude: 127.129,
    status: 'OK',
    node_id: 'demo-node-03',
    people_count: 31,
    area_m2: 90,
    capacity: 55,
    density_per_m2: 0.3444,
    occupancy_ratio: 0.5636,
    congestion_score: 56.4,
    congestion_level: 'MEDIUM',
    confidence: 0.86,
  },
];

export const getDemoLocationsResponse = (
  windowSeconds: number,
): LocationsStatusResponse => {
  const now = new Date().toISOString();
  return {
    generated_at: now,
    window_seconds: windowSeconds,
    locations: DEMO_LOCATIONS.map((location) => ({
      ...location,
      measured_at: now,
      received_at: now,
      window_seconds: windowSeconds,
    })),
  };
};
