import type { Artifact, DetectionProfile, DetectorFingerprint, Job } from './types';

export interface InvolvedTarget {
  track_id: number | null;
  entity_id?: number | null;
  team?: string | null;
  role?: string | null;
}
export interface CandidateEvidence {
  source?: string;
  event_time_s?: number;
  emitted_time_s?: number;
  evidence_start_s?: number;
  evidence_end_s?: number;
  region_xyxy?: [number, number, number, number] | null;
  source_width?: number;
  source_height?: number;
  involved_targets?: InvolvedTarget[];
  detection_profile?: DetectionProfile;
  model_id?: string;
  offence_score?: number;
  action_score?: number;
  severity_score?: number;
}
export interface CandidateRecord {
  media_pts_seconds: number;
  source_frame_id: number;
  event: {
    id: string;
    event_type: 'foul_candidate';
    confidence: number;
    reviewed: boolean;
    foul_details?: {
      action?: string | null;
      severity?: string | null;
      offence_score?: number;
      action_score?: number;
      severity_score?: number;
    };
    event_time_s?: number;
    emitted_time_s?: number;
    evidence?: CandidateEvidence;
  };
}
export const foulActionLabels: Record<string, string> = {
  Holding: '拉扯',
  Tackle: '铲球',
  Tackling: '铲球',
  'Standing tackling': '站立抢断',
  'Standing tackle': '站立抢断',
  'Standing Tackle': '站立抢断',
  Pushing: '推搡',
  Challenge: '身体争抢',
  Dive: '假摔',
  'High leg': '抬脚过高',
  'High Leg': '抬脚过高',
  Elbowing: '肘击',
};
export function foulActionLabel(action?: string | null) {
  return action ? foulActionLabels[action] || action : '动作待确认';
}
export function detectionProfileOf(value?: Pick<Job | Artifact, 'detection_profile'> | null) {
  return value?.detection_profile || 'legacy-v1';
}
export function matchesDetectorSnapshot(
  value: Job | Artifact,
  expected?: DetectorFingerprint | null,
) {
  if (!expected) return true;
  const actual = value.detector_fingerprint;
  return Boolean(
    actual &&
    (
      ['config_sha256', 'model_sha256', 'core_manifest_sha256', 'external_source_sha256'] as const
    ).every((key) => actual[key] === expected[key]),
  );
}
export function latestProfileJob(
  jobs: Job[],
  kind: 'tracking' | 'foul',
  caseId: string,
  profile: DetectionProfile,
  fingerprint?: DetectorFingerprint | null,
) {
  if (kind === 'foul' && profile === 'mvit-contact-v3' && !fingerprint) return undefined;
  return jobs.find(
    (job) =>
      job.kind === kind &&
      job.case_id === caseId &&
      (kind !== 'foul' ||
        (detectionProfileOf(job) === profile && matchesDetectorSnapshot(job, fingerprint))),
  );
}
export function profileArtifacts(
  job: Job | null,
  prepared: Artifact[],
  caseId: string,
  kind: 'tracking' | 'foul',
  profile: DetectionProfile,
  fingerprint?: DetectorFingerprint | null,
) {
  if (kind === 'foul' && profile === 'mvit-contact-v3' && !fingerprint) return [];
  if (
    job &&
    (job.case_id !== caseId ||
      job.kind !== kind ||
      (kind === 'foul' &&
        (detectionProfileOf(job) !== profile || !matchesDetectorSnapshot(job, fingerprint))))
  )
    return [];
  const mode = kind === 'foul' ? 'foul_only' : 'projection_only';
  // A pending, failed or incomplete new task owns its own empty result set.
  // Prepared artifacts are available only before a task has been selected.
  const source = job ? job.artifacts || [] : [...prepared].reverse();
  return source.filter(
    (artifact) =>
      (!artifact.mode || artifact.mode === mode) &&
      (kind !== 'foul' ||
        (detectionProfileOf(artifact) === profile &&
          matchesDetectorSnapshot(artifact, fingerprint))),
  );
}
export function candidateEventTime(record: CandidateRecord) {
  return (
    record.event.evidence?.event_time_s ?? record.event.event_time_s ?? record.media_pts_seconds
  );
}
export function candidateEmittedTime(record: CandidateRecord) {
  return record.event.evidence?.emitted_time_s ?? record.event.emitted_time_s ?? null;
}
export function candidateRegionLabel(record: CandidateRecord) {
  const evidence = record.event.evidence,
    box = evidence?.region_xyxy;
  const width = evidence?.source_width,
    height = evidence?.source_height;
  if (
    !box ||
    !width ||
    !height ||
    !Number.isFinite(width) ||
    !Number.isFinite(height) ||
    width <= 0 ||
    height <= 0 ||
    box.length !== 4 ||
    !box.every(Number.isFinite) ||
    box[0] < 0 ||
    box[1] < 0 ||
    box[2] > width ||
    box[3] > height ||
    box[0] >= box[2] ||
    box[1] >= box[3]
  )
    return null;
  const x = (box[0] + box[2]) / 2 / width,
    y = (box[1] + box[3]) / 2 / height;
  const horizontal = x < 1 / 3 ? '左' : x > 2 / 3 ? '右' : '中';
  const vertical = y < 1 / 3 ? '上部' : y > 2 / 3 ? '下部' : '部';
  return `画面${horizontal}${vertical}`;
}
export function targetLabel(target: InvolvedTarget) {
  if (!Number.isInteger(target.track_id) || target.track_id === null || target.track_id < 0)
    return '身份待确认';
  const team = target.team === 'home' ? 'H' : target.team === 'away' ? 'A' : 'U';
  const role =
    target.role === 'referee'
      ? 'R'
      : target.role === 'goalkeeper'
        ? 'G'
        : target.role === 'outfield'
          ? 'P'
          : '?';
  return `${team}-${role}#${target.track_id}`;
}
export function parseCandidateEvents(text: string): CandidateRecord[] {
  const records: CandidateRecord[] = [];
  text.split(/\r?\n/).forEach((line, index) => {
    if (!line.trim()) return;
    const value = JSON.parse(line) as CandidateRecord;
    if (value.event?.event_type !== 'foul_candidate') return;
    const eventTime = candidateEventTime(value),
      emitted = candidateEmittedTime(value);
    if (
      !Number.isFinite(value.media_pts_seconds) ||
      value.media_pts_seconds < 0 ||
      typeof value.event.id !== 'string' ||
      !Number.isFinite(value.event.confidence) ||
      !Number.isFinite(eventTime) ||
      eventTime < 0 ||
      (emitted !== null && (!Number.isFinite(emitted) || emitted < eventTime))
    ) {
      throw new Error(`候选记录格式错误：第 ${index + 1} 行`);
    }
    records.push(value);
  });
  return records.sort((a, b) => candidateEventTime(a) - candidateEventTime(b));
}
