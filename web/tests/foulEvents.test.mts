import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../src/api.ts';
import {
  candidateEmittedTime,
  candidateEventTime,
  candidateRegionLabel,
  detectionProfileOf,
  foulActionLabel,
  matchesDetectorSnapshot,
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

test('profile artifact selection cannot reuse legacy or different experimental records', () => {
  const prepared = [
    { id: 'legacy', kind: 'video', label: 'legacy' },
    { id: 'pair', kind: 'video', label: 'pair', detection_profile: 'mvit-pair-v2' as const },
    { id: 'full', kind: 'video', label: 'full', detection_profile: 'mvit-full-v2' as const },
  ];
  assert.equal(detectionProfileOf(prepared[0]), 'legacy-v1');
  assert.deepEqual(profileArtifacts(null, prepared, 'foul-1', 'foul', 'mvit-full-v2'), [
    prepared[2],
  ]);
  assert.deepEqual(profileArtifacts(null, prepared, 'foul-1', 'foul', 'multidim-full-v2'), []);
  assert.deepEqual(
    profileArtifacts(job('other-case', 'mvit-full-v2'), prepared, 'foul-2', 'foul', 'mvit-full-v2'),
    [],
  );
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
  assert.equal(matchesDetectorSnapshot(current, snapshot), true);
  assert.equal(matchesDetectorSnapshot(previous, snapshot), false);
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

test('structured candidate timelines distinguish contact PTS from prompt PTS', () => {
  assert.equal(candidateEventTime(candidate), 4.05);
  assert.equal(candidateEmittedTime(candidate), 5.1);
  assert.equal(foulActionLabel(candidate.event.foul_details?.action), '铲球');
  assert.equal(candidate.event.evidence?.evidence_end_s, 5.1);
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

test('top-level candidate timing is preserved when localized evidence is absent', () => {
  const record = {
    ...candidate,
    event: { ...candidate.event, evidence: undefined, event_time_s: 3.25, emitted_time_s: 4.5 },
  };
  assert.equal(candidateEventTime(record), 3.25);
  assert.equal(candidateEmittedTime(record), 4.5);
  assert.equal(candidateRegionLabel(record), null);
});

test('prepared foul requests use a specific job and immutable result revision', async () => {
  const original = globalThis.fetch;
  const requests: { url: string; method: string; body: unknown }[] = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url: String(url), method: options?.method || 'GET', body: options?.body });
    return new Response('{}');
  };
  try {
    await api.foulPreparation();
    await api.job('job/name');
    await api.foulResult('result/name');
    await api.foulFrames('result/name', 'rev/with space', 500);
    assert.deepEqual(
      requests.map((request) => request.url),
      [
        '/api/foul/preparation',
        '/api/experiments/jobs/job%2Fname',
        '/api/foul/results/result%2Fname',
        '/api/foul/results/result%2Fname/frames?offset=500&limit=500&revision=rev%2Fwith%20space',
      ],
    );
    assert.ok(requests.every((request) => request.method === 'GET' && request.body === undefined));
  } finally {
    globalThis.fetch = original;
  }
});
