import { useCallback, useEffect, useState } from 'react';
import type { Catalog, Artifact } from '../types';
import type { CandidateRecord } from '../events';
import { parseCandidateEvents } from '../events';
import { api, mediaUrl, request } from '../api';
import { useJob, useResource } from '../hooks';
import MediaPlayer from '../components/MediaPlayer';
import EventTimeline from '../components/EventTimeline';
import { Alert, Empty, Footer, Icon, PageHeader } from '../components/UI';
function artifactSource(artifact: Artifact) {
  return mediaUrl(artifact.url || `/api/experiments/artifacts/${encodeURIComponent(artifact.id)}`)!;
}
const statusLabels: Record<string, string> = {
  queued: '排队中',
  running: '正在运行',
  completed: '已完成',
  succeeded: '已完成',
  failed: '运行失败',
  error: '运行失败',
  cancelled: '已取消',
};
const actionLabels: Record<string, string> = {
  Holding: '拉扯',
  Tackling: '铲球',
  'Standing tackling': '站立抢断',
  Pushing: '推搡',
  Challenge: '身体争抢',
  Dive: '假摔',
  'High leg': '抬脚过高',
  Elbowing: '肘击',
};
interface ExperimentReport {
  status?: string;
  environment?: { device?: string; gpu?: string };
  processed_frames?: number;
  fps_including_render_and_json?: number;
  unknown_team_rate_all_persons?: number;
  projected_player_rate?: number;
  foul_candidates?: number;
  foul_fp32_retry_windows?: number;
  config?: { foul_replicated_single_view_slots?: number };
}
export default function Experiments({
  kind,
  catalog,
}: {
  kind: 'tracking' | 'foul';
  catalog: Catalog | null;
}) {
  const clips =
    catalog?.clips.filter((c) =>
      kind === 'tracking'
        ? c.id === 'tracking-projection' || c.id === 'calibration'
        : c.id.startsWith('foul'),
    ) || [];
  const [selected, setSelected] = useState(''),
    [artifact, setArtifact] = useState<Artifact | null>(null),
    [showReport, setShowReport] = useState(false),
    [event, setEvent] = useState<CandidateRecord | null>(null),
    [seek, setSeek] = useState(0),
    [seekToken, setSeekToken] = useState(0);
  const runner = useJob();
  useEffect(() => {
    if (clips.length && !selected)
      setSelected(clips.find((c) => c.id === 'tracking-projection')?.id || clips[0].id);
  }, [clips, selected]);
  useEffect(() => {
    setEvent(null);
    setShowReport(false);
    runner.setJob(null);
    setSeek(0);
  }, [selected]);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    api
      .jobs()
      .then(({ jobs }) => {
        const latest = jobs.find((job) => job.kind === kind && job.case_id === selected);
        if (active && latest) runner.setJob(latest);
      })
      .catch(() => {
        /* Prepared outputs remain available if the job service is unavailable. */
      });
    return () => {
      active = false;
    };
  }, [selected, kind]);
  const clip = clips.find((c) => c.id === selected),
    mode = kind === 'tracking' ? 'projection_only' : 'foul_only';
  const fresh = Boolean(runner.job?.artifacts?.length);
  const artifacts = (fresh ? runner.job!.artifacts : clip?.artifacts || []).filter(
    (a) => !a.mode || a.mode === mode,
  );
  const video = artifacts.find((a) => a.kind === 'video'),
    reportArtifact = artifacts.find((a) => a.kind === 'report' || a.kind === 'json'),
    eventsArtifact = artifacts.find((a) => a.kind === 'events');
  useEffect(() => {
    setArtifact(video || null);
    setEvent(null);
  }, [selected, video?.id]);
  const reportLoader = useCallback(
    () =>
      reportArtifact
        ? request<ExperimentReport>(artifactSource(reportArtifact))
        : Promise.resolve(null),
    [reportArtifact?.id],
  );
  const report = useResource(reportLoader);
  const eventLoader = useCallback(async () => {
    if (!eventsArtifact) return [];
    const response = await fetch(artifactSource(eventsArtifact));
    if (!response.ok) throw new Error(`候选记录读取失败 (${response.status})`);
    return parseCandidateEvents(await response.text());
  }, [eventsArtifact?.id]);
  const records = useResource(eventLoader);
  const completed = ['completed', 'succeeded'].includes(runner.job?.status || '');
  const resultSource = fresh ? '最近一次 CUDA 运行' : '已准备的 CUDA 输出';
  function selectEvent(record: CandidateRecord) {
    setEvent(record);
    setSeek(record.media_pts_seconds);
    setSeekToken((n) => n + 1);
  }
  const status = runner.job
    ? statusLabels[runner.job.status] || runner.job.status
    : video
      ? '已准备结果'
      : '尚未运行';
  return (
    <>
      <div className="workspace">
        <main className="main-column">
          <PageHeader
            title={kind === 'tracking' ? '场地跟踪' : '实时犯规检测'}
            description={
              kind === 'tracking' ? '从视频坐标到二维球场' : '捕捉候选事件，保留审阅依据'
            }
          >
            <button
              className="button primary"
              disabled={
                !clip ||
                runner.busy ||
                runner.job?.status === 'running' ||
                runner.job?.status === 'queued'
              }
              onClick={() => void runner.start(kind, clip!.id)}
            >
              <Icon name="play" />
              {runner.busy ? '提交中…' : kind === 'tracking' ? '运行跟踪' : '开始检测'}
            </button>
          </PageHeader>
          <Alert message={runner.error || runner.job?.error || records.error} />
          <MediaPlayer
            src={artifact ? artifactSource(artifact) : clip?.source_url}
            poster={artifact ? undefined : clip?.poster_url}
            label={
              artifact
                ? `${resultSource} · ${kind === 'tracking' ? '跟踪与场地投影' : '犯规候选检测'}`
                : clip?.title
            }
            seekTo={seek}
            seekToken={seekToken}
          />
          <div className="artifact-rail">
            <button className={!artifact ? 'selected' : ''} onClick={() => setArtifact(null)}>
              原始视频
            </button>
            {video && (
              <button
                className={artifact?.id === video.id ? 'selected' : ''}
                onClick={() => setArtifact(video)}
              >
                {kind === 'tracking' ? '跟踪与场地投影' : '候选检测输出'}
                <small>{fresh ? '最近运行' : '已准备结果'}</small>
              </button>
            )}
          </div>
          {kind === 'tracking' ? (
            <p className="output-note">
              跟踪、分队与 2D 投影在输出画面中同步呈现；未知身份和未投影目标保留原状态。
            </p>
          ) : (
            <EventTimeline
              events={records.data || []}
              duration={clip?.duration_seconds || 0}
              selected={event?.event.id || null}
              loading={records.loading}
              onSelect={selectEvent}
            />
          )}
          {showReport && reportArtifact && (
            <section className="report-panel">
              <div className="section-line">
                <h2>实验报告</h2>
                <button className="text-button" onClick={() => setShowReport(false)}>
                  收起
                </button>
              </div>
              <a
                href={artifactSource(reportArtifact)}
                target="_blank"
                rel="noreferrer"
                className="button secondary"
              >
                打开完整报告 <Icon name="arrow" />
              </a>
              <p className="quiet">报告记录本次模型配置与输出统计；候选结果需人工复核。</p>
              <Alert message={report.error} />
            </section>
          )}
        </main>
        <aside className="inspector">
          <section>
            <h2>输入视频</h2>
            <label className="field">
              <span className="sr-only">输入视频</span>
              <select value={selected} onChange={(e) => setSelected(e.target.value)}>
                {!clips.length && <option value="">等待视频目录</option>}
                {clips.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.filename || c.title}
                  </option>
                ))}
              </select>
            </label>
            <p className="quiet">
              真实视频输入 ·{' '}
              {clip?.duration_seconds ? `${clip.duration_seconds.toFixed(2)} 秒` : '时长读取中'}
            </p>
          </section>
          {kind === 'tracking' ? (
            <section>
              <h2>跟踪流程</h2>
              <ol className="pipeline">
                {[
                  ['检测球员', '读取真实视频中的球员目标'],
                  ['分队与身份关联', '保留未知身份并维持跟踪'],
                  ['单应性投影', '将可投影坐标转换到二维球场'],
                ].map(([name, description], i) => (
                  <li key={name}>
                    <span>{String(i + 1).padStart(2, '0')}</span>
                    <div>
                      <strong>{name}</strong>
                      <p>{description}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </section>
          ) : (
            <section>
              <h2>运行状态</h2>
              <div className="run-state">
                <span className={`dot ${runner.job?.status === 'running' ? 'pulse' : ''}`} />
                {status}
              </div>
              {runner.job && (
                <progress
                  max="100"
                  value={runner.job.progress <= 1 ? runner.job.progress * 100 : runner.job.progress}
                />
              )}
              <p className="quiet">
                {fresh ? '最近任务在远端 GPU 执行' : '可播放已准备结果，或立即运行新的 CUDA 任务'}
              </p>
            </section>
          )}
          <section>
            <h2>{kind === 'tracking' ? '运行结果' : '事件详情'}</h2>
            {kind === 'foul' ? (
              event ? (
                <div className="candidate-detail">
                  <div className="result-source">模型候选 · 尚未人工确认</div>
                  <h3>
                    {actionLabels[event.event.foul_details?.action || ''] ||
                      event.event.foul_details?.action ||
                      '候选事件'}
                  </h3>
                  <p>{event.event.foul_details?.severity || '未提供严重程度'}</p>
                  <div className="result-line">
                    <span>视频时间</span>
                    <strong>{event.media_pts_seconds.toFixed(2)} s</strong>
                  </div>
                  <div className="result-line">
                    <span>置信度</span>
                    <strong>{Math.round(event.event.confidence * 100)}%</strong>
                  </div>
                  <p className="quiet">
                    来源：{event.event.evidence?.source || 'CUDA 模型'} · 单视频候选检测
                  </p>
                </div>
              ) : (
                <Empty>
                  {records.data?.length
                    ? '选择检测事件查看详情'
                    : completed
                      ? '本次运行无候选事件'
                      : '选择检测事件查看详情'}
                </Empty>
              )
            ) : (
              <div className="job-detail">
                <div className="run-state">{status}</div>
                {runner.job && (
                  <progress
                    max="100"
                    value={
                      runner.job.progress <= 1 ? runner.job.progress * 100 : runner.job.progress
                    }
                  />
                )}
                <p className="quiet">{resultSource}</p>
              </div>
            )}
            {report.data && (
              <div className="report-stats">
                {report.data.fps_including_render_and_json !== undefined && (
                  <div className="result-line">
                    <span>离线处理速度</span>
                    <strong>{report.data.fps_including_render_and_json.toFixed(1)} FPS</strong>
                  </div>
                )}
                {report.data.unknown_team_rate_all_persons !== undefined && (
                  <div className="result-line">
                    <span>未知球队占比</span>
                    <strong>{(report.data.unknown_team_rate_all_persons * 100).toFixed(1)}%</strong>
                  </div>
                )}
                {kind === 'tracking' && report.data.projected_player_rate !== undefined && (
                  <div className="result-line">
                    <span>可投影目标占比</span>
                    <strong>{(report.data.projected_player_rate * 100).toFixed(1)}%</strong>
                  </div>
                )}
              </div>
            )}
            <button
              className="button secondary full"
              disabled={!reportArtifact}
              onClick={() => setShowReport(true)}
            >
              查看报告
            </button>
          </section>
        </aside>
      </div>
      <Footer>
        {kind === 'tracking'
          ? '真实视频输入 · 结果由远端 CUDA 任务生成'
          : '真实视频输入 · 单视频候选结果需人工复核'}
      </Footer>
    </>
  );
}
