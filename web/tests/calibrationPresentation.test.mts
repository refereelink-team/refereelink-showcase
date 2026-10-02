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
  validateSegment,
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
test('native source duration bounds the segment and rejects reversed or overlong selections', () => {
  assert.equal(validateSegment(0, 19313, 19313), null);
  assert.match(validateSegment(0, 21451, 19313)!, /超出/);
  assert.match(validateSegment(500, 500, 19313)!, /晚于/);
  assert.match(validateSegment(0, 60001, 120000)!, /60/);
  assert.match(validateSegment(NaN, 1000, 19313)!, /晚于/);
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
