import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import type { Clip } from '../../types';
import type {
  CalibrationFilter,
  CalibrationLabel,
  CalibrationMetadata,
  CalibrationSnapshot,
} from '../../types/calibration';
import { api } from '../../api';
import { Alert, Icon, PageHeader } from '../UI';
import { timecode } from '../MediaPlayer';
import CalibrationVideo from './CalibrationVideo';
import BestCrop from './BestCrop';
import {
  acceptSnapshot,
  calibrationIssues,
  filterOptions,
  isWeak,
  labelColor,
  labelOf,
  labelOptions,
  labelTitle,
  metadataMatches,
  minimumSamples,
  snapshotIsBusy,
  trackMatches,
  validateSegment,
} from './calibrationPresentation';
import './calibration.css';

const sessionKey = 'refereelink:calibration:practice';
type Draft = { startMs: number; endMs: number; selected: number | null; filter: CalibrationFilter };
const emptyDraft: Draft = { startMs: 0, endMs: 0, selected: null, filter: 'unlabelled' };
let pendingCreation: Promise<CalibrationSnapshot> | null = null;
let cachedPracticeId: string | null = null;
function readStorage(key: string) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}
function writeStorage(key: string, value: string) {
  try {
    sessionStorage.setItem(key, value);
  } catch {
    /* The remote session still keeps labels. */
  }
}
async function loadPractice() {
  const id = readStorage(sessionKey) || cachedPracticeId;
  if (id) return api.calibration(id);
  if (!pendingCreation) {
    pendingCreation = api
      .createCalibration()
      .then((value) => {
        cachedPracticeId = value.id;
        writeStorage(sessionKey, value.id);
        return value;
      })
      .finally(() => {
        pendingCreation = null;
      });
  }
  return pendingCreation;
}
function readDraft(snapshot: CalibrationSnapshot): Draft {
  try {
    const value = JSON.parse(readStorage(sessionKey + ':' + snapshot.id) || 'null');
    if (
      value &&
      Number.isFinite(value.startMs) &&
      Number.isFinite(value.endMs) &&
      filterOptions.some((item) => item.value === value.filter)
    )
      return { ...value, selected: Number.isInteger(value.selected) ? value.selected : null };
  } catch {
    /* Invalid local view state does not discard remote labels. */
  }
  return {
    ...emptyDraft,
    startMs: snapshot.session.clip_start_ms || 0,
    endMs: snapshot.session.clip_end_ms || Math.min(30000, snapshot.source.duration_ms),
  };
}
const operationTitle: Record<string, string> = {
  prepare: '准备球员',
  label: '保存标注',
  labels: '保存标注',
  validate: '检查结果',
  reset: '重新选片',
};
export default function CalibrationWorkspace({ clip, active }: { clip: Clip; active: boolean }) {
  const [snapshot, setSnapshot] = useState<CalibrationSnapshot | null>(null),
    [metadata, setMetadata] = useState<CalibrationMetadata | null>(null),
    [draft, setDraft] = useState<Draft>(emptyDraft),
    [view, setView] = useState<'choose' | 'label' | 'result'>('choose'),
    [sourcePreview, setSourcePreview] = useState(false),
    [time, setTime] = useState(0),
    [sourceDuration, setSourceDuration] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<string | null>(null),
    [metadataError, setMetadataError] = useState<string | null>(null),
    [retry, setRetry] = useState(0),
    [resetPrompt, setResetPrompt] = useState(false);
  const current = useRef(snapshot),
    mounted = useRef(false),
    video = useRef<HTMLVideoElement>(null),
    pendingSeek = useRef<number | null>(null);
  current.current = snapshot;
  function apply(next: CalibrationSnapshot) {
    const accepted = acceptSnapshot(current.current, next, current.current?.id);
    current.current = accepted;
    setSnapshot(accepted);
    return accepted;
  }
  useEffect(() => {
    mounted.current = true;
    let disposed = false;
    loadPractice()
      .then((value) => {
        if (disposed) return;
        apply(value);
        setDraft(readDraft(value));
        setView(
          value.session.validation_report ? 'result' : value.session.clip_id ? 'label' : 'choose',
        );
      })
      .catch((value) => {
        if (!disposed) setError(value instanceof Error ? value.message : '无法读取标定练习');
      });
    return () => {
      disposed = true;
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (snapshot) writeStorage(sessionKey + ':' + snapshot.id, JSON.stringify(draft));
  }, [snapshot?.id, draft]);
  useEffect(() => {
    if (!snapshotIsBusy(snapshot) || !snapshot) return;
    let disposed = false,
      timer: ReturnType<typeof setTimeout> | null = null;
    async function poll() {
      try {
        const next = await api.calibration(snapshot!.id);
        if (disposed) return;
        apply(next);
        setError(null);
        if (snapshotIsBusy(next)) timer = setTimeout(() => void poll(), 1000);
        else if (next.operation === 'validate' || next.session.validation_report) setView('result');
        else if (next.session.clip_id) setView('label');
      } catch (value) {
        if (disposed) return;
        setError(value instanceof Error ? value.message : '读取练习进度失败');
        timer = setTimeout(() => void poll(), 2500);
      }
    }
    timer = setTimeout(() => void poll(), 700);
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [snapshot?.id, snapshot?.status, snapshot?.revision]);
  const preparingClip = Boolean(snapshotIsBusy(snapshot) && snapshot?.operation === 'prepare');
  useEffect(() => {
    if (!snapshot?.session.clip_id) {
      setMetadata(null);
      return;
    }
    if (preparingClip) return;
    let disposed = false;
    setMetadataError(null);
    api
      .calibrationMetadata(snapshot.id)
      .then((value) => {
        if (disposed) return;
        if (!metadataMatches(value, snapshot)) throw new Error('球员记录与当前片段不匹配');
        setMetadata(value);
      })
      .catch((value) => {
        if (!disposed)
          setMetadataError(value instanceof Error ? value.message : '无法读取球员记录');
      });
    return () => {
      disposed = true;
    };
  }, [snapshot?.id, snapshot?.session.clip_id, snapshot?.metadata_url, preparingClip, retry]);
  const pending = busy || snapshotIsBusy(snapshot);
  const hasClip = Boolean(snapshot?.session.clip_id);
  const alignedMetadata =
    snapshot && metadata && metadataMatches(metadata, snapshot) ? metadata : null;
  const tracks = snapshot?.session.tracks || [];
  const selected = tracks.find((track) => track.track_id === draft.selected) || null;
  const visibleTracks = snapshot
    ? tracks.filter((track) => trackMatches(track, draft.filter, snapshot))
    : [];
  const report = snapshot?.session.validation_report;
  const ready = Boolean(snapshot?.session.ready && report?.passed);
  const durationMs =
    sourceDuration > 0 ? Math.round(sourceDuration * 1000) : snapshot?.source.duration_ms || 0;
  const segmentError = validateSegment(draft.startMs, draft.endMs, durationMs);
  const labelledCount = snapshot
    ? tracks.filter((track) => {
        const label = labelOf(track, snapshot);
        return label && label !== 'ignore';
      }).length
    : 0;
  const unlabelled = snapshot ? tracks.filter((track) => !labelOf(track, snapshot)).length : 0;
  const step =
    pending && snapshot?.operation === 'prepare' ? 1 : view === 'result' ? 3 : hasClip ? 2 : 0;
  const source =
    sourcePreview || !hasClip
      ? snapshot?.source_url || clip.source_url
      : snapshot?.video_url || clip.source_url;
  async function mutate(
    action: (value: CalibrationSnapshot) => Promise<CalibrationSnapshot>,
    nextView?: typeof view,
  ) {
    if (!current.current || pending) return;
    setBusy(true);
    setError(null);
    try {
      const next = await action(current.current);
      if (!mounted.current) return;
      apply(next);
      if (nextView) setView(nextView);
    } catch (value) {
      if (!mounted.current) return;
      setError(value instanceof Error ? value.message : '练习操作未完成');
      try {
        const latest = await api.calibration(current.current!.id);
        if (mounted.current) apply(latest);
      } catch {
        /* Preserve the last confirmed labels while the service reconnects. */
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  async function refresh() {
    setError(null);
    try {
      const restoring = !current.current;
      const next = current.current
        ? await api.calibration(current.current.id)
        : await loadPractice();
      if (!mounted.current) return;
      apply(next);
      if (restoring) {
        setDraft(readDraft(next));
        setView(
          next.session.validation_report ? 'result' : next.session.clip_id ? 'label' : 'choose',
        );
      }
      setRetry((value) => value + 1);
    } catch (value) {
      if (mounted.current) setError(value instanceof Error ? value.message : '无法读取练习');
    }
  }
  function inspect(id: number, seek = true) {
    setDraft((value) => ({ ...value, selected: id }));
    setView('label');
    setSourcePreview(false);
    const track = tracks.find((item) => item.track_id === id);
    if (seek && track) {
      pendingSeek.current = track.representative_timestamp_ms / 1000;
      if (video.current && !sourcePreview && video.current.readyState >= 1) {
        video.current.pause();
        video.current.currentTime = pendingSeek.current;
        pendingSeek.current = null;
      }
    }
  }
  function label(value: CalibrationLabel) {
    if (!selected) return;
    void mutate(
      (session) => api.labelCalibration(session.id, session.revision, selected.track_id, value),
      'label',
    );
  }
  function returnToGroup(filter: CalibrationFilter, ids: number[]) {
    setDraft((value) => ({ ...value, filter }));
    setView('label');
    if (ids.length) inspect(ids[0]);
  }
  async function reset() {
    setResetPrompt(false);
    await mutate((session) => api.resetCalibration(session.id, session.revision), 'choose');
    if (current.current && !current.current.session.clip_id) {
      setMetadata(null);
      setSourcePreview(false);
      setDraft({ ...emptyDraft, endMs: Math.min(30000, current.current.source.duration_ms) });
    }
  }
  return (
    <div className="cal-workspace">
      <PageHeader title="球队标定" description="选一段清晰画面，告诉系统谁是主队、客队与裁判。">
        <span className={ready ? 'tracking-state complete' : 'tracking-state'} role="status">
          {pending
            ? snapshot?.status === 'queued'
              ? '等待任务'
              : (operationTitle[snapshot?.operation || ''] || '处理中') + '…'
            : ready
              ? '本次练习通过'
              : hasClip
                ? '练习标注已保留'
                : snapshot
                  ? '选择片段'
                  : '连接后台中…'}
        </span>
        {hasClip || snapshot?.status === 'error' ? (
          <button
            className="button secondary small"
            disabled={pending}
            onClick={() => setResetPrompt(true)}
          >
            重新选片
          </button>
        ) : null}
      </PageHeader>
      <nav className="cal-steps" aria-label="标定步骤">
        {['选择片段', '准备球员', '标注球员', '检查结果'].map((title, index) => (
          <button
            key={title}
            aria-current={step === index ? 'step' : undefined}
            className={step === index ? 'current' : index < step ? 'done' : ''}
            disabled={
              pending ||
              (index === 0 && hasClip) ||
              index === 1 ||
              (index === 2 && !alignedMetadata) ||
              (index === 3 && !report)
            }
            onClick={() => setView(index === 0 ? 'choose' : index === 2 ? 'label' : 'result')}
          >
            <span>{index + 1}</span>
            {title}
          </button>
        ))}
      </nav>
      <Alert message={error || snapshot?.error || metadataError} onRetry={() => void refresh()} />
      {resetPrompt ? (
        <div className="cal-reset-prompt" role="alertdialog" aria-label="重新选择标定片段">
          <p>重新选片会清除本次练习的球员标注。受保护演示不受影响。</p>
          <button className="button secondary small" onClick={() => setResetPrompt(false)}>
            保留标注
          </button>
          <button className="button primary small" onClick={() => void reset()}>
            重新选片
          </button>
        </div>
      ) : null}
      <div className="cal-layout">
        <main className="cal-main">
          <section className="cal-video-card">
            <div className="tracking-card-header">
              <h2>{sourcePreview || !hasClip ? '原始标定视频' : '本次练习片段'}</h2>
              {hasClip ? (
                <button className="text-button" onClick={() => setSourcePreview((value) => !value)}>
                  {sourcePreview ? '返回球员标注' : '查看原视频'}
                </button>
              ) : (
                <span className="cal-caption">暂停后选择起止时间</span>
              )}
            </div>
            <CalibrationVideo
              source={source}
              poster={clip.poster_url}
              metadata={hasClip && !sourcePreview ? alignedMetadata : null}
              snapshot={snapshot}
              selected={draft.selected}
              videoRef={video}
              active={active}
              onTime={setTime}
              onDuration={(seconds) => {
                if (!hasClip || sourcePreview) {
                  setSourceDuration(seconds);
                  if (!hasClip && seconds > 0)
                    setDraft((value) => ({
                      ...value,
                      endMs:
                        value.endMs <= 0 || value.endMs > seconds * 1000
                          ? Math.round(seconds * 1000)
                          : value.endMs,
                    }));
                }
              }}
              onSelect={(id) => inspect(id, false)}
              onReady={(element) => {
                if (hasClip && !sourcePreview && pendingSeek.current !== null) {
                  element.pause();
                  element.currentTime = Math.min(
                    element.duration,
                    Math.max(0, pendingSeek.current),
                  );
                  pendingSeek.current = null;
                }
              }}
            />
            {!hasClip ? (
              <div className="cal-segment">
                <div className="cal-segment-fields">
                  <label>
                    开始 <span>秒</span>
                    <input
                      type="number"
                      min="0"
                      step="0.001"
                      max={durationMs / 1000}
                      value={draft.startMs / 1000}
                      disabled={pending}
                      onChange={(event) =>
                        setDraft((value) => ({
                          ...value,
                          startMs: Math.round(Number(event.target.value) * 1000),
                        }))
                      }
                    />
                    <button
                      className="text-button"
                      disabled={pending}
                      onClick={() =>
                        setDraft((value) => ({ ...value, startMs: Math.round(time * 1000) }))
                      }
                    >
                      设为当前
                    </button>
                  </label>
                  <label>
                    结束 <span>秒</span>
                    <input
                      type="number"
                      min="0"
                      step="0.001"
                      max={durationMs / 1000}
                      value={draft.endMs / 1000}
                      disabled={pending}
                      onChange={(event) =>
                        setDraft((value) => ({
                          ...value,
                          endMs: Math.round(Number(event.target.value) * 1000),
                        }))
                      }
                    />
                    <button
                      className="text-button"
                      disabled={pending}
                      onClick={() =>
                        setDraft((value) => ({ ...value, endMs: Math.round(time * 1000) }))
                      }
                    >
                      设为当前
                    </button>
                  </label>
                </div>
                <div className="cal-segment-submit">
                  <span className={segmentError ? 'cal-segment-error' : 'cal-caption'}>
                    {segmentError ||
                      `已选 ${((draft.endMs - draft.startMs) / 1000).toFixed(1)} 秒 · 最长 60 秒`}
                  </span>
                  <button
                    className="button primary"
                    disabled={pending || !snapshot || Boolean(segmentError)}
                    onClick={() => {
                      video.current?.pause();
                      void mutate((session) =>
                        api.prepareCalibration(
                          session.id,
                          session.revision,
                          draft.startMs,
                          draft.endMs,
                        ),
                      );
                    }}
                  >
                    <Icon name="arrow" />
                    准备球员
                  </button>
                </div>
              </div>
            ) : (
              <div className="cal-clip-summary">
                <span>
                  原片 {timecode((snapshot?.session.clip_start_ms || 0) / 1000)} —{' '}
                  {timecode((snapshot?.session.clip_end_ms || 0) / 1000)}
                </span>
                <span>
                  {snapshot?.session.observed_frames || 0} 帧 · {tracks.length} 位球员
                </span>
              </div>
            )}
          </section>
          {pending ? (
            <div className="cal-progress" role="status">
              <progress
                max="1"
                value={
                  snapshot?.operation === 'prepare' && snapshot.session.processing_progress > 0
                    ? Math.min(1, snapshot.session.processing_progress)
                    : undefined
                }
              />
              <span>
                {snapshot?.status === 'queued'
                  ? '任务排队中，已保留当前标注'
                  : (operationTitle[snapshot?.operation || ''] || '正在处理') + '，请稍候'}
              </span>
            </div>
          ) : null}
          {view === 'result' && report && snapshot ? (
            <section className={ready ? 'cal-result passed' : 'cal-result'}>
              <div className="cal-result-title">
                <h2>{ready ? '本次练习已通过' : '再检查几位球员'}</h2>
                <button className="text-button" onClick={() => setView('label')}>
                  返回标注
                </button>
              </div>
              <p>
                {ready
                  ? '练习结果已保存，可继续修正标签后重新检查。'
                  : '人工标签已保留。点击下面的球员组继续补充或修正。'}
              </p>
              <div className="cal-result-counts">
                <span>
                  主队{' '}
                  <strong>{report.home_eligible_track_count ?? report.home_track_count}</strong> /{' '}
                  {report.min_tracks_per_team || 3} 位可用
                </span>
                <span>
                  客队{' '}
                  <strong>{report.away_eligible_track_count ?? report.away_track_count}</strong> /{' '}
                  {report.min_tracks_per_team || 3} 位可用
                </span>
                <span>每位至少 {report.min_samples_per_track || 5} 个有效样本</span>
              </div>
              <p className="cal-role-report">
                门将映射：{report.goalkeeper_mapping_ready ? '已建立' : '未齐全（可选）'} ·
                裁判映射：{report.referee_mapping_ready ? '已建立' : '未建立（可选）'}
              </p>
              {calibrationIssues(snapshot).map((issue, index) => (
                <div className="cal-issue" key={index}>
                  <p>{issue.text}</p>
                  <button
                    className="text-button"
                    onClick={() => returnToGroup(issue.filter, issue.ids)}
                  >
                    查看相关球员
                    <Icon name="arrow" size={14} />
                  </button>
                  {issue.ids.length ? (
                    <div className="cal-issue-ids">
                      {issue.ids.map((id) => (
                        <button key={id} onClick={() => inspect(id)}>
                          #{id}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))}
            </section>
          ) : null}
          {hasClip && snapshot ? (
            <section className="cal-track-browser" aria-label="片段球员">
              <div className="cal-browser-heading">
                <h2>片段球员</h2>
                <span>
                  {labelledCount} 已标注 · {unlabelled} 待标注
                </span>
              </div>
              <div className="cal-filters" role="group" aria-label="筛选标定球员">
                {filterOptions.map((option) => (
                  <button
                    key={option.value}
                    className={draft.filter === option.value ? 'selected' : ''}
                    aria-pressed={draft.filter === option.value}
                    onClick={() => setDraft((value) => ({ ...value, filter: option.value }))}
                  >
                    {option.title}
                    <span>
                      {tracks.filter((track) => trackMatches(track, option.value, snapshot)).length}
                    </span>
                  </button>
                ))}
              </div>
              <div className="cal-track-list">
                {visibleTracks.map((track) => (
                  <button
                    key={track.track_id}
                    className={draft.selected === track.track_id ? 'selected' : ''}
                    aria-pressed={draft.selected === track.track_id}
                    onClick={() => inspect(track.track_id)}
                  >
                    <i style={{ background: labelColor(labelOf(track, snapshot)) }} />
                    <strong>#{track.track_id}</strong>
                    <span>{labelTitle(labelOf(track, snapshot))}</span>
                    <small>
                      {track.quality_observation_count} 清晰帧
                      {isWeak(track, snapshot) ? ' · 弱样本' : ''}
                    </small>
                  </button>
                ))}
                {!visibleTracks.length ? (
                  <div className="cal-list-empty">
                    这个分组没有球员，选择其他分组或点击画面中的跟踪框。
                  </div>
                ) : null}
              </div>
              {snapshot.auto_ignored_track_ids?.length ? (
                <p className="cal-auto-ignore">
                  {snapshot.auto_ignored_track_ids.length}{' '}
                  位弱样本球员已自动忽略。可在“弱样本”中检查并重新标注。
                </p>
              ) : null}
            </section>
          ) : null}
        </main>
        <aside className="cal-inspector">
          {selected && alignedMetadata && snapshot ? (
            <>
              <div className="cal-inspector-heading">
                <h2>球员 #{selected.track_id}</h2>
                <span>{labelTitle(labelOf(selected, snapshot))}</span>
              </div>
              <BestCrop
                key={snapshot.id + ':' + alignedMetadata.clip_id + ':' + selected.track_id}
                source={snapshot.video_url}
                track={selected}
                metadata={alignedMetadata}
              />
              <p className="cal-crop-note">
                代表画面 · {timecode(selected.representative_timestamp_ms / 1000)}
              </p>
              <dl className="cal-quality">
                <div>
                  <dt>清晰帧</dt>
                  <dd>
                    {selected.quality_observation_count} / {selected.observation_count}
                  </dd>
                </div>
                <div>
                  <dt>已采样</dt>
                  <dd>
                    {selected.sample_count || 0} / 至少 {minimumSamples(snapshot)}
                  </dd>
                </div>
              </dl>
              <div
                className="cal-label-buttons"
                role="group"
                aria-label={`为球员 ${selected.track_id} 标注角色`}
              >
                {labelOptions.map((option) => (
                  <button
                    key={option.value}
                    style={{ '--label-color': option.color } as CSSProperties}
                    className={labelOf(selected, snapshot) === option.value ? 'selected' : ''}
                    aria-pressed={labelOf(selected, snapshot) === option.value}
                    disabled={pending}
                    onClick={() => label(option.value)}
                  >
                    <i />
                    <strong>{option.title}</strong>
                    <span>{option.detail}</span>
                  </button>
                ))}
              </div>
              <p className="cal-label-note">
                {isWeak(selected, snapshot)
                  ? '此球员清晰样本较少；人工标签会保留，校验按有效样本计算。'
                  : '按球衣与角色判断。标注后系统会提取样本，随时可以修正。'}
              </p>
            </>
          ) : (
            <div className="cal-inspector-empty">
              <Icon name="file" size={28} />
              <h2>{hasClip ? '选择一位球员' : '从清晰片段开始'}</h2>
              <p>
                {hasClip
                  ? '点击画面中的跟踪框，或下方球员列表，查看代表画面并标注。'
                  : '尽量让主客队各有多位球员清楚入镜。准备完成后，再标记球队与角色。'}
              </p>
            </div>
          )}
          {hasClip ? (
            <div className="cal-check-action">
              <button
                className="button primary full"
                disabled={pending || !alignedMetadata || labelledCount === 0}
                onClick={() =>
                  void mutate(
                    (session) => api.validateCalibration(session.id, session.revision),
                    'result',
                  )
                }
              >
                检查结果
                <Icon name="arrow" />
              </button>
              <span>建议主队、客队各至少 3 位清晰球员</span>
            </div>
          ) : null}
          <div className="cal-demo-boundary">
            <strong>独立标定练习</strong>
            <p>本次标注与结果仅用于练习，不会启用为比赛标定，也不会覆盖受保护演示。</p>
            <span>{snapshot?.demo.ready ? '受保护演示已准备' : '受保护演示状态由后台管理'}</span>
          </div>
        </aside>
      </div>
    </div>
  );
}
