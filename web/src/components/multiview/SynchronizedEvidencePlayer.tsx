// Synchronization and attention rendering adapted from RefereeLink's MIT-licensed review page.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import type {
  EvidenceView,
  LocalizationBox,
  MultiviewCase,
  MultiviewDecision,
} from '../../types/multiview';
import { mediaUrl } from '../../api';
import { Icon } from '../UI';
import {
  clamp,
  commonTime,
  containedMediaBounds,
  spatialFocusAtTime,
  localizationTier,
  localizationWindow,
  loadedPausedMediaTime,
  mediaPosition,
  playbackClockTime,
  temporalFocusStrength,
  timelineDuration,
  timelinePercent,
  viewRange,
} from './evidencePlayback';
import type { MediaBounds } from './evidencePlayback';
import './synchronized-evidence.css';

export interface EvidenceReadiness {
  allVideosReady: boolean;
  playableVideoCount: number;
  totalViewCount: number;
  loadingCameraIds: string[];
  missingCameraIds: string[];
  failedCameraIds: string[];
}

export interface EvidenceFocusRequest {
  token: string | number;
  commonTimeS: number;
}

export interface SynchronizedEvidencePlayerProps {
  caseData: MultiviewCase;
  decision: MultiviewDecision | null;
  onReadinessChange?: (readiness: EvidenceReadiness) => void;
  focusRequest?: EvidenceFocusRequest | null;
  onTimeChange?: (commonTimeS: number) => void;
}

function spatialSourceLabel(box: LocalizationBox) {
  if (box.source === 'gradcam') return 'Visual Attribution';
  if (box.source === 'optical_flow') return 'Motion Cue';
  if (box.source === 'scripted') return 'Rule Cue';
  return 'Visual Focus';
}

function temporalFocusLabel(source: 'gradcam' | 'event_prior') {
  return source === 'gradcam' ? 'Temporal Saliency' : 'Temporal Focus';
}

function diagnosticReason(decision: MultiviewDecision | null, index: number): string | undefined {
  const temporal = decision?.detail.temporal_localization;
  if (!temporal || typeof temporal !== 'object') return undefined;
  const record = temporal as Record<string, unknown>;
  const views = record.views;
  const diagnostic =
    views && typeof views === 'object'
      ? (views as Record<string, unknown>)[String(index)]
      : undefined;
  const reason =
    diagnostic && typeof diagnostic === 'object'
      ? (diagnostic as Record<string, unknown>).reason
      : record.reason;
  return typeof reason === 'string' ? reason : undefined;
}

function reasonTitle(reason: string) {
  const labels: Record<string, string> = {
    flat_response: 'Flat temporal response',
    full_window_response: 'Broad temporal response',
    zero_response: 'No model activation',
    low_view_attention: 'Low view attention',
    crop_boundary_contact: 'Activation reaches the model crop boundary',
    low_spatial_contrast: 'Low spatial contrast',
    gradcam_failed: 'Grad-CAM could not be computed',
    no_offence: 'The model predicted No Offence and returned no attribution',
    event_prior: 'Selected review interval',
    optical_flow_event_prior: 'Selected review interval',
  };
  return (
    labels[reason] ??
    (reason.toLowerCase().includes('prior') ? 'Focus unavailable' : reason.replaceAll('_', ' '))
  );
}

