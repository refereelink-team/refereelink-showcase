import test from 'node:test';
import assert from 'node:assert/strict';
import { observeVideoPresentation } from '../src/components/multiview/videoPresentation.ts';

class Video extends EventTarget {
  paused = true;
  seeking = false;
  readyState = 4;
  currentTime = 2.5;
  nextId = 0;
  callbacks = new Map<number, VideoFrameRequestCallback>();
  requestVideoFrameCallback(callback: VideoFrameRequestCallback) {
    const id = ++this.nextId;
    this.callbacks.set(id, callback);
    return id;
  }
  cancelVideoFrameCallback(id: number) {
    this.callbacks.delete(id);
  }
  present(mediaTime: number) {
    const callbacks = [...this.callbacks.values()];
    this.callbacks.clear();
    callbacks.forEach((callback) => callback(0, { mediaTime } as VideoFrameCallbackMetadata));
  }
  event(name: string) {
    this.dispatchEvent(new Event(name));
  }
}

const observe = (video: Video) => {
  const times: Array<number | null> = [];
  const dispose = observeVideoPresentation(video as unknown as HTMLVideoElement, (time) =>
    times.push(time),
  );
  return { times, dispose };
};

test('continuous seeks retain the displayed frame and accept a newly presented PTS before seeked', () => {
  const video = new Video();
  const { times, dispose } = observe(video);
  video.present(2.48);
  for (const target of [2.6, 2.7, 2.8, 2.9]) {
    video.currentTime = target;
    video.seeking = true;
    video.readyState = 1;
    video.event('seeking');
    video.event('waiting');
    video.event('stalled');
    video.event('timeupdate');
    assert.equal(times.at(-1), 2.48);
  }
  video.present(2.76);
  assert.equal(times.at(-1), 2.76);
  assert.ok(times.every((time) => time !== null));
  video.seeking = false;
  video.readyState = 4;
  video.event('seeked');
  assert.equal(times.at(-1), 2.9);
  video.present(2.88);
  assert.equal(times.at(-1), 2.88);
  dispose();
});

test('a completed paused seek restores a loaded frame when presentation callbacks are throttled', () => {
  const video = new Video();
  const { times, dispose } = observe(video);
  video.present(2.48);
  video.seeking = true;
  video.currentTime = 3.1;
  video.event('seeking');
  assert.equal(times.at(-1), 2.48);
  video.seeking = false;
  video.event('seeked');
  assert.equal(times.at(-1), 3.1);
  video.present(3.08);
  video.event('timeupdate');
  video.event('pause');
  assert.equal(times.at(-1), 3.08);
  video.event('stalled');
  assert.equal(times.at(-1), 3.08);
  dispose();
});

test('ordinary playback stalls clear attribution while seek-related waits retain the displayed frame', () => {
  const video = new Video();
  video.paused = false;
  const { times, dispose } = observe(video);
  video.present(2.48);
  video.seeking = true;
  video.currentTime = 3;
  video.event('seeking');
  video.event('waiting');
  assert.equal(times.at(-1), 2.48);
  video.seeking = false;
  video.event('seeked');
  video.present(2.96);
  video.readyState = 1;
  video.event('waiting');
  assert.equal(times.at(-1), null);
  video.readyState = 4;
  video.present(3);
  assert.equal(times.at(-1), 3);
  dispose();
});

test('new sources and media errors clear old frames and cleanup removes pending observers', () => {
  for (const event of ['loadstart', 'emptied', 'error']) {
    const video = new Video();
    const { times, dispose } = observe(video);
    video.present(2.48);
    const obsoleteCallback = [...video.callbacks.values()][0];
    video.event(event);
    assert.equal(times.at(-1), null);
    obsoleteCallback(0, { mediaTime: 3 } as VideoFrameCallbackMetadata);
    assert.equal(times.at(-1), null);
    dispose();
    assert.equal(video.callbacks.size, 0);
    const count = times.length;
    video.event('seeked');
    video.present(3);
    assert.equal(times.length, count);
  }
});

test('invalid presented timestamps cannot replace a valid displayed frame', () => {
  const video = new Video();
  const { times, dispose } = observe(video);
  video.present(2.48);
  for (const pts of [Number.NaN, Infinity, -1]) video.present(pts);
  assert.equal(times.at(-1), 2.48);
  assert.equal(video.callbacks.size, 1);
  dispose();
});

test('the media-event fallback also retains its last frame until a seek completes', () => {
  const video = new Video();
  Object.defineProperty(video, 'requestVideoFrameCallback', { value: undefined });
  const { times, dispose } = observe(video);
  video.currentTime = 3;
  video.seeking = true;
  video.event('seeking');
  video.event('waiting');
  video.event('timeupdate');
  assert.equal(times.at(-1), 2.5);
  video.seeking = false;
  video.event('seeked');
  assert.equal(times.at(-1), 3);
  video.paused = false;
  video.currentTime = 3.04;
  video.event('timeupdate');
  assert.equal(times.at(-1), 3.04);
  dispose();
});

test('an old frame presented during a seek cannot impersonate the completed target frame', () => {
  for (const target of [0, 5]) {
    const video = new Video();
    const { times, dispose } = observe(video);
    video.present(2.48);
    video.currentTime = target;
    video.seeking = true;
    video.event('seeking');
    video.present(2.48);
    assert.equal(times.at(-1), 2.48);
    video.seeking = false;
    video.event('seeked');
    assert.equal(times.at(-1), target);
    dispose();
  }
});
