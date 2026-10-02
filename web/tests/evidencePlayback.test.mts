import test from 'node:test';
import assert from 'node:assert/strict';
import {
  commonTime,
  containedMediaBounds,
  decisionFocusTime,
  normalizedFocusRect,
  spatialFocusAtTime,
  localizationTier,
  localizationWindow,
  loadedPausedMediaTime,
  localTime,
  mediaPosition,
  playbackClockTime,
  temporalFocusStrength,
  timelineDuration,
  timelinePercent,
  viewRange,
} from '../src/components/multiview/evidencePlayback.ts';
import type {
  EvidenceView,
  LocalizationBox,
  MultiviewCase,
  MultiviewDecision,
} from '../src/types/multiview.ts';

const camera = (id: string, offset: number): EvidenceView => ({
  camera_id: id,
  display_name: id,
  role: 'main',
  sync_offset_ms: offset,
  quality: 'test',
  media_url: '/test.mp4',
  media_kind: 'video',
});
const box: LocalizationBox = {
  rect: [10, 20, 30, 40],
  score: 0.8,
  source: 'gradcam',
  active_start_s: 2,
  active_end_s: 3,
  peak_s: 2.5,
  temporal_source: 'gradcam',
};

test('offsets preserve one common time across positive and negative camera clocks', () => {
  const first = camera('main', 400);
  const second = camera('side', -600);
  assert.equal(localTime(2, first), 2.4);
  assert.equal(localTime(2, second), 1.4);
  assert.equal(commonTime(localTime(2, first), first), 2);
  assert.equal(commonTime(localTime(2, second), second), 2);
});

test('short and delayed cameras report their unavailable ranges without moving the shared clock', () => {
  const delayed = camera('delayed', -1000);
  assert.deepEqual(viewRange(delayed, 2), { startS: 1, endS: 3 });
  assert.deepEqual(mediaPosition(0.5, delayed, 2), { timeS: 0, state: 'before' });
  assert.deepEqual(mediaPosition(1.5, delayed, 2), { timeS: 0.5, state: 'active' });
  assert.deepEqual(mediaPosition(4, delayed, 2), { timeS: 2, state: 'after' });
  assert.deepEqual(mediaPosition(1, delayed, Number.NaN), { timeS: 0, state: 'unavailable' });
  assert.equal(viewRange(delayed, 0), null);
});

test('shared timeline covers longer camera tails and clamps marks to real bounds', () => {
  assert.equal(timelineDuration([camera('a', 0), camera('b', -1000)], { a: 3, b: 5 }), 6);
  assert.equal(timelinePercent(-1, 6), 0);
  assert.equal(timelinePercent(8, 6), 100);
  assert.equal(timelinePercent(2, 0), 0);
});

test('explicit review intervals preserve provenance while missing responses invent no focus', () => {
  assert.equal(localizationWindow({ rect: [0, 0, 10, 10], score: 0.5, source: 'gradcam' }), null);
  assert.deepEqual(localizationWindow({ ...box, temporal_source: 'event_prior' }), {
    startS: 2,
    endS: 3,
    peakS: 2.5,
    source: 'event_prior',
  });
  assert.equal(localizationWindow({ ...box, temporal_source: null }), null);
  assert.equal(localizationWindow({ ...box, temporal_source: undefined }), null);
  assert.equal(localizationWindow({ ...box, active_start_s: null }), null);
  assert.equal(localizationWindow({ ...box, peak_s: null }), null);
});

test('temporal focus rejects invalid windows and hidden evidence without inventing a peak', () => {
  assert.deepEqual(localizationWindow(box), { startS: 2, endS: 3, peakS: 2.5, source: 'gradcam' });
  assert.equal(localizationWindow({ ...box, active_start_s: Number.NaN }), null);
  assert.equal(localizationWindow({ ...box, active_end_s: Infinity }), null);
  assert.equal(localizationWindow({ ...box, active_start_s: -1 }), null);
  assert.equal(localizationWindow({ ...box, active_start_s: 4 }), null);
  assert.equal(localizationWindow({ ...box, active_end_s: 2 }), null);
  assert.equal(localizationWindow({ ...box, peak_s: 10 }), null);
  assert.equal(localizationWindow({ ...box, display_tier: 'hidden' }), null);
  assert.equal(localizationWindow({ ...box, reliable: false }), null);
  // Spatial cautions do not invalidate an otherwise genuine temporal response.
  assert.notEqual(localizationWindow({ ...box, reliable: false, display_tier: 'caution' }), null);
});

