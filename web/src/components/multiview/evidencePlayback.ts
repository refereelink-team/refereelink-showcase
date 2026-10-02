// Playback and attention helpers adapted from RefereeLink's MIT-licensed multi-view review page.
import type {
  EvidenceView,
  LocalizationBox,
  MultiviewCase,
  MultiviewDecision,
} from '../../types/multiview.ts';

export interface LocalizationWindow {
  startS: number;
  endS: number;
  peakS: number;
  source: 'gradcam' | 'event_prior';
}

export interface MediaBounds {
  left: number;
  top: number;
  width: number;
  height: number;
}

export function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

export function viewOffsetSeconds(view: Pick<EvidenceView, 'sync_offset_ms'>): number {
  return Number.isFinite(view.sync_offset_ms) ? view.sync_offset_ms / 1000 : 0;
}

export function localTime(commonTimeS: number, view: Pick<EvidenceView, 'sync_offset_ms'>): number {
  return commonTimeS + viewOffsetSeconds(view);
}

export function commonTime(localTimeS: number, view: Pick<EvidenceView, 'sync_offset_ms'>): number {
  return localTimeS - viewOffsetSeconds(view);
}

export function viewRange(view: Pick<EvidenceView, 'sync_offset_ms'>, durationS: number) {
  if (!Number.isFinite(durationS) || durationS <= 0) return null;
  const offset = viewOffsetSeconds(view);
  return { startS: Math.max(0, -offset), endS: Math.max(0, durationS - offset) };
}

export function mediaPosition(
  commonTimeS: number,
  view: Pick<EvidenceView, 'sync_offset_ms'>,
  durationS: number,
) {
  const target = localTime(commonTimeS, view);
  const validDuration = Number.isFinite(durationS) && durationS > 0 ? durationS : 0;
  return {
    timeS: clamp(Number.isFinite(target) ? target : 0, 0, validDuration),
    state: !validDuration
      ? 'unavailable'
      : target < 0
        ? 'before'
        : target >= validDuration
          ? 'after'
          : 'active',
  } as const;
}

export function timelineDuration(views: EvidenceView[], durations: Record<string, number>): number {
  return views.reduce((duration, view) => {
    const range = viewRange(view, durations[view.camera_id] ?? 0);
    return Math.max(duration, range?.endS ?? 0);
  }, 0);
}

export function timelinePercent(timeS: number, durationS: number): number {
  return Number.isFinite(timeS) && Number.isFinite(durationS) && durationS > 0
    ? clamp((timeS / durationS) * 100, 0, 100)
    : 0;
}

export function playbackClockTime(
  elapsedTimeS: number,
  previousTimeS: number,
  durationS: number,
  referenceTimeS: number | null,
  referenceProgressAgeMs: number,
): number {
  const followsReference =
    referenceTimeS !== null &&
    Number.isFinite(referenceTimeS) &&
    referenceProgressAgeMs < 180 &&
    Math.abs(referenceTimeS - elapsedTimeS) < 0.35;
  const next = followsReference ? referenceTimeS : elapsedTimeS;
  // The independent elapsed clock keeps healthy views moving when a reference stalls.
  return clamp(Math.max(previousTimeS, next), 0, durationS);
}

export function localizationWindow(box: LocalizationBox, eventTimeS: number): LocalizationWindow {
  if (Number.isFinite(box.active_start_s) && Number.isFinite(box.active_end_s)) {
    const startS = Math.max(0, box.active_start_s as number);
    const endS = Math.max(startS, box.active_end_s as number);
    const peakS = Number.isFinite(box.peak_s) ? (box.peak_s as number) : (startS + endS) / 2;
    return {
      startS,
      endS,
      peakS: clamp(peakS, startS, endS),
      source: box.temporal_source === 'event_prior' ? 'event_prior' : 'gradcam',
    };
  }
  // A missing model window is explicitly presented as an event prior, never as CAM evidence.
  const peakS = Number.isFinite(eventTimeS) ? Math.max(0, eventTimeS) : 0;
  return { startS: Math.max(0, peakS - 0.5), endS: peakS + 0.5, peakS, source: 'event_prior' };
}

export function temporalFocusStrength(
  box: LocalizationBox,
  currentTimeS: number,
  window: LocalizationWindow,
): number {
  if (!Number.isFinite(currentTimeS)) return 0;
  const fadeS = Math.max(0.12, Math.min(0.2, (window.endS - window.startS) * 0.35));
  let gate = 1;
  if (currentTimeS < window.startS) gate = (currentTimeS - window.startS + fadeS) / fadeS;
  else if (currentTimeS > window.endS) gate = (window.endS + fadeS - currentTimeS) / fadeS;
  gate = clamp(gate, 0, 1);
  if (!gate) return 0;
  const bin = box.temporal_bins?.find(
    (item) =>
      currentTimeS >= item.start_s && currentTimeS <= item.end_s && Number.isFinite(item.score),
  );
  return gate * (bin ? 0.65 + 0.35 * clamp(bin.score, 0, 1) : 0.82);
}

export function containedMediaBounds(
  containerWidth: number,
  containerHeight: number,
  mediaWidth: number,
  mediaHeight: number,
): MediaBounds | null {
  if (
    ![containerWidth, containerHeight, mediaWidth, mediaHeight].every(
      (value) => Number.isFinite(value) && value > 0,
    )
  )
    return null;
  const scale = Math.min(containerWidth / mediaWidth, containerHeight / mediaHeight);
  const width = mediaWidth * scale;
  const height = mediaHeight * scale;
  return { left: (containerWidth - width) / 2, top: (containerHeight - height) / 2, width, height };
}

export function expandedFocusRect(rect: LocalizationBox['rect']): LocalizationBox['rect'] | null {
  if (!rect?.every(Number.isFinite) || rect[2] <= 0 || rect[3] <= 0) return null;
  const [left, top, width, height] = rect;
  const padX = Math.max(3.5, width * 0.25);
  const padY = Math.max(3.5, height * 0.25);
  const x = clamp(left - padX, 0, 100);
  const y = clamp(top - padY, 0, 100);
  const right = clamp(left + width + padX, 0, 100);
  const bottom = clamp(top + height + padY, 0, 100);
  return right > x && bottom > y ? [x, y, right - x, bottom - y] : null;
}

export function localizationTier(box: LocalizationBox): 'normal' | 'caution' | 'hidden' {
  return box.display_tier ?? (box.reliable === false ? 'hidden' : 'normal');
}

export function decisionFocusTime(caseData: MultiviewCase, decision: MultiviewDecision): number {
  const view = caseData.videos[0];
  if (!view) return Math.max(0, caseData.event_time_s);
  const box = decision.localization[view.camera_id];
  if (!box) return Math.max(0, caseData.event_time_s);
  const window = localizationWindow(box, localTime(caseData.event_time_s, view));
  return Math.max(0, commonTime(window.peakS, view));
}
