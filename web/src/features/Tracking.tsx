import { useEffect, useRef, useState } from 'react';
import type { Catalog, Clip, Job } from '../types';
import type { TrackingCase, TrackingFrame } from '../types/tracking';
import { api } from '../api';
import { Alert, Icon, PageHeader } from '../components/UI';
import TrackingScene from '../components/tracking/TrackingScene';
import {
  collectTrackingFrames,
  trackingArtifactId,
  caseWithResult,
} from '../components/tracking/trackingPresentation';
import './tracking.css';

async function readCompletedResult(job: Job) {
  const result = await api.trackingResult(trackingArtifactId(job));
  if (result.case_id !== job.case_id || result.job_id !== job.id)
    throw new Error('本次跟踪结果与任务不匹配');
  return result;
}
const pendingStates = new Set(['queued', 'running']);
function jobStatus(job: Job | null) {
  if (job?.status === 'queued') return '等待 CUDA 任务';
  if (job?.status === 'running') return '正在跟踪';
  if (job && ['failed', 'error', 'cancelled'].includes(job.status)) return '跟踪未完成';
  return null;
}
function TrackingSession({ clip }: { clip: Clip }) {
  const [descriptor, setDescriptor] = useState<TrackingCase | null>(null),
    [job, setJob] = useState<Job | null>(null),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [error, setError] = useState<string | null>(null),
    [framesState, setFramesState] = useState<{
      id: string;
      revision: string;
      frames: TrackingFrame[];
    } | null>(null),
    [loadedFrames, setLoadedFrames] = useState(0),
    [frameError, setFrameError] = useState<string | null>(null),
    [retry, setRetry] = useState(0);
  const generation = useRef(0),
    requestedJob = useRef<string | null>(null),
    mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const ticket = ++generation.current;
    setLoading(true);
    api
      .trackingCase(clip.id)
      .then(async (value) => {
        if (value.case_id !== clip.id) throw new Error('跟踪视频与当前页面不匹配');
        if (
          requestedJob.current &&
          job?.id === requestedJob.current &&
          ['completed', 'succeeded'].includes(job.status)
        ) {
          const completed = await readCompletedResult(job);
          value = { ...value, latest_result: completed, active_job: null };
        }
        if (!mounted.current || ticket !== generation.current) return;
        setDescriptor(value);
        setJob((current) => value.active_job || (requestedJob.current ? current : null));
        setError(null);
      })
      .catch((value) => {
        if (mounted.current && ticket === generation.current)
          setError(value instanceof Error ? value.message : '无法读取跟踪任务');
      })
      .finally(() => {
        if (mounted.current && ticket === generation.current) setLoading(false);
      });
    return () => {
      mounted.current = false;
      generation.current++;
    };
  }, [clip.id, retry]);
  useEffect(() => {
    if (!job || !pendingStates.has(job.status)) return;
    let disposed = false,
      timer: ReturnType<typeof setTimeout> | null = null;
    async function poll() {
      let observed = job!;
      try {
        const next = await api.job(job!.id);
        if (disposed || next.case_id !== clip.id || next.id !== job!.id) return;
        observed = next;
        setError(null);
        if (pendingStates.has(next.status)) {
          setJob(next);
          timer = setTimeout(() => void poll(), 1500);
          return;
        }
        if (['completed', 'succeeded'].includes(next.status)) {
          const completed = await readCompletedResult(next);
          if (disposed) return;
          setDescriptor((current) => caseWithResult(current, completed, clip.poster_url));
        }
        setJob(next);
      } catch (value) {
        if (disposed) return;
        setError(value instanceof Error ? value.message : '无法读取任务进度');
        if (!pendingStates.has(observed.status)) {
          setJob(observed);
          return;
        }
        timer = setTimeout(() => void poll(), 3000);
      }
    }
    timer = setTimeout(() => void poll(), 1000);
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [clip.id, job?.id, job?.status]);
  const active = busy || Boolean(job && pendingStates.has(job.status));
  const rejected = Boolean(job && ['failed', 'error', 'cancelled'].includes(job.status));
  const candidate = !active && !rejected ? descriptor?.latest_result || null : null;
  const result =
    candidate && (!requestedJob.current || candidate.job_id === requestedJob.current)
      ? candidate
      : null;
  useEffect(() => {
    if (!result) {
      setFramesState(null);
      setFrameError(null);
      setLoadedFrames(0);
      return;
    }
    let disposed = false;
    setFramesState(null);
    setFrameError(null);
    setLoadedFrames(0);
    collectTrackingFrames(
      result,
      (offset) => api.trackingFrames(result.id, result.revision, offset),
      () => !disposed,
      setLoadedFrames,
    )
      .then((frames) => {
        if (!disposed && frames)
          setFramesState({ id: result.id, revision: result.revision, frames });
      })
      .catch((value) => {
        if (!disposed) setFrameError(value instanceof Error ? value.message : '跟踪记录读取失败');
      });
    return () => {
      disposed = true;
    };
  }, [result?.id, result?.revision, retry]);
  async function start() {
    if (active || !mounted.current) return;
    const ticket = ++generation.current;
    setBusy(true);
    setError(null);
    setLoading(false);
    setFramesState(null);
    requestedJob.current = 'pending';
    try {
      const next = await api.startTracking(clip.id);
      if (!mounted.current || ticket !== generation.current) return;
      if (next.case_id !== clip.id) throw new Error('任务与当前视频不匹配');
      requestedJob.current = next.id;
      setJob(next);
    } catch (value) {
      if (mounted.current && ticket === generation.current)
        setError(value instanceof Error ? value.message : '无法启动跟踪');
    } finally {
      if (mounted.current && ticket === generation.current) setBusy(false);
    }
  }
  const frames =
    result && framesState?.id === result.id && framesState.revision === result.revision
      ? framesState.frames
      : [];
  const status = busy
    ? '提交任务中…'
    : jobStatus(job) ||
      (loading
        ? '连接后端中…'
        : frameError
          ? '跟踪记录不可用'
          : result && !(framesState?.id === result.id && framesState.revision === result.revision)
            ? '读取跟踪记录…'
            : result
              ? '跟踪完成'
              : '尚未运行');
  const ready = result && frames.length ? result : null;
  return (
    <>
      <PageHeader title="场地跟踪">
        <span className={ready ? 'tracking-state complete' : 'tracking-state'} role="status">
          {status}
        </span>
        <button
          className="button primary"
          disabled={active || loading}
          onClick={() => void start()}
        >
          <Icon name="play" />
          {active ? '运行中…' : ready ? '重新跟踪' : '开始跟踪'}
        </button>
      </PageHeader>
      <Alert
        message={error || (!busy ? job?.error : null) || frameError}
        onRetry={active ? undefined : () => setRetry((value) => value + 1)}
      />
      {active && job ? (
        <div className="tracking-progress">
          <progress
            max="1"
            value={Math.min(1, Math.max(0, job.progress > 1 ? job.progress / 100 : job.progress))}
          />
          <span>{Math.round((job.progress > 1 ? job.progress / 100 : job.progress) * 100)}%</span>
        </div>
      ) : null}
      {result && !ready && !frameError ? (
        <div className="tracking-progress">
          <progress max={result.frame_count || 1} value={loadedFrames} />
          <span>
            {loadedFrames} / {result.frame_count}
          </span>
        </div>
      ) : null}
      <TrackingScene
        source={ready?.source.url || descriptor?.source_url || clip.source_url}
        poster={descriptor?.poster_url || clip.poster_url}
        result={ready}
        frames={frames}
        status={status}
      />
    </>
  );
}
export default function Tracking({ catalog }: { catalog: Catalog | null }) {
  const clips =
    catalog?.clips.filter(
      (clip) => clip.id === 'tracking-projection' || clip.id === 'calibration',
    ) || [];
  const [selected, setSelected] = useState('tracking-projection');
  const clip = clips.find((item) => item.id === selected) || clips[0];
  return (
    <div className="tracking-workspace">
      <div className="tracking-case-rail" role="group" aria-label="跟踪视频">
        {clips.map((item) => (
          <button
            key={item.id}
            className={clip?.id === item.id ? 'selected' : ''}
            aria-pressed={clip?.id === item.id}
            onClick={() => setSelected(item.id)}
          >
            {item.id === 'calibration' ? '标定视频' : '比赛视频'}
          </button>
        ))}
      </div>
      {clip ? (
        <TrackingSession key={clip.id} clip={clip} />
      ) : (
        <div className="tracking-inline-empty">正在读取视频…</div>
      )}
    </div>
  );
}
