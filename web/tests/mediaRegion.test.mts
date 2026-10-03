import test from 'node:test';
import assert from 'node:assert/strict';
import { regionForPresentation, type MediaRegion } from '../src/components/mediaRegion.ts';

const region: MediaRegion = { timeS: 3.834, box: [419, 297, 458, 393], width: 852, height: 480 };

test('a selected contact region follows the presented original frame only near its observed time', () => {
  assert.equal(regionForPresentation(region, 3.834, 852, 480), region);
  assert.equal(regionForPresentation(region, 3.9, 852, 480), region);
  assert.equal(regionForPresentation(region, 4.2, 852, 480), null);
  assert.equal(regionForPresentation(region, 0, 852, 480), null);
  assert.equal(regionForPresentation(region, null, 852, 480), null);
});

test('a composite output, resized source metadata or invalid geometry cannot reuse source coordinates', () => {
  assert.equal(regionForPresentation(region, 3.834, 1704, 528), null);
  assert.equal(regionForPresentation(region, 3.834, 0, 0), null);
  assert.equal(
    regionForPresentation({ ...region, box: [419, 297, 900, 393] }, 3.834, 852, 480),
    null,
  );
  assert.equal(regionForPresentation({ ...region, timeS: NaN }, 3.834, 852, 480), null);
});
