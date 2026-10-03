import { useEffect, useRef, useState } from 'react';
import { Icon, Empty } from './UI';
import { mediaUrl } from '../api';
import { observeVideoPresentation } from './multiview/videoPresentation';
import { regionForPresentation, type MediaRegion } from './mediaRegion';
export function timecode(value: number) {
  if (!Number.isFinite(value)) return '0:00';
  return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`;
}
export default function MediaPlayer({
  src,
  poster,
  label,
  onTime,
  seekTo,
  seekToken,
  candidateRegion,
}: {
  src?: string | null;
  poster?: string | null;
  label?: string;
  onTime?: (time: number) => void;
  seekTo?: number;
  seekToken?: number;
  candidateRegion?: MediaRegion;
}) {
  const video = useRef<HTMLVideoElement>(null),
    frame = useRef<HTMLDivElement>(null);
  const [playing, setPlaying] = useState(false),
    [time, setTime] = useState(0),
    [duration, setDuration] = useState(0),
    [muted, setMuted] = useState(true),
    [speed, setSpeed] = useState('1'),
    [presentedTime, setPresentedTime] = useState<number | null>(null),
    [nativeSize, setNativeSize] = useState({ width: 0, height: 0 }),
    [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setTime(0);
    setDuration(0);
    setPlaying(false);
    setError(null);
    setPresentedTime(null);
    setNativeSize({ width: 0, height: 0 });
  }, [src]);
  useEffect(() => {
    if (!video.current || !candidateRegion) return;
    return observeVideoPresentation(video.current, setPresentedTime);
  }, [src, Boolean(candidateRegion)]);
  useEffect(() => {
    if (video.current && seekTo !== undefined) video.current.currentTime = Math.max(0, seekTo);
  }, [seekTo, seekToken]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && document.fullscreenElement === frame.current)
        void document.exitFullscreen();
    };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, []);
  const play = async () => {
    const v = video.current;
    if (!v) return;
    if (v.paused) {
      try {
        await v.play();
      } catch {
        setError('视频暂时无法播放，请稍后重试');
      }
    } else v.pause();
  };
  const fullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await frame.current?.requestFullscreen();
    } catch {
      setError('当前浏览器不支持全屏');
    }
  };
  const contactRegion = regionForPresentation(
    candidateRegion,
    presentedTime,
    nativeSize.width,
    nativeSize.height,
  );
  return (
    <div
      className="media-frame"
      ref={frame}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.code === 'Space') {
          e.preventDefault();
          void play();
        }
        if (e.key === 'ArrowRight' && video.current) video.current.currentTime += 5;
        if (e.key === 'ArrowLeft' && video.current)
          video.current.currentTime = Math.max(0, video.current.currentTime - 5);
      }}
    >
      {src ? (
        <video
          key={src}
          ref={video}
          src={mediaUrl(src)}
          poster={mediaUrl(poster)}
          playsInline
          preload="metadata"
          muted={muted}
          onLoadedMetadata={(e) => {
            setDuration(e.currentTarget.duration);
            setNativeSize({
              width: e.currentTarget.videoWidth,
              height: e.currentTarget.videoHeight,
            });
            e.currentTarget.playbackRate = Number(speed);
            if (seekTo !== undefined)
              e.currentTarget.currentTime = Math.min(seekTo, e.currentTarget.duration);
          }}
          onTimeUpdate={(e) => {
            setTime(e.currentTarget.currentTime);
            onTime?.(e.currentTarget.currentTime);
          }}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onError={() => setError('视频资源不可用，请检查后端媒体连接')}
          onClick={() => void play()}
        />
      ) : (
        <Empty>选择输入视频</Empty>
      )}
      {contactRegion && (
        <svg
          className="media-contact-region"
          viewBox={`0 0 ${contactRegion.width} ${contactRegion.height}`}
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="检测到的事件区域"
        >
          <rect
            x={contactRegion.box[0]}
            y={contactRegion.box[1]}
            width={contactRegion.box[2] - contactRegion.box[0]}
            height={contactRegion.box[3] - contactRegion.box[1]}
            fill="rgba(247, 191, 54, 0.10)"
            stroke="#f7bf36"
            strokeWidth="2"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      )}
      {label && <span className="media-label">{label}</span>}
      {error && (
        <div className="media-error" role="alert">
          {error}
        </div>
      )}
      <div className="player-controls">
        <button aria-label={playing ? '暂停' : '播放'} disabled={!src} onClick={() => void play()}>
          <Icon name={playing ? 'pause' : 'play'} />
        </button>
        <span className="player-time">
          {timecode(time)} <em>/</em> {timecode(duration)}
        </span>
        <input
          aria-label="播放进度"
          type="range"
          min="0"
          max={duration || 1}
          step="0.05"
          value={Math.min(time, duration || 1)}
          disabled={!duration}
          onChange={(e) => {
            const nextTime = Number(e.target.value);
            setTime(nextTime);
            if (video.current) video.current.currentTime = nextTime;
          }}
        />
        <select
          aria-label="播放速度"
          value={speed}
          onChange={(e) => {
            setSpeed(e.target.value);
            if (video.current) video.current.playbackRate = Number(e.target.value);
          }}
        >
          {[0.5, 1, 1.5, 2].map((n) => (
            <option key={n} value={n}>
              {n}×
            </option>
          ))}
        </select>
        <button aria-label={muted ? '开启声音' : '静音'} onClick={() => setMuted(!muted)}>
          <Icon name={muted ? 'mute' : 'volume'} />
        </button>
        <button aria-label="全屏" onClick={() => void fullscreen()}>
          <Icon name="expand" />
        </button>
      </div>
    </div>
  );
}
