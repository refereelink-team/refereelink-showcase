import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { api, request, mediaUrl } from '../src/api.ts';
const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});
test('preserves server error detail for review conflicts and failed jobs', async () => {
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ detail: 'review revision conflict' }), {
      status: 409,
      headers: { 'Content-Type': 'application/json' },
    });
  await assert.rejects(request('/review'), /review revision conflict/);
});
test('rejects invalid successful responses instead of treating empty data as success', async () => {
  globalThis.fetch = async () => new Response('<html>upstream unavailable</html>', { status: 200 });
  await assert.rejects(request('/catalog'), /空响应/);
});
test('request timeout returns a retryable error and releases pending request', async () => {
  let aborted = false;
  globalThis.fetch = async (_url, options) =>
    new Promise((_resolve, reject) =>
      options?.signal?.addEventListener('abort', () => {
        aborted = true;
        reject(new DOMException('timed out', 'AbortError'));
      }),
    );
  await assert.rejects(request('/catalog', undefined, 20), /后台响应超时/);
  assert.equal(aborted, true);
});
test('media gateway keeps remote CUDA origin on the same browser origin', () => {
  assert.equal(
    mediaUrl('http://cuda-host.example:8000/api/multiview/media/case/view?x=1'),
    '/api/multiview/media/case/view?x=1',
  );
  assert.equal(mediaUrl('/media/input/foul-1.mp4'), '/media/input/foul-1.mp4');
  assert.equal(
    mediaUrl('https://another-cuda-host.example/api/multiview/live/preview/phone'),
    '/api/multiview/live/preview/phone',
  );
  assert.equal(mediaUrl('https://video.example/clip.mp4'), 'https://video.example/clip.mp4');
  assert.equal(mediaUrl(null), undefined);
});
test('successful payload retains false hardware and idle values', async () => {
  const snapshot = { source_mode: 'idle', hardware_verified: false, position: null };
  globalThis.fetch = async () =>
    new Response(JSON.stringify(snapshot), { headers: { 'Content-Type': 'application/json' } });
  assert.deepEqual(await request('/telemetry'), snapshot);
});

import { candidateEventTime, candidateEmittedTime } from '../src/events.ts';
test('structured foul results keep candidate PTS, details and review status intact', async () => {
  const result = {
    id: 'result/name',
    revision: 'rev-1',
    events: [
      {
        media_pts_seconds: 2.434,
        source_frame_id: 74,
        event: {
          id: 'actual',
          event_type: 'foul_candidate',
          confidence: 0.558,
          reviewed: false,
          foul_details: { action: 'Holding' },
        },
      },
    ],
  };
  let endpoint = '';
  globalThis.fetch = async (url) => {
    endpoint = String(url);
    return new Response(JSON.stringify(result));
  };
  const actual = await api.foulResult('result/name');
  assert.equal(endpoint, '/api/foul/results/result%2Fname');
  assert.deepEqual(actual, result);
  assert.equal(candidateEventTime(actual.events[0]), 2.434);
  assert.equal(candidateEmittedTime(actual.events[0]), null);
});

test('structured revision conflicts retain the server revision for safe review recovery', async () => {
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        detail: { code: 'REVIEW_REVISION_CONFLICT', expected_revision: 1, current_revision: 2 },
      }),
      { status: 409 },
    );
  await assert.rejects(request('/review'), /服务器已是修订 2.*草稿已保留/);
});
