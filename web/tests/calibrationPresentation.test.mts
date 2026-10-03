import test from 'node:test';
import assert from 'node:assert/strict';
import type {
  CalibrationMetadata,
  CalibrationSnapshot,
  CalibrationTrack,
} from '../src/types/calibration.ts';
import {
  acceptSnapshot,
  calibrationFrameAt,
  calibrationIssues,
  labelOf,
  metadataMatches,
  snapshotIsBusy,
  trackMatches,
  canPrepareFullVideo,
  fullVideoBounds,
  createFullVideoPreparationCoordinator,
  validCalibrationBox,
} from '../src/components/calibration/calibrationPresentation.ts';

const track: CalibrationTrack = {
  track_id: 13,
  first_timestamp_ms: 0,
  last_timestamp_ms: 1000,
  observation_count: 30,
  quality_observation_count: 3,
  representative_frame_index: 15,
  representative_timestamp_ms: 500,
  representative_bbox: [10, 20, 40, 80],
  representative_quality_score: 0.8,
  label: 'ignore',
  sample_count: 3,
};
const snapshot: CalibrationSnapshot = {
  id: 'practice',
  revision: 8,
  status: 'idle',
  operation: 'validate',
  error: null,
  source_url: '/media/calibration.mp4',
  video_url: '/api/calibration/sessions/practice/video',
  metadata_url: '/api/calibration/sessions/practice/metadata',
  source: { duration_ms: 19300, fps: 30, width: 1920, height: 1080 },
  session: {
    state: 'review',
    ready: false,
    processing_progress: 1,
    observed_frames: 579,
    clip_id: 'clip',
    clip_start_ms: 0,
    clip_end_ms: 19000,
    clip_duration_ms: 19000,
    tracks: [track],
    validation_report: {
      passed: false,
      reasons: ['home_outfield_tracks_insufficient'],
      home_track_count: 1,
      away_track_count: 3,
      home_sample_count: 3,
      away_sample_count: 30,
      min_samples_per_track: 5,
      min_tracks_per_team: 3,
      home_eligible_track_count: 0,
      away_eligible_track_count: 3,
      excluded_track_ids: [13],
      goalkeeper_mapping_ready: false,
      referee_mapping_ready: false,
      leave_one_track_out_accuracy: null,
    },
  },
  labels: { '13': 'home_outfield' },
  auto_ignored_track_ids: [],
  demo: { ready: true, protected: true },
};
const metadata: CalibrationMetadata = {
  session_id: 'practice',
  clip_id: 'clip',
  fps: 30,
  width: 1920,
  height: 1080,
  frame_count: 3,
  duration_ms: 100,
  tracks: [track],
  frames: [0, 33, 67].map((timestamp_ms, frame_index) => ({
    timestamp_ms,
    frame_index,
    players: [],
  })),
};

test('stale polling cannot roll back labels or leak a different session into the practice', () => {
  assert.equal(
    acceptSnapshot(snapshot, { ...snapshot, revision: 7, labels: {} }, 'practice'),
    snapshot,
  );
  assert.throws(() => acceptSnapshot(snapshot, { ...snapshot, id: 'demo' }, 'practice'), /不匹配/);
  const progress = { ...snapshot, status: 'running' };
  assert.equal(acceptSnapshot(snapshot, progress, 'practice'), progress);
  assert.equal(snapshotIsBusy(progress), true);
  assert.equal(snapshotIsBusy({ ...snapshot, status: 'queued' }), true);
  assert.equal(snapshotIsBusy(snapshot), false);
  assert.equal(snapshotIsBusy({ ...snapshot, status: 'error' }), false);
});
test('a manual role correction overrides initial auto-ignore without hiding its quality limits', () => {
  assert.equal(labelOf(track, snapshot), 'home_outfield');
  assert.equal(trackMatches(track, 'home', snapshot), true);
  assert.equal(trackMatches(track, 'weak', snapshot), true);
  assert.equal(trackMatches(track, 'ignored', snapshot), false);
  assert.equal(trackMatches(track, 'unlabelled', snapshot), false);
  const corrected = { ...snapshot, labels: { '13': 'referee' as const } };
  assert.equal(trackMatches(track, 'roles', corrected), true);
  assert.equal(trackMatches(track, 'home', corrected), false);
});
test('failed validation links exact excluded players and their team group without mutating labels', () => {
  const before = structuredClone(snapshot);
  const issues = calibrationIssues(snapshot);
  assert.deepEqual(
    issues.map((issue) => ({ filter: issue.filter, ids: issue.ids })),
    [
      { filter: 'home', ids: [13] },
      { filter: 'weak', ids: [13] },
    ],
  );
  assert.deepEqual(snapshot, before);
  assert.equal(snapshot.demo.ready, true);
  assert.equal(snapshot.session.ready, false);
});
test('clip metadata from an older preparation never overlays the current video', () => {
  assert.equal(metadataMatches(metadata, snapshot), true);
  assert.equal(metadataMatches({ ...metadata, clip_id: 'old' }, snapshot), false);
  assert.equal(metadataMatches({ ...metadata, session_id: 'demo' }, snapshot), false);
});
test('video time chooses only recorded prior frames and rejects gaps and times outside the clip', () => {
  assert.equal(calibrationFrameAt(metadata, 0.05)?.frame_index, 1);
  assert.equal(calibrationFrameAt(metadata, -0.01), null);
  assert.equal(calibrationFrameAt(metadata, 0.5), null);
  assert.equal(calibrationFrameAt(metadata, NaN), null);
  assert.equal(calibrationFrameAt({ ...metadata, frames: [] }, 0), null);
});
test('whole source bounds are exact, reject invalid or overlong media, and never truncate', () => {
  assert.deepEqual(fullVideoBounds(snapshot), [0, 19300]);
  for (const duration_ms of [0, NaN, Infinity]) {
    assert.throws(
      () => fullVideoBounds({ ...snapshot, source: { ...snapshot.source, duration_ms } }),
      /不可用/,
    );
  }
  assert.throws(
    () => fullVideoBounds({ ...snapshot, source: { ...snapshot.source, duration_ms: 60001 } }),
    /60/,
  );
});
test('source-pixel overlays reject broken and off-frame boxes', () => {
  assert.equal(validCalibrationBox([10, 20, 40, 80], 1920, 1080), true);
  assert.equal(validCalibrationBox([40, 20, 10, 80], 1920, 1080), false);
  assert.equal(validCalibrationBox([0, 0, 1921, 1080], 1920, 1080), false);
  assert.equal(validCalibrationBox([0, 0, NaN, 80], 1920, 1080), false);
});

