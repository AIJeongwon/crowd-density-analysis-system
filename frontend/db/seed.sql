-- LOCAL EXAMPLE ONLY: matches backend/app/environment.example.json.
-- Review the position, area and capacity before using real sensor data.
-- Re-running this seed does not overwrite existing location settings.
INSERT INTO locations (location_id, display_name, latitude, longitude, area_m2, capacity)
VALUES ('moran-market-gate-1', '모란민속5일장', 37.429325616, 127.126600316, 100.0, 50)
ON CONFLICT(location_id) DO NOTHING;
