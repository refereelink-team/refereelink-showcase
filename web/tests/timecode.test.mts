import test from 'node:test';
import assert from 'node:assert/strict';
import { timecode } from '../src/components/timecode.ts';

test('shared tracking and calibration timecodes retain minute and second formatting', () => {
  for (const [seconds, expected] of [
    [0, '0:00'],
    [9.99, '0:09'],
    [59.999, '0:59'],
    [60, '1:00'],
    [125.5, '2:05'],
    [3600, '60:00'],
  ] as const)
    assert.equal(timecode(seconds), expected);
});

test('unavailable media duration is rendered as zero without invalid timecodes', () => {
  for (const seconds of [NaN, Infinity, -Infinity]) assert.equal(timecode(seconds), '0:00');
});
