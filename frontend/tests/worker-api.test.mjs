import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { after, before, beforeEach, test } from 'node:test';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';
import { postInferenceResult, listLocationStatuses, getLocationStatus, listRecentResults, health } from '../lib/server/api.ts';
import { parseInferenceResult, parseBoundedInteger } from '../lib/server/models.ts';
import { saveInferenceResult, getLocationStatuses } from '../lib/server/inferenceRepository.ts';

// Real local workerd/D1, not an in-memory repository mock. No Cloudflare login or
// remote database is involved. Node's test runner is already available in Node 22.
const mf = new Miniflare(convertV4MiniflareOptions({
  modules: true,
  script: 'export default { fetch() { return new Response("test"); } };',
  compatibilityDate: '2026-08-20',
  d1Databases: ['DB'],
}));
let env;
const payload = {
  node_id: 'pi-001', location_id: 'gate-1',
  timestamp: '2020-01-01T00:00:00+00:00', people_count: 20, confidence: 0.8,
};
const get = (path) => new Request(`http://localhost${path}`);
const post = (body = payload, token = 'test-only-token') => new Request('http://localhost/api/inference-results', {
  method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
  body: JSON.stringify(body),
});

before(async () => {
  env = { DB: await mf.getD1Database('DB'), SENSOR_API_TOKEN: 'test-only-token' };
  const schema = readFileSync(new URL('../migrations/0001_crowd.sql', import.meta.url), 'utf8');
  for (const sql of schema.split(';').map((part) => part.trim()).filter(Boolean)) {
    await env.DB.prepare(sql).run();
  }
});
after(async () => { await mf.dispose(); });
beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare('DELETE FROM inference_results'),
    env.DB.prepare('DELETE FROM locations'),
    env.DB.prepare('INSERT INTO locations VALUES (?, ?, ?, ?, ?, ?)')
      .bind('gate-1', '입구', 37.4293, 127.1266, 100, 50),
  ]);
});

test('authenticated ingestion persists in D1 and preserves the dashboard response', async () => {
  const response = await postInferenceResult(post({ ...payload, received_at: '1900-01-01' }), env);
  assert.equal(response.status, 201);
  const saved = (await response.json()).result;
  assert.ok(Date.parse(saved.received_at) > Date.now() - 10000);
  const snapshot = await listLocationStatuses(get('/api/locations/statuses'), env);
  assert.equal(snapshot.headers.get('Cache-Control'), 'no-store');
  const body = await snapshot.json();
  assert.equal(body.window_seconds, 30);
  assert.equal(body.locations[0].status, 'OK');
  assert.equal(body.locations[0].people_count, 20);
  assert.equal(body.locations[0].density_per_m2, 0.2);
  assert.equal(body.locations[0].occupancy_ratio, 0.4);
  assert.equal(body.locations[0].congestion_score, 40);
  assert.equal(body.locations[0].congestion_level, 'MEDIUM');
  assert.equal(body.locations[0].measured_at, '2020-01-01T00:00:00.000Z');
  assert.equal((await getLocationStatus(get('/'), env, 'gate-1')).status, 200);
});

test('missing/wrong credentials cannot write, and an unset secret fails closed', async () => {
  assert.equal((await postInferenceResult(post(payload, 'wrong'), env)).status, 401);
  const request = post();
  request.headers.delete('Authorization');
  assert.equal((await postInferenceResult(request, env)).status, 401);
  assert.equal((await postInferenceResult(post(), { DB: env.DB })).status, 503);
  assert.equal(await env.DB.prepare('SELECT count(*) AS n FROM inference_results').first('n'), 0);
});

test('invalid payloads are rejected without inserts', async () => {
  for (const invalid of [null, [], { ...payload, node_id: ' ' },
    { ...payload, people_count: true }, { ...payload, people_count: -1 },
    { ...payload, people_count: 1.5 }, { ...payload, confidence: 1.1 },
    { ...payload, confidence: '0.8' }, { ...payload, timestamp: '2026-02-30T00:00:00Z' }]) {
    assert.equal((await postInferenceResult(post(invalid), env)).status, 400);
  }
  const invalidJson = post();
  assert.equal((await postInferenceResult(new Request(invalidJson.url, {
    method: 'POST', headers: invalidJson.headers, body: '{',
  }), env)).status, 400);
  assert.equal(await env.DB.prepare('SELECT count(*) AS n FROM inference_results').first('n'), 0);
});

