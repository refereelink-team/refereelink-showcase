import test from 'node:test';
import assert from 'node:assert/strict';
import {
  collectFoulFrames,
  emittedCandidateAtTime,
  emittedCandidatesAtTime,
  foulFrameArtifact,
  foulFrameAtTime,
  observedPlayer,
  foulPlayerKey,
  foulTargetMatches,
  foulRegionsAtTime,
  newFoulReplayCycle,
  restartFoulReplayCycle,
  advanceFoulReplayCycle,
} from '../src/components/foul/foulPresentation.ts';
import type { FoulFrame, FoulResult, FoulPlayer } from '../src/types/foul.ts';
import type { CandidateRecord } from '../src/events.ts';

const frames: FoulFrame[] = [
  { frame_id: 1, time_s: 0, players: [] },
  { frame_id: 2, time_s: 0.034, players: [] },
  { frame_id: 3, time_s: 0.21, players: [] },
];
const result: FoulResult = {
  schema_version: 1,
  id: 'task-frame-states',
  revision: 'revision-1',
  case_id: 'foul-1',
  detection_profile: 'mvit-contact-v3',
  model_id: 'mvit-v2-local',
  frame_count: 3,
  source: {
    url: '/media/input/foul-1.mp4',
    width: 852,
    height: 480,
    fps: 30,
    duration_s: 1,
    sha256: 'source',
  },
  frames_url: '/frames',
  events: [],
};
const player: FoulPlayer = {
  track_id: 10,
  entity_id: 8,
  team: 'home',
  role: 'outfield',
  confidence: 0.9,
  track_status: 'detected',
  missing_frames: 0,
  bbox: [300, 200, 350, 300],
};
/** Synthetic timestamps and regions exercise replay mechanics without runtime annotations. */
function event(id: string, time: number, emitted?: number): CandidateRecord {
  return {
    media_pts_seconds: time,
    source_frame_id: 1,
    event: {
      id,
      event_type: 'foul_candidate',
      confidence: 0.6,
      reviewed: false,
      evidence: { event_time_s: time, emitted_time_s: emitted },
    },
  };
}

test('native frames use source PTS for backward seeks, variable cadence and missing-frame gaps', () => {
  assert.equal(foulFrameAtTime(frames, 0.034, 30), frames[1]);
  assert.equal(foulFrameAtTime(frames, 0.0338, 30), frames[1]); // millisecond metadata rounding
  assert.equal(foulFrameAtTime(frames, 0.029, 30), frames[0]);
  assert.equal(foulFrameAtTime(frames, 0.15, 30), null);
  assert.equal(foulFrameAtTime(frames, 0.22, 30), frames[2]);
  assert.equal(foulFrameAtTime(frames, 0, 30), frames[0]);
  assert.equal(foulFrameAtTime(frames, null, 30), null);
  assert.equal(foulFrameAtTime(frames, NaN, 30), null);
  assert.equal(foulFrameAtTime([{ frame_id: 1, time_s: 1, players: [] }], 0.9, 30), null);
});

test('alerts follow recorded emission PTS and disappear on rewind, without inventing legacy timing', () => {
  const first = event('first', 2.25, 2.6),
    second = event('second', 3.4, 3.7);
  assert.equal(emittedCandidateAtTime([first], 2.25), null);
  assert.equal(emittedCandidateAtTime([first], 2.6), first);
  assert.equal(emittedCandidateAtTime([first, second], 3.8), second);
  assert.equal(emittedCandidateAtTime([first], 2), null);
  assert.equal(emittedCandidateAtTime([first], 4.6), null);
  assert.equal(emittedCandidateAtTime([event('legacy', 2)], 2), null);
  const simultaneous = event('simultaneous', 2.1, 2.6);
  assert.deepEqual(emittedCandidatesAtTime([first, simultaneous], 2.6), [first, simultaneous]);
});

test('contact boxes follow contact PTS without event selection or early alert disclosure', () => {
  const contact = event('contact', 2.25, 2.6);
  const evidence = contact.event.evidence!;
  evidence.region_xyxy = [120, 80, 180, 160];
  evidence.source_width = 852;
  evidence.source_height = 480;
  const records = [contact, event('unlocalized', 2.25, 2.6)];
  const boxes = (time: number | null, width = 852, height = 480) =>
    foulRegionsAtTime(records, time, width, height);
  assert.deepEqual(
    boxes(2.25).map(({ id, region }) => [id, region.box]),
    [['contact', evidence.region_xyxy]],
  );
  assert.equal(emittedCandidateAtTime(records, 2.25), null);
  assert.deepEqual(advanceFoulReplayCycle(newFoulReplayCycle(), records, 2.25, 2.25).revealed, []);
  assert.deepEqual(boxes(2.6), []); // A contact box is not carried onto a later moving frame.
  assert.deepEqual(boxes(0), []); // New loop clears the contact marker.
  assert.equal(boxes(2.25).length, 1); // The next loop and backward review restore it.
  assert.deepEqual(boxes(null), []); // Seeking has no decoded presentation yet.
  assert.deepEqual(boxes(2.25, 1280, 720), []);
  evidence.region_xyxy = [120, 80, 900, 160];
  assert.deepEqual(boxes(2.25), []);
});

