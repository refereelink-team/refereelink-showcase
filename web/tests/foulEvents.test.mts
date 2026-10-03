import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../src/api.ts';
import {
  candidateEmittedTime,
  candidateEventTime,
  candidateRegionLabel,
  detectionProfileOf,
  foulActionLabel,
  latestProfileJob,
  parseCandidateEvents,
  profileArtifacts,
  targetLabel,
} from '../src/events.ts';
import type { CandidateRecord } from '../src/events.ts';
import type { Job } from '../src/types.ts';

const candidate: CandidateRecord = {
  media_pts_seconds: 5.1,
  source_frame_id: 153,
  event: {
    id: 'episode-1',
    event_type: 'foul_candidate',
    confidence: 0.72,
    reviewed: false,
    foul_details: { action: 'Tackle' },
    evidence: {
      event_time_s: 4.05,
      emitted_time_s: 5.1,
      evidence_start_s: 3.2,
      evidence_end_s: 5.1,
      region_xyxy: [300, 320, 550, 470],
      source_width: 852,
      source_height: 480,
    },
  },
};
function job(id: string, profile?: Job['detection_profile']): Job {
  return {
    id,
    kind: 'foul',
    case_id: 'foul-1',
    status: 'completed',
    progress: 1,
    artifacts: [],
    detection_profile: profile,
  };
}

test('new profile selection cannot reuse legacy records or a different experimental branch', () => {
  const jobs = [job('legacy'), job('pair', 'mvit-pair-v2'), job('full', 'mvit-full-v2')];
  assert.equal(detectionProfileOf(jobs[0]), 'legacy-v1');
  assert.equal(latestProfileJob(jobs, 'foul', 'foul-1', 'mvit-full-v2')?.id, 'full');
  assert.equal(latestProfileJob(jobs, 'foul', 'foul-1', 'legacy-v1')?.id, 'legacy');
  assert.equal(latestProfileJob(jobs, 'foul', 'foul-1', 'multidim-full-v2'), undefined);
  assert.equal(latestProfileJob(jobs, 'foul', 'foul-2', 'mvit-pair-v2'), undefined);
});

test('accepted contact results match the current code, model and configuration snapshot', () => {
  const snapshot = {
    config_sha256: 'config',
    model_sha256: 'model',
    core_manifest_sha256: 'core',
    external_source_sha256: 'author',
  };
  const current = { ...job('current', 'mvit-contact-v3'), detector_fingerprint: snapshot };
  const previous = {
    ...job('previous', 'mvit-contact-v3'),
    detector_fingerprint: { ...snapshot, core_manifest_sha256: 'old-core' },
  };
  assert.equal(
    latestProfileJob([previous, current], 'foul', 'foul-1', 'mvit-contact-v3', snapshot)?.id,
    'current',
  );
  assert.equal(latestProfileJob([current], 'foul', 'foul-1', 'mvit-contact-v3'), undefined);
  assert.deepEqual(
    profileArtifacts(previous, [], 'foul-1', 'foul', 'mvit-contact-v3', snapshot),
    [],
  );
  const prepared = {
    id: 'accepted',
    kind: 'video',
    label: 'contact',
    detection_profile: 'mvit-contact-v3' as const,
    detector_fingerprint: snapshot,
  };
  assert.deepEqual(
    profileArtifacts(null, [prepared], 'foul-1', 'foul', 'mvit-contact-v3', snapshot),
    [prepared],
  );
  assert.deepEqual(profileArtifacts(null, [prepared], 'foul-1', 'foul', 'mvit-contact-v3'), []);
});

test('selected empty, pending or failed jobs never substitute prepared results', () => {
  const prepared = [
    { id: 'old', kind: 'video', label: 'old' },
    { id: 'newer', kind: 'video', label: 'newer', detection_profile: 'mvit-full-v2' as const },
  ];
  assert.deepEqual(profileArtifacts(null, prepared, 'foul-1', 'foul', 'mvit-full-v2'), [
    prepared[1],
  ]);
  for (const status of ['queued', 'running', 'failed', 'completed']) {
    const selected = { ...job('own', 'mvit-full-v2'), status };
    assert.deepEqual(profileArtifacts(selected, prepared, 'foul-1', 'foul', 'mvit-full-v2'), []);
  }
  assert.deepEqual(
    profileArtifacts(job('other', 'mvit-pair-v2'), prepared, 'foul-1', 'foul', 'mvit-full-v2'),
    [],
  );
  assert.deepEqual(
    profileArtifacts(job('other', 'mvit-full-v2'), prepared, 'foul-2', 'foul', 'mvit-full-v2'),
    [],
  );
});

test('timeline points at the interaction while prompt time remains separately recorded', () => {
  const earlier = {
    ...candidate,
    event: {
      ...candidate.event,
      id: 'earlier',
      evidence: { event_time_s: 1.1, emitted_time_s: 1.7 },
    },
  };
  const records = parseCandidateEvents([candidate, earlier].map(JSON.stringify).join('\n'));
  assert.equal(records[0].event.id, 'earlier');
  assert.equal(candidateEventTime(records[1]), 4.05);
  assert.equal(candidateEmittedTime(records[1]), 5.1);
  assert.equal(foulActionLabel(records[1].event.foul_details?.action), '铲球');
  assert.equal(records[1].event.evidence?.evidence_end_s, 5.1);
});

test('historical event PTS still works without invented prompt times or identities', () => {
  const old = { ...candidate, event: { ...candidate.event, evidence: undefined } };
  assert.equal(candidateEventTime(old), 5.1);
  assert.equal(candidateEmittedTime(old), null);
  assert.equal(candidateRegionLabel(old), null);
  assert.equal(foulActionLabel(null), '动作待确认');
  assert.equal(targetLabel({ track_id: null, team: 'home' }), '身份待确认');
  assert.equal(targetLabel({ track_id: 22, team: 'unknown', role: 'outfield' }), 'U-P#22');
  assert.equal(targetLabel({ track_id: 10, team: 'home', role: 'unknown' }), 'H-?#10');
});

test('region descriptions use observed image geometry and reject broken coordinates', () => {
  assert.equal(candidateRegionLabel(candidate), '画面中下部');
  const invalid = {
    ...candidate,
    event: {
      ...candidate.event,
      evidence: {
        ...candidate.event.evidence,
        region_xyxy: [1, 1, 999, 10] as [number, number, number, number],
      },
    },
  };
  assert.equal(candidateRegionLabel(invalid), null);
});

test('causal event records reject prompts before their recorded interaction', () => {
  const invalid = {
    ...candidate,
    event: { ...candidate.event, evidence: { event_time_s: 4, emitted_time_s: 3 } },
  };
  assert.throws(() => parseCandidateEvents(JSON.stringify(invalid)), /候选记录格式错误/);
});

test('job requests carry an explicit experiment profile while old callers remain compatible', async () => {
  const original = globalThis.fetch;
  const bodies: Record<string, unknown>[] = [];
  globalThis.fetch = async (_url, options) => {
    bodies.push(JSON.parse(String(options?.body)));
    return new Response('{}', { headers: { 'Content-Type': 'application/json' } });
  };
  try {
    await api.createJob('foul', 'foul-1', 'mvit-pair-v2');
    await api.createJob('tracking', 'calibration');
    assert.deepEqual(bodies[0], {
      kind: 'foul',
      case_id: 'foul-1',
      detection_profile: 'mvit-pair-v2',
    });
    assert.deepEqual(bodies[1], { kind: 'tracking', case_id: 'calibration' });
  } finally {
    globalThis.fetch = original;
  }
});
