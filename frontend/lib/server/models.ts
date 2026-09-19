export interface WorkerBindings {
  DB: D1Database;
  SENSOR_API_TOKEN?: string;
}

export interface InferenceResult {
  node_id: string;
  location_id: string;
  timestamp: string;
  received_at: number;
  people_count: number;
  confidence: number;
}

export interface LocationRow {
  location_id: string;
  display_name: string | null;
  latitude: number | null;
  longitude: number | null;
  area_m2: number;
  capacity: number;
}

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function invalid(message: string): never {
  throw new ApiError(400, 'validation_error', message);
}

function requiredString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  if (typeof value !== 'string' || !value.trim()) {
    return invalid(`${key} must be a non-empty string`);
  }
  return value.trim();
}

function parseTimestamp(value: string): string {
  // Naive timestamps are UTC, just like the Python API. Accept fractional seconds
  // from Python datetime.isoformat(), but persist millisecond precision in JS.
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}):(\d{2})(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})?)?$/.exec(value);
  if (!match) return invalid('timestamp must be ISO 8601 format');
  const [, year, month, day, hour = '00', minute = '00', second = '00', fraction = '', zone = 'Z'] = match;
  const calendar = new Date(`${year}-${month}-${day}T00:00:00Z`);
  if (!Number.isFinite(calendar.getTime()) ||
      calendar.toISOString().slice(0, 10) !== `${year}-${month}-${day}` ||
      Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59) {
    return invalid('timestamp must be ISO 8601 format');
  }
  const parsed = new Date(`${year}-${month}-${day}T${hour}:${minute}:${second}${fraction.slice(0, 4)}${zone}`);
  if (!Number.isFinite(parsed.getTime())) return invalid('timestamp must be ISO 8601 format');
  return parsed.toISOString();
}

export function parseInferenceResult(payload: unknown, now = Date.now()): InferenceResult {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return invalid('payload must be a JSON object');
  }
  const fields = payload as Record<string, unknown>;
  const node_id = requiredString(fields, 'node_id');
  const location_id = requiredString(fields, 'location_id');
  const timestamp = parseTimestamp(requiredString(fields, 'timestamp'));
  const people_count = fields.people_count;
  const confidence = fields.confidence;
  if (typeof people_count !== 'number' || !Number.isSafeInteger(people_count) || people_count < 0) {
    return invalid('people_count must be a non-negative safe integer');
  }
  if (typeof confidence !== 'number' || !Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
    return invalid('confidence must be a number between 0 and 1');
  }
  return { node_id, location_id, timestamp, people_count, confidence, received_at: now };
}

export function serializeResult(result: InferenceResult) {
  return { ...result, received_at: new Date(result.received_at).toISOString() };
}

export function parseBoundedInteger(value: string | null, fallback: number, maximum: number): number {
  if (value === null || !/^[+-]?\d+$/.test(value.trim())) return fallback;
  const parsed = Number(value);
  if (Number.isNaN(parsed)) return fallback;
  return Math.max(1, Math.min(maximum, parsed));
}
