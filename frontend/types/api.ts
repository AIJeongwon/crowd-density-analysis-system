export type CongestionLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'UNKNOWN';
export type LocationDataStatus =
  | 'OK'
  | 'NO_DATA'
  | 'LOCATION_NOT_CONFIGURED';

export interface LocationStatus {
  location_id: string;
  display_name: string | null;
  latitude: number | null;
  longitude: number | null;
  status: LocationDataStatus;
  node_id: string | null;
  measured_at: string | null;
  received_at: string | null;
  people_count: number | null;
  area_m2: number | null;
  capacity: number | null;
  density_per_m2: number | null;
  occupancy_ratio: number | null;
  congestion_score: number | null;
  congestion_level: CongestionLevel;
  confidence: number;
  window_seconds: number;
}

export interface LocationsStatusResponse {
  generated_at: string;
  window_seconds: number;
  locations: LocationStatus[];
}
