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

export function localizationWindow(box: LocalizationBox): LocalizationWindow | null {
  if (
    (box.temporal_source !== 'gradcam' && box.temporal_source !== 'event_prior') ||
    (box.temporal_source === 'gradcam' && localizationTier(box) === 'hidden') ||
    !Number.isFinite(box.active_start_s) ||
    !Number.isFinite(box.active_end_s) ||
    !Number.isFinite(box.peak_s)
  )
    return null;
  const startS = box.active_start_s as number;
  const endS = box.active_end_s as number;
  const peakS = box.peak_s as number;
  if (startS < 0 || endS <= startS || peakS < startS || peakS > endS) return null;
  // Use only explicitly returned review/model intervals; incomplete responses invent no focus.
  return { startS, endS, peakS, source: box.temporal_source };
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

export function loadedPausedMediaTime(media: {
  paused: boolean;
  seeking: boolean;
  readyState: number;
  currentTime: number;
}): number | null {
  return media.paused &&
    !media.seeking &&
    media.readyState >= 2 &&
    Number.isFinite(media.currentTime) &&
    media.currentTime >= 0
    ? media.currentTime
    : null;
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

export function normalizedFocusRect(rect: LocalizationBox['rect']): LocalizationBox['rect'] | null {
  if (!Array.isArray(rect) || rect.length !== 4 || !rect.every(Number.isFinite)) return null;
  const [left, top, width, height] = rect;
  if (left < 0 || top < 0 || width <= 0 || height <= 0 || left + width > 100 || top + height > 100)
    return null;
  // Preserve the model geometry exactly; presentation never enlarges an attribution.
  return rect;
}

export interface SpatialFocus {
  rect: LocalizationBox['rect'];
  source: 'sampled' | 'legacy';
}

export function spatialFocusAtTime(box: LocalizationBox, mediaTimeS: number): SpatialFocus | null {
  if (
    !Number.isFinite(mediaTimeS) ||
    mediaTimeS < 0 ||
    localizationTier(box) === 'hidden' ||
    box.source !== 'gradcam'
  )
    return null;
  if (box.spatial_bins !== undefined && box.spatial_bins !== null) {
    if (!Array.isArray(box.spatial_bins) || !box.spatial_bins.length) return null;
    for (const bin of box.spatial_bins) {
      if (
        !bin ||
        typeof bin !== 'object' ||
        !Number.isFinite(bin.start_s) ||
        !Number.isFinite(bin.end_s) ||
        bin.start_s < 0 ||
        bin.end_s <= bin.start_s ||
        !Number.isFinite(bin.score) ||
        bin.score <= 0 ||
        bin.score > 1 ||
        !normalizedFocusRect(bin.rect)
      )
        return null;
    }
    const bins = [...box.spatial_bins].sort((first, second) => first.start_s - second.start_s);
    if (bins.some((bin, index) => index > 0 && bin.start_s < bins[index - 1].end_s)) return null;
    const bin = bins.find((item) => mediaTimeS >= item.start_s && mediaTimeS < item.end_s);
    // Gaps, malformed data, and times outside the model samples have no substitute rectangle.
    return bin ? { rect: bin.rect, source: 'sampled' } : null;
  }
  // Aggregate CAM uses its explicitly returned review/model interval, without adding geometry.
  if (!localizationWindow(box)) return null;
  const rect = normalizedFocusRect(box.rect);
  return rect ? { rect, source: 'legacy' } : null;
}

export function localizationTier(box: LocalizationBox): 'normal' | 'caution' | 'hidden' {
  return box.display_tier ?? (box.reliable === false ? 'hidden' : 'normal');
}

export function decisionFocusTime(
  caseData: MultiviewCase,
  decision: MultiviewDecision,
): number | null {
  let best: { timeS: number; attention: number } | null = null;
  for (const [index, view] of caseData.videos.entries()) {
    const box = decision.localization[view.camera_id];
    const window = box ? localizationWindow(box) : null;
    if (!window) continue;
    const timeS = commonTime(window.peakS, view);
    if (!Number.isFinite(timeS) || timeS < 0) continue;
    const weight = decision.view_attention?.[index];
    const attention = Number.isFinite(weight) && weight >= 0 ? weight : 0;
    if (!best || attention > best.attention) best = { timeS, attention };
  }
  if (best) return best.timeS;
  const sampling = decision.detail?.sampling;
  const temporal = decision.detail?.temporal_localization;
  const usesReviewTime =
    sampling &&
    typeof sampling === 'object' &&
    (sampling as Record<string, unknown>).uses_event_prior === true &&
    temporal &&
    typeof temporal === 'object' &&
    (temporal as Record<string, unknown>).timestamp_source === 'event_prior';
  return usesReviewTime &&
    Number.isFinite(decision.timestamp) &&
    (decision.timestamp as number) >= 0
    ? decision.timestamp
    : null;
}
