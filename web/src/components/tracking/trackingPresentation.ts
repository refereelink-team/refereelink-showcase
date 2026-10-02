import type { Job } from '../../types.ts';
import type {
  TrackedEntity,
  TrackingFrame,
  TrackingPitch,
  TrackingResult,
  TrackingFramesPage,
  TrackingCase,
} from '../../types/tracking.ts';

export const trackingColors = {
  home: '#a8eb44',
  away: '#70b7ff',
  referee: '#f4ba53',
  unknown: '#c4cbd7',
};
export function entityColor(entity: TrackedEntity) {
  if (entity.role === 'referee') return trackingColors.referee;
  if (entity.team === 'home') return trackingColors.home;
  if (entity.team === 'away') return trackingColors.away;
  return trackingColors.unknown;
}
export function teamLabel(team: string) {
  return (
    { home: '主队', away: '客队', none: '无球队', unknown: '球队未确定' }[team] || '球队未确定'
  );
}
export function roleLabel(role: string) {
  return (
    {
      outfield: '球员',
      goalkeeper: '守门员',
      referee: '裁判',
      staff: '工作人员',
      unknown: '身份未确定',
    }[role] || '身份未确定'
  );
}
export function entityKey(entity: TrackedEntity, frameId: number, index: number) {
  if (entity.track_id !== null) return 'track:' + entity.track_id;
  if (entity.entity_id !== null) return 'entity:' + entity.entity_id;
  return 'observation:' + frameId + ':' + index;
}
export function entityLabel(entity: TrackedEntity) {
  return entity.track_id === null ? roleLabel(entity.role) : '#' + entity.track_id;
}
export function validBox(box: TrackedEntity['bbox'], width: number, height: number) {
  if (!box || box.length !== 4 || !box.every(Number.isFinite)) return false;
  const [x1, y1, x2, y2] = box;
  return x1 >= 0 && y1 >= 0 && x2 > x1 && y2 > y1 && x2 <= width && y2 <= height;
}
export function fieldPoint(
  entity: { field_x: number | null; field_y: number | null },
  pitch: Pick<TrackingPitch, 'length_m' | 'width_m'>,
): [number, number] | null {
  const { field_x: x, field_y: y } = entity;
  if (x === null || y === null || !Number.isFinite(x) || !Number.isFinite(y)) return null;
  return x >= 0 && y >= 0 && x <= pitch.length_m && y <= pitch.width_m ? [x, y] : null;
}
/** Align the fixed paint-registration axes with the footage without changing observations. */
export function pitchDisplayPoint(
  point: [number, number],
  result: Pick<TrackingResult, 'pitch' | 'provenance'>,
): [number, number] {
  const reverseX =
    result.provenance.pitch_profile_id === 'source-informed105' &&
    result.provenance.paint_enabled === true;
  return [reverseX ? result.pitch.length_m - point[0] : point[0], point[1]];
}
export function usableProjection(frame: TrackingFrame | null) {
  return Boolean(frame && ['fresh', 'reused'].includes(frame.homography_status));
}
/** Never display a future observation or hold a box across a missing-data gap. */
export function frameIndexAtTime(frames: TrackingFrame[], time: number, fps: number) {
  if (!frames.length || !Number.isFinite(time) || time < frames[0].source_pts_s - 0.001) return -1;
  let low = 0,
    high = frames.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (frames[middle].source_pts_s <= time + 0.001) low = middle;
    else high = middle - 1;
  }
  const maxAge = Number.isFinite(fps) && fps > 0 ? 2 / fps + 0.02 : 0.12;
  return time - frames[low].source_pts_s <= maxAge ? low : -1;
}
/** Return recorded points only; gaps create separate SVG paths rather than invented motion. */
export function recordedTrajectory(
  frames: TrackingFrame[],
  index: number,
  key: string,
  pitch: TrackingPitch,
  seconds = 2,
) {
  if (index < 0 || key.startsWith('observation:')) return [];
  const start = frames[index].source_pts_s - seconds;
  const segments: [number, number][][] = [];
  let segment: [number, number][] = [];
  let previous = -Infinity;
  let previousEpoch = frames[index].geometry_epoch;
  for (let i = index; i >= 0 && frames[i].source_pts_s >= start; i--) {
    const frame = frames[i];
    const entity = frame.players.find((player, n) => entityKey(player, frame.frame_id, n) === key);
    const point = entity && usableProjection(frame) ? fieldPoint(entity, pitch) : null;
    if (
      !point ||
      frame.geometry_epoch !== previousEpoch ||
      (previous !== -Infinity && previous - frame.source_pts_s > 0.15)
    ) {
      if (segment.length > 1) segments.push(segment.reverse());
      segment = [];
    }
    if (point) segment.push(point);
    previous = frame.source_pts_s;
    previousEpoch = frame.geometry_epoch;
  }
  if (segment.length > 1) segments.push(segment.reverse());
  return segments.reverse();
}
export async function collectTrackingFrames(
  result: TrackingResult,
  load: (offset: number) => Promise<TrackingFramesPage>,
  isCurrent: () => boolean,
  onProgress: (loaded: number) => void = () => {},
) {
  const frames: TrackingFrame[] = [];
  const seen = new Set<number>();
  let offset = 0;
  while (isCurrent()) {
    const page = await load(offset);
    if (!isCurrent()) return null;
    if (
      page.result_id !== result.id ||
      page.revision !== result.revision ||
      page.offset !== offset ||
      page.total !== result.frame_count
    )
      throw new Error('跟踪结果与视频不匹配，请重新运行');
    for (const frame of page.frames) {
      if (
        !Number.isFinite(frame.source_pts_s) ||
        frame.source_pts_s < 0 ||
        seen.has(frame.frame_id) ||
        (frames.length && frame.source_pts_s <= frames[frames.length - 1].source_pts_s)
      )
        throw new Error('跟踪时间记录无效，请重新运行');
      seen.add(frame.frame_id);
      frames.push(frame);
    }
    if (frames.length > result.frame_count) throw new Error('跟踪帧数无效，请重新运行');
    onProgress(frames.length);
    if (page.next_offset === null) {
      if (frames.length !== result.frame_count) throw new Error('跟踪结果尚不完整，请重新运行');
      return frames;
    }
    if (page.next_offset <= offset || page.next_offset !== offset + page.frames.length)
      throw new Error('跟踪记录分页无效，请重新运行');
    offset = page.next_offset;
  }
  return null;
}

/** Resolve this job's records directly, even if another client has completed a newer run. */
export function trackingArtifactId(job: Job) {
  const artifact = job.artifacts.find(
    (item) =>
      item.kind === 'data' &&
      item.label === 'frame-states.jsonl' &&
      (!item.mode || item.mode === 'projection_only'),
  );
  if (!artifact) throw new Error('本次任务未提供跟踪记录，请重新运行');
  return artifact.id;
}

export function caseWithResult(
  current: TrackingCase | null,
  result: TrackingResult,
  poster: string | null,
): TrackingCase {
  return {
    ...(current || {
      case_id: result.case_id,
      source_url: result.source.url,
      poster_url: poster,
      duration_seconds: result.source.duration_seconds,
      decoded_frames: result.source.decoded_frames,
      result_warning: null,
    }),
    latest_result: result,
    active_job: null,
  };
}
