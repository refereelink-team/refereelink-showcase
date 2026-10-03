// Track displayed video frames independently from pending timeline seek targets.
import { loadedPausedMediaTime } from './evidencePlayback.ts';

export function observeVideoPresentation(
  video: HTMLVideoElement,
  onPresentation: (mediaTimeS: number | null) => void,
): () => void {
  const usesPresentedFrames = typeof video.requestVideoFrameCallback === 'function';
  let disposed = false;
  let requestId: number | null = null;
  let pendingSeek = video.seeking;
  let frameGeneration = 0;
  let presentationTimeS: number | null = null;
  let lastPresentedFrame: { timeS: number; targetS: number | null } | null = null;

  const publish = (timeS: number | null) => {
    if (disposed || (timeS !== null && (!Number.isFinite(timeS) || timeS < 0))) return;
    presentationTimeS = timeS;
    onPresentation(timeS);
  };
  const captureLoadedFrame = () => {
    const timeS = loadedPausedMediaTime(video);
    if (timeS === null) return false;
    publish(
      lastPresentedFrame &&
        lastPresentedFrame.targetS !== null &&
        Math.abs(lastPresentedFrame.targetS - timeS) < 0.001
        ? lastPresentedFrame.timeS
        : timeS,
    );
    return true;
  };
  const requestPresentedFrame = () => {
    if (!usesPresentedFrames || disposed || requestId !== null) return;
    const generation = frameGeneration;
    requestId = video.requestVideoFrameCallback((_now, metadata) => {
      if (disposed || generation !== frameGeneration) return;
      requestId = null;
      // The compositor can present a seek frame before the seeked event arrives.
      // Its PTS is authoritative even while currentTime targets a later frame.
      if (Number.isFinite(metadata.mediaTime) && metadata.mediaTime >= 0) {
        lastPresentedFrame = {
          timeS: metadata.mediaTime,
          targetS: video.seeking ? null : video.currentTime,
        };
        publish(metadata.mediaTime);
      }
      requestPresentedFrame();
    });
  };
  const invalidate = (event: Event) => {
    if (event.type === 'seeking') {
      // The old frame remains on screen while the decoder loads the new target.
      pendingSeek = true;
      return;
    }
    if (event.type === 'waiting' || event.type === 'stalled') {
      if ((pendingSeek || video.seeking) && presentationTimeS !== null) return;
      // A late preload stall does not invalidate an already loaded paused frame.
      if (captureLoadedFrame()) return;
    }
    if (event.type !== 'waiting' && event.type !== 'stalled') {
      frameGeneration += 1;
      if (requestId !== null) video.cancelVideoFrameCallback(requestId);
      requestId = null;
    }
    pendingSeek = false;
    lastPresentedFrame = null;
    publish(null);
  };
  const resumeFromReadyFrame = () => {
    if (video.seeking || video.readyState < 2) return;
    pendingSeek = false;
    if (!captureLoadedFrame() && !usesPresentedFrames) publish(video.currentTime);
    requestPresentedFrame();
  };
  const captureMediaEvent = () => {
    if (video.seeking || video.readyState < 2) return;
    if (video.paused) captureLoadedFrame();
    else if (!usesPresentedFrames) publish(video.currentTime);
  };
  const invalidatingEvents = ['seeking', 'waiting', 'stalled', 'emptied', 'loadstart', 'error'];
  const resumingEvents = ['loadeddata', 'seeked', 'canplay', 'playing'];
  const mediaEvents = ['timeupdate', 'pause'];
  invalidatingEvents.forEach((event) => video.addEventListener(event, invalidate));
  resumingEvents.forEach((event) => video.addEventListener(event, resumeFromReadyFrame));
  mediaEvents.forEach((event) => video.addEventListener(event, captureMediaEvent));
  resumeFromReadyFrame();
  requestPresentedFrame();

  return () => {
    disposed = true;
    if (requestId !== null) video.cancelVideoFrameCallback(requestId);
    invalidatingEvents.forEach((event) => video.removeEventListener(event, invalidate));
    resumingEvents.forEach((event) => video.removeEventListener(event, resumeFromReadyFrame));
    mediaEvents.forEach((event) => video.removeEventListener(event, captureMediaEvent));
  };
}