test('a successful team check still explains retained excluded labels without implying optional roles passed', () => {
  const passed = {
    ...snapshot,
    session: {
      ...snapshot.session,
      ready: true,
      validation_report: { ...snapshot.session.validation_report!, passed: true, reasons: [] },
    },
  };
  assert.deepEqual(
    calibrationIssues(passed).map((issue) => ({ filter: issue.filter, ids: issue.ids })),
    [{ filter: 'weak', ids: [13] }],
  );
  assert.equal(labelOf(track, passed), 'home_outfield');
  assert.equal(passed.session.validation_report.goalkeeper_mapping_ready, false);
});

function emptySession() {
  return {
    ...snapshot,
    operation: null,
    labels: {},
    session: {
      ...snapshot.session,
      clip_id: null,
      tracks: [],
      validation_report: null,
      ready: false,
    },
  };
}
test('entry prepares only an active fresh session and preserves every restored session state', () => {
  const fresh = emptySession();
  assert.equal(canPrepareFullVideo(fresh, true), true);
  assert.equal(canPrepareFullVideo(fresh, false), false);
  for (const status of ['queued', 'running', 'error'])
    assert.equal(canPrepareFullVideo({ ...fresh, status }, true), false);
  assert.equal(canPrepareFullVideo(snapshot, true), false);
  assert.equal(canPrepareFullVideo({ ...fresh, labels: snapshot.labels }, true), false);
  assert.equal(
    canPrepareFullVideo({ ...fresh, session: { ...fresh.session, tracks: [track] } }, true),
    false,
  );
  assert.equal(
    canPrepareFullVideo(
      {
        ...fresh,
        session: { ...fresh.session, validation_report: snapshot.session.validation_report },
      },
      true,
    ),
    false,
  );
});
test('StrictMode and remount subscribers share one full-video request; pending retry also deduplicates', async () => {
  let count = 0;
  let resolve!: (value: CalibrationSnapshot) => void;
  const coordinator = createFullVideoPreparationCoordinator((value, start, end) => {
    count++;
    assert.equal(value.revision, snapshot.revision);
    assert.deepEqual([start, end], [0, 19300]);
    return new Promise((done) => {
      resolve = done;
    });
  });
  const first = coordinator.prepare(emptySession());
  assert.equal(coordinator.prepare(emptySession()), first);
  assert.equal(coordinator.prepare(emptySession(), true), first);
  await Promise.resolve();
  assert.equal(count, 1);
  resolve({ ...snapshot, status: 'queued' });
  await first;
  assert.equal(coordinator.prepare(emptySession()), first);
});
test('failed preparation remains failed through polls and remounts until explicit retry; reset renews once', async () => {
  let count = 0;
  const coordinator = createFullVideoPreparationCoordinator(async () => {
    count++;
    if (count === 1) throw new Error('connection failed');
    return { ...snapshot, status: 'queued' };
  });
  const first = coordinator.prepare(emptySession());
  await assert.rejects(first, /connection failed/);
  await assert.rejects(coordinator.prepare(emptySession()), /connection failed/);
  await assert.rejects(
    coordinator.prepare({ ...emptySession(), revision: 9 }),
    /connection failed/,
  );
  assert.equal(count, 1);
  await coordinator.prepare(emptySession(), true);
  assert.equal(count, 2);
  coordinator.clear(snapshot.id);
  const reset = coordinator.prepare({ ...emptySession(), revision: 10 });
  assert.equal(coordinator.prepare({ ...emptySession(), revision: 10 }), reset);
  await reset;
  assert.equal(count, 3);
});
