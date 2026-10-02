import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { request, mediaUrl } from '../src/api.ts';
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

import { parseCandidateEvents } from '../src/events.ts';
test('candidate timeline uses actual media PTS and omits non-candidate decisions', () => {
  const events = parseCandidateEvents(
    JSON.stringify({
      media_pts_seconds: 2.434,
      source_frame_id: 74,
      event: {
        id: 'actual',
        event_type: 'foul_candidate',
        confidence: 0.558,
        foul_details: { action: 'Holding' },
      },
    }) +
      '\n' +
      JSON.stringify({ media_pts_seconds: 1, event: { event_type: 'no_offence' } }),
  );
  assert.equal(events.length, 1);
  assert.equal(events[0].media_pts_seconds, 2.434);
  assert.equal(events[0].event.foul_details?.action, 'Holding');
});
