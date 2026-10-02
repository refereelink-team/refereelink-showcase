import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { mediaUrl } from '../../api';
import type { CalibrationMetadata, CalibrationSnapshot } from '../../types/calibration';
import {
  calibrationFrameAt,
  labelColor,
  labelTitle,
  validCalibrationBox,
} from './calibrationPresentation';

export default function CalibrationVideo({
  source,
  poster,
  metadata,
  snapshot,
  selected,
  videoRef,
  active,
  onTime,
  onDuration,
  onSelect,
  onReady,
}: {
  source: string;
  poster: string | null;
  metadata: CalibrationMetadata | null;
  snapshot: CalibrationSnapshot | null;
  selected: number | null;
  videoRef: RefObject<HTMLVideoElement | null>;
  active: boolean;
  onTime: (seconds: number) => void;
  onDuration: (seconds: number) => void;
  onSelect: (id: number) => void;
  onReady?: (video: HTMLVideoElement) => void;
}) {
  const [time, setTime] = useState(0),
    [dimensions, setDimensions] = useState([16, 9]),
    [ready, setReady] = useState(false),
    [seeking, setSeeking] = useState(false),
    [error, setError] = useState<string | null>(null);
  const onTimeRef = useRef(onTime);
  onTimeRef.current = onTime;
  useEffect(() => {
    setReady(false);
    setTime(0);
    setError(null);
    setSeeking(false);
  }, [source]);
  useEffect(() => {
    if (!active) videoRef.current?.pause();
  }, [active, videoRef]);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let disposed = false,
      handle = 0;
    const update = (_now: number, frame: VideoFrameCallbackMetadata) => {
      if (disposed) return;
      setTime(frame.mediaTime);
      onTimeRef.current(frame.mediaTime);
      handle = video.requestVideoFrameCallback(update);
    };
    if ('requestVideoFrameCallback' in video) handle = video.requestVideoFrameCallback(update);
    return () => {
      disposed = true;
      if ('cancelVideoFrameCallback' in video) video.cancelVideoFrameCallback(handle);
    };
  }, [source, videoRef]);
  const aligned =
    !metadata || (ready && dimensions[0] === metadata.width && dimensions[1] === metadata.height);
  const frame = metadata && aligned && !seeking ? calibrationFrameAt(metadata, time) : null;
  return (
    <div className="cal-video-stage" style={{ aspectRatio: dimensions[0] + ' / ' + dimensions[1] }}>
      <video
        ref={videoRef}
        src={mediaUrl(source)}
        poster={mediaUrl(poster)}
        controls
        playsInline
        muted
        preload="metadata"
        onLoadedMetadata={(event) => {
          const video = event.currentTarget;
          setDimensions([video.videoWidth || 16, video.videoHeight || 9]);
          onDuration(Number.isFinite(video.duration) ? video.duration : 0);
          setReady(true);
          onReady?.(video);
        }}
        onDurationChange={(event) => {
          const duration = event.currentTarget.duration;
          if (Number.isFinite(duration)) onDuration(duration);
        }}
        onSeeking={() => setSeeking(true)}
        onSeeked={(event) => {
          setSeeking(false);
          setTime(event.currentTarget.currentTime);
          onTime(event.currentTarget.currentTime);
        }}
        onTimeUpdate={(event) => {
          setTime(event.currentTarget.currentTime);
          onTime(event.currentTarget.currentTime);
        }}
        onError={() => setError('标定视频暂时不可用，请检查后台连接')}
      />
      {metadata && frame && snapshot ? (
        <svg
          className="cal-box-layer"
          viewBox={`0 0 ${metadata.width} ${metadata.height}`}
          aria-label="点击画面中的球员进行标注"
        >
          {frame.players.map((player) => {
            if (!validCalibrationBox(player.bbox, metadata.width, metadata.height)) return null;
            const [x1, y1, x2, y2] = player.bbox;
            const label = snapshot.labels[String(player.track_id)] || null;
            const font = Math.max(18, metadata.width / 70);
            return (
              <g
                key={player.track_id}
                className={selected === player.track_id ? 'cal-box selected' : 'cal-box'}
                style={{ color: labelColor(label) }}
                role="button"
                tabIndex={0}
                aria-label={`球员 ${player.track_id}，${labelTitle(label)}`}
                onClick={() => {
                  videoRef.current?.pause();
                  onSelect(player.track_id);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    event.stopPropagation();
                    videoRef.current?.pause();
                    onSelect(player.track_id);
                  }
                }}
              >
                <rect
                  x={x1}
                  y={y1}
                  width={x2 - x1}
                  height={y2 - y1}
                  vectorEffect="non-scaling-stroke"
                />
                <rect
                  className="cal-box-tag"
                  x={x1}
                  y={Math.max(0, y1 - font * 1.4)}
                  width={font * 3.5}
                  height={font * 1.4}
                />
                <text x={x1 + font * 0.25} y={Math.max(font, y1 - font * 0.3)} fontSize={font}>
                  #{player.track_id}
                </text>
              </g>
            );
          })}
        </svg>
      ) : null}
      {error || (ready && !aligned) ? (
        <div className="cal-media-error" role="alert">
          {error || '视频与跟踪框尺寸不匹配，暂不显示跟踪框'}
        </div>
      ) : null}
    </div>
  );
}
