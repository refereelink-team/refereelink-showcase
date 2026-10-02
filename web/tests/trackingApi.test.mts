import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../src/api.ts';
test('native tracking requests are scoped by escaped case and immutable result revision', async () => {
  const original = globalThis.fetch;
  const requests: { url: string; method?: string; body?: string | null }[] = [];
  globalThis.fetch = async (input, options) => {
    requests.push({
      url: String(input),
      method: options?.method,
      body: options?.body as string | undefined,
    });
    return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
  };
  try {
    await api.trackingCase('case/name');
    await api.startTracking('case/name');
    await api.trackingFrames('result/name', 'abc123', 240);
    await api.trackingResult('result/name');
    assert.equal(requests[0].url, '/api/tracking/cases/case%2Fname');
    assert.equal(requests[3].url, '/api/tracking/results/result%2Fname');
    assert.equal(requests[1].method, 'POST');
    assert.equal(requests[1].url, '/api/tracking/cases/case%2Fname/jobs');
    assert.equal(
      requests[2].url,
      '/api/tracking/results/result%2Fname/frames?offset=240&limit=240&revision=abc123',
    );
  } finally {
    globalThis.fetch = original;
  }
});
