import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { setImmediate as nextTurn } from 'node:timers/promises';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

// Exercise the actual client functions/effect dependencies without a Kakao key,
// browser, network, or additional packages. React/SDK boundaries are small fakes.
function loadClientModule(file, dependencies, globals = {}) {
  const source = readFileSync(new URL('../' + file, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  });
  const exports = {};
  runInNewContext(outputText, {
    exports, Error, DOMException, AbortController, URL,
    require: (name) => {
      assert.ok(name in dependencies, 'Unexpected dependency: ' + name);
      return dependencies[name];
    },
    ...globals,
  });
  return exports;
}

const snapshot = (count = 1) => ({
  generated_at: '2026-09-19T00:00:00Z',
  window_seconds: 30,
  locations: [{ location_id: 'gate', status: 'OK', people_count: count }],
});
const response = (body) => ({ ok: true, status: 200, json: async () => body });
const waitForAbort = (signal) => new Promise((_, reject) => {
  if (signal.aborted) reject(signal.reason);
  else signal.addEventListener('abort', () => reject(signal.reason), { once: true });
});
const pendingFetch = (_url, { signal }) => waitForAbort(signal);

function createPollingHarness(fetch) {
  let now = 0;
  let nextId = 0;
  const timers = new Map();
  const listeners = new Map();
  const document = {
    visibilityState: 'visible',
    addEventListener: (name, handler) => listeners.set(name, handler),
    removeEventListener: (name) => listeners.delete(name),
  };
  const timerGlobals = {
    setTimeout: (callback, delay) => {
      const id = ++nextId;
      timers.set(id, { callback, at: now + delay });
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
  };
  const config = {
    API_BASE_URL: '', API_REQUEST_TIMEOUT_MS: 10_000,
    REFRESH_INTERVAL_MS: 5_000, STATUS_WINDOW_SECONDS: 30,
    ENABLE_DEMO_FALLBACK: false,
  };
  const api = loadClientModule('lib/api.ts', { '@/lib/config': config }, {
    fetch, window: { location: { origin: 'http://localhost' } },
  });
  const values = [];
  const effects = [];
  const hook = loadClientModule('hooks/useLocationStatuses.ts', {
    react: {
      useState: (initial) => {
        const index = values.length;
        values.push(initial);
        return [initial, (value) => {
          values[index] = typeof value === 'function' ? value(values[index]) : value;
        }];
      },
      useRef: (current) => ({ current }),
      useCallback: (callback) => callback,
      useEffect: (effect) => effects.push(effect),
    },
    '@/lib/api': api,
    '@/lib/config': config,
    '@/lib/demoData': {},
  }, { document, ...timerGlobals });
  hook.useLocationStatuses();
  const cleanup = effects[0]();
  return {
    state: () => ({
      snapshot: values[0], phase: values[1], message: values[2], isRefreshing: values[3],
    }),
    timers,
    async advance(milliseconds) {
      const end = now + milliseconds;
      while (true) {
        const next = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
        if (!next || next[1].at > end) break;
        now = next[1].at;
        timers.delete(next[0]);
        next[1].callback();
        await nextTurn();
      }
      now = end;
      await nextTurn();
    },
    visibility(value) {
      document.visibilityState = value;
      listeners.get('visibilitychange')();
    },
    async dispose() {
      cleanup();
      await nextTurn();
      assert.equal(timers.size, 0, 'cleanup must remove request and polling timers');
    },
  };
}

test('count updates preserve map position; selection/coordinate changes still pan', () => {
  let effects = [];
  let mode = 'loading';
  let refIndex = 0;
  const positions = [];
  const references = [
    { current: {} },
    { current: { panTo: (position) => positions.push(position) } },
    { current: { LatLng: class {
      constructor(latitude, longitude) { Object.assign(this, { latitude, longitude }); }
    } } },
    { current: new Map() },
    { current: () => {} },
  ];
  const { default: MapComponent } = loadClientModule('components/KakaoDensityMap.tsx', {
    react: {
      useRef: () => references[refIndex++],
      useState: () => [mode, () => {}],
      useMemo: (calculate) => calculate(),
      useEffect: (effect, dependencies) => effects.push({ effect, dependencies }),
    },
    'react/jsx-runtime': { jsx: () => null, jsxs: () => null },
    'lucide-react': {},
    '@/lib/config': { KAKAO_MAP_APP_KEY: 'test-only' },
    '@/lib/congestion': {},
    '@/lib/loadKakaoMaps': {},
  });
  let previous;
  function render(locations, selectedId) {
    refIndex = 0;
    effects = [];
    MapComponent({ locations, selectedId, onSelect: () => {} });
    const focus = effects.find(({ effect }) => String(effect).includes('.panTo('));
    assert.ok(focus, 'map focus effect must exist');
    if (!previous || focus.dependencies.some((value, i) => !Object.is(value, previous[i]))) {
      focus.effect();
    }
    previous = focus.dependencies;
  }
  const gate = { location_id: 'gate', latitude: 37.4, longitude: 127.1, people_count: 1 };
  render([gate], 'gate');
  assert.equal(positions.length, 0);
  mode = 'ready';
  render([gate], 'gate');
  assert.equal(positions.length, 1);
  render([{ ...gate, people_count: 8 }], 'gate');
  assert.equal(positions.length, 1, 'polling must not recenter a dragged map');
  render([{ ...gate, location_id: 'other' }], 'other');
  assert.equal(positions.length, 2, 'new selection still pans at identical coordinates');
  render([{ ...gate, location_id: 'other', latitude: 37.5 }], 'other');
  assert.equal(positions.length, 3);
  assert.equal(positions.at(-1).latitude, 37.5);
  render([{ ...gate, location_id: 'other', latitude: null, longitude: null }], 'other');
  assert.equal(positions.length, 3, 'missing coordinates must not pan');
});

test('a timed-out request releases refresh state and retries after the polling interval', async (t) => {
  let calls = 0;
  const recovered = snapshot(5);
  const harness = createPollingHarness((...args) => ++calls === 1
    ? pendingFetch(...args) : Promise.resolve(response(recovered)));
  t.after(() => harness.dispose());
  await harness.advance(9_999);
  assert.equal(harness.state().isRefreshing, true);
  await harness.advance(1);
  assert.equal(harness.state().phase, 'error');
  assert.match(harness.state().message, /시간이 초과/);
  assert.equal(harness.state().isRefreshing, false);
  await harness.advance(4_999);
  assert.equal(calls, 1);
  await harness.advance(1);
  assert.equal(calls, 2);
  assert.equal(harness.state().phase, 'live');
  assert.equal(harness.state().snapshot, recovered);
  assert.equal(harness.state().message, null);
});

test('timeouts retain the last live data and mark it as stale', async (t) => {
  let calls = 0;
  const live = snapshot(7);
  const harness = createPollingHarness((...args) => ++calls === 1
    ? Promise.resolve(response(live)) : pendingFetch(...args));
  t.after(() => harness.dispose());
  await nextTurn();
  await harness.advance(5_000);
  await harness.advance(10_000);
  assert.equal(harness.state().phase, 'stale');
  assert.equal(harness.state().snapshot, live);
  assert.equal(harness.state().isRefreshing, false);
  assert.equal(harness.timers.size, 1, 'only the next polling timer should remain');
});

test('the request deadline also covers a stalled response body', async (t) => {
  const harness = createPollingHarness(async (_url, { signal }) => ({
    ok: true, status: 200, json: () => waitForAbort(signal),
  }));
  t.after(() => harness.dispose());
  await nextTurn();
  await harness.advance(10_000);
  assert.equal(harness.state().phase, 'error');
  assert.match(harness.state().message, /시간이 초과/);
  assert.equal(harness.state().isRefreshing, false);
});

test('hiding the tab cancels quietly and showing it resumes immediately', async (t) => {
  let calls = 0;
  const harness = createPollingHarness((...args) => ++calls === 1
    ? pendingFetch(...args) : Promise.resolve(response(snapshot())));
  t.after(() => harness.dispose());
  harness.visibility('hidden');
  await nextTurn();
  assert.equal(harness.state().message, null);
  assert.equal(harness.state().isRefreshing, false);
  assert.equal(harness.timers.size, 0);
  await harness.advance(20_000);
  assert.equal(calls, 1);
  harness.visibility('visible');
  await nextTurn();
  assert.equal(calls, 2);
  assert.equal(harness.state().phase, 'live');
});

test('a late cancelled request cannot overwrite or reschedule the newer request', async (t) => {
  const requests = [];
  const harness = createPollingHarness(() => new Promise((resolve) => requests.push(resolve)));
  t.after(() => harness.dispose());
  harness.visibility('hidden');
  harness.visibility('visible');
  assert.equal(requests.length, 2);
  requests[0](response(snapshot(99)));
  await nextTurn();
  assert.equal(harness.state().snapshot, null);
  assert.equal(harness.state().isRefreshing, true);
  assert.equal(harness.timers.size, 1, 'keep only the newer request deadline');
  const latest = snapshot(3);
  requests[1](response(latest));
  await nextTurn();
  assert.equal(harness.state().snapshot, latest);
  assert.equal(harness.state().phase, 'live');
  assert.equal(harness.timers.size, 1);
});

test('unmount aborts in-flight work without scheduling another request', async () => {
  let signal;
  const harness = createPollingHarness((_url, options) => {
    signal = options.signal;
    return waitForAbort(signal);
  });
  await harness.dispose();
  assert.equal(signal.aborted, true);
  assert.equal(harness.state().message, null);
});

test('API timeout configuration has a safe default and supports overrides', () => {
  for (const [value, expected] of [[undefined, 10_000], ['0', 10_000], ['invalid', 10_000], ['2500', 2_500]]) {
    const config = loadClientModule('lib/config.ts', {}, {
      process: { env: { NEXT_PUBLIC_API_TIMEOUT_MS: value } },
    });
    assert.equal(config.API_REQUEST_TIMEOUT_MS, expected);
  }
});

test('sensor data transitions between waiting and live independently of API success', async (t) => {
  const empty = { ...snapshot(), locations: [] };
  const waiting = {
    ...snapshot(),
    locations: [{ location_id: 'gate', status: 'NO_DATA', people_count: null }],
  };
  const partial = {
    ...snapshot(0),
    locations: [...snapshot(0).locations, { location_id: 'other', status: 'NO_DATA', people_count: null }],
  };
  const sequence = [empty, waiting, partial, waiting, new Error('offline'), snapshot(4)];
  let calls = 0;
  const harness = createPollingHarness(() => {
    const result = sequence[calls++];
    return result instanceof Error ? Promise.reject(result) : Promise.resolve(response(result));
  });
  t.after(() => harness.dispose());
  await nextTurn();
  assert.equal(harness.state().phase, 'waiting', 'no configured locations is not LIVE');
  await harness.advance(5_000);
  assert.equal(harness.state().phase, 'waiting', 'NO_DATA is not LIVE');
  await harness.advance(5_000);
  assert.equal(harness.state().phase, 'live', 'one fresh zero-person reading is LIVE');
  await harness.advance(5_000);
  assert.equal(harness.state().phase, 'waiting', 'expired readings return to waiting');
  await harness.advance(5_000);
  assert.equal(harness.state().phase, 'stale', 'API failure overrides waiting');
  assert.equal(harness.state().snapshot, waiting, 'preserve the last server snapshot on failure');
  await harness.advance(5_000);
  assert.equal(harness.state().phase, 'live');
  assert.equal(harness.state().message, null);
});

test('an initial API error recovers to waiting when the server has no sensor data', async (t) => {
  let calls = 0;
  const harness = createPollingHarness(async () => ++calls === 1
    ? { ok: false, status: 503, json: async () => ({ error: 'service_unavailable' }) }
    : response({ ...snapshot(), locations: [] }));
  t.after(() => harness.dispose());
  await nextTurn();
  assert.equal(harness.state().phase, 'error');
  await harness.advance(5_000);
  assert.equal(harness.state().phase, 'waiting');
  assert.equal(harness.state().message, null);
});

test('the status badge renders LIVE, waiting and ERROR with distinct descriptions/icons', () => {
  let phase = 'loading';
  let badge;
  const jsx = (type, props) => {
    const element = { type, props };
    if (props?.role === 'status') badge = element;
    return element;
  };
  const { default: Dashboard } = loadClientModule('components/CrowdDashboard.tsx', {
    react: { useState: () => [null, () => {}], useMemo: (calculate) => calculate() },
    'react/jsx-runtime': { jsx, jsxs: jsx },
    'lucide-react': { Clock3: 'clock', Wifi: 'wifi', WifiOff: 'wifi-off' },
    '@/components/KakaoDensityMap': { default: () => null },
    '@/hooks/useLocationStatuses': {
      useLocationStatuses: () => ({
        snapshot: null, phase, message: null, isRefreshing: false, refresh: () => {},
      }),
    },
    '@/lib/congestion': {},
    '@/lib/config': { REFRESH_INTERVAL_MS: 5_000 },
  });
  for (const [value, label, detail, icon] of [
    ['loading', '대기', '연결 확인 중', 'clock'],
    ['waiting', '대기', '센서 데이터 대기', 'clock'],
    ['live', 'LIVE', '5초 자동 갱신', 'wifi'],
    ['error', 'ERROR', '자동 재시도 중', 'wifi-off'],
    ['stale', 'ERROR', '자동 재시도 중', 'wifi-off'],
    ['demo', 'DEMO', '예시 데이터', 'wifi'],
  ]) {
    phase = value;
    Dashboard();
    assert.equal(badge.props.className, 'connection-pill phase-' + value);
    assert.equal(badge.props.children[0].type, icon);
    assert.equal(badge.props.children.find((child) => child.type === 'strong').props.children, label);
    assert.equal(badge.props.children.find((child) => child.type === 'span').props.children, detail);
  }
});