test('attention fades outside its window and uses only genuine temporal score bins', () => {
  const window = localizationWindow(box);
  assert.ok(window);
  assert.equal(temporalFocusStrength(box, 0, window), 0);
  assert.equal(temporalFocusStrength(box, 5, window), 0);
  assert.equal(
    temporalFocusStrength(
      { ...box, temporal_bins: [{ start_s: 2, end_s: 3, score: 1 }] },
      2.5,
      window,
    ),
    1,
  );
  assert.equal(
    temporalFocusStrength(
      { ...box, temporal_bins: [{ start_s: 2, end_s: 3, score: 0 }] },
      2.5,
      window,
    ),
    0.65,
  );
  assert.equal(localizationTier({ ...box, reliable: false }), 'hidden');
  assert.equal(localizationTier({ ...box, display_tier: 'caution' }), 'caution');
});

test('loaded paused frames remain usable after late buffering events while seeks and new loads do not', () => {
  const loaded = { paused: true, seeking: false, readyState: 4, currentTime: 2.26 };
  assert.equal(loadedPausedMediaTime(loaded), 2.26);
  assert.equal(loadedPausedMediaTime({ ...loaded, readyState: 2, currentTime: 0 }), 0);
  assert.equal(loadedPausedMediaTime({ ...loaded, paused: false }), null);
  assert.equal(loadedPausedMediaTime({ ...loaded, seeking: true }), null);
  assert.equal(loadedPausedMediaTime({ ...loaded, readyState: 1 }), null);
  assert.equal(loadedPausedMediaTime({ ...loaded, currentTime: Number.NaN }), null);
});

test('overlay coordinates account for letterboxing and reject invalid geometry', () => {
  assert.deepEqual(containedMediaBounds(400, 400, 1600, 900), {
    left: 0,
    top: 87.5,
    width: 400,
    height: 225,
  });
  assert.equal(containedMediaBounds(400, 0, 1600, 900), null);
  assert.equal(containedMediaBounds(400, 400, Number.NaN, 900), null);
  assert.deepEqual(normalizedFocusRect([0, 0, 100, 100]), [0, 0, 100, 100]);
  assert.equal(normalizedFocusRect([10, 10, -1, 20]), null);
});

test('attribution preserves the exact model rectangle without presentation padding', () => {
  const rect: LocalizationBox['rect'] = [40.2, 36.4, 2.1, 4.3];
  assert.equal(normalizedFocusRect(rect), rect);
  assert.deepEqual(spatialFocusAtTime({ ...box, rect }, 2.5), { rect, source: 'legacy' });
  assert.equal(normalizedFocusRect([-1, 0, 10, 10]), null);
  assert.equal(normalizedFocusRect([95, 0, 10, 10]), null);
  assert.equal(normalizedFocusRect([0, 95, 10, 10]), null);
  assert.equal(normalizedFocusRect([0, 0, 0, 10]), null);
});

test('spatial samples follow the displayed media bin without extrapolating or requiring a peak', () => {
  const first: LocalizationBox['rect'] = [10, 20, 20, 30];
  const second: LocalizationBox['rect'] = [40, 25, 20, 30];
  const sampled: LocalizationBox = {
    ...box,
    active_start_s: null,
    active_end_s: null,
    peak_s: null,
    temporal_source: null,
    reliable: false,
    display_tier: 'caution',
    spatial_bins: [
      { start_s: 2, end_s: 2.5, rect: first, score: 0.8 },
      { start_s: 2.5, end_s: 3, rect: second, score: 0.7 },
    ],
  };
  assert.equal(localizationWindow(sampled), null);
  assert.deepEqual(spatialFocusAtTime(sampled, 2.49), { rect: first, source: 'sampled' });
  assert.deepEqual(spatialFocusAtTime(sampled, 2.5), { rect: second, source: 'sampled' });
  assert.equal(spatialFocusAtTime(sampled, 1.9), null);
  assert.equal(spatialFocusAtTime(sampled, 3), null);
  assert.equal(spatialFocusAtTime(sampled, Number.NaN), null);
  assert.equal(spatialFocusAtTime({ ...sampled, display_tier: 'hidden' }, 2.5), null);
  assert.deepEqual(spatialFocusAtTime({ ...sampled, temporal_source: 'event_prior' }, 2.5), {
    rect: second,
    source: 'sampled',
  });
  assert.equal(spatialFocusAtTime({ ...sampled, spatial_bins: [] }, 2.5), null);
  assert.equal(spatialFocusAtTime({ ...sampled, spatial_bins: null }, 2.5), null);
});

test('aggregate attribution uses a returned review interval without expanding geometry or unhiding a region', () => {
  const aggregate: LocalizationBox = { ...box, temporal_source: 'event_prior', spatial_bins: null };
  assert.deepEqual(spatialFocusAtTime(aggregate, 2.5), { rect: box.rect, source: 'legacy' });
  assert.equal(spatialFocusAtTime({ ...aggregate, spatial_bins: [] }, 2.5), null);
  assert.equal(spatialFocusAtTime({ ...aggregate, display_tier: 'hidden' }, 2.5), null);
  assert.ok(localizationWindow({ ...aggregate, display_tier: 'hidden' }));
});

