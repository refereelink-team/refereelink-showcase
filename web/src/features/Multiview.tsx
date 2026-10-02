import { useEffect, useState } from 'react';
import type { Catalog } from '../types';
import type { MultiviewCase, ReviewState } from '../types/multiview';
import { api, mediaUrl, request } from '../api';
import { useAction, useResource } from '../hooks';
import { Alert, Empty, Footer, Modes, PageHeader, Icon } from '../components/UI';
import SynchronizedEvidencePlayer from '../components/multiview/SynchronizedEvidencePlayer';
import type { EvidenceReadiness } from '../components/multiview/SynchronizedEvidencePlayer';
import { decisionFocusTime } from '../components/multiview/evidencePlayback';
import FoulLocationPitch from '../components/multiview/FoulLocationPitch';
import FactsEditor from '../components/multiview/FactsEditor';
import ReviewAssessment from '../components/multiview/ReviewAssessment';
import { useMultiviewReview } from './useMultiviewReview';
import './multiview.css';

const stateLabels: Record<ReviewState, string> = {
  pending: '待复核',
  reviewed: '已复核',
  uncertain: '待确认',
  archived: '已归档',
};
const filters = ['all', 'pending', 'reviewed', 'uncertain', 'archived'] as const;
type Filter = (typeof filters)[number];
const noMedia: EvidenceReadiness = {
  allVideosReady: false,
  playableVideoCount: 0,
  totalViewCount: 0,
  loadingCameraIds: [],
  missingCameraIds: [],
  failedCameraIds: [],
};
function linkedCase() {
  return new URLSearchParams(window.location.search).get('case') || '';
}

