import { useRef, useState } from 'react';
import { mediaUrl } from '../../api';
import type { CalibrationMetadata, CalibrationTrack } from '../../types/calibration';
import { validCalibrationBox } from './calibrationPresentation';

export default function BestCrop({
  source,
  track,
  metadata,
}: {
  source: string;
  track: CalibrationTrack;
  metadata: CalibrationMetadata;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [ready, setReady] = useState(false),
    [error, setError] = useState(false);
  function draw(video: HTMLVideoElement) {
    const box = track.representative_bbox;
    const target = canvas.current;
    if (
      !target ||
      !validCalibrationBox(box, metadata.width, metadata.height) ||
      video.videoWidth !== metadata.width ||
      video.videoHeight !== metadata.height ||
      Math.abs(video.currentTime * 1000 - track.representative_timestamp_ms) >
        1500 / Math.max(metadata.fps, 1)
    )
      return;
    try {
      const context = target.getContext('2d');
      if (!context) return;
      const [x1, y1, x2, y2] = box,
        width = x2 - x1,
        height = y2 - y1;
      const scale = Math.min(target.width / width, target.height / height);
      context.clearRect(0, 0, target.width, target.height);
      context.drawImage(
        video,
        x1,
        y1,
        width,
        height,
        (target.width - width * scale) / 2,
        (target.height - height * scale) / 2,
        width * scale,
        height * scale,
      );
      setReady(true);
    } catch {
      setError(true);
    }
  }
  return (
    <div className="cal-best-crop">
      <video
        src={mediaUrl(source)}
        muted
        playsInline
        preload="auto"
        className="cal-crop-source"
        aria-hidden="true"
        tabIndex={-1}
        onLoadedMetadata={(event) => {
          const video = event.currentTarget;
          video.currentTime = track.representative_timestamp_ms / 1000;
        }}
        onLoadedData={(event) => draw(event.currentTarget)}
        onSeeked={(event) => draw(event.currentTarget)}
        onError={() => setError(true)}
      />
      <canvas
        ref={canvas}
        width="224"
        height="188"
        role="img"
        aria-label={`球员 ${track.track_id} 的代表画面裁剪`}
      />
      {!ready ? <span>{error ? '裁剪暂不可用，可在主画面确认' : '读取代表画面…'}</span> : null}
    </div>
  );
}
