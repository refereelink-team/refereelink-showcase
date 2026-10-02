import type {
  CalibrationFilter,
  CalibrationFrame,
  CalibrationLabel,
  CalibrationMetadata,
  CalibrationSnapshot,
  CalibrationTrack,
} from '../../types/calibration';

export const labelOptions: {
  value: CalibrationLabel;
  title: string;
  detail: string;
  color: string;
}[] = [
  { value: 'home_outfield', title: '主队', detail: 'HOME', color: '#70a53b' },
  { value: 'away_outfield', title: '客队', detail: 'AWAY', color: '#5989d8' },
  { value: 'home_goalkeeper', title: '主队门将', detail: 'HOME GK', color: '#9982cf' },
  { value: 'away_goalkeeper', title: '客队门将', detail: 'AWAY GK', color: '#c48a67' },
  { value: 'referee', title: '裁判', detail: 'REF', color: '#c6983b' },
  { value: 'ignore', title: '忽略', detail: 'IGNORE', color: '#929aa7' },
];
export const filterOptions: { value: CalibrationFilter; title: string }[] = [
  { value: 'unlabelled', title: '待标注' },
  { value: 'home', title: '主队' },
  { value: 'away', title: '客队' },
  { value: 'roles', title: '门将 / 裁判' },
  { value: 'weak', title: '弱样本' },
  { value: 'ignored', title: '已忽略' },
  { value: 'all', title: '全部' },
];
export function labelOf(track: CalibrationTrack, snapshot: CalibrationSnapshot) {
  return snapshot.labels[String(track.track_id)] || track.label || null;
}
export function labelTitle(label: CalibrationLabel | null) {
  return labelOptions.find((item) => item.value === label)?.title || '待标注';
}
export function labelColor(label: CalibrationLabel | null) {
  return labelOptions.find((item) => item.value === label)?.color || '#d4dbe5';
}
export function minimumSamples(snapshot: CalibrationSnapshot) {
  return snapshot.session.validation_report?.min_samples_per_track || 5;
}
export function isWeak(track: CalibrationTrack, snapshot: CalibrationSnapshot) {
  return track.quality_observation_count < minimumSamples(snapshot);
}
export function trackMatches(
  track: CalibrationTrack,
  filter: CalibrationFilter,
  snapshot: CalibrationSnapshot,
) {
  const label = labelOf(track, snapshot);
  if (filter === 'unlabelled') return !label;
  if (filter === 'home') return label === 'home_outfield';
  if (filter === 'away') return label === 'away_outfield';
  if (filter === 'roles')
    return ['home_goalkeeper', 'away_goalkeeper', 'referee'].includes(label || '');
  if (filter === 'ignored') return label === 'ignore';
  if (filter === 'weak') return isWeak(track, snapshot);
  return true;
}
export function snapshotIsBusy(snapshot: CalibrationSnapshot | null) {
  return Boolean(snapshot && ['queued', 'running'].includes(snapshot.status));
}
export function acceptSnapshot(
  current: CalibrationSnapshot | null,
  next: CalibrationSnapshot,
  expectedId?: string,
): CalibrationSnapshot {
  if (expectedId && next.id !== expectedId) throw new Error('标定响应与当前练习不匹配');
  if (current && current.id === next.id && next.revision < current.revision) return current;
  return next;
}
export function validateSegment(startMs: number, endMs: number, durationMs: number) {
  if (![startMs, endMs, durationMs].every(Number.isFinite) || startMs < 0 || endMs <= startMs)
    return '结束时间需要晚于开始时间';
  if (endMs > durationMs) return '选取时间超出原始视频';
  if (endMs - startMs > 60000) return '请选取 60 秒以内的片段';
  return null;
}
export function metadataMatches(metadata: CalibrationMetadata, snapshot: CalibrationSnapshot) {
  return metadata.session_id === snapshot.id && metadata.clip_id === snapshot.session.clip_id;
}
export function calibrationFrameAt(
  metadata: CalibrationMetadata,
  seconds: number,
): CalibrationFrame | null {
  const time = seconds * 1000;
  if (!Number.isFinite(time) || time < 0) return null;
  let low = 0,
    high = metadata.frames.length - 1,
    found = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (metadata.frames[mid].timestamp_ms <= time) {
      found = mid;
      low = mid + 1;
    } else high = mid - 1;
  }
  const frame = metadata.frames[found];
  if (!frame || time - frame.timestamp_ms > 1500 / Math.max(1, metadata.fps)) return null;
  return frame;
}
export function validCalibrationBox(box: number[], width: number, height: number) {
  return (
    box.length === 4 &&
    box.every(Number.isFinite) &&
    box[0] >= 0 &&
    box[1] >= 0 &&
    box[2] > box[0] &&
    box[3] > box[1] &&
    box[2] <= width &&
    box[3] <= height
  );
}
export interface CalibrationIssue {
  text: string;
  filter: CalibrationFilter;
  ids: number[];
}
export function calibrationIssues(snapshot: CalibrationSnapshot): CalibrationIssue[] {
  const report = snapshot.session.validation_report;
  if (!report) return [];
  const tracks = snapshot.session.tracks;
  const group = (label: CalibrationLabel) =>
    tracks.filter((track) => labelOf(track, snapshot) === label);
  const ids = (label: CalibrationLabel) => group(label).map((track) => track.track_id);
  const issues: CalibrationIssue[] = [];
  for (const [side, label, title] of [
    ['home', 'home_outfield', '主队'],
    ['away', 'away_outfield', '客队'],
  ] as const) {
    if (report.reasons.some((reason) => reason.startsWith(side + '_'))) {
      issues.push({
        text: title + '可用球员或样本不足，检查标注并补充清晰球员。',
        filter: side,
        ids: ids(label),
      });
    }
  }
  if (report.excluded_track_ids?.length) {
    issues.push({
      text: '这些球员的有效样本不足，已排除出本次校验；人工标签仍保留。',
      filter: 'weak',
      ids: report.excluded_track_ids,
    });
  }
  const other: Record<string, string> = {
    outfield_prototypes_not_separable: '主客队外观过于接近，请核对两组球员的标签。',
    leave_one_track_out_failed: '球队外观一致性不足，请检查误标或遮挡严重的球员。',
    appearance_features_unavailable: '外观特征尚不可用，请重试检查或查看后台错误。',
  };
  for (const reason of report.reasons.filter(
    (reason) => !reason.startsWith('home_') && !reason.startsWith('away_'),
  )) {
    issues.push({ text: other[reason] || reason, filter: 'all', ids: [] });
  }
  return issues;
}