function EvidenceTile({
  view,
  index,
  selected,
  onSelect,
  register,
  onMetadata,
  onReady,
  onError,
  onWaiting,
  onRetry,
  error,
  loading,
  waiting,
  duration,
  commonTimeS,
  box,
  attention,
  showAttention,
  analysisAvailable,
  unavailableReason,
}: {
  view: EvidenceView;
  index: number;
  selected: boolean;
  onSelect: () => void;
  register: (id: string, node: HTMLVideoElement | null) => void;
  onMetadata: (id: string) => void;
  onReady: (id: string) => void;
  onError: (id: string) => void;
  onWaiting: (id: string, value: boolean) => void;
  onRetry: (id: string) => void;
  error?: string;
  loading: boolean;
  waiting: boolean;
  duration: number;
  commonTimeS: number;
  box?: LocalizationBox;
  attention?: number;
  showAttention: boolean;
  analysisAvailable: boolean;
  unavailableReason?: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const media = useRef<HTMLVideoElement | HTMLImageElement | null>(null);
  const [bounds, setBounds] = useState<MediaBounds | null>(null);
  const [presentationTimeS, setPresentationTimeS] = useState<number | null>(null);
  const lastPresentedFrame = useRef<{ timeS: number; targetS: number } | null>(null);
  const capturePausedFrame = useCallback((video: HTMLVideoElement) => {
    const loadedTimeS = loadedPausedMediaTime(video);
    if (loadedTimeS === null) return;
    const presented = lastPresentedFrame.current;
    // Prefer a known presented PTS for this loaded frame, otherwise use ready media time.
    setPresentationTimeS(
      presented && Math.abs(presented.targetS - loadedTimeS) < 0.001
        ? presented.timeS
        : loadedTimeS,
    );
  }, []);
  function captureReadyPausedFrame(video: HTMLVideoElement) {
    if (loadedPausedMediaTime(video) === null) return;
    capturePausedFrame(video);
    // A loaded paused frame has no pending playback-buffer wait.
    onWaiting(view.camera_id, false);
  }
  const measure = useCallback(() => {
    const host = container.current;
    const node = media.current;
    if (!host || !node) return;
    const width = node instanceof HTMLVideoElement ? node.videoWidth : node.naturalWidth;
    const height = node instanceof HTMLVideoElement ? node.videoHeight : node.naturalHeight;
    const next = containedMediaBounds(host.clientWidth, host.clientHeight, width, height);
    if (next)
      setBounds((previous) =>
        previous &&
        Object.keys(next).every(
          (key) =>
            Math.abs(previous[key as keyof MediaBounds] - next[key as keyof MediaBounds]) < 0.25,
        )
          ? previous
          : next,
      );
  }, []);
  useEffect(() => {
    const node = container.current;
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    if (node) observer?.observe(node);
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [measure]);
  useEffect(() => {
    const video = media.current;
    setPresentationTimeS(null);
    lastPresentedFrame.current = null;
    if (!(video instanceof HTMLVideoElement)) return;
    let disposed = false;
    let requestId: number | null = null;
    const usesPresentedFrames = typeof video.requestVideoFrameCallback === 'function';
    let blocked = false;
    const invalidate = (event: Event) => {
      // Preload may report a late network stall while a paused frame remains fully loaded.
      if (
        (event.type === 'waiting' || event.type === 'stalled') &&
        loadedPausedMediaTime(video) !== null
      ) {
        blocked = false;
        capturePausedFrame(video);
        return;
      }
      blocked = true;
      lastPresentedFrame.current = null;
      setPresentationTimeS(null);
    };
    const publish = (timeS: number) => {
      if (!disposed && !video.seeking && video.readyState >= 2 && Number.isFinite(timeS))
        setPresentationTimeS(timeS);
    };
    const captureCurrentTime = () => {
      if (!blocked) publish(video.currentTime);
    };
    const receiveFrame: VideoFrameRequestCallback = (_now, metadata) => {
      blocked = false;
      if (!video.seeking && video.readyState >= 2)
        lastPresentedFrame.current = { timeS: metadata.mediaTime, targetS: video.currentTime };
      publish(metadata.mediaTime);
      if (!disposed) requestId = video.requestVideoFrameCallback(receiveFrame);
    };
    const requestPresentedFrame = () => {
      if (!usesPresentedFrames || disposed) return;
      if (requestId !== null) video.cancelVideoFrameCallback(requestId);
      requestId = video.requestVideoFrameCallback(receiveFrame);
    };
    const resumeFromReadyFrame = () => {
      if (video.seeking || video.readyState < 2) return;
      blocked = false;
      // A paused seek can present its only frame while seeking is still true.
      // seeked/loadeddata confirm data at currentTime; the next presentation
      // callback replaces this media-time snapshot with its exact frame PTS.
      if (video.paused) capturePausedFrame(video);
      else captureCurrentTime();
      requestPresentedFrame();
    };
    const invalidatingEvents = ['seeking', 'waiting', 'stalled', 'emptied', 'loadstart'];
    invalidatingEvents.forEach((event) => video.addEventListener(event, invalidate));
    const fallbackEvents = ['timeupdate', 'pause'];
    const resumingEvents = ['loadeddata', 'seeked', 'canplay', 'playing'];
    resumingEvents.forEach((event) => video.addEventListener(event, resumeFromReadyFrame));
    if (!usesPresentedFrames)
      fallbackEvents.forEach((event) => video.addEventListener(event, captureCurrentTime));
    resumeFromReadyFrame();
    requestPresentedFrame();
    return () => {
      disposed = true;
      if (requestId !== null) video.cancelVideoFrameCallback(requestId);
      invalidatingEvents.forEach((event) => video.removeEventListener(event, invalidate));
      fallbackEvents.forEach((event) => video.removeEventListener(event, captureCurrentTime));
      resumingEvents.forEach((event) => video.removeEventListener(event, resumeFromReadyFrame));
    };
  }, [view.camera_id, view.media_url, capturePausedFrame]);
  const position = mediaPosition(commonTimeS, view, duration);
  const attentionWindow = box ? localizationWindow(box) : null;
  const focus =
    box && presentationTimeS !== null ? spatialFocusAtTime(box, presentationTimeS) : null;
  const rect = focus?.rect;
  const tier = box ? localizationTier(box) : 'normal';
  const strength =
    box &&
    focus &&
    presentationTimeS !== null &&
    presentationTimeS >= 0 &&
    presentationTimeS < duration &&
    showAttention &&
    !error &&
    !loading &&
    !waiting &&
    !(media.current instanceof HTMLVideoElement && media.current.seeking) &&
    tier !== 'hidden'
      ? (focus.source === 'sampled'
          ? 0.9
          : attentionWindow
            ? temporalFocusStrength(box, presentationTimeS, attentionWindow)
            : 0) * (tier === 'caution' ? 0.64 : 1)
      : 0;
  const evidenceReasons = [
    ...new Set([
      ...(box?.reliability_reasons ?? []),
      ...(unavailableReason ? [unavailableReason] : []),
    ]),
  ]
    .map(reasonTitle)
    .join(' · ');
  const spatialTitle = box
    ? `Spatial method: ${box.source}${evidenceReasons ? ` · ${evidenceReasons}` : ''}`
    : evidenceReasons || 'The model returned no spatial attribution for this view';
  const hasAttention =
    Number.isFinite(attention) && (attention as number) >= 0 && (attention as number) <= 1;
  const status = error
    ? '读取失败'
    : !view.media_url
      ? '缺少视频'
      : view.media_kind !== 'video'
        ? '静态证据帧'
        : loading
          ? '正在加载'
          : position.state === 'before'
            ? '尚未进入该机位片段'
            : position.state === 'after'
              ? '该机位片段已结束'
              : waiting
                ? '正在缓冲'
                : null;
  return (
    <article
      className={`se-tile ${selected ? 'se-primary' : ''}`}
      data-presentation-time={presentationTimeS ?? ''}
      data-focus-source={focus?.source ?? 'none'}
      data-focus-rect={rect?.join(',') ?? ''}
      data-overlay-strength={strength}
      data-loading={loading}
      data-waiting={waiting}
    >
      <div className="se-tile-media" ref={container}>
        {view.media_url ? (
          view.media_kind === 'video' ? (
            <video
              ref={(node) => {
                media.current = node;
                register(view.camera_id, node);
              }}
              src={mediaUrl(view.media_url)}
              muted
              playsInline
              preload="auto"
              onLoadedMetadata={() => {
                measure();
                onMetadata(view.camera_id);
              }}
              onLoadedData={(event) => {
                measure();
                captureReadyPausedFrame(event.currentTarget);
                onReady(view.camera_id);
              }}
              onCanPlay={(event) => {
                captureReadyPausedFrame(event.currentTarget);
                onReady(view.camera_id);
              }}
              onSeeked={(event) => captureReadyPausedFrame(event.currentTarget)}
              onTimeUpdate={(event) => captureReadyPausedFrame(event.currentTarget)}
              onPause={(event) => captureReadyPausedFrame(event.currentTarget)}
              onWaiting={(event) => {
                if (loadedPausedMediaTime(event.currentTarget) !== null)
                  captureReadyPausedFrame(event.currentTarget);
                else onWaiting(view.camera_id, true);
              }}
              onPlaying={() => onWaiting(view.camera_id, false)}
              onError={() => onError(view.camera_id)}
            />
          ) : (
            <img
              ref={(node) => {
                media.current = node;
              }}
              src={mediaUrl(view.media_url)}
              alt={`${view.display_name}证据帧`}
              onLoad={measure}
              onError={() => onError(view.camera_id)}
            />
          )
        ) : (
          <div className="se-no-media">暂无可播放视频</div>
        )}
        {bounds && rect && strength > 0 && (
          <div className="se-coordinate-layer" style={bounds}>
            <div
              className={`se-focus-region ${tier} ${box?.source ?? ''}`}
              style={{
                left: `${rect[0]}%`,
                top: `${rect[1]}%`,
                width: `${rect[2]}%`,
                height: `${rect[3]}%`,
                opacity: strength,
              }}
              aria-hidden="true"
            />
          </div>
        )}
        <button
          type="button"
          className="se-view-label"
          onClick={onSelect}
          aria-pressed={selected}
          title="设为主视角，保持共同播放时间"
        >
          <strong>机位 {index + 1}</strong>
          <span>{view.display_name}</span>
          {hasAttention && <small>Weight {Math.round((attention as number) * 100)}%</small>}
        </button>
        <span className="se-offset">
          {view.sync_offset_ms >= 0 ? '+' : ''}
          {view.sync_offset_ms} ms
        </span>
        {status && (
          <div
            className={`se-video-status ${error ? 'is-error' : ''}`}
            role={error ? 'alert' : 'status'}
          >
            <span>{status}</span>
            {error && (
              <button type="button" onClick={() => onRetry(view.camera_id)}>
                重试
              </button>
            )}
          </div>
        )}
        {analysisAvailable && showAttention && (
          <div className={`se-source-label ${tier}`}>
            <span className="se-source-chip" title={spatialTitle}>
              <small>Spatial</small>
              {tier === 'hidden'
                ? 'Attribution Hidden'
                : box
                  ? spatialSourceLabel(box)
                  : 'Attribution Unavailable'}
            </span>
            <span
              className="se-source-chip"
              title={
                attentionWindow
                  ? attentionWindow.source === 'gradcam'
                    ? 'Grad-CAM temporal response'
                    : 'Selected review interval'
                  : evidenceReasons || 'No reliable model-derived temporal focus is available'
              }
            >
              <small>Temporal</small>
              {attentionWindow
                ? temporalFocusLabel(attentionWindow.source)
                : 'Temporal Unavailable'}
            </span>
            {tier !== 'normal' && (
              <span className="se-source-quality" title={evidenceReasons || spatialTitle}>
                {tier === 'hidden' ? 'Low Signal · Hidden' : 'Limited Signal'}
              </span>
            )}
          </div>
        )}
      </div>
    </article>
  );
}

export default function SynchronizedEvidencePlayer({
  caseData,
  decision,
  onReadinessChange,
  focusRequest,
  onTimeChange,
}: SynchronizedEvidencePlayerProps) {
  const root = useRef<HTMLDivElement>(null);
  const videos = useRef(new Map<string, HTMLVideoElement>());
  const pendingPlay = useRef(new Set<string>());
  const [durations, setDurations] = useState<Record<string, number>>({});
  const [ready, setReady] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [waiting, setWaiting] = useState<Record<string, boolean>>({});
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [muted, setMuted] = useState(true);
  const [showAttention, setShowAttention] = useState(true);
  const [primary, setPrimary] = useState(caseData.videos[0]?.camera_id ?? '');
  const [controlError, setControlError] = useState<string | null>(null);
  const commonRef = useRef(0);
  const playingRef = useRef(false);
  const errorsRef = useRef(errors);
  const waitingRef = useRef(waiting);
  const activeCaseId = useRef(caseData.case_id);
  const referenceClock = useRef({ id: '', time: -1, advancedAt: 0 });
  const callbacks = useRef({ onReadinessChange, onTimeChange });
  const clock = useRef({ time: 0, timestamp: 0 });
  const consumedFocus = useRef<string | number | null>(null);
  errorsRef.current = errors;
  waitingRef.current = waiting;
  activeCaseId.current = caseData.case_id;
  callbacks.current = { onReadinessChange, onTimeChange };
  const duration = useMemo(
    () =>
      timelineDuration(
        caseData.videos.filter(
          (view) => !errors[view.camera_id] && view.media_url && view.media_kind === 'video',
        ),
        durations,
      ),
    [caseData.videos, durations, errors],
  );
  const readiness = useMemo<EvidenceReadiness>(() => {
    const missingCameraIds = caseData.videos
      .filter((view) => !view.media_url || view.media_kind !== 'video')
      .map((view) => view.camera_id);
    const failedCameraIds = caseData.videos
      .filter((view) => errors[view.camera_id])
      .map((view) => view.camera_id);
    const loadingCameraIds = caseData.videos
      .filter(
        (view) =>
          view.media_url &&
          view.media_kind === 'video' &&
          !errors[view.camera_id] &&
          !ready[view.camera_id],
      )
      .map((view) => view.camera_id);
    const playableVideoCount = caseData.videos.filter(
      (view) =>
        view.media_url &&
        view.media_kind === 'video' &&
        ready[view.camera_id] &&
        !errors[view.camera_id],
    ).length;
    return {
      allVideosReady: caseData.videos.length > 0 && playableVideoCount === caseData.videos.length,
      playableVideoCount,
      totalViewCount: caseData.videos.length,
      loadingCameraIds,
      missingCameraIds,
      failedCameraIds,
    };
  }, [caseData.videos, errors, ready]);
  useEffect(() => {
    callbacks.current.onReadinessChange?.(readiness);
  }, [readiness]);
  const pauseAll = useCallback(() => {
    videos.current.forEach((video) => video.pause());
  }, []);
  const register = useCallback((id: string, node: HTMLVideoElement | null) => {
    if (node) videos.current.set(id, node);
    else videos.current.delete(id);
  }, []);
  useEffect(() => {
    pauseAll();
    playingRef.current = false;
    commonRef.current = 0;
    consumedFocus.current = null;
    pendingPlay.current.clear();
    const nextDurations: Record<string, number> = {};
    const nextReady: Record<string, boolean> = {};
    const nextErrors: Record<string, string> = {};
    // Cached media can finish loading before effects run, including StrictMode's second mount.
    // Rebuild state from mounted elements instead of waiting for already-fired media events.
    caseData.videos.forEach((view) => {
      const video = videos.current.get(view.camera_id);
      if (!video || !view.media_url || view.media_kind !== 'video') return;
      if (video.error) {
        nextErrors[view.camera_id] = '视频资源不可用，请检查媒体连接或编码';
        return;
      }
      if (video.readyState >= 1 && Number.isFinite(video.duration) && video.duration > 0) {
        nextDurations[view.camera_id] = video.duration;
        nextReady[view.camera_id] = video.readyState >= 2;
        const initialTime = mediaPosition(0, view, video.duration).timeS;
        if (Math.abs(video.currentTime - initialTime) > 0.001) video.currentTime = initialTime;
      }
    });
    errorsRef.current = nextErrors;
    waitingRef.current = {};
    referenceClock.current = { id: '', time: -1, advancedAt: 0 };
    setPlaying(false);
    setTime(0);
    setDurations(nextDurations);
    setReady(nextReady);
    setErrors(nextErrors);
    setWaiting({});
    setControlError(null);
    setPrimary(caseData.videos[0]?.camera_id ?? '');
    return pauseAll;
    // A catalog refresh must not reset an already playing case.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseData.case_id, pauseAll]);

  function markError(id: string, message = '视频资源不可用，请检查媒体连接或编码') {
    videos.current.get(id)?.pause();
    errorsRef.current = { ...errorsRef.current, [id]: message };
    setErrors((previous) => (previous[id] === message ? previous : { ...previous, [id]: message }));
    setReady((previous) => (previous[id] ? { ...previous, [id]: false } : previous));
  }
  function playVideo(id: string, video: HTMLVideoElement) {
    if (!video.paused || pendingPlay.current.has(id) || errorsRef.current[id]) return;
    pendingPlay.current.add(id);
    const caseId = caseData.case_id;
    void video
      .play()
      .catch((error: unknown) => {
        // Seeks can abort a pending play request without indicating a broken source.
        if (error instanceof DOMException && error.name === 'AbortError') return;
        if (videos.current.get(id) === video && caseId === activeCaseId.current)
          markError(id, '该机位无法播放，请重试');
      })
      .finally(() => pendingPlay.current.delete(id));
  }
  function synchronize(target: number, play: boolean, force = false) {
    caseData.videos.forEach((view) => {
      const video = videos.current.get(view.camera_id);
      if (
        !video ||
        errorsRef.current[view.camera_id] ||
        video.readyState < 1 ||
        !Number.isFinite(video.duration)
      )
        return;
      const position = mediaPosition(target, view, video.duration);
      video.playbackRate = rate;
      video.muted = muted || view.camera_id !== primary;
      if (force || (!video.seeking && Math.abs(video.currentTime - position.timeS) > 0.18))
        video.currentTime = position.timeS;
      if (play && position.state === 'active') playVideo(view.camera_id, video);
      else video.pause();
    });
  }
  function seek(target: number) {
    const next = clamp(target, 0, duration || Math.max(0, target));
    commonRef.current = next;
    referenceClock.current = { id: '', time: -1, advancedAt: 0 };
    clock.current = { time: next, timestamp: performance.now() };
    setTime(next);
    callbacks.current.onTimeChange?.(next);
    synchronize(next, playingRef.current, true);
  }
  function onMetadata(id: string) {
    const video = videos.current.get(id);
    if (!video || !Number.isFinite(video.duration) || video.duration <= 0) return;
    setDurations((previous) =>
      previous[id] === video.duration ? previous : { ...previous, [id]: video.duration },
    );
    const view = caseData.videos.find((item) => item.camera_id === id);
    if (view) video.currentTime = mediaPosition(commonRef.current, view, video.duration).timeS;
    video.playbackRate = rate;
    video.muted = muted || id !== primary;
  }
  function onReady(id: string) {
    const video = videos.current.get(id);
    if (!video || !Number.isFinite(video.duration) || video.duration <= 0 || video.readyState < 2)
      return;
    setReady((previous) => (previous[id] ? previous : { ...previous, [id]: true }));
    setWaiting((previous) => (previous[id] ? { ...previous, [id]: false } : previous));
    if (playingRef.current) synchronize(commonRef.current, true);
  }
  function retry(id: string) {
    const nextErrors = { ...errorsRef.current };
    delete nextErrors[id];
    errorsRef.current = nextErrors;
    setErrors((previous) => {
      const next = { ...previous };
      delete next[id];
      return next;
    });
    setReady((previous) => ({ ...previous, [id]: false }));
    videos.current.get(id)?.load();
  }
  function togglePlayback() {
    setControlError(null);
    if (playingRef.current) {
      playingRef.current = false;
      setPlaying(false);
      pauseAll();
      return;
    }
    if (!readiness.playableVideoCount || !duration) return;
    const next = commonRef.current >= duration - 0.01 ? 0 : commonRef.current;
    seek(next);
    clock.current = { time: next, timestamp: performance.now() };
    playingRef.current = true;
    setPlaying(true);
    synchronize(next, true, true);
  }
  function step(direction: -1 | 1) {
    playingRef.current = false;
    setPlaying(false);
    pauseAll();
    seek(commonRef.current + direction * 0.04);
  }
  useEffect(() => {
    videos.current.forEach((video, id) => {
      video.playbackRate = rate;
      video.muted = muted || id !== primary;
    });
    clock.current = { time: commonRef.current, timestamp: performance.now() };
  }, [rate, muted, primary]);
  useEffect(() => {
    if (!playing || !duration) return;
    let frame = 0;
    let lastRender = 0;
    let lastPublish = 0;
    const update = (now: number) => {
      const elapsedTime = clock.current.time + ((now - clock.current.timestamp) / 1000) * rate;
      let referenceTime: number | null = null;
      let referenceAge = Number.POSITIVE_INFINITY;
      let next = elapsedTime;
      const reference = caseData.videos.find((view) => {
        const video = videos.current.get(view.camera_id);
        return (
          video &&
          !errorsRef.current[view.camera_id] &&
          !waitingRef.current[view.camera_id] &&
          !video.paused &&
          !video.seeking &&
          video.readyState >= 3 &&
          mediaPosition(next, view, video.duration).state === 'active'
        );
      });
      const master = reference ? videos.current.get(reference.camera_id) : undefined;
      if (master && reference && !master.paused && !master.seeking && master.readyState >= 3) {
        const mediaTime = commonTime(master.currentTime, reference);
        const previousClock = referenceClock.current;
        if (
          previousClock.id !== reference.camera_id ||
          Math.abs(mediaTime - previousClock.time) > 0.002
        ) {
          referenceClock.current = { id: reference.camera_id, time: mediaTime, advancedAt: now };
        }
        // A stalled reference must not freeze healthy cameras or truncate longer views.
        referenceTime = mediaTime;
        referenceAge = now - referenceClock.current.advancedAt;
      }
      next = playbackClockTime(
        elapsedTime,
        commonRef.current,
        duration,
        referenceTime,
        referenceAge,
      );
      commonRef.current = next;
      synchronize(next, true);
      // Keep the media clock precise while throttling React and parent form updates.
      if (now - lastRender >= 125 || next >= duration) {
        setTime(next);
        lastRender = now;
      }
      if (now - lastPublish >= 250 || next >= duration) {
        callbacks.current.onTimeChange?.(next);
        lastPublish = now;
      }
      if (next >= duration) {
        playingRef.current = false;
        setPlaying(false);
        pauseAll();
        return;
      }
      frame = window.requestAnimationFrame(update);
    };
    frame = window.requestAnimationFrame(update);
    return () => window.cancelAnimationFrame(frame);
    // Synchronization reads the latest media elements instead of storing per-frame React state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, duration, rate, muted, primary, caseData.case_id, pauseAll]);
  useEffect(() => {
    if (
      !focusRequest ||
      !duration ||
      consumedFocus.current === focusRequest.token ||
      !Number.isFinite(focusRequest.commonTimeS)
    )
      return;
    consumedFocus.current = focusRequest.token;
    playingRef.current = false;
    setPlaying(false);
    pauseAll();
    seek(focusRequest.commonTimeS);
    // The request token permits repeated seeks to the same attention peak.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusRequest?.token, duration, caseData.case_id, pauseAll]);

  async function fullscreen() {
    try {
      if (document.fullscreenElement === root.current) await document.exitFullscreen();
      else await root.current?.requestFullscreen();
    } catch {
      setControlError('当前浏览器无法进入全屏');
    }
  }
  const canPlay = readiness.playableVideoCount > 0 && duration > 0;
  return (
    <div className="se-player" ref={root}>
      <div className="se-evidence-heading">
        <span>
          同步证据 <small>{caseData.videos.length} 个机位</small>
        </span>
        {decision && (
          <label className="se-attention-toggle">
            <input
              type="checkbox"
              checked={showAttention}
              onChange={(event) => setShowAttention(event.target.checked)}
            />
            Visual Attribution
          </label>
        )}
      </div>
      <div className={`se-mosaic se-count-${caseData.videos.length}`}>
        {caseData.videos.map((view, index) => (
          <EvidenceTile
            key={`${caseData.case_id}:${view.camera_id}`}
            view={view}
            index={index}
            selected={view.camera_id === primary}
            onSelect={() => setPrimary(view.camera_id)}
            register={register}
            onMetadata={onMetadata}
            onReady={onReady}
            onError={(id) => markError(id)}
            onWaiting={(id, value) => {
              waitingRef.current = { ...waitingRef.current, [id]: value };
              setWaiting((previous) =>
                previous[id] === value ? previous : { ...previous, [id]: value },
              );
            }}
            onRetry={retry}
            error={errors[view.camera_id]}
            loading={!ready[view.camera_id]}
            waiting={Boolean(waiting[view.camera_id])}
            duration={durations[view.camera_id] ?? 0}
            commonTimeS={time}
            box={decision?.localization[view.camera_id]}
            attention={decision?.view_attention[index]}
            showAttention={showAttention}
            analysisAvailable={decision !== null}
            unavailableReason={diagnosticReason(decision, index)}
          />
        ))}
        {!caseData.videos.length && <div className="se-no-media">该案例没有视频机位</div>}
      </div>
      <div className="se-tools">
        <button
          type="button"
          aria-label={playing ? '暂停全部机位' : '播放全部机位'}
          disabled={!canPlay}
          onClick={togglePlayback}
        >
          <Icon name={playing ? 'pause' : 'play'} />
        </button>
        <button
          type="button"
          className="se-step"
          aria-label="全部机位后退 0.04 秒"
          disabled={!canPlay}
          onClick={() => step(-1)}
        >
          −0.04s
        </button>
        <button
          type="button"
          className="se-step"
          aria-label="全部机位前进 0.04 秒"
          disabled={!canPlay}
          onClick={() => step(1)}
        >
          +0.04s
        </button>
        <span className="se-time" title="共同播放时间">
          {time.toFixed(2)} <em>/</em> {duration.toFixed(2)} s
        </span>
        <input
          aria-label="多机位共享时间轴"
          style={{ '--se-progress': `${timelinePercent(time, duration)}%` } as CSSProperties}
          type="range"
          min="0"
          max={duration || 1}
          step="0.01"
          value={Math.min(time, duration || 1)}
          disabled={!canPlay}
          onChange={(event) => seek(Number(event.target.value))}
        />
        <select
          aria-label="全部机位播放速度"
          value={rate}
          onChange={(event) => setRate(Number(event.target.value))}
        >
          {[0.25, 0.5, 1, 1.5, 2].map((speed) => (
            <option value={speed} key={speed}>
              {speed}×
            </option>
          ))}
        </select>
        <button
          type="button"
          aria-label={muted ? '开启主视角声音' : '静音'}
          onClick={() => setMuted(!muted)}
        >
          <Icon name={muted ? 'mute' : 'volume'} />
        </button>
        <button type="button" aria-label="同步证据全屏" onClick={() => void fullscreen()}>
          <Icon name="expand" />
        </button>
      </div>
      {controlError && (
        <p className="se-control-error" role="alert">
          {controlError}
        </p>
      )}
      <div className="se-timelines" aria-label="各机位证据时间轴">
        {caseData.videos.map((view, index) => {
          const box = decision?.localization[view.camera_id];
          const window = box ? localizationWindow(box) : null;
          const range = !errors[view.camera_id]
            ? viewRange(view, durations[view.camera_id] ?? 0)
            : null;
          const start = window ? commonTime(window.startS, view) : 0;
          const end = window ? commonTime(window.endS, view) : 0;
          const peak = window ? commonTime(window.peakS, view) : 0;
          const windowVisible = window && range && end >= range.startS && start <= range.endS;
          const windowStart = timelinePercent(Math.max(start, range?.startS ?? 0), duration);
          const windowEnd = timelinePercent(Math.min(end, range?.endS ?? duration), duration);
          return (
            <div className="se-timeline-row" key={view.camera_id}>
              <span className="se-track-label" title={view.display_name}>
                机位 {index + 1}
              </span>
              <div
                className="se-timeline-track"
                role="slider"
                tabIndex={canPlay ? 0 : -1}
                aria-label={`机位 ${index + 1} 同期时间轴`}
                aria-valuemin={0}
                aria-valuemax={duration}
                aria-valuenow={Math.min(time, duration)}
                aria-disabled={!canPlay}
                onPointerDown={(event) => {
                  if (!canPlay) return;
                  event.currentTarget.setPointerCapture(event.pointerId);
                  const rect = event.currentTarget.getBoundingClientRect();
                  seek(clamp((event.clientX - rect.left) / rect.width, 0, 1) * duration);
                }}
                onPointerMove={(event) => {
                  if (!canPlay || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
                  const rect = event.currentTarget.getBoundingClientRect();
                  seek(clamp((event.clientX - rect.left) / rect.width, 0, 1) * duration);
                }}
                onKeyDown={(event) => {
                  if (!canPlay) return;
                  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                    event.preventDefault();
                    step(event.key === 'ArrowLeft' ? -1 : 1);
                  } else if (event.key === 'Home' || event.key === 'End') {
                    event.preventDefault();
                    seek(event.key === 'Home' ? 0 : duration);
                  }
                }}
              >
                {range && (
                  <span
                    className="se-media-range"
                    style={{
                      left: `${timelinePercent(range.startS, duration)}%`,
                      width: `${Math.max(0, timelinePercent(range.endS, duration) - timelinePercent(range.startS, duration))}%`,
                    }}
                  />
                )}
                {windowVisible && (
                  <span
                    className={`se-temporal-window ${window.source} ${box ? localizationTier(box) : ''}`}
                    style={{
                      left: `${windowStart}%`,
                      width: `${Math.max(0.3, windowEnd - windowStart)}%`,
                    }}
                    title={`${window.source === 'gradcam' ? 'Temporal Saliency' : 'Focus Window'} · ${start.toFixed(2)}–${end.toFixed(2)} s`}
                  />
                )}
                {windowVisible && peak >= (range?.startS ?? 0) && peak <= (range?.endS ?? 0) && (
                  <span
                    className="se-temporal-peak"
                    style={{ left: `${timelinePercent(peak, duration)}%` }}
                    title={`Focus Peak · ${peak.toFixed(2)} s`}
                  />
                )}
                <span
                  className="se-playhead"
                  style={{ left: `${timelinePercent(time, duration)}%` }}
                />
              </div>
              <span className="se-track-time">
                {ready[view.camera_id] && !errors[view.camera_id]
                  ? `${mediaPosition(time, view, durations[view.camera_id] ?? 0).timeS.toFixed(2)} / ${(durations[view.camera_id] ?? 0).toFixed(2)} s`
                  : errors[view.camera_id]
                    ? '读取失败'
                    : !view.media_url || view.media_kind !== 'video'
                      ? '缺少视频'
                      : '加载中'}
              </span>
            </div>
          );
        })}
      </div>
      <div className="se-timeline-legend">
        <span>
          <i className="se-legend-range" />
          Evidence Range
        </span>
        {decision &&
          (caseData.videos.some((view) => {
            const box = decision.localization[view.camera_id];
            return box && localizationWindow(box) !== null;
          }) ? (
            <span>
              <i className="se-legend-focus" />
              Focus Window
            </span>
          ) : (
            <span title="No reliable model-derived temporal focus is available">
              Temporal Unavailable
            </span>
          ))}
      </div>
    </div>
  );
}
