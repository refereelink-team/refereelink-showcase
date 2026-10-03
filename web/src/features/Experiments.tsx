import { useCallback, useEffect, useRef, useState } from 'react';
import type { Catalog, Artifact, DetectionProfile } from '../types';
import type { CandidateRecord } from '../events';
import {
  candidateEmittedTime,
  candidateEventTime,
  candidateRegionLabel,
  foulActionLabel,
  latestProfileJob,
  matchesDetectorSnapshot,
  profileArtifacts,
  targetLabel,
} from '../events';
import { api, mediaUrl, request } from '../api';
import { ExperimentRequestScope } from '../experimentRequestScope';
import { useJob, useResource } from '../hooks';
import MediaPlayer from '../components/MediaPlayer';
import FoulScene from '../components/foul/FoulScene';
import {
  collectFoulFrames,
  foulFrameArtifact,
  severityLabel,
} from '../components/foul/foulPresentation';
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
  model_id?: string;
  detection_profile?: DetectionProfile;
  alert_latency_s?: { mean?: number; p95?: number };
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
    [cycleEvents, setCycleEvents] = useState<{ key: string; records: CandidateRecord[] }>({
      key: '',
      records: [],
    });
  const preparationLoader = useCallback(
    () => (kind === 'foul' ? api.foulPreparation() : Promise.resolve(null)),
    [kind],
  );
  const preparation = useResource(preparationLoader, kind === 'foul' ? 1500 : 0);
  const prepared = preparation.data?.clips.find((item) => item.case_id === selected);
  const preparationKey = `${preparation.data?.boot_id || ''}:${prepared?.job_id || ''}:${prepared?.result_id || ''}:${prepared?.revision || ''}`;
  const detectionProfile: DetectionProfile = kind === 'foul' ? 'mvit-contact-v3' : 'legacy-v1';
  const qualification =
    detectionProfile === 'mvit-contact-v3' ? catalog?.foul_detection?.qualification : null;
  const fingerprintKey = qualification
    ? [
        qualification.config_sha256,
        qualification.model_sha256,
        qualification.core_manifest_sha256,
        qualification.external_source_sha256,
      ].join(':')
    : '';
  const runner = useJob(
    `${kind}:${selected}:${detectionProfile}:${fingerprintKey}:${preparationKey}`,
  );
  useEffect(() => {
    if (clips.length && !selected)
      setSelected(clips.find((c) => c.id === 'tracking-projection')?.id || clips[0].id);
  }, [clips, selected]);
  useEffect(() => {
    setEvent(null);
    setShowReport(false);
    runner.setJob(null);
  }, [selected, detectionProfile, fingerprintKey, preparationKey]);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    const ticket = runner.hydrationTicket();
    if (kind === 'foul') {
      if (prepared?.job_id)
        api
          .job(prepared.job_id)
          .then((job) => {
            if (
              active &&
              job.id === prepared.job_id &&
              job.case_id === selected &&
              job.kind === 'foul' &&
              job.detection_profile === detectionProfile
            )
              runner.hydrateJob(job, ticket);
          })
          .catch(() => {});
    } else
      api
        .jobs()
        .then(({ jobs }) => {
          const latest = latestProfileJob(jobs, kind, selected, detectionProfile, qualification);
          if (active && latest) runner.hydrateJob(latest, ticket);
        })
        .catch(() => {
          /* Prepared outputs remain available if the job service is unavailable. */
        });
    return () => {
      active = false;
    };
  }, [selected, kind, detectionProfile, fingerprintKey, preparationKey]);
  const clip = clips.find((c) => c.id === selected);
  const fresh = Boolean(runner.job?.artifacts?.length);
  const artifacts = profileArtifacts(
    runner.job,
    clip?.artifacts || [],
    selected,
    kind,
    detectionProfile,
    qualification,
  );
  const video = artifacts.find((a) => a.kind === 'video'),
    reportArtifact = artifacts.find((a) => a.kind === 'report' || a.kind === 'json'),
    eventsArtifact = artifacts.find((a) => a.kind === 'events');
  useEffect(() => {
    setArtifact(kind === 'foul' ? null : video || null);
    setEvent(null);
  }, [selected, video?.id, kind]);
  const reportLoader = useCallback(
    () =>
      reportArtifact
        ? request<ExperimentReport>(artifactSource(reportArtifact))
        : Promise.resolve(null),
    [reportArtifact?.id],
  );
  const report = useResource(reportLoader);
  const framesArtifact = foulFrameArtifact(artifacts, eventsArtifact);
  const nativeScope = useRef(new ExperimentRequestScope());
  const nativeKey = `${selected}:${detectionProfile}:${fingerprintKey}:${preparationKey}:${framesArtifact?.id || ''}`;
  nativeScope.current.activate(nativeKey);
  const nativeLoader = useCallback(async () => {
    if (
      kind !== 'foul' ||
      !framesArtifact ||
      !clip ||
      prepared?.status !== 'ready' ||
      framesArtifact.id !== prepared.result_id
    )
      return null;
    const ticket = nativeScope.current.ticket();
    if (ticket.scope !== nativeKey) return null;
    const result = await api.foulResult(framesArtifact.id);
    if (!nativeScope.current.current(ticket)) return null;
    if (
      result.id !== framesArtifact.id ||
      result.revision !== prepared.revision ||
      result.case_id !== clip.id ||
      result.source.url !== clip.source_url ||
      result.detection_profile !== detectionProfile ||
      !matchesDetectorSnapshot(
        { ...framesArtifact, detector_fingerprint: result.detector_fingerprint },
        qualification,
      )
    )
      throw new Error('检测标注与当前视频或模型配置不匹配');
    const frames = await collectFoulFrames(
      result,
      (offset) => api.foulFrames(result.id, result.revision, offset),
      () => nativeScope.current.current(ticket),
    );
    if (!nativeScope.current.current(ticket)) return null;
    return { result, frames };
  }, [kind, nativeKey]);
  const native = useResource(nativeLoader);
  const nativeData =
    prepared?.status === 'ready' &&
    native.data?.result.id === prepared.result_id &&
    native.data.result.revision === prepared.revision &&
    native.data?.result.id === framesArtifact?.id &&
    native.data?.result.case_id === selected &&
    native.data.result.detection_profile === detectionProfile &&
    native.data.result.source.url === clip?.source_url &&
    framesArtifact &&
    matchesDetectorSnapshot(
      { ...framesArtifact, detector_fingerprint: native.data.result.detector_fingerprint },
      qualification,
    )
      ? native.data
      : null;
  const records = {
    data: nativeData?.result.events || [],
    loading: native.loading,
    error: native.error,
  };
  const cycleTicket = nativeScope.current.ticket();
  const handleCycleEvents = useCallback(
    (values: CandidateRecord[]) => {
      if (!nativeScope.current.current(cycleTicket)) return;
      setCycleEvents({ key: nativeKey, records: values });
      setEvent(
        (previous) =>
          values.find((item) => item.event.id === previous?.event.id) || values.at(-1) || null,
      );
    },
    [nativeKey],
  );
  const visibleRecords = nativeData && cycleEvents.key === nativeKey ? cycleEvents.records : [];
  const selectedEvent = event
    ? visibleRecords.find((record) => record.event.id === event.event.id) || null
    : null;
  const legacy = kind === 'foul' && detectionProfile === 'legacy-v1';
  const resultSource = legacy
    ? '历史算法结果'
    : fresh
      ? '最近一次 CUDA 运行'
      : '已准备的 CUDA 输出';
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
            {kind === 'tracking' && (
              <button
                className="button primary"
                disabled={
                  !clip ||
                  runner.busy ||
                  runner.job?.status === 'running' ||
                  runner.job?.status === 'queued'
                }
                onClick={() => void runner.start('tracking', clip!.id)}
              >
                <Icon name="play" />
                {runner.busy ? '提交中…' : '运行跟踪'}
              </button>
            )}
          </PageHeader>
          <Alert
            message={
              preparation.error ||
              (prepared?.status === 'error' ? '初始化失败，请重启后端重试' : null) ||
              runner.error ||
              runner.job?.error ||
              records.error
            }
            onRetry={records.error ? () => void native.refresh() : undefined}
          />
          {kind === 'foul' ? (
            <FoulScene
              key={`${nativeKey}:${nativeData?.result.id || ''}:${nativeData?.result.revision || ''}`}
              source={clip?.source_url}
              poster={clip?.poster_url}
              result={nativeData?.result || null}
              frames={nativeData?.frames || []}
              selected={selectedEvent}
              onSelect={setEvent}
              onEventsChange={handleCycleEvents}
              loading={Boolean(framesArtifact) && records.loading}
              emptyMessage={
                runner.job?.status === 'running' || runner.job?.status === 'queued'
                  ? '正在检测候选事件…'
                  : native.error
                    ? '标注读取失败，请重试'
                    : nativeData
                      ? '等待事件'
                      : video
                        ? '此结果缺少原生标注，请重新检测'
                        : '运行检测后查看事件'
              }
            />
          ) : (
            <MediaPlayer
              src={artifact ? artifactSource(artifact) : clip?.source_url}
              poster={artifact ? undefined : clip?.poster_url}
              label={
                artifact
                  ? `${resultSource} · ${kind === 'tracking' ? '跟踪与场地投影' : '犯规候选检测'}`
                  : clip?.title
              }
            />
          )}
          {kind === 'tracking' && (
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
                  <small>{legacy ? '历史算法' : fresh ? '最近运行' : '已准备结果'}</small>
                </button>
              )}
            </div>
          )}
          {kind === 'tracking' ? (
            <p className="output-note">
              跟踪、分队与 2D 投影在输出画面中同步呈现；未知身份和未投影目标保留原状态。
            </p>
          ) : (
            <details className="foul-export-menu">
              <summary>下载结果</summary>
              <a href={mediaUrl(clip?.source_url)} download>
                原始视频
              </a>
              {video && (
                <a href={artifactSource(video)} download>
                  标注视频
                </a>
              )}
              {eventsArtifact && (
                <a href={artifactSource(eventsArtifact)} download>
                  事件记录
                </a>
              )}
            </details>
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
                <span
                  className={`dot ${nativeData || prepared?.status === 'preparing' ? 'pulse' : ''}`}
                />
                {nativeData
                  ? '正在实时运行'
                  : prepared?.status === 'error' || preparation.error
                    ? '运行异常'
                    : prepared?.status === 'disabled'
                      ? '实时运行未启动'
                      : '正在初始化'}
              </div>
              {!nativeData && prepared?.status === 'preparing' && (
                <progress
                  max="100"
                  value={prepared.progress <= 1 ? prepared.progress * 100 : prepared.progress}
                />
              )}
              <p className="quiet">CUDA · MViT</p>
            </section>
          )}
          <section>
            <h2>{kind === 'tracking' ? '运行结果' : '事件详情'}</h2>
            {kind === 'foul' ? (
              selectedEvent ? (
                <div className="candidate-detail">
                  <div className="result-source">
                    {legacy ? '历史算法候选' : '模型候选'} · 尚未人工确认
                  </div>
                  <h3>{foulActionLabel(selectedEvent.event.foul_details?.action)}</h3>
                  <p>{severityLabel(selectedEvent.event.foul_details?.severity)}</p>
                  <div className="result-line">
                    <span>事件发生</span>
                    <strong>{candidateEventTime(selectedEvent).toFixed(2)} s</strong>
                  </div>
                  {candidateEmittedTime(selectedEvent) !== null && (
                    <div className="result-line">
                      <span>提示发出</span>
                      <strong>{candidateEmittedTime(selectedEvent)!.toFixed(2)} s</strong>
                    </div>
                  )}
                  <div className="result-line">
                    <span>{legacy ? '旧版综合分数' : '模型分数'}</span>
                    <strong>{Math.round(selectedEvent.event.confidence * 100)}%</strong>
                  </div>
                  {(['offence_score', 'action_score', 'severity_score'] as const).map((key, i) => {
                    const score =
                      selectedEvent.event.evidence?.[key] ??
                      selectedEvent.event.foul_details?.[key];
                    return typeof score === 'number' && Number.isFinite(score) ? (
                      <div className="result-line" key={key}>
                        <span>{['犯规分数', '动作分数', '严重程度分数'][i]}</span>
                        <strong>{(score * 100).toFixed(1)}%</strong>
                      </div>
                    ) : null;
                  })}
                  <div className="result-line">
                    <span>事件区域</span>
                    <strong>{candidateRegionLabel(selectedEvent) || '区域待确认'}</strong>
                  </div>
                  <div className="result-line">
                    <span>涉及目标</span>
                    <strong>
                      {selectedEvent.event.evidence?.involved_targets?.length
                        ? selectedEvent.event.evidence.involved_targets.map(targetLabel).join(' / ')
                        : '身份待确认'}
                    </strong>
                  </div>
                  {Boolean(selectedEvent.event.evidence?.involved_targets?.length) && (
                    <p className="quiet">目标编号为本次运行的跟踪编号。</p>
                  )}
                  {selectedEvent.event.evidence?.evidence_start_s !== undefined &&
                    selectedEvent.event.evidence?.evidence_end_s !== undefined && (
                      <div className="result-line">
                        <span>证据区间</span>
                        <strong>
                          {selectedEvent.event.evidence.evidence_start_s.toFixed(2)}–
                          {selectedEvent.event.evidence.evidence_end_s.toFixed(2)} s
                        </strong>
                      </div>
                    )}
                  <p className="quiet">
                    {selectedEvent.event.evidence?.model_id ||
                      runner.job?.model_id ||
                      report.data?.model_id ||
                      'MViT'}
                    {' · '}
                    {selectedEvent.event.evidence?.detection_profile || detectionProfile}
                  </p>
                </div>
              ) : (
                <Empty>等待事件</Empty>
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
            {report.data && (kind === 'tracking' || showReport) && (
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
