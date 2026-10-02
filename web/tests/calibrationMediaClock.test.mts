import test from 'node:test';
import assert from 'node:assert/strict';
import {
  observeCalibrationMedia,
  type CalibrationMediaElement,
} from '../src/components/calibration/calibrationMediaClock.ts';

class FakeVideo implements CalibrationMediaElement {
  currentTime = 0;
  seeking = false;
  nextId = 1;
  listeners = new Map<string, Set<() => void>>();
  callbacks = new Map<number, (now: number, frame: { mediaTime: number }) => void>();
  canceled: number[] = [];
  addEventListener(type: string, listener: () => void) {
    const set = this.listeners.get(type) || new Set();
    set.add(listener);
    this.listeners.set(type, set);
  }
  removeEventListener(type: string, listener: () => void) {
    this.listeners.get(type)?.delete(listener);
  }
  emit(type: string) {
    for (const listener of this.listeners.get(type) || []) listener();
  }
  requestVideoFrameCallback = (callback: (now: number, frame: { mediaTime: number }) => void) => {
    const id = this.nextId++;
    this.callbacks.set(id, callback);
    return id;
  };
  cancelVideoFrameCallback = (handle: number) => {
    this.canceled.push(handle);
    // Retain canceled callbacks so tests can reproduce callbacks already queued by the browser.
  };
  present(id: number, mediaTime: number) {
    const callback = this.callbacks.get(id)!;
    this.callbacks.delete(id);
    callback(0, { mediaTime });
  }
}
function setup() {
  const video = new FakeVideo();
  const times: number[] = [];
  const seeking: boolean[] = [];
  const dispose = observeCalibrationMedia(
    video,
    (time) => times.push(time),
    (value) => seeking.push(value),
  );
  return { video, times, seeking, dispose };
}
test('a backward seek ignores canceled and old-frame callbacks after seeked', () => {
  const { video, times, seeking } = setup();
  video.currentTime = 12;
  video.present(1, 12);
  video.currentTime = 2;
  video.seeking = true;
  video.emit('seeking');
  video.emit('timeupdate');
  video.present(2, 12); // Already queued callback from before the seek.
  video.seeking = false;
  video.emit('seeked');
  video.present(3, 12); // A delayed old decoded frame in the new generation.
  assert.deepEqual(times, [12]);
  assert.equal(seeking.at(-1), true);
  video.present(4, 2);
  assert.deepEqual(times, [12, 2]);
  assert.equal(seeking.at(-1), false);
});
test('paused seek keeps the sole new presentation callback across seeked', () => {
  const { video, times } = setup();
  video.currentTime = 4;
  video.seeking = true;
  video.emit('seeking');
  const callbackId = video.nextId - 1;
  video.seeking = false;
  video.emit('seeked');
  assert.equal(video.nextId - 1, callbackId);
  video.present(callbackId, 3.9667); // The displayed frame PTS can precede the requested seek.
  video.emit('timeupdate');
  assert.deepEqual(times, [3.9667]);
});
test('a new frame arriving before seeked remains authoritative', () => {
  const { video, times, seeking } = setup();
  video.currentTime = 5;
  video.seeking = true;
  video.emit('seeking');
  video.seeking = false;
  video.present(2, 5);
  video.emit('seeked');
  assert.deepEqual(times, [5]);
  assert.equal(seeking.at(-1), false);
});
test('repeated forward and backward dragging cannot restore an earlier seek generation', () => {
  const { video, times } = setup();
  for (const time of [8, 1, 10, 3]) {
    video.currentTime = time;
    video.seeking = true;
    video.emit('seeking');
  }
  video.seeking = false;
  video.emit('seeked');
  for (let id = 1; id < 5; id++) video.present(id, [0, 8, 1, 10][id - 1]);
  assert.deepEqual(times, []);
  video.present(5, 3);
  assert.deepEqual(times, [3]);
});
test('ordinary playback accepts displayed PTS instead of the requested native time', () => {
  const { video, times } = setup();
  video.currentTime = 1;
  video.emit('timeupdate');
  video.present(1, 0.9667);
  video.currentTime = 1.3;
  video.present(2, 1.2667);
  assert.deepEqual(times, [0.9667, 1.2667]);
});
test('source disposal removes listeners and ignores already queued presentations', () => {
  const { video, times, dispose } = setup();
  dispose();
  video.present(1, 10);
  video.emit('seeking');
  assert.deepEqual(times, []);
  assert.equal(
    [...video.listeners.values()].every((listeners) => listeners.size === 0),
    true,
  );
  assert.equal(video.nextId, 2);
});
test('browsers without a frame callback fall back only after seeking finishes', () => {
  const video = new FakeVideo();
  const media: CalibrationMediaElement = {
    get currentTime() {
      return video.currentTime;
    },
    get seeking() {
      return video.seeking;
    },
    addEventListener: video.addEventListener.bind(video),
    removeEventListener: video.removeEventListener.bind(video),
  };
  const times: number[] = [];
  const seeking: boolean[] = [];
  observeCalibrationMedia(
    media,
    (time) => times.push(time),
    (value) => seeking.push(value),
  );
  video.currentTime = 8;
  video.emit('timeupdate');
  video.currentTime = 2;
  video.seeking = true;
  video.emit('seeking');
  video.emit('timeupdate');
  assert.deepEqual(times, [8]);
  video.seeking = false;
  video.emit('seeked');
  assert.deepEqual(times, [8, 2]);
  assert.deepEqual(seeking, [true, false]);
});

test('paused presented frame while seeking is retained until seeked without another callback', () => {
  const { video, times, seeking } = setup();
  video.currentTime = 2;
  video.seeking = true;
  video.emit('seeking');
  video.present(2, 1.9667);
  assert.deepEqual(times, []);
  assert.equal(seeking.at(-1), true);
  video.seeking = false;
  video.emit('seeked');
  assert.deepEqual(times, [1.9667]);
  assert.equal(seeking.at(-1), false);
});
test('a deferred presentation is invalidated by a subsequent seek generation', () => {
  const { video, times, seeking } = setup();
  video.currentTime = 8;
  video.seeking = true;
  video.emit('seeking');
  video.present(2, 8);
  video.currentTime = 2;
  video.emit('seeking');
  video.seeking = false;
  video.emit('seeked');
  assert.deepEqual(times, []);
  assert.equal(seeking.at(-1), true);
  video.present(4, 2);
  assert.deepEqual(times, [2]);
});
test('seeked cannot publish a deferred frame when the scrubber has moved again', () => {
  const { video, times } = setup();
  video.currentTime = 8;
  video.seeking = true;
  video.emit('seeking');
  video.present(2, 8);
  video.currentTime = 2;
  video.seeking = false;
  video.emit('seeked');
  assert.deepEqual(times, []);
  video.present(3, 2);
  assert.deepEqual(times, [2]);
});
