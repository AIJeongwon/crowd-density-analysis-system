import { timingSafeEqual } from 'node:crypto';
import { ApiError, parseBoundedInteger, parseInferenceResult, serializeResult } from './models.ts';
import type { WorkerBindings } from './models.ts';
import { getLocationStatuses, getRecentResults, saveInferenceResult } from './inferenceRepository.ts';
import { buildLocationStatus } from './scoring.ts';

const MAX_BODY_BYTES = 1_000_000;

function json(payload: unknown, status = 200): Response {
  return Response.json(payload, { status, headers: { 'Cache-Control': 'no-store' } });
}

async function respond(operation: () => Promise<Response>): Promise<Response> {
  try { return await operation(); }
  catch (error) {
    if (error instanceof ApiError) {
      return json({ error: error.code, message: error.message }, error.status);
    }
    // Do not send SQL details, incoming bodies or credentials to the browser.
    console.error('CDAS database operation failed', error instanceof Error ? error.name : 'UnknownError');
    return json({ error: 'service_unavailable', message: 'Database is unavailable. Check the DB binding and migrations.' }, 503);
  }
}

function authorizeSensor(request: Request, env: WorkerBindings): void {
  const expected = env.SENSOR_API_TOKEN?.trim();
  if (!expected) throw new ApiError(503, 'sensor_auth_not_configured', 'Sensor authentication is not configured.');
  const match = /^Bearer (\S+)$/i.exec(request.headers.get('Authorization') || '');
  const actualBytes = Buffer.from(match?.[1] || '');
  const expectedBytes = Buffer.from(expected);
  if (actualBytes.length !== expectedBytes.length || !timingSafeEqual(actualBytes, expectedBytes)) {
    throw new ApiError(401, 'unauthorized', 'A valid sensor token is required.');
  }
}

async function readPayload(request: Request): Promise<unknown> {
  if (request.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/json') {
    throw new ApiError(415, 'unsupported_media_type', 'Content-Type must be application/json.');
  }
  if (Number(request.headers.get('Content-Length')) > MAX_BODY_BYTES) {
    throw new ApiError(413, 'payload_too_large', 'Request body is too large.');
  }
  if (!request.body) throw new ApiError(400, 'validation_error', 'Request body is required.');
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BODY_BYTES) {
        await reader.cancel();
        throw new ApiError(413, 'payload_too_large', 'Request body is too large.');
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new ApiError(400, 'invalid_json', 'Request body must contain valid JSON.');
  }
}

export function postInferenceResult(request: Request, env: WorkerBindings): Promise<Response> {
  return respond(async () => {
    authorizeSensor(request, env);
    const result = parseInferenceResult(await readPayload(request));
    await saveInferenceResult(env.DB, result);
    return json({ result: serializeResult(result) }, 201);
  });
}

export function listLocationStatuses(request: Request, env: WorkerBindings): Promise<Response> {
  return respond(async () => {
    const windowSeconds = parseBoundedInteger(new URL(request.url).searchParams.get('window_seconds'), 30, 3600);
    const now = Date.now();
    return json({ generated_at: new Date(now).toISOString(), window_seconds: windowSeconds,
      locations: await getLocationStatuses(env.DB, windowSeconds, now) });
  });
}

export function getLocationStatus(request: Request, env: WorkerBindings, locationId: string): Promise<Response> {
  return respond(async () => {
    const windowSeconds = parseBoundedInteger(new URL(request.url).searchParams.get('window_seconds'), 30, 3600);
    const now = Date.now();
    const statuses = await getLocationStatuses(env.DB, windowSeconds, now, locationId);
    const status = statuses[0] ?? buildLocationStatus(locationId, null, null, windowSeconds, now);
    return json(status, status.status === 'OK' ? 200 : 404);
  });
}

export function listRecentResults(request: Request, env: WorkerBindings): Promise<Response> {
  return respond(async () => {
    const query = new URL(request.url).searchParams;
    const results = await getRecentResults(env.DB, parseBoundedInteger(query.get('limit'), 20, 100),
      query.get('location_id'), query.get('node_id'));
    return json({ results: results.map(serializeResult) });
  });
}

export function health(env: WorkerBindings): Promise<Response> {
  return respond(async () => {
    // Check the schema as well as the binding; SELECT 1 would hide missing migrations.
    await env.DB.prepare('SELECT location_id FROM locations LIMIT 1').first();
    await env.DB.prepare('SELECT id FROM inference_results LIMIT 1').first();
    return json({ status: 'ok' });
  });
}
