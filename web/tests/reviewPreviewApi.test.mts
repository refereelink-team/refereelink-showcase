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

test('strict incomplete saves retain unknowns and optimistic revision while legacy callers may omit the policy', async () => {
  const facts = emptyFacts();
  const requests: Record<string, unknown>[] = [];
  globalThis.fetch = async (_url, options) => {
    requests.push(JSON.parse(String(options?.body)));
    return new Response(JSON.stringify({ review: {} }));
  };
  const draft = {
    expected_revision: 4,
    analysis_id: null,
    facts,
    review_state: 'uncertain' as const,
  };
  await api.saveReview('case', { ...draft, preserve_unknowns: true });
  await api.saveReview('case', draft);
  assert.deepEqual(requests[0], { ...draft, preserve_unknowns: true });
  assert.equal(Object.hasOwn(requests[1], 'preserve_unknowns'), false);
  assert.deepEqual(facts, emptyFacts());
});
