import { useEffect, useMemo, useRef, useState } from 'react';
import type { CandidateRecord } from '../../events';
import {
  candidateEventTime,
  candidateEmittedTime,
  foulActionLabel,
  targetLabel,
} from '../../events';
import type { FoulFrame, FoulResult } from '../../types/foul';
import { mediaUrl } from '../../api';
import EventTimeline from '../EventTimeline';
import { Alert, Icon } from '../UI';
import { observeVideoPresentation } from '../multiview/videoPresentation';
import {
  emittedCandidateAtTime,
  emittedCandidatesAtTime,
  foulFrameAtTime,
  foulPlayerKey,
  foulTargetMatches,
  foulRegionsAtTime,
  newFoulReplayCycle,
  restartFoulReplayCycle,
  advanceFoulReplayCycle,
  observedPlayer,
  severityLabel,
} from './foulPresentation';
import './foul.css';
const noEvents: CandidateRecord[] = [];

export default function FoulScene({
  source,
  poster,
  result,
  frames,
  selected,
  onSelect,
  onEventsChange,
  loading,
  emptyMessage,
}: {
  source?: string;
  poster?: string | null;
  result: FoulResult | null;
  frames: FoulFrame[];
  selected: CandidateRecord | null;
  onSelect: (record: CandidateRecord | null) => void;
  onEventsChange: (records: CandidateRecord[]) => void;
  loading: boolean;
  emptyMessage: string;
}) {
  const video = useRef<HTMLVideoElement>(null),
    root = useRef<HTMLDivElement>(null),
    replay = useRef(newFoulReplayCycle());
  const [time, setTime] = useState(0),
    [presentedTime, setPresentedTime] = useState<number | null>(null),
    [duration, setDuration] = useState(0),
    [size, setSize] = useState({ width: 0, height: 0 }),
    [playing, setPlaying] = useState(false),
    [muted, setMuted] = useState(true),
    [rate, setRate] = useState(1),
    [showPlayers, setShowPlayers] = useState(false),
    [showEvent, setShowEvent] = useState(true),
    [reviewing, setReviewing] = useState(false),
    [inspected, setInspected] = useState<string | null>(null),
    [error, setError] = useState<string | null>(null),
    [revealed, setRevealed] = useState<string[]>([]);
  const events = result?.events || noEvents;
  const visibleEvents = useMemo(
    () => events.filter((record) => revealed.includes(record.event.id)),
    [events, revealed],
  );
  useEffect(() => onEventsChange(visibleEvents), [visibleEvents, onEventsChange]);
  useEffect(() => {
    const element = video.current;
    if (!element) return;
    return observeVideoPresentation(element, (value) => {
      const previous = replay.current;
      const next = advanceFoulReplayCycle(previous, events, value, element.currentTime);
      if (value !== null && next === previous) return;
      replay.current = next;
      if (next.revealed !== previous.revealed) setRevealed(next.revealed);
      if (next.epoch !== previous.epoch) {
        setReviewing(false);
        setInspected(null);
        onSelect(null);
      }
      setPresentedTime(value);
      if (value !== null) setTime(value);
    });
  }, [source, events, onSelect]);
  useEffect(() => {
    if (!result || loading || !video.current) return;
    void video.current.play().catch(() => setError('点击播放开始'));
  }, [result?.id, result?.revision, loading]);
  const aligned = Boolean(
    result && size.width === result.source.width && size.height === result.source.height,
  );
  const currentFrame = aligned ? foulFrameAtTime(frames, presentedTime, result!.source.fps) : null;
  const players =
    currentFrame?.players.filter((player) => observedPlayer(player, size.width, size.height)) || [];
  const inspectedPlayer = inspected
    ? players.find((player) => foulPlayerKey(player) === inspected)
    : null;
  const prompt = emittedCandidateAtTime(visibleEvents, presentedTime);
  const activePrompts = emittedCandidatesAtTime(visibleEvents, presentedTime);
  useEffect(() => {
    if (playing && !reviewing && prompt) onSelect(prompt);
  }, [prompt?.event.id, playing, reviewing, onSelect]);
  const regions =
    aligned && showEvent ? foulRegionsAtTime(events, presentedTime, size.width, size.height) : [];
  const activeRecord = reviewing ? selected : prompt;
  const reliableTargets = activeRecord?.event.evidence?.involved_targets || [];
  function seek(value: number) {
    const element = video.current;
    if (!element || !duration) return;
    element.pause();
    element.currentTime = Math.max(0, Math.min(duration, value));
    setTime(element.currentTime);
    setInspected(null);
  }
  function inspect(record: CandidateRecord) {
    onSelect(record);
    setReviewing(true);
    seek(candidateEventTime(record));
  }
  async function play() {
    const element = video.current;
    if (!element) return;
    if (!element.paused) {
      element.pause();
      return;
    }
    setReviewing(false);
    try {
      await element.play();
      setError(null);
    } catch {
      setError('视频暂时无法播放，请重试');
    }
  }
  async function fullscreen() {
    try {
      if (document.fullscreenElement === root.current) await document.exitFullscreen();
      else await root.current?.requestFullscreen();
    } catch {
      setError('当前浏览器不支持全屏');
    }
  }
  const sourceUrl = result?.source.url || source;
  function restartAtBeginning() {
    if (
      !video.current ||
      video.current.currentTime > 0.15 ||
      replay.current.lastTime === null ||
      replay.current.lastTime <= 0.15
    )
      return;
    const next = restartFoulReplayCycle(replay.current);
    replay.current = next;
    setRevealed(next.revealed);
    setReviewing(false);
    setInspected(null);
    setPresentedTime(null);
    onSelect(null);
  }
  return (
    <div className="foul-scene" ref={root}>
      <div className="foul-scene-heading">
        <span className="foul-scene-title">
          原始画面 <small>AI Detection</small>
        </span>
        <div className="foul-overlay-options" aria-label="检测叠层">
          <label>
            <input
              type="checkbox"
              checked={showPlayers}
              onChange={(e) => setShowPlayers(e.target.checked)}
            />
            球员跟踪
          </label>
          <label>
            <input
              type="checkbox"
              checked={showEvent}
              onChange={(e) => setShowEvent(e.target.checked)}
            />
            事件区域
          </label>
        </div>
      </div>
      <div
        className="foul-stage"
        tabIndex={0}
        aria-label="犯规证据视频"
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          if (e.code === 'Space') {
            e.preventDefault();
            void play();
          } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
            e.preventDefault();
            setReviewing(false);
            seek(time + (e.key === 'ArrowLeft' ? -0.04 : 0.04));
          }
        }}
      >
        <video
          key={sourceUrl}
          ref={video}
          src={result ? mediaUrl(sourceUrl) : undefined}
          poster={mediaUrl(poster)}
          muted={muted}
          playsInline
          autoPlay={Boolean(result && !loading)}
          loop
          preload="metadata"
          onLoadedMetadata={(e) => {
            const element = e.currentTarget;
            setDuration(element.duration);
            setSize({ width: element.videoWidth, height: element.videoHeight });
            element.playbackRate = rate;
          }}
          onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
          onSeeking={restartAtBeginning}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onError={() => setError('原始视频不可用，请检查后端媒体连接')}
          onClick={() => void play()}
        />
        {aligned && (showPlayers || regions.length > 0) && (
          <svg
            className="foul-detection-overlay"
            viewBox={`0 0 ${size.width} ${size.height}`}
            preserveAspectRatio="xMidYMid meet"
            aria-label="原生检测标注"
          >
            {showPlayers &&
              players.map((player, index) => {
                const box = player.bbox!,
                  key = foulPlayerKey(player);
                const involved = reliableTargets.some((target) =>
                  foulTargetMatches(target, player, players),
                );
                const label = targetLabel(player);
                const labelY = Math.max(0, box[1] - 17);
                return (
                  <g
                    key={key || `observation:${currentFrame!.frame_id}:${index}`}
                    className={`foul-player ${player.team} ${involved ? 'involved' : ''} ${key === inspected ? 'inspected' : ''}`}
                    role={key ? 'button' : undefined}
                    tabIndex={key ? 0 : undefined}
                    aria-label={key ? `查看跟踪目标 ${label}` : undefined}
                    onClick={
                      key
                        ? () => {
                            video.current?.pause();
                            setInspected(key);
                          }
                        : undefined
                    }
                    onKeyDown={
                      key
                        ? (e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              video.current?.pause();
                              setInspected(key);
                            }
                          }
                        : undefined
                    }
                  >
                    <title>{label} · 本次运行的跟踪编号</title>
                    <rect
                      className="foul-player-box"
                      x={box[0]}
                      y={box[1]}
                      width={box[2] - box[0]}
                      height={box[3] - box[1]}
                      vectorEffect="non-scaling-stroke"
                    />
                    <rect
                      className="foul-player-label-bg"
                      x={box[0]}
                      y={labelY}
                      width={Math.max(47, label.length * 6.2 + 10)}
                      height="16"
                      rx="3"
                    />
                    <text x={box[0] + 5} y={labelY + 11}>
                      {label}
                    </text>
                  </g>
                );
              })}
            {regions.map(({ id, region }) => (
              <rect
                key={id}
                className="foul-contact-box"
                role="img"
                aria-label="犯规位置"
                x={region.box[0]}
                y={region.box[1]}
                width={region.box[2] - region.box[0]}
                height={region.box[3] - region.box[1]}
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </svg>
        )}
      </div>
      <Alert
        message={
          error ||
          (result && size.width > 0 && !aligned ? '原始视频尺寸与检测结果不匹配，标注已隐藏' : null)
        }
      />
      <div className="foul-controls">
        <button
          className="foul-play"
          disabled={!sourceUrl || !duration}
          aria-label={playing ? '暂停视频' : '播放视频'}
          onClick={() => void play()}
        >
          <Icon name={playing ? 'pause' : 'play'} />
        </button>
        <button disabled={!duration} aria-label="后退 0.04 秒" onClick={() => seek(time - 0.04)}>
          −
        </button>
        <button disabled={!duration} aria-label="前进 0.04 秒" onClick={() => seek(time + 0.04)}>
          +
        </button>
        <span className="foul-clock">
          {time.toFixed(2)} <em>/</em> {duration.toFixed(2)} s
        </span>
        <select
          aria-label="犯规视频播放速度"
          value={rate}
          onChange={(e) => {
            const next = Number(e.target.value);
            setRate(next);
            if (video.current) video.current.playbackRate = next;
          }}
        >
          {[0.5, 1, 1.5, 2].map((speed) => (
            <option key={speed} value={speed}>
              {speed}×
            </option>
          ))}
        </select>
        <button aria-label={muted ? '开启视频声音' : '静音视频'} onClick={() => setMuted(!muted)}>
          <Icon name={muted ? 'mute' : 'volume'} />
        </button>
        <button aria-label="犯规证据全屏" onClick={() => void fullscreen()}>
          <Icon name="expand" />
        </button>
      </div>
      {showEvent && activeRecord && (
        <div className={`foul-event-callout ${reviewing ? 'review' : ''}`} role="status">
          <span className="foul-ai-mark">AI</span>
          <div>
            <strong>{foulActionLabel(activeRecord.event.foul_details?.action)}</strong>
            <span>
              {reviewing ? '事件复核' : '模型候选'} ·{' '}
              {severityLabel(activeRecord.event.foul_details?.severity)}
            </span>
          </div>
          <span className="foul-callout-time">{candidateEventTime(activeRecord).toFixed(2)}s</span>
          {!reviewing && (
            <button className="text-button" onClick={() => inspect(activeRecord)}>
              定位事件 <Icon name="arrow" size={14} />
            </button>
          )}
          {reviewing && (
            <button className="text-button" onClick={() => setReviewing(false)}>
              结束定位
            </button>
          )}
        </div>
      )}
      {showEvent && !reviewing && activePrompts.length > 1 && (
        <div className="foul-concurrent-prompts" aria-label="同期模型提示">
          <span>{activePrompts.length} 个候选</span>
          {activePrompts.map((record) => (
            <button className="text-button" key={record.event.id} onClick={() => inspect(record)}>
              {foulActionLabel(record.event.foul_details?.action)} ·{' '}
              {candidateEventTime(record).toFixed(2)}s
            </button>
          ))}
        </div>
      )}
      {inspectedPlayer && (
        <div className="foul-target-inspection">
          <strong>{targetLabel(inspectedPlayer)}</strong>
          <span>检测分数 {Math.round((inspectedPlayer.confidence || 0) * 100)}%</span>
          <small>跟踪编号</small>
          <button className="text-button" onClick={() => setInspected(null)}>
            关闭
          </button>
        </div>
      )}
      <EventTimeline
        events={visibleEvents}
        duration={duration || result?.source.duration_s || 0}
        selected={selected?.event.id || null}
        loading={loading}
        onSelect={inspect}
        emptyMessage={emptyMessage}
        currentTime={time}
        onSeek={(value) => {
          setReviewing(false);
          seek(value);
        }}
        disabled={!duration}
      />
      <span className="sr-only">
        {prompt ? `模型提示发出于 ${candidateEmittedTime(prompt)!.toFixed(2)} 秒` : ''}
      </span>
    </div>
  );
}
