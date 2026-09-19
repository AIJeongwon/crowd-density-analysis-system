CREATE TABLE locations (
  location_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(location_id)) > 0),
  display_name TEXT,
  latitude REAL,
  longitude REAL,
  area_m2 REAL NOT NULL CHECK (area_m2 > 0),
  capacity INTEGER NOT NULL CHECK (capacity > 0 AND typeof(capacity) = 'integer'),
  CHECK (
    (latitude IS NULL AND longitude IS NULL) OR
    (latitude IS NOT NULL AND longitude IS NOT NULL AND
     latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)
  )
);

-- Unknown location IDs are retained, matching the original Python ingestion API.
-- They are not displayed until the matching locations row is configured.
CREATE TABLE inference_results (
  id INTEGER PRIMARY KEY,
  node_id TEXT NOT NULL CHECK (length(trim(node_id)) > 0),
  location_id TEXT NOT NULL CHECK (length(trim(location_id)) > 0),
  timestamp TEXT NOT NULL,
  received_at INTEGER NOT NULL,
  people_count INTEGER NOT NULL CHECK (people_count >= 0 AND typeof(people_count) = 'integer'),
  confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1)
);

CREATE INDEX idx_results_location_latest
  ON inference_results(location_id, received_at DESC, id DESC);
CREATE INDEX idx_results_location_recent
  ON inference_results(location_id, id DESC);
CREATE INDEX idx_results_node_recent
  ON inference_results(node_id, id DESC);
PRAGMA optimize;
