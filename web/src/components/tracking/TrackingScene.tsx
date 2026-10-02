import { useEffect, useRef, useState } from 'react';
import type { TrackingFrame, TrackingResult, TrackingPitch } from '../../types/tracking';
import { mediaUrl } from '../../api';
import { Icon } from '../UI';
import { timecode } from '../MediaPlayer';
import {
  entityColor,
  entityKey,
  entityLabel,
  fieldPoint,
  frameIndexAtTime,
  recordedTrajectory,
  roleLabel,
  teamLabel,
  validBox,
  usableProjection,
} from './trackingPresentation';

function PitchLines({ pitch }: { pitch: TrackingPitch }) {
  const {
    length_m: length,
    width_m: width,
    penalty_area_length_m: penaltyLength,
    penalty_area_width_m: penaltyWidth,
    goal_area_length_m: goalLength,
    goal_area_width_m: goalWidth,
  } = pitch;
  return (
    <g className="tracking-pitch-lines">
      <rect x="0" y="0" width={length} height={width} />
      <path d={`M${length / 2},0 V${width}`} />
      <circle cx={length / 2} cy={width / 2} r={pitch.center_circle_radius_m} />
      <circle cx={length / 2} cy={width / 2} r=".45" className="tracking-pitch-spot" />
      {[0, 1].map((side) => (
        <g key={side}>
          <rect
            x={side ? length - penaltyLength : 0}
            y={(width - penaltyWidth) / 2}
            width={penaltyLength}
            height={penaltyWidth}
          />
          <rect
            x={side ? length - goalLength : 0}
            y={(width - goalWidth) / 2}
            width={goalLength}
            height={goalWidth}
          />
          <circle
            cx={side ? length - pitch.penalty_spot_distance_m : pitch.penalty_spot_distance_m}
            cy={width / 2}
            r=".45"
            className="tracking-pitch-spot"
          />
          <path
            d={`M${side ? length + 1.3 : -1.3},${(width - pitch.goal_width_m) / 2} V${(width + pitch.goal_width_m) / 2}`}
          />
        </g>
      ))}
    </g>
  );
}
export default function TrackingScene({
  source,
  poster,
  result,
  frames,
  status,
}: {
  source: string;
  poster: string | null;
  result: TrackingResult | null;
  frames: TrackingFrame[];
  status: string;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const [time, setTime] = useState(0),
    [duration, setDuration] = useState(0),
    [playing, setPlaying] = useState(false),
    [speed, setSpeed] = useState(1),
    [dimensions, setDimensions] = useState<[number, number]>([16, 9]),
    [selected, setSelected] = useState<string | null>(null),
    [showBoxes, setShowBoxes] = useState(true),
    [showTrails, setShowTrails] = useState(true),
    [metadataReady, setMetadataReady] = useState(false),
    [seeking, setSeeking] = useState(false),
    [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setTime(0);
    setDuration(0);
    setMetadataReady(false);
    setPlaying(false);
    setSelected(null);
    setSeeking(false);
    setError(null);
  }, [source]);
  useEffect(() => {
    const element = video.current;
    if (!element) return;
    let disposed = false,
      requestId = 0;
    const update = (_now: number, metadata: VideoFrameCallbackMetadata) => {
      if (disposed) return;
      setTime(metadata.mediaTime);
      requestId = element.requestVideoFrameCallback(update);
    };
    const fallback = () => {
      if (disposed) return;
      setTime(element.currentTime);
      requestId = requestAnimationFrame(fallback);
    };
    if ('requestVideoFrameCallback' in element)
      requestId = element.requestVideoFrameCallback(update);
    else requestId = requestAnimationFrame(fallback);
    return () => {
      disposed = true;
      if ('cancelVideoFrameCallback' in element) element.cancelVideoFrameCallback(requestId);
      else cancelAnimationFrame(requestId);
    };
  }, [source]);
  useEffect(() => {
    setSelected(null);
  }, [result?.id, result?.revision]);
  const aligned =
    !result ||
    (metadataReady &&
      dimensions[0] === result.source.width &&
      dimensions[1] === result.source.height);
  const index =
    result && aligned && !seeking ? frameIndexAtTime(frames, time, result.source.fps) : -1;
  const frame = index >= 0 ? frames[index] : null;
  const targets =
    frame?.players.map((entity, n) => ({ entity, key: entityKey(entity, frame.frame_id, n) })) ||
    [];
  const target = targets.find((item) => item.key === selected);
  const trajectories =
    result && selected && showTrails
      ? recordedTrajectory(frames, index, selected, result.pitch)
      : [];
  const projected = usableProjection(frame);
  const projectionCount =
    result && projected
      ? targets.filter(({ entity }) => fieldPoint(entity, result.pitch)).length
      : 0;
  const ballPoint =
    result && projected && frame?.ball && ['fresh', 'predicted'].includes(frame.ball.status)
      ? fieldPoint(frame.ball, result.pitch)
      : null;
  async function togglePlay() {
    const element = video.current;
    if (!element) return;
    if (!element.paused) {
      element.pause();
      return;
    }
    try {
      await element.play();
      setError(null);
    } catch {
      setError('视频暂时无法播放，请重试');
    }
  }
  function seek(value: number) {
    if (!video.current || !duration) return;
    setSeeking(true);
    video.current.currentTime = Math.min(duration, Math.max(0, value));
    setTime(video.current.currentTime);
  }
  async function fullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await stage.current?.requestFullscreen();
    } catch {
      setError('当前浏览器不支持全屏');
    }
  }
  function inspect(key: string) {
    setSelected(key);
  }
  const player = target?.entity;
  return (
    <div className="tracking-body">
      <main className="tracking-main">
        <section className="tracking-video-card">
          <div className="tracking-card-header">
            <h2>原始画面</h2>
            <label className="tracking-toggle">
              <input
                type="checkbox"
                checked={showBoxes}
                onChange={(e) => setShowBoxes(e.target.checked)}
              />
              跟踪框
            </label>
          </div>
          <div
            className="tracking-video-stage"
            ref={stage}
            style={{ aspectRatio: dimensions[0] + ' / ' + dimensions[1] }}
            tabIndex={0}
            aria-label="跟踪视频，空格播放，左右方向键定位"
            onKeyDown={(event) => {
              if (event.target !== event.currentTarget) return;
              if (event.code === 'Space') {
                event.preventDefault();
                void togglePlay();
              }
              if (event.key === 'ArrowRight') {
                event.preventDefault();
                seek(time + 1 / (result?.source.fps || 30));
              }
              if (event.key === 'ArrowLeft') {
                event.preventDefault();
                seek(time - 1 / (result?.source.fps || 30));
              }
            }}
          >
            <video
              ref={video}
              src={mediaUrl(source)}
              poster={mediaUrl(poster)}
              playsInline
              muted
              preload="auto"
              onLoadedMetadata={(event) => {
                const element = event.currentTarget;
                setDuration(Number.isFinite(element.duration) ? element.duration : 0);
                setMetadataReady(true);
                setDimensions([element.videoWidth || 16, element.videoHeight || 9]);
                element.playbackRate = speed;
              }}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
              onSeeking={() => setSeeking(true)}
              onSeeked={(event) => {
                setSeeking(false);
                setTime(event.currentTarget.currentTime);
              }}
              onTimeUpdate={(event) => {
                if (!playing) setTime(event.currentTarget.currentTime);
              }}
              onError={() => setError('原始视频不可用，请检查后端连接')}
            />
            {result && frame && showBoxes ? (
              <svg
                className="tracking-box-layer"
                viewBox={`0 0 ${result.source.width} ${result.source.height}`}
                preserveAspectRatio="xMidYMid meet"
                aria-label="当前帧跟踪目标"
              >
                {targets.map(({ entity, key }) => {
                  if (!validBox(entity.bbox, result.source.width, result.source.height))
                    return null;
                  const [x1, y1, x2, y2] = entity.bbox!;
                  const labelSize = Math.max(16, result.source.width / 75);
                  return (
                    <g
                      key={key}
                      className={[
                        'tracking-box',
                        selected === key ? 'selected' : '',
                        entity.missing_frames > 0 ? 'held' : '',
                      ]
                        .filter(Boolean)
                        .join(' ')}
                      style={{ color: entityColor(entity) }}
                      tabIndex={0}
                      role="button"
                      aria-label={
                        entityLabel(entity) +
                        ' ' +
                        teamLabel(entity.team) +
                        ' ' +
                        roleLabel(entity.role)
                      }
                      onClick={() => inspect(key)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          inspect(key);
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
                        className="tracking-box-tag"
                        x={x1}
                        y={Math.max(0, y1 - labelSize * 1.5)}
                        width={Math.max(
                          labelSize * 3,
                          entityLabel(entity).length * labelSize * 0.7,
                        )}
                        height={labelSize * 1.5}
                      />
                      <text
                        x={x1 + labelSize * 0.3}
                        y={Math.max(labelSize * 1.05, y1 - labelSize * 0.35)}
                        fontSize={labelSize}
                      >
                        {entityLabel(entity)}
                      </text>
                    </g>
                  );
                })}
                {frame.ball &&
                ['fresh', 'predicted'].includes(frame.ball.status) &&
                frame.ball.image_x !== null &&
                frame.ball.image_y !== null &&
                Number.isFinite(frame.ball.image_x) &&
                Number.isFinite(frame.ball.image_y) ? (
                  <circle
                    className={`tracking-ball ${frame.ball.status}`}
                    cx={frame.ball.image_x}
                    cy={frame.ball.image_y}
                    r={result.source.width / 160}
                    vectorEffect="non-scaling-stroke"
                  />
                ) : null}
              </svg>
            ) : null}
            {error || (metadataReady && !aligned) ? (
              <div className="tracking-video-alert" role="alert">
                {error || '视频尺寸与跟踪结果不匹配，请重新运行'}
              </div>
            ) : null}
          </div>
          <div className="tracking-player-controls">
            <button
              className="icon-button"
              aria-label={playing ? '暂停跟踪视频' : '播放跟踪视频'}
              onClick={() => void togglePlay()}
              disabled={!duration}
            >
              <Icon name={playing ? 'pause' : 'play'} />
            </button>
            <span className="tracking-time">
              {timecode(time)} <span>/ {timecode(duration)}</span>
            </span>
            <input
              aria-label="跟踪视频进度"
              type="range"
              min="0"
              max={duration || 1}
              step="0.001"
              value={Math.min(time, duration || 1)}
              disabled={!duration}
              onChange={(event) => seek(Number(event.target.value))}
            />
            <select
              aria-label="跟踪播放速度"
              value={speed}
              onChange={(event) => {
                const value = Number(event.target.value);
                setSpeed(value);
                if (video.current) video.current.playbackRate = value;
              }}
            >
              {[0.5, 1, 1.5, 2].map((value) => (
                <option key={value} value={value}>
                  {value}×
                </option>
              ))}
            </select>
            <button
              className="icon-button"
              aria-label="全屏跟踪画面"
              onClick={() => void fullscreen()}
            >
              <Icon name="expand" />
            </button>
          </div>
        </section>
        <section className="tracking-pitch-card">
          <div className="tracking-card-header">
            <h2>场地视图</h2>
            <label className="tracking-toggle">
              <input
                type="checkbox"
                checked={showTrails}
                onChange={(event) => setShowTrails(event.target.checked)}
              />
              轨迹
            </label>
          </div>
          {result ? (
            <svg
              className="tracking-pitch"
              viewBox={`-4 -4 ${result.pitch.length_m + 8} ${result.pitch.width_m + 8}`}
              role="group"
              aria-label="同步二维球场，选择目标查看轨迹"
            >
              <PitchLines pitch={result.pitch} />
              {trajectories.map((points, n) => (
                <polyline
                  key={n}
                  className="tracking-trail"
                  points={points.map((point) => point.join(',')).join(' ')}
                  style={{ stroke: player ? entityColor(player) : '#9da5b3' }}
                />
              ))}
              {targets.map(({ entity, key }) => {
                const point = projected ? fieldPoint(entity, result.pitch) : null;
                if (!point) return null;
                return (
                  <g
                    key={key}
                    className={selected === key ? 'tracking-dot selected' : 'tracking-dot'}
                    style={{ color: entityColor(entity) }}
                    tabIndex={0}
                    role="button"
                    aria-label={entityLabel(entity) + ' ' + teamLabel(entity.team) + ' 场地位置'}
                    onClick={() => inspect(key)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        inspect(key);
                      }
                    }}
                  >
                    <circle cx={point[0]} cy={point[1]} r={selected === key ? 1.8 : 1.3} />
                    {selected === key ? (
                      <text x={point[0]} y={point[1] - 3}>
                        {entityLabel(entity)}
                      </text>
                    ) : null}
                  </g>
                );
              })}
              {ballPoint ? (
                <circle
                  className={`tracking-pitch-ball ${frame?.ball?.status}`}
                  cx={ballPoint[0]}
                  cy={ballPoint[1]}
                  r=".85"
                />
              ) : null}
            </svg>
          ) : (
            <div className="tracking-pitch-empty">{status}</div>
          )}
          <div className="tracking-legend">
            <span>
              <i style={{ background: '#a8eb44' }} />
              主队
            </span>
            <span>
              <i style={{ background: '#70b7ff' }} />
              客队
            </span>
            <span>
              <i style={{ background: '#f4ba53' }} />
              裁判
            </span>
            <span>
              <i style={{ background: '#c4cbd7' }} />
              未确定
            </span>
            {result && frame ? (
              <span className="tracking-projection-count">
                {projected
                  ? projectionCount + ' 个场地位置'
                  : frame.homography_status === 'stale'
                    ? '投影暂未更新'
                    : '投影不可用'}
              </span>
            ) : null}
          </div>
        </section>
      </main>
      <aside className="tracking-inspector">
        <div className="tracking-card-header">
          <h2>跟踪目标</h2>
          <span className="tracking-count">{targets.length}</span>
        </div>
        {player ? (
          <div className="tracking-selected-target">
            <div className="tracking-target-heading">
              <i style={{ background: entityColor(player) }} />
              <strong>{entityLabel(player)}</strong>
              <button className="text-button" onClick={() => setSelected(null)}>
                取消选择
              </button>
            </div>
            <dl>
              <div>
                <dt>球队</dt>
                <dd>{teamLabel(player.team)}</dd>
              </div>
              <div>
                <dt>身份</dt>
                <dd>{roleLabel(player.role)}</dd>
              </div>
              <div>
                <dt>场地坐标</dt>
                <dd>
                  {result && projected && fieldPoint(player, result.pitch)
                    ? player.field_x!.toFixed(1) + ', ' + player.field_y!.toFixed(1) + ' m'
                    : frame?.homography_status === 'stale'
                      ? '投影暂未更新'
                      : '未投影'}
                </dd>
              </div>
              <div>
                <dt>跟踪状态</dt>
                <dd>{player.missing_frames > 0 ? '暂未检测' : '已检测'}</dd>
              </div>
              <div>
                <dt>检测置信度</dt>
                <dd>
                  {player.confidence !== null && Number.isFinite(player.confidence)
                    ? Math.round(player.confidence * 100) + '%'
                    : '未提供'}
                </dd>
              </div>
            </dl>
          </div>
        ) : selected ? (
          <div className="tracking-inline-empty">该目标未出现在当前帧</div>
        ) : null}
        {targets.length ? (
          <div className="tracking-target-list" role="group" aria-label="当前帧目标">
            {targets.map(({ entity, key }) => (
              <button
                key={key}
                className={selected === key ? 'selected' : ''}
                aria-pressed={selected === key}
                onClick={() => inspect(key)}
              >
                <i style={{ background: entityColor(entity) }} />
                <strong>{entityLabel(entity)}</strong>
                <span>{teamLabel(entity.team)}</span>
                <small>{roleLabel(entity.role)}</small>
              </button>
            ))}
          </div>
        ) : (
          <div className="tracking-inline-empty">
            {frames.length ? (seeking ? '正在定位…' : '当前帧无可用目标') : status}
          </div>
        )}
        {result ? (
          <details className="tracking-run-details">
            <summary>运行详情</summary>
            <dl>
              <div>
                <dt>执行设备</dt>
                <dd>CUDA</dd>
              </div>
              <div>
                <dt>处理帧数</dt>
                <dd>{result.frame_count}</dd>
              </div>
              <div>
                <dt>处理速度</dt>
                <dd>
                  {result.metrics.actual_fps === null
                    ? '未提供'
                    : result.metrics.actual_fps.toFixed(1) + ' FPS'}
                </dd>
              </div>
              <div>
                <dt>可投影占比</dt>
                <dd>
                  {result.metrics.projected_player_rate === null
                    ? '未提供'
                    : Math.round(result.metrics.projected_player_rate * 100) + '%'}
                </dd>
              </div>
            </dl>
          </details>
        ) : null}
      </aside>
    </div>
  );
}