export function LiveInputs({ onCapture }: { onCapture?: (id: string) => void }) {
  const live = useResource(api.live, 4000),
    action = useAction();
  async function control(kind: 'start' | 'stop') {
    const result = await action.run(() => api.liveControl(kind));
    if (result) live.setData(result);
  }
  async function capture() {
    const result = await action.run(api.trigger);
    if (result?.capture_state === 'capture_ready') onCapture?.(result.case_id);
    else if (result) action.setError('回放捕获失败，请检查现场数据源');
  }
  return (
    <section className="live-inputs">
      <div className="section-line">
        <h2>现场输入</h2>
        <div className="inline-actions">
          <button
            className="button secondary small"
            disabled={action.busy || !live.data?.configured}
            onClick={() => void control(live.data?.ingest_running ? 'stop' : 'start')}
          >
            {live.data?.ingest_running ? '停止接收' : '启动接收'}
          </button>
          {onCapture && (
            <button
              className="button primary small"
              disabled={action.busy || !live.data?.trigger_ready}
              onClick={() => void capture()}
            >
              捕获回放
            </button>
          )}
        </div>
      </div>
      <Alert message={live.error || action.error} onRetry={() => void live.refresh()} />
      <div className="live-grid">
        {live.data?.cameras.map((camera) => (
          <div className="live-camera" key={camera.camera_id}>
            {camera.preview_url && camera.receiving ? (
              <img src={mediaUrl(camera.preview_url)} alt={`${camera.display_name}现场画面`} />
            ) : (
              <Empty compact>等待现场画面</Empty>
            )}
            <div className="live-caption">
              <strong>{camera.display_name}</strong>
              <span className={`signal ${camera.receiving ? 'online' : ''}`}>
                {camera.receiving ? '正在接收' : '未接收'}
              </span>
            </div>
            <p>
              {camera.source_type === 'field' ? '手机输入' : 'RTSP 输入'} · 回放缓存{' '}
              {camera.buffer_seconds.toFixed(1)}s
            </p>
            {camera.last_error && <small className="error-text">{camera.last_error}</small>}
          </div>
        ))}
      </div>
      {!live.data && <Empty>{live.loading ? '正在读取现场状态…' : '现场服务不可用'}</Empty>}
      <p className="quiet">{live.data?.message || '现场状态来自后端接收服务'}</p>
      {live.data?.phone_connect_origin && (
        <p className="quiet">
          手机入口：
          <a href={live.data.phone_connect_origin} target="_blank" rel="noreferrer">
            {live.data.phone_connect_origin}
          </a>
        </p>
      )}
    </section>
  );
}
export default function Multiview({
  catalog,
  reload,
}: {
  catalog: Catalog | null;
  reload: () => Promise<void>;
}) {
  const [mode, setMode] = useState<'video' | 'live'>('video');
  const [selection, setSelection] = useState(linkedCase);
  const [cases, setCases] = useState<MultiviewCase[]>([]);
  const [filter, setFilter] = useState<Filter>('all');
  const [panel, setPanel] = useState<'facts' | 'rules'>('facts');
  const [readiness, setReadiness] = useState(noMedia);
  const [focusRequest, setFocusRequest] = useState<{
    token: string | number;
    commonTimeS: number;
  }>();
  const status = useResource(api.multiviewStatus, 15000);
  const workflow = useMultiviewReview(selection);
  const current = cases.find((c) => c.case_id === selection);
  useEffect(() => {
    if (!catalog) return;
    setCases((previous) =>
      catalog.cases.map((item) => ({
        ...item,
        review_state:
          workflow.states[item.case_id]?.state ??
          previous.find((c) => c.case_id === item.case_id)?.review_state ??
          item.review_state,
        review_revision: workflow.states[item.case_id]?.revision ?? item.review_revision,
      })),
    );
    setSelection((value) => value || catalog.cases[0]?.case_id || '');
  }, [catalog, workflow.states]);
  useEffect(() => {
    const restore = () => {
      const id = linkedCase();
      if (id) setSelection(id);
    };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, []);
  useEffect(() => {
    if (!selection) return;
    const url = new URL(window.location.href);
    url.searchParams.set('case', selection);
    window.history.replaceState(null, '', url);
    setReadiness(noMedia);
    setFocusRequest(undefined);
  }, [selection]);
  function choose(id: string) {
    setSelection(id);
  }
  async function analyze() {
    const result = await workflow.analyze();
    if (result && current) {
      const commonTimeS = decisionFocusTime(current, result);
      if (commonTimeS !== null) setFocusRequest({ token: result.analysis_id, commonTimeS });
    }
  }
  async function capture(id: string) {
    try {
      const result = await request<{ cases: MultiviewCase[] }>('/api/multiview/cases');
      setCases(result.cases);
      setSelection(id);
      setMode('video');
      await reload();
    } catch (error) {
      workflow.showError(error);
    }
  }
  const decision = workflow.decision;
  const pendingCount = cases.filter((c) =>
    ['pending', 'uncertain'].includes(c.review_state),
  ).length;
  const visible = cases.filter((c) => filter === 'all' || c.review_state === filter);
  const modelReady =
    status.data?.ready && status.data.cuda_available && status.data.mode === 'model';
  const mediaReason = !current
    ? '选择案例后开始分析'
    : readiness.failedCameraIds.length
      ? '部分机位无法解码，请检查视频'
      : readiness.missingCameraIds.length
        ? '案例缺少可分析的视频'
        : !readiness.allVideosReady
          ? '等待所有机位的视频加载'
          : '';
  const modelReason = status.error
    ? '无法读取模型状态'
    : status.loading
      ? '正在检查模型资源'
      : !modelReady
        ? `CUDA 模型尚未就绪${status.data?.missing.length ? '：' + status.data.missing.join('、') : ''}`
        : '';
  const blockedReason = modelReason || mediaReason;
  const editable = Boolean(current) && !workflow.loading && !workflow.saving;
  return (
    <>
      <div className="mv-workbench">
        <main className="mv-evidence-column">
          <PageHeader
            title="多视角判罚"
            description="同步查看证据，逐项确认判罚事实"
            leading={
              <Modes
                label="输入模式"
                value={mode}
                onChange={setMode}
                options={[
                  { value: 'video', label: '视频' },
                  { value: 'live', label: '实时' },
                ]}
              />
            }
          >
            <button
              className="button primary"
              disabled={
                mode === 'live' ||
                !current ||
                workflow.analyzing ||
                workflow.saving ||
                workflow.loading ||
                !modelReady ||
                !readiness.allVideosReady
              }
              onClick={() => void analyze()}
              title={blockedReason || undefined}
            >
              <Icon name="play" /> {workflow.analyzing ? '正在分析…' : '开始分析'}
            </button>
          </PageHeader>
          <Alert
            message={workflow.error || status.error}
            onRetry={() => {
              void status.refresh();
              void workflow.refresh();
            }}
          />
          {mode === 'live' ? (
            <LiveInputs onCapture={(id) => void capture(id)} />
          ) : (
            <>
              <section className="mv-case-queue" aria-label="案例队列">
                <div className="mv-queue-heading">
                  <span>
                    {cases.length} 个案例 · {pendingCount} 个待处理
                  </span>
                  <label>
                    复核状态
                    <select
                      aria-label="筛选复核状态"
                      value={filter}
                      onChange={(e) => setFilter(e.target.value as Filter)}
                    >
                      {filters.map((value) => (
                        <option key={value} value={value}>
                          {value === 'all' ? '全部' : stateLabels[value]}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <div className="mv-case-list">
                  {visible.map((item) => {
                    const number = cases.findIndex((c) => c.case_id === item.case_id) + 1;
                    return (
                      <button
                        key={item.case_id}
                        className={`mv-case-option ${item.case_id === selection ? 'selected' : ''}`}
                        aria-pressed={item.case_id === selection}
                        onClick={() => choose(item.case_id)}
                      >
                        <strong>案例 {number}</strong>
                        <span>
                          <i className={`mv-state-dot ${item.review_state}`} />
                          {stateLabels[item.review_state]}
                        </span>
                      </button>
                    );
                  })}
                </div>
                {!visible.length && (
                  <Empty compact>{cases.length ? '没有符合筛选条件的案例' : '正在加载案例'}</Empty>
                )}
              </section>
              {current ? (
                <SynchronizedEvidencePlayer
                  key={current.case_id}
                  caseData={current}
                  decision={decision}
                  onReadinessChange={setReadiness}
                  focusRequest={focusRequest}
                />
              ) : (
                <Empty>未找到该案例，请从队列重新选择</Empty>
              )}
              {workflow.notice && (
                <p className="notice mv-notice" role="status">
                  {workflow.notice}
                </p>
              )}
              {blockedReason && (
                <p className="quiet mv-readiness" role="status">
                  {blockedReason}
                </p>
              )}
              <div className="mv-evidence-note">
                <span>同步播放 · ±0.04 秒精细定位</span>
                <span>模型建议需人工确认</span>
              </div>
            </>
          )}
        </main>
        <aside className="mv-inspector" aria-label="判罚复核">
          <section className="mv-model-panel">
            <div className="section-line">
              <h2>模型建议</h2>
              {decision && (
                <span className={`mv-source-label ${decision.mode}`}>
                  {decision.mode === 'model' ? 'CUDA 模型' : '脚本结果'}
                </span>
              )}
            </div>
            {mode === 'live' ? (
              <Empty compact>捕获回放后分析</Empty>
            ) : decision ? (
              <>
                <div className="mv-model-result">
                  <h3>{decision.decision_zh || decision.decision}</h3>
                  <span className="mv-confidence">
                    {Math.round(decision.confidence * 100)}%<small>置信度</small>
                  </span>
                </div>
                <p className="quiet">
                  {decision.action} · {decision.severity}
                  {decision.inference_ms !== null
                    ? ` · ${Math.round(decision.inference_ms)} ms`
                    : ''}
                </p>
                {current && decision.view_attention.length > 0 && (
                  <div className="mv-attention" aria-label="机位关注权重">
                    {current.videos.map((view, index) => {
                      const weight = decision.view_attention[index];
                      return Number.isFinite(weight) ? (
                        <div key={view.camera_id}>
                          <span>机位 {index + 1}</span>
                          <meter
                            min="0"
                            max="1"
                            value={Math.max(0, Math.min(1, weight))}
                            aria-label={`机位 ${index + 1}关注权重`}
                          />
                          <strong>{Math.round(weight * 100)}%</strong>
                        </div>
                      ) : null;
                    })}
                  </div>
                )}
              </>
            ) : (
              <Empty compact>
                {workflow.loading ? '正在读取已有分析…' : '点击开始分析，生成模型建议'}
              </Empty>
            )}
          </section>
          {mode === 'video' && (
            <>
              <div className="mv-review-tabs" role="tablist" aria-label="复核面板">
                {(['facts', 'rules'] as const).map((value) => (
                  <button
                    key={value}
                    id={`mv-review-tab-${value}`}
                    role="tab"
                    aria-selected={panel === value}
                    aria-controls={`mv-review-panel-${value}`}
                    tabIndex={panel === value ? 0 : -1}
                    className={panel === value ? 'selected' : ''}
                    onClick={() => setPanel(value)}
                    onKeyDown={(event) => {
                      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
                        event.preventDefault();
                        const next =
                          event.key === 'Home'
                            ? 'facts'
                            : event.key === 'End'
                              ? 'rules'
                              : panel === 'facts'
                                ? 'rules'
                                : 'facts';
                        setPanel(next);
                        document.getElementById(`mv-review-tab-${next}`)?.focus();
                      }
                    }}
                  >
                    {value === 'facts' ? '事实确认' : '规则依据'}
                    {value === 'rules' && workflow.dirty && <i aria-label="待重新评估" />}
                  </button>
                ))}
              </div>
              <div
                className="mv-review-content"
                id={`mv-review-panel-${panel}`}
                role="tabpanel"
                aria-labelledby={`mv-review-tab-${panel}`}
              >
                {panel === 'facts' ? (
                  <>
                    <FoulLocationPitch
                      key={selection}
                      location={workflow.facts.location}
                      geometry={
                        workflow.dirty ? null : (workflow.review?.assessment.geometry ?? null)
                      }
                      offenderTeam={workflow.facts.offender_team}
                      homeDefendsSide={workflow.facts.home_defends_side}
                      dirty={workflow.dirty}
                      saving={workflow.saving}
                      disabled={!editable || workflow.analyzing}
                      onChange={(location) => workflow.edit({ ...workflow.facts, location })}
                    />
                    <FactsEditor
                      facts={workflow.facts}
                      onChange={workflow.edit}
                      newModelFields={workflow.newModelFields}
                      prefillToken={workflow.prefillToken}
                      disabled={!editable || workflow.analyzing}
                    />
                  </>
                ) : (
                  <ReviewAssessment
                    assessment={workflow.review?.assessment ?? null}
                    explanation={workflow.explanation}
                    revision={workflow.review?.revision ?? null}
                    dirty={workflow.dirty}
                    explaining={workflow.explaining}
                    onExplain={() => void workflow.explain()}
                    explainDisabled={!workflow.review || workflow.dirty || workflow.saving}
                  />
                )}
              </div>
              <section className="mv-review-actions">
                <button
                  className="text-button mv-reload-review"
                  disabled={workflow.loading || workflow.saving || workflow.analyzing}
                  onClick={() => void workflow.refresh()}
                >
                  重新载入复核
                </button>
                <p className="mv-draft-status" role="status">
                  {workflow.loading
                    ? '正在读取复核…'
                    : workflow.dirty
                      ? '草稿已修改 · 保存后重新评估'
                      : workflow.review
                        ? `${stateLabels[workflow.review.review_state]} · 修订 ${workflow.review.revision}`
                        : '尚未保存复核'}
                </p>
                <div className="mv-save-row">
                  <button
                    className="button secondary"
                    disabled={!editable || workflow.analyzing}
                    onClick={() => void workflow.save(workflow.review?.review_state ?? 'pending')}
                  >
                    {workflow.saving ? '正在保存…' : '保存草稿'}
                  </button>
                  <button
                    className="button primary"
                    disabled={!editable || workflow.analyzing}
                    onClick={() => void workflow.save('reviewed')}
                  >
                    确认复核
                  </button>
                </div>
                <div className="mv-secondary-row">
                  <button
                    className="text-button"
                    disabled={!editable || workflow.analyzing}
                    onClick={() => void workflow.save('uncertain')}
                  >
                    标记待确认
                  </button>
                  <button
                    className="text-button"
                    disabled={!editable || workflow.analyzing}
                    onClick={() => void workflow.save('archived')}
                  >
                    归档
                  </button>
                  {workflow.dirty && (
                    <button
                      className="text-button"
                      disabled={!editable || workflow.analyzing}
                      onClick={workflow.discard}
                    >
                      还原草稿
                    </button>
                  )}
                </div>
              </section>
            </>
          )}
        </aside>
      </div>
      <Footer>
        {mode === 'live' ? '现场输入 · 设备状态来自后端' : '视频案例 · 模型建议与人工复核分别记录'}
      </Footer>
    </>
  );
}
