import type { InferenceResult, LocationRow } from './models.ts';
import { buildLocationStatus } from './scoring.ts';

export async function saveInferenceResult(db: D1Database, result: InferenceResult): Promise<void> {
  await db.prepare(`
    INSERT INTO inference_results
      (node_id, location_id, timestamp, received_at, people_count, confidence)
    VALUES (?, ?, ?, ?, ?, ?)
  `).bind(result.node_id, result.location_id, result.timestamp,
    result.received_at, result.people_count, result.confidence).run();
}

export async function getRecentResults(
  db: D1Database, limit: number, locationId: string | null, nodeId: string | null,
): Promise<InferenceResult[]> {
  const conditions: string[] = [];
  const values: (string | number)[] = [];
  if (locationId) { conditions.push('location_id = ?'); values.push(locationId); }
  if (nodeId) { conditions.push('node_id = ?'); values.push(nodeId); }
  const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';
  const result = await db.prepare(`
    SELECT node_id, location_id, timestamp, received_at, people_count, confidence
    FROM inference_results ${where} ORDER BY id DESC LIMIT ?
  `).bind(...values, limit).all<InferenceResult>();
  return result.results;
}

type StatusRow = LocationRow & {
  node_id: string | null;
  timestamp: string | null;
  received_at: number | null;
  people_count: number | null;
  confidence: number | null;
};

export async function getLocationStatuses(
  db: D1Database, windowSeconds: number, now: number, locationId?: string,
) {
  // One indexed seek per configured location, not a full history scan on every poll.
  const statement = db.prepare(`
    SELECT l.*, r.node_id, r.timestamp, r.received_at, r.people_count, r.confidence
    FROM locations l
    LEFT JOIN inference_results r ON r.id = (
      SELECT latest.id FROM inference_results latest
      WHERE latest.location_id = l.location_id AND latest.received_at >= ?
      ORDER BY latest.received_at DESC, latest.id DESC LIMIT 1
    )
    ${locationId === undefined ? '' : 'WHERE l.location_id = ?'}
    ORDER BY l.location_id
  `);
  const cutoff = now - windowSeconds * 1000;
  const rows = await (locationId === undefined
    ? statement.bind(cutoff) : statement.bind(cutoff, locationId)).all<StatusRow>();
  return rows.results.map((row) => buildLocationStatus(row.location_id, row,
    row.node_id === null ? null : {
      node_id: row.node_id, location_id: row.location_id,
      timestamp: row.timestamp!, received_at: row.received_at!,
      people_count: row.people_count!, confidence: row.confidence!,
    }, windowSeconds, now));
}