test('frame and event artifacts stay in one task instead of mixing two latest files', () => {
  const candidates = { id: 'new-candidate-events', kind: 'events', label: 'events' };
  const matching = { id: 'new-frame-states', kind: 'data', label: 'frames' };
  assert.equal(
    foulFrameArtifact(
      [{ id: 'old-frame-states', kind: 'data', label: 'frames' }, matching],
      candidates,
    ),
    matching,
  );
  assert.equal(
    foulFrameArtifact([{ id: 'old-frame-states', kind: 'data', label: 'frames' }], candidates),
    undefined,
  );
  assert.equal(foulFrameArtifact([matching]), undefined);
});

test('two replay loops clear events, reject late tail frames and reveal only arrived emissions', () => {
  const records = [event('first', 2.25, 2.6), event('second', 3.4, 3.7), event('legacy', 1)];
  let state = newFoulReplayCycle();
  state = advanceFoulReplayCycle(state, records, 2.5, 2.5);
  assert.deepEqual(state.revealed, []);
  state = advanceFoulReplayCycle(state, records, 2.6, 2.6);
  assert.deepEqual(state.revealed, ['first']);
  // Paused evidence review precedes emission but retains this cycle's event.
  state = advanceFoulReplayCycle(state, records, 2.25, 2.25);
  assert.deepEqual(state.revealed, ['first']);
  state = advanceFoulReplayCycle(state, records, 4.3, 4.3);
  assert.deepEqual(state.revealed, ['first', 'second']);
  for (let cycle = 1; cycle <= 2; cycle++) {
    state = restartFoulReplayCycle(state);
    assert.equal(state.epoch, cycle);
    assert.deepEqual(state.revealed, []);
    assert.equal(advanceFoulReplayCycle(state, records, 4.3, 0), state);
    state = advanceFoulReplayCycle(state, records, 0, 0);
    assert.deepEqual(state.revealed, []);
    assert.equal(advanceFoulReplayCycle(state, records, 4.3, 0.03), state);
    state = advanceFoulReplayCycle(state, records, 2.6, 2.6);
    assert.deepEqual(state.revealed, ['first']);
    state = advanceFoulReplayCycle(state, records, 4.3, 4.3);
  }
  const wrapped = advanceFoulReplayCycle(state, records, 0, 0);
  assert.equal(wrapped.epoch, 3); // fallback when native loop doesn't emit ended
  assert.deepEqual(wrapped.revealed, []);
});

test('only observed boxes render; missing players and invalid source geometry remain hidden', () => {
  assert.equal(observedPlayer(player, 852, 480), true);
  assert.equal(observedPlayer({ ...player, track_status: 'predicted' }, 852, 480), false);
  assert.equal(observedPlayer({ ...player, missing_frames: 1 }, 852, 480), false);
  assert.equal(observedPlayer({ ...player, bbox: [300, 200, 900, 300] }, 852, 480), false);
  assert.equal(foulPlayerKey({ ...player, track_id: null, entity_id: null }), null);
  assert.equal(foulPlayerKey(player), 'track:10');
  assert.equal(foulPlayerKey({ ...player, track_id: 11 }), 'track:11');
  const duplicate = { ...player, track_id: 11 };
  assert.equal(
    foulTargetMatches({ track_id: 10, entity_id: 8 }, duplicate, [player, duplicate]),
    false,
  );
  assert.equal(
    foulTargetMatches({ track_id: 10, entity_id: 8 }, player, [player, duplicate]),
    true,
  );
  assert.equal(
    foulTargetMatches({ track_id: null, entity_id: 8 }, player, [player, duplicate]),
    false,
  );
  assert.equal(foulTargetMatches({ track_id: null, entity_id: 8 }, player, [player]), true);
});

test('pagination is revision bound, ordered and complete, and stops when its case becomes obsolete', async () => {
  const good = async (offset: number) => ({
    result_id: result.id,
    revision: result.revision,
    offset,
    total: 3,
    next_offset: offset === 0 ? 2 : null,
    frames: offset === 0 ? frames.slice(0, 2) : frames.slice(2),
  });
  assert.deepEqual(await collectFoulFrames(result, good), frames);
  await assert.rejects(
    collectFoulFrames(result, async (offset) => ({ ...(await good(offset)), revision: 'old' })),
    /不匹配/,
  );
  await assert.rejects(
    collectFoulFrames(result, async (offset) => ({ ...(await good(offset)), next_offset: null })),
    /不完整/,
  );
  await assert.rejects(
    collectFoulFrames(result, async (offset) => ({ ...(await good(offset)), frames: [] })),
    /不匹配/,
  );
  await assert.rejects(
    collectFoulFrames(result, async (offset) => ({
      ...(await good(offset)),
      frames: [frames[1], frames[0]],
    })),
    /时间戳/,
  );
  let current = true;
  const calls: number[] = [];
  assert.deepEqual(
    await collectFoulFrames(
      result,
      async (offset) => {
        calls.push(offset);
        current = false;
        return good(offset);
      },
      () => current,
    ),
    [],
  );
  assert.deepEqual(calls, [0]);
});
