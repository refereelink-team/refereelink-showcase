import test from 'node:test';
import assert from 'node:assert/strict';
import {
  collectTrackingFrames,
  entityColor,
  entityKey,
  fieldPoint,
  frameIndexAtTime,
  pitchDisplayPoint,
  recordedTrajectory,
  validBox,
  usableProjection,
  trackingArtifactId,
  caseWithResult,
} from '../src/components/tracking/trackingPresentation.ts';
import type {
  TrackedEntity,
  TrackingFrame,
  TrackingFramesPage,
  TrackingPitch,
  TrackingResult,
} from '../src/types/tracking.ts';
const pitch: TrackingPitch = {
  length_m: 120,
  width_m: 70,
  penalty_area_length_m: 20.15,
  penalty_area_width_m: 41,
  goal_area_length_m: 5.5,
  goal_area_width_m: 18.32,
  center_circle_radius_m: 9.15,
  penalty_spot_distance_m: 11,
  goal_width_m: 7.32,
};
function player(values: Partial<TrackedEntity> = {}): TrackedEntity {
  return {
    track_id: 4,
    entity_id: null,
    track_status: 'detected',
    missing_frames: 0,
    role: 'outfield',
    team: 'home',
    confidence: 0.9,
    team_confidence: 0.7,
    role_confidence: 0.8,
    bbox: [10, 20, 30, 80],
    field_x: 20,
    field_y: 10,
    velocity_x: null,
    velocity_y: null,
    ...values,
  };
}
function frame(time: number, entities = [player()], id = Math.round(time * 1000)): TrackingFrame {
  return {
    frame_id: id,
    source_pts_s: time,
    homography_status: 'fresh',
    players: entities,
    ball: null,
  };
}
const result: TrackingResult = {
  id: 'real-cuda-frames',
  revision: 'a'.repeat(64),
  case_id: 'calibration',
  job_id: null,
  source: {
    url: '/media/input/calibration.mp4',
    width: 1920,
    height: 1080,
    fps: 30,
    duration_seconds: 20,
    decoded_frames: 3,
  },
  pitch,
  frame_count: 3,
  frames_url: '/api/tracking/results/real-cuda-frames/frames',
  provenance: { device: 'cuda', live: false, recorded_at: null },
  metrics: {
    actual_fps: 6,
    latency_ms: 170,
    homography_available_frame_rate: 0.8,
    projected_player_rate: 0.7,
    unknown_role_rate: 0.1,
    unknown_team_rate: 0.2,
  },
};
function page(
  offset: number,
  frames: TrackingFrame[],
  next: number | null,
  revision = result.revision,
): TrackingFramesPage {
  return {
    result_id: result.id,
    revision,
    total: result.frame_count,
    offset,
    frames,
    next_offset: next,
  };
}
test('source PTS selection never takes a future frame or extrapolates a missing interval', () => {
  const frames = [frame(0), frame(0.04), frame(0.08), frame(0.5)];
  assert.equal(frameIndexAtTime(frames, -0.01, 25), -1);
  assert.equal(frameIndexAtTime(frames, 0.06, 25), 1);
  assert.equal(frameIndexAtTime(frames, 0.25, 25), -1);
  assert.equal(frameIndexAtTime(frames, 0.5, 25), 3);
  assert.equal(frameIndexAtTime(frames, 1, 25), -1);
});
test('projection uses response pitch units and rejects unknown and off-field values', () => {
  assert.deepEqual(fieldPoint(player({ field_x: 119, field_y: 69 }), pitch), [119, 69]);
  assert.equal(fieldPoint(player({ field_x: 121 }), pitch), null);
  assert.equal(fieldPoint(player({ field_y: null }), pitch), null);
  assert.equal(fieldPoint(player({ field_x: NaN }), pitch), null);
});
const paintResult: TrackingResult = {
  ...result,
  pitch: { ...pitch, length_m: 105, width_m: 68 },
  provenance: {
    ...result.provenance,
    pitch_profile_id: 'source-informed105',
    paint_enabled: true,
  },
};
test('paint-registration pitch display matches footage halves without changing world coordinates', () => {
  const leftGoal: [number, number] = [105, 34];
  assert.deepEqual(pitchDisplayPoint(leftGoal, paintResult), [0, 34]);
  assert.deepEqual(pitchDisplayPoint([0, 34], paintResult), [105, 34]);
  assert.deepEqual(pitchDisplayPoint([52.5, 34], paintResult), [52.5, 34]);
  assert.deepEqual(leftGoal, [105, 34]);
  const a: [number, number] = [95, 10],
    b: [number, number] = [91, 13];
  const displayedA = pitchDisplayPoint(a, paintResult),
    displayedB = pitchDisplayPoint(b, paintResult);
  assert.equal(Math.hypot(displayedB[0] - displayedA[0], displayedB[1] - displayedA[1]), 5);
});
test('legacy, unregistered and unknown profiles retain their recorded display orientation', () => {
  const point: [number, number] = [20, 10];
  for (const provenance of [
    result.provenance,
    { ...paintResult.provenance, paint_enabled: false },
    { ...paintResult.provenance, pitch_profile_id: 'legacy' },
    { ...paintResult.provenance, pitch_profile_id: 'unknown' },
  ]) {
    assert.deepEqual(pitchDisplayPoint(point, { ...paintResult, provenance }), point);
  }
});
test('players, ball and recorded trails share one display transform and preserve epoch gaps', () => {
  const frames = [frame(0), frame(0.04), frame(0.08), frame(0.12)];
  frames[0].geometry_epoch = frames[1].geometry_epoch = 1;
  frames[2].geometry_epoch = frames[3].geometry_epoch = 2;
  frames.forEach((value, n) => {
    value.players[0].field_x = 95 - n;
  });
  const saved = JSON.stringify(frames);
  const segments = recordedTrajectory(frames, 3, 'track:4', paintResult.pitch);
  assert.deepEqual(
    segments.map((points) => points.map((point) => pitchDisplayPoint(point, paintResult))),
    [
      [
        [10, 10],
        [11, 10],
      ],
      [
        [12, 10],
        [13, 10],
      ],
    ],
  );
  const playerPoint = fieldPoint(frames[3].players[0], paintResult.pitch)!;
  const ballPoint = fieldPoint({ field_x: 92, field_y: 10 }, paintResult.pitch)!;
  assert.deepEqual(pitchDisplayPoint(playerPoint, paintResult), [13, 10]);
  assert.deepEqual(pitchDisplayPoint(ballPoint, paintResult), [13, 10]);
  assert.equal(JSON.stringify(frames), saved);
});
test('source-pixel boxes stay inside the original video and reject invalid geometry', () => {
  assert.equal(validBox([10, 10, 20, 20], 1920, 1080), true);
  assert.equal(validBox([10, 10, 1921, 20], 1920, 1080), false);
  assert.equal(validBox([10, 10, 5, 20], 1920, 1080), false);
  assert.equal(validBox(null, 1920, 1080), false);
});
test('unknown entities do not acquire persistent invented identities', () => {
  const observation = player({ track_id: null, entity_id: null, team: 'unknown' });
  assert.notEqual(entityKey(observation, 1, 0), entityKey(observation, 2, 0));
  assert.equal(entityColor(observation), '#c4cbd7');
  assert.equal(entityColor(player({ role: 'referee' })), '#f4ba53');
});
test('trajectory contains only recorded coordinates and splits observation gaps', () => {
  const frames = [
    frame(0, [player({ field_x: 1 })]),
    frame(0.04, [player({ field_x: 2 })]),
    frame(0.08, []),
    frame(0.12, [player({ field_x: 4 })]),
    frame(0.16, [player({ field_x: 5 })]),
  ];
  assert.deepEqual(recordedTrajectory(frames, 4, 'track:4', pitch), [
    [
      [1, 10],
      [2, 10],
    ],
    [
      [4, 10],
      [5, 10],
    ],
  ]);
  assert.deepEqual(recordedTrajectory(frames, 4, 'observation:1:0', pitch), []);
});
test('complete pagination preserves exact source PTS and revision', async () => {
  const offsets: number[] = [];
  const frames = await collectTrackingFrames(
    result,
    async (offset) => {
      offsets.push(offset);
      return offset ? page(2, [frame(0.08)], null) : page(0, [frame(0), frame(0.04)], 2);
    },
    () => true,
  );
  assert.deepEqual(offsets, [0, 2]);
  assert.deepEqual(
    frames?.map((value) => value.source_pts_s),
    [0, 0.04, 0.08],
  );
});
test('mixed result revisions never become renderable records', async () => {
  await assert.rejects(
    collectTrackingFrames(
      result,
      async () => page(0, [frame(0)], null, 'b'.repeat(64)),
      () => true,
    ),
    /不匹配/,
  );
});
test('incomplete or nonmonotonic records fail instead of filling missing data', async () => {
  await assert.rejects(
    collectTrackingFrames(
      result,
      async () => page(0, [frame(0)], null),
      () => true,
    ),
    /不完整/,
  );
  await assert.rejects(
    collectTrackingFrames(
      result,
      async () => page(0, [frame(0.04), frame(0), frame(0.08)], null),
      () => true,
    ),
    /时间记录无效/,
  );
});
test('late response from a discarded case is ignored before validation or progress', async () => {
  let current = true,
    progress = false;
  const frames = await collectTrackingFrames(
    result,
    async () => {
      current = false;
      return page(0, [], null, 'invalid');
    },
    () => current,
    () => {
      progress = true;
    },
  );
  assert.equal(frames, null);
  assert.equal(progress, false);
});
test('repeating pagination is rejected rather than entering an infinite request loop', async () => {
  await assert.rejects(
    collectTrackingFrames(
      result,
      async () => page(0, [frame(0)], 0),
      () => true,
    ),
    /分页无效/,
  );
});

