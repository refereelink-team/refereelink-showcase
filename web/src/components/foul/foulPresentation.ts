import type { CandidateRecord, InvolvedTarget } from '../../events.ts';
import { candidateEmittedTime, candidateEventTime } from '../../events.ts';
import { regionForPresentation } from '../mediaRegion.ts';
import type { Artifact } from '../../types.ts';
import type { FoulFrame, FoulFramesPage, FoulPlayer, FoulResult } from '../../types/foul.ts';

/** Frame data and event data must be selected from one registered result group. */
export function foulFrameArtifact(artifacts: Artifact[], events?: Artifact) {
  if (!events) return undefined;
  const group = events.id.replace(/-candidate-events$/, '');
  return artifacts.find((item) => item.kind === 'data' && item.id === `${group}-frame-states`);
}

export async function collectFoulFrames(
  result: FoulResult,
  load: (offset: number) => Promise<FoulFramesPage>,
  current: () => boolean = () => true,
): Promise<FoulFrame[]> {
  const frames: FoulFrame[] = [];
  let offset = 0;
  while (offset < result.frame_count) {
    if (!current()) return [];
    const page = await load(offset);
    if (!current()) return [];
    if (
      page.result_id !== result.id ||
      page.revision !== result.revision ||
      page.offset !== offset ||
      page.total !== result.frame_count ||
      !page.frames.length ||
      page.frames.length > 500 ||
      offset + page.frames.length > result.frame_count
    )
      throw new Error('逐帧结果与当前检测任务不匹配');
    for (const frame of page.frames) {
      const previous = frames.at(-1);
      if (
        !Number.isInteger(frame.frame_id) ||
        !Number.isFinite(frame.time_s) ||
        frame.time_s < 0 ||
        frame.time_s > result.source.duration_s ||
        !Array.isArray(frame.players) ||
        (previous && (frame.frame_id <= previous.frame_id || frame.time_s <= previous.time_s))
      )
        throw new Error('逐帧时间戳无效');
      frames.push(frame);
    }
    offset += page.frames.length;
    if (page.next_offset !== (offset === result.frame_count ? null : offset))
      throw new Error('逐帧结果分页不完整');
  }
  return frames;
}

/** Use recorded PTS, never frameIndex / nominal FPS or interpolation across gaps. */
export function foulFrameAtTime(frames: FoulFrame[], time: number | null, fps: number) {
  if (time === null || !Number.isFinite(time) || time < 0 || !frames.length) return null;
  let left = 0,
    right = frames.length - 1,
    index = -1;
  while (left <= right) {
    const middle = Math.floor((left + right) / 2);
    // Source metadata rounds PTS to milliseconds; tolerate that rounding only.
    if (frames[middle].time_s <= time + 0.0005) {
      index = middle;
      left = middle + 1;
    } else right = middle - 1;
  }
  if (index < 0) return null;
  const frame = frames[index];
  const maxAge = Math.min(0.15, Math.max(0.04, 2 / Math.max(1, fps)));
  return time - frame.time_s <= maxAge ? frame : null;
}

export function observedPlayer(player: FoulPlayer, width: number, height: number) {
  const box = player.bbox;
  return Boolean(
    player.track_status === 'detected' &&
    !(player.missing_frames && player.missing_frames > 0) &&
    box &&
    box.length === 4 &&
    box.every(Number.isFinite) &&
    box[0] >= 0 &&
    box[1] >= 0 &&
    box[2] <= width &&
    box[3] <= height &&
    box[2] > box[0] &&
    box[3] > box[1],
  );
}

export function foulPlayerKey(player: FoulPlayer) {
  // Track IDs are unique per frame; an ambiguous entity association is not a key.
  if (Number.isInteger(player.track_id) && player.track_id !== null)
    return `track:${player.track_id}`;
  if (
    Number.isInteger(player.entity_id) &&
    player.entity_id !== null &&
    player.entity_id !== undefined
  )
    return `entity:${player.entity_id}`;
  return null;
}

