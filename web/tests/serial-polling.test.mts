import { test } from 'node:test';
import assert from 'node:assert/strict';
import { serialPolling } from '../src/serialPolling.ts';

function clock() {
  let id = 0;
  const pending = new Map<number, () => void>();
  return {
    pending,
    timers: {
      set: (fn: () => void) => {
        pending.set(++id, fn);
        return id;
      },
      clear: (n: number) => {
        pending.delete(n);
      },
    },
    tick() {
      const work = [...pending.values()];
      pending.clear();
      work.forEach((fn) => fn());
    },
  };
}
const settle = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

test('slow status replies cannot overlap or starve later polling', async () => {
  const manual = clock();
  let resolve!: () => void;
  let calls = 0;
  const stop = serialPolling(
    () => {
      calls++;
      return new Promise<void>((r) => {
        resolve = r;
      });
    },
    1000,
    manual.timers,
  );
  assert.equal(calls, 1);
  manual.tick();
  manual.tick();
  assert.equal(calls, 1);
  assert.equal(manual.pending.size, 0);
  resolve();
  await settle();
  assert.equal(manual.pending.size, 1);
  manual.tick();
  assert.equal(calls, 2);
  stop();
  resolve();
  await settle();
  assert.equal(manual.pending.size, 0);
});
test('unmount during an outstanding reply never restarts the poll', async () => {
  const manual = clock();
  let resolve!: () => void;
  const stop = serialPolling(
    () =>
      new Promise<void>((r) => {
        resolve = r;
      }),
    1000,
    manual.timers,
  );
  stop();
  resolve();
  await settle();
  assert.equal(manual.pending.size, 0);
});
test('a failed reply schedules a bounded retry that cleanup can cancel', async () => {
  const manual = clock();
  const stop = serialPolling(
    async () => {
      throw new Error('collector reconnecting');
    },
    1000,
    manual.timers,
  );
  await settle();
  assert.equal(manual.pending.size, 1);
  stop();
  assert.equal(manual.pending.size, 0);
});
