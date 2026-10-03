/** Keep overlays on presented frames rather than the native scrubber's requested time. */
export interface CalibrationMediaElement {
  currentTime: number;
  seeking: boolean;
  addEventListener(type: string, listener: () => void): void;
  removeEventListener(type: string, listener: () => void): void;
  requestVideoFrameCallback?: (
    callback: (now: number, frame: { mediaTime: number }) => void,
  ) => number;
  cancelVideoFrameCallback?: (handle: number) => void;
}

export function observeCalibrationMedia(
  video: CalibrationMediaElement,
  publish: (seconds: number) => void,
  seekingChanged: (seeking: boolean) => void,
) {
  let disposed = false;
  let generation = 0;
  let handle: number | null = null;
  let waitingForSeekFrame = false;
  let deferredFrame: { generation: number; mediaTime: number } | null = null;
  const hasFrameClock = typeof video.requestVideoFrameCallback === 'function';
  function schedule() {
    if (disposed || !hasFrameClock || handle !== null) return;
    const requestedGeneration = generation;
    handle = video.requestVideoFrameCallback!((_now, frame) => {
      // Canceled callbacks may already be queued; they must not alter the new clock.
      if (disposed || requestedGeneration !== generation) return;
      handle = null;
      const matchesSeek = Math.abs(frame.mediaTime - video.currentTime) <= 0.1;
      // Chrome can present a paused seek's only frame before clearing its seeking flag.
      // Retain that actual PTS until seeked rather than waiting for another presentation.
      if (video.seeking) {
        deferredFrame =
          Number.isFinite(frame.mediaTime) && matchesSeek
            ? { generation: requestedGeneration, mediaTime: frame.mediaTime }
            : null;
      }
      if (
        Number.isFinite(frame.mediaTime) &&
        !video.seeking &&
        (!waitingForSeekFrame || matchesSeek)
      ) {
        deferredFrame = null;
        waitingForSeekFrame = false;
        seekingChanged(false);
        publish(frame.mediaTime);
      }
      schedule();
    });
  }
  function invalidate() {
    generation += 1;
    deferredFrame = null;
    if (handle !== null) video.cancelVideoFrameCallback?.(handle);
    handle = null;
  }
  function onSeeking() {
    invalidate();
    waitingForSeekFrame = true;
    seekingChanged(true);
    schedule();
  }
  function onSeeked() {
    if (hasFrameClock) {
      if (
        deferredFrame &&
        deferredFrame.generation === generation &&
        !video.seeking &&
        Math.abs(deferredFrame.mediaTime - video.currentTime) <= 0.1
      ) {
        waitingForSeekFrame = false;
        seekingChanged(false);
        publish(deferredFrame.mediaTime);
      }
      deferredFrame = null;
      // Keep the callback armed: a paused seek may present only one new frame.
      schedule();
    } else {
      waitingForSeekFrame = false;
      seekingChanged(false);
      publish(video.currentTime);
    }
  }
  function onTimeUpdate() {
    if (!hasFrameClock && !video.seeking) publish(video.currentTime);
  }
  const listeners = { seeking: onSeeking, seeked: onSeeked, timeupdate: onTimeUpdate };
  for (const [type, listener] of Object.entries(listeners)) video.addEventListener(type, listener);
  schedule();
  return () => {
    disposed = true;
    invalidate();
    for (const [type, listener] of Object.entries(listeners))
      video.removeEventListener(type, listener);
  };
}
