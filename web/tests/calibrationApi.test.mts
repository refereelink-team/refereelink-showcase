import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../src/api.ts';

test('practice mutations target an escaped independent session with the current revision', async () => {
  const original = globalThis.fetch;
  const requests: { url: string; method?: string; body: unknown }[] = [];
  globalThis.fetch = async (input, options) => {
    requests.push({
      url: String(input),
      method: options?.method,
      body: options?.body ? JSON.parse(String(options.body)) : null,
    });
    return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
  };
  try {
    await api.createCalibration();
    await api.calibration('practice/name');
    await api.prepareCalibration('practice/name', 3, 1000, 18000);
    await api.labelCalibration('practice/name', 5, 24, 'referee');
    await api.validateCalibration('practice/name', 7);
    await api.resetCalibration('practice/name', 9);
    await api.calibrationMetadata('practice/name');
    assert.deepEqual(requests[0], { url: '/api/calibration/sessions', method: 'POST', body: {} });
    assert.equal(requests[1].url, '/api/calibration/sessions/practice%2Fname');
    assert.deepEqual(requests[2].body, { revision: 3, start_ms: 1000, end_ms: 18000 });
    assert.deepEqual(requests[3].body, { revision: 5, track_id: 24, label: 'referee' });
    assert.deepEqual(requests[4].body, { revision: 7 });
    assert.deepEqual(requests[5].body, { revision: 9 });
    assert.equal(requests[6].url, '/api/calibration/sessions/practice%2Fname/metadata');
    assert.deepEqual(
      requests.slice(2, 6).map((value) => value.url),
      [
        '/api/calibration/sessions/practice%2Fname/prepare',
        '/api/calibration/sessions/practice%2Fname/labels',
        '/api/calibration/sessions/practice%2Fname/validate',
        '/api/calibration/sessions/practice%2Fname/reset',
      ],
    );
    assert.equal(
      requests.some((value) => /activate|pipeline|tracking|demo/.test(value.url)),
      false,
    );
  } finally {
    globalThis.fetch = original;
  }
});