test('body limits apply even without Content-Length', async () => {
  const request = post({ ...payload, padding: 'x'.repeat(1_000_001) });
  assert.equal((await postInferenceResult(request, env)).status, 413);
  const wrongType = post();
  wrongType.headers.set('Content-Type', 'text/plain');
  assert.equal((await postInferenceResult(wrongType, env)).status, 415);
});

test('empty/expired data retains coordinates and uses NO_DATA, never a fake zero', async () => {
  await saveInferenceResult(env.DB, parseInferenceResult(payload, Date.now() - 31000));
  const response = await listLocationStatuses(get('/api/locations/statuses'), env);
  const [status] = (await response.json()).locations;
  assert.equal(status.status, 'NO_DATA');
  assert.equal(status.people_count, null);
  assert.equal(status.latitude, 37.4293);
  assert.equal(status.confidence, 0);
  assert.equal((await getLocationStatus(get('/'), env, 'gate-1')).status, 404);
  const missing = await getLocationStatus(get('/'), env, 'unknown');
  assert.equal(missing.status, 404);
  assert.equal((await missing.json()).status, 'LOCATION_NOT_CONFIGURED');
});

test('window boundaries and deterministic latest selection use server receipt time', async () => {
  const now = Date.now();
  await saveInferenceResult(env.DB, parseInferenceResult({ ...payload, people_count: 0 }, now - 30000));
  assert.equal((await getLocationStatuses(env.DB, 30, now))[0].people_count, 0);
  assert.equal((await getLocationStatuses(env.DB, 29, now))[0].status, 'NO_DATA');
  await saveInferenceResult(env.DB, parseInferenceResult({ ...payload, people_count: 35 }, now));
  await saveInferenceResult(env.DB, parseInferenceResult({ ...payload, people_count: 70 }, now));
  const [latest] = await getLocationStatuses(env.DB, 30, now);
  assert.equal(latest.people_count, 70);
  assert.equal(latest.congestion_score, 100);
  assert.equal(latest.congestion_level, 'HIGH');
});

test('recent results preserve filtering/order and SQL values are bound safely', async () => {
  await postInferenceResult(post(), env);
  await postInferenceResult(post({ ...payload, node_id: 'pi-002', people_count: 25 }), env);
  await postInferenceResult(post({ ...payload, location_id: 'unknown', people_count: 30 }), env);
  const recent = await listRecentResults(get('/api/inference-results/recent?location_id=gate-1&limit=1'), env);
  assert.deepEqual((await recent.json()).results.map((r) => r.people_count), [25]);
  const node = await listRecentResults(get('/api/inference-results/recent?node_id=pi-001&location_id=gate-1'), env);
  assert.deepEqual((await node.json()).results.map((r) => r.people_count), [20]);
  assert.equal((await getLocationStatus(get('/'), env, "gate-1' OR 1=1 --")).status, 404);
  const list = await listLocationStatuses(get('/api/locations/statuses'), env);
  assert.equal((await list.json()).locations.length, 1);
});

test('query bounds and ISO timestamps match the existing protocol', () => {
  assert.equal(parseBoundedInteger('0', 30, 3600), 1);
  assert.equal(parseBoundedInteger('999999', 30, 3600), 3600);
  assert.equal(parseBoundedInteger('1.5', 30, 3600), 30);
  assert.equal(parseInferenceResult({ ...payload, timestamp: '2026-09-19T12:34:56.123456+09:00' }).timestamp,
    '2026-09-19T03:34:56.123Z');
  assert.equal(parseInferenceResult({ ...payload, timestamp: '2026-09-19T12:34:56' }).timestamp,
    '2026-09-19T12:34:56.000Z');
});

test('health verifies D1 schema; database failures return a recoverable 503', async () => {
  assert.equal((await health(env)).status, 200);
  const broken = { DB: { prepare() { throw new Error('private database detail'); } } };
  const response = await listLocationStatuses(get('/api/locations/statuses'), broken);
  assert.equal(response.status, 503);
  assert.ok(!(await response.text()).includes('private database detail'));
  assert.equal((await health(broken)).status, 503);
});

test('latest lookup uses the composite index and schema prevents invalid coordinates', async () => {
  const plan = await env.DB.prepare(`EXPLAIN QUERY PLAN SELECT id FROM inference_results
    WHERE location_id = ? AND received_at >= ? ORDER BY received_at DESC, id DESC LIMIT 1`)
    .bind('gate-1', Date.now() - 30000).all();
  assert.match(JSON.stringify(plan.results), /idx_results_location_latest/);
  await assert.rejects(() => env.DB.prepare('INSERT INTO locations VALUES (?, ?, ?, ?, ?, ?)')
    .bind('bad', null, 37, null, 100, 50).run());
});
