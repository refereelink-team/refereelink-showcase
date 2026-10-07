import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../src/api.ts';
import { emptyFacts } from '../src/components/multiview/reviewFacts.ts';

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

test('preview sends unknown facts without a saved revision or fabricated location', async () => {
  const facts = emptyFacts();
  const before = structuredClone(facts);
  let captured: { url: string; options?: RequestInit } | undefined;
  globalThis.fetch = async (url, options) => {
    captured = { url: String(url), options };
    return new Response(JSON.stringify({ can_finalize: false }));
  };
  await api.previewReview('case/with space', facts);
  assert.equal(captured?.url, '/api/multiview/cases/case%2Fwith%20space/review/preview');
  assert.equal(captured?.options?.method, 'POST');
  assert.deepEqual(JSON.parse(String(captured?.options?.body)), { facts });
  assert.deepEqual(facts, before);
});

test('repeated previews send only the current draft and never save or explain a review', async () => {
  const facts = emptyFacts();
  const requests: { url: string; method?: string; body: unknown }[] = [];
  globalThis.fetch = async (url, options) => {
    requests.push({
      url: String(url),
      method: options?.method,
      body: JSON.parse(String(options?.body)),
    });
    return new Response(JSON.stringify({ can_finalize: false }));
  };
  await api.previewReview('case', facts);
  const changed = {
    ...facts,
    contact: { value: false, source: 'human' as const, confidence: null, confirmed: true },
  };
  await api.previewReview('case', changed);
  assert.deepEqual(requests, [
    { url: '/api/multiview/cases/case/review/preview', method: 'POST', body: { facts } },
    { url: '/api/multiview/cases/case/review/preview', method: 'POST', body: { facts: changed } },
  ]);
  assert.deepEqual(facts, emptyFacts());
});