/** Explicit track associations take precedence; ambiguous entities stay unassigned. */
export function foulTargetMatches(
  target: InvolvedTarget,
  player: FoulPlayer,
  observed: FoulPlayer[],
) {
  if (Number.isInteger(target.track_id) && target.track_id !== null)
    return target.track_id === player.track_id;
  return Boolean(
    Number.isInteger(target.entity_id) &&
    target.entity_id !== null &&
    target.entity_id !== undefined &&
    target.entity_id === player.entity_id &&
    observed.filter((item) => item.entity_id === target.entity_id).length === 1,
  );
}

/** Prepared contact evidence follows its observed frame, independently of alert selection. */
export function foulRegionsAtTime(
  events: CandidateRecord[],
  presentedTime: number | null,
  width: number,
  height: number,
) {
  return events.flatMap((record) => {
    const evidence = record.event.evidence;
    if (!evidence?.region_xyxy) return [];
    const region = regionForPresentation(
      {
        timeS: candidateEventTime(record),
        box: evidence.region_xyxy,
        width: evidence.source_width || 0,
        height: evidence.source_height || 0,
      },
      presentedTime,
      width,
      height,
    );
    return region ? [{ id: record.event.id, region }] : [];
  });
}

export interface FoulReplayCycle {
  epoch: number;
  lastTime: number | null;
  revealed: string[];
  awaitingStart: boolean;
}
export const newFoulReplayCycle = (): FoulReplayCycle => ({
  epoch: 0,
  lastTime: null,
  revealed: [],
  awaitingStart: false,
});
export function restartFoulReplayCycle(state: FoulReplayCycle): FoulReplayCycle {
  return { epoch: state.epoch + 1, lastTime: null, revealed: [], awaitingStart: true };
}

/** A loop is a new causal replay; decoded tail callbacks cannot repopulate it. */
export function advanceFoulReplayCycle(
  state: FoulReplayCycle,
  events: CandidateRecord[],
  presented: number | null,
  requested: number,
): FoulReplayCycle {
  if (presented === null || !Number.isFinite(presented) || presented < 0) return state;
  if (state.awaitingStart && Math.abs(presented - requested) > 0.15) return state;
  if (state.epoch > 0 && presented > requested + 0.15) return state;
  const wrapped = presented <= 0.15 && state.lastTime !== null && state.lastTime > presented + 0.15;
  const current = wrapped ? restartFoulReplayCycle(state) : state;
  const additions = events
    .filter((record) => {
      const emitted = candidateEmittedTime(record);
      return (
        emitted !== null && presented >= emitted && !current.revealed.includes(record.event.id)
      );
    })
    .map((record) => record.event.id);
  return {
    ...current,
    lastTime: presented,
    awaitingStart: false,
    revealed: additions.length ? [...current.revealed, ...additions] : current.revealed,
  };
}

/** Missing emission timestamps in historical records never become invented alerts. */
export function emittedCandidatesAtTime(events: CandidateRecord[], time: number | null) {
  if (time === null || !Number.isFinite(time)) return [];
  return events.filter((record) => {
    const emitted = candidateEmittedTime(record);
    return emitted !== null && time >= emitted && time < emitted + 2;
  });
}

export function emittedCandidateAtTime(events: CandidateRecord[], time: number | null) {
  if (time === null || !Number.isFinite(time)) return null;
  let latest: CandidateRecord | null = null;
  for (const record of events) {
    const emitted = candidateEmittedTime(record);
    if (
      emitted !== null &&
      time >= emitted &&
      time < emitted + 2 &&
      (!latest || emitted > candidateEmittedTime(latest)!)
    )
      latest = record;
  }
  return latest;
}

export function severityLabel(value?: string | null) {
  const labels: Record<string, string> = {
    'Offence + Yellow Card': '黄牌建议',
    'Offence + Red Card': '红牌建议',
    'Offence + No Card': '无牌建议',
    'No offence': '不犯规',
    no_offence: '不犯规',
  };
  return value ? labels[value] || value : '处罚待确认';
}