test('spatial bins reject overlaps and invalid data instead of reverting to the static rectangle', () => {
  const first = { start_s: 2, end_s: 2.5, rect: box.rect, score: 0.8 };
  const second = { start_s: 2.75, end_s: 3, rect: box.rect, score: 0.6 };
  assert.equal(spatialFocusAtTime({ ...box, spatial_bins: [first, second] }, 2.6), null);
  assert.equal(
    spatialFocusAtTime({ ...box, spatial_bins: [first, { ...second, start_s: 2.4 }] }, 2.45),
    null,
  );
  assert.equal(
    spatialFocusAtTime({ ...box, spatial_bins: [{ ...first, start_s: -1 }] }, 2.1),
    null,
  );
  assert.equal(spatialFocusAtTime({ ...box, spatial_bins: [{ ...first, end_s: 2 }] }, 2.1), null);
  assert.equal(
    spatialFocusAtTime({ ...box, spatial_bins: [{ ...first, score: Number.NaN }] }, 2.1),
    null,
  );
  assert.equal(spatialFocusAtTime({ ...box, spatial_bins: [{ ...first, score: 0 }] }, 2.1), null);
  assert.equal(
    spatialFocusAtTime({ ...box, spatial_bins: [{ ...first, rect: [90, 20, 20, 30] }] }, 2.1),
    null,
  );
});

test('analysis focus uses an explicit model or review peak and only camera synchronization offsets', () => {
  const caseData = { videos: [camera('main', 500), camera('side', -400)] } as MultiviewCase;
  const decision = {
    localization: { main: box, side: { ...box, peak_s: 2.75 } },
    view_attention: [0.2, 0.8],
  } as MultiviewDecision;
  assert.equal(decisionFocusTime(caseData, decision), 3.15);
  assert.equal(
    decisionFocusTime(caseData, {
      ...decision,
      localization: { main: box, side: { ...box, peak_s: 2.75, temporal_source: 'event_prior' } },
    }),
    3.15,
  );
  assert.equal(
    decisionFocusTime(caseData, {
      ...decision,
      localization: { main: box, side: { ...box, display_tier: 'hidden' } },
    }),
    2,
  );
  assert.equal(
    decisionFocusTime(caseData, {
      ...decision,
      localization: {
        main: box,
        side: { ...box, peak_s: 2.75, temporal_source: 'event_prior', display_tier: 'hidden' },
      },
    }),
    3.15,
  );
});

test('analysis with no explicit temporal evidence does not guess a review time', () => {
  const caseData = { videos: [camera('main', 500)] } as MultiviewCase;
  assert.equal(decisionFocusTime(caseData, { localization: {} } as MultiviewDecision), null);
  assert.equal(
    decisionFocusTime(caseData, {
      localization: { main: { ...box, active_start_s: null, active_end_s: null } },
    } as MultiviewDecision),
    null,
  );
  assert.equal(
    decisionFocusTime(caseData, {
      localization: { main: { ...box, temporal_source: 'event_prior', peak_s: null } },
    } as MultiviewDecision),
    null,
  );
  assert.equal(
    decisionFocusTime(
      { videos: [camera('main', 5000)] } as MultiviewCase,
      {
        localization: { main: box },
      } as MultiviewDecision,
    ),
    null,
  );
});

test('an explicit review timestamp may focus a no-box result only with declared provenance', () => {
  const caseData = { videos: [camera('main', 500)] } as MultiviewCase;
  const decision = {
    localization: {},
    timestamp: 2.1,
    detail: {
      sampling: { uses_event_prior: true },
      temporal_localization: { timestamp_source: 'event_prior' },
    },
  } as unknown as MultiviewDecision;
  assert.equal(decisionFocusTime(caseData, decision), 2.1);
  assert.equal(decisionFocusTime(caseData, { ...decision, detail: {} }), null);
  assert.equal(decisionFocusTime(caseData, { ...decision, timestamp: null }), null);
  assert.equal(
    decisionFocusTime(caseData, { ...decision, detail: { sampling: { uses_event_prior: false } } }),
    null,
  );
});

test('reference stalls fall back to the independent clock without reversing the common playhead', () => {
  assert.equal(playbackClockTime(2.1, 2, 6, 2, 200), 2.1);
  assert.equal(playbackClockTime(2.1, 2, 6, 2.08, 20), 2.08);
  assert.equal(playbackClockTime(2.1, 2.09, 6, 2.08, 20), 2.09);
  assert.equal(playbackClockTime(4, 3.9, 6, 0.2, 20), 4);
  assert.equal(playbackClockTime(7, 5.9, 6, null, Infinity), 6);
});