test('stale transforms suppress projected points and break recorded trails', () => {
  const frames = [
    frame(0),
    frame(0.04),
    { ...frame(0.08), homography_status: 'stale' },
    frame(0.12),
    frame(0.16),
  ];
  assert.equal(usableProjection(frames[0]), true);
  assert.equal(usableProjection({ ...frames[0], homography_status: 'reused' }), true);
  assert.equal(usableProjection(frames[2]), false);
  assert.equal(usableProjection(null), false);
  assert.equal(recordedTrajectory(frames, 4, 'track:4', pitch).length, 2);
});
test('completed jobs resolve their own immutable frame artifact instead of a newer case result', () => {
  const job = {
    id: 'own-job',
    kind: 'tracking' as const,
    case_id: 'calibration',
    status: 'completed',
    progress: 1,
    artifacts: [
      {
        id: 'own-job-frame-states',
        kind: 'data',
        mode: 'projection_only',
        label: 'frame-states.jsonl',
      },
      { id: 'video', kind: 'video', label: 'annotated.mp4' },
    ],
  };
  assert.equal(trackingArtifactId(job), 'own-job-frame-states');
  assert.throws(() => trackingArtifactId({ ...job, artifacts: [] }), /未提供/);
});

test('a completed immutable result can recover from an initial case lookup failure', () => {
  const restored = caseWithResult(null, result, '/media/posters/calibration.jpg');
  assert.equal(restored.case_id, result.case_id);
  assert.equal(restored.source_url, result.source.url);
  assert.equal(restored.duration_seconds, result.source.duration_seconds);
  assert.equal(restored.latest_result, result);
  assert.equal(restored.active_job, null);
});

test('trajectory never connects separately registered coordinate epochs', () => {
  const frames = [frame(0), frame(0.04), frame(0.08), frame(0.12)];
  frames[0].geometry_epoch = frames[1].geometry_epoch = 1;
  frames[2].geometry_epoch = frames[3].geometry_epoch = 2;
  assert.deepEqual(recordedTrajectory(frames, 3, 'track:4', pitch), [
    [
      [20, 10],
      [20, 10],
    ],
    [
      [20, 10],
      [20, 10],
    ],
  ]);
});
