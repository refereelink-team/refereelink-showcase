import LivePreview from '../components/LivePreview';
import { useEffect, useState } from 'react';
import type { Catalog } from '../types';
import type { MultiviewCase } from '../types/multiview';
import { api, mediaUrl, request } from '../api';
import { useAction, useResource } from '../hooks';
import { Alert, Empty, Footer, Modes, PageHeader, Icon } from '../components/UI';
import SynchronizedEvidencePlayer from '../components/multiview/SynchronizedEvidencePlayer';
import type { EvidenceReadiness } from '../components/multiview/SynchronizedEvidencePlayer';
import { decisionFocusTime } from '../components/multiview/evidencePlayback';
import ReviewGuide from '../components/multiview/ReviewGuide';
import { adoptModelFields } from '../components/multiview/reviewGuideFlow';
import { useMultiviewReview } from './useMultiviewReview';
import { liveSourceLabel } from './liveSourceLabel';
import './multiview.css';

// Presentation order is independent of the original case IDs and archived media paths.
const hiddenFieldCases = new Set([
  'manual-20261004-04-01',
  'manual-20261004-04-02',
  'manual-20261004-05-02',
]);
const fieldDemoTitles = new Map([
  ['manual-20261004-02-01', '实地测试案例1'],
  ['manual-20261004-03-01', '实地测试案例2'],
  ['manual-20261004-05-01', '实地测试案例3'],
]);
function displayCases(items: MultiviewCase[]): MultiviewCase[] {
  return items
    .filter((item) => !hiddenFieldCases.has(item.case_id))
    .map((item) => {
      const title = fieldDemoTitles.get(item.case_id);
      return title ? { ...item, title } : item;
    });
}
function visibleSelection(id: string, items: MultiviewCase[]): string {
  return !id || hiddenFieldCases.has(id) ? items[0]?.case_id || '' : id;
}

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
  const live = useResource(api.live, 1000),
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
              <LivePreview
                url={mediaUrl(camera.preview_url) ?? camera.preview_url}
                alt={`${camera.display_name}现场画面`}
              />
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
              {liveSourceLabel(camera)} · 回放缓存 {camera.buffer_seconds.toFixed(1)}s
            </p>
            {camera.last_error && <small className="error-text">{camera.last_error}</small>}
          </div>
        ))}
      </div>
      {!live.data && <Empty>{live.loading ? '正在读取现场状态…' : '现场服务不可用'}</Empty>}
      {live.data?.phone?.video_transport === 'rtsp' && (
        <p className="quiet">
          手机、摄像头与 Mac 接同一交换机。手机使用 RefereeLink App 有线
          RTSP；位姿与本地离线录制保留。
        </p>
      )}
      <p className="quiet">
        {live.data?.message || '现场状态来自后端接收服务；预览约每秒两帧，录像保留原始帧率'}
      </p>
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
  const [selection, setSelection] = useState(() => visibleSelection(linkedCase(), []));
  const [cases, setCases] = useState<MultiviewCase[]>([]);
  useEffect(() => {
    if (!catalog) return;
    const visible = displayCases(catalog.cases);
    setCases(visible);
    setSelection((value) => visibleSelection(value, visible));
  }, [catalog]);
  useEffect(() => {
    const restore = () => {
      const id = linkedCase();
      if (id) setSelection(visibleSelection(id, cases));
    };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, [cases]);
  useEffect(() => {
    if (!selection) return;
    const url = new URL(window.location.href);
    url.searchParams.set('case', selection);
    window.history.replaceState(null, '', url);
  }, [selection]);
  async function capture(id: string) {
    const result = await request<{ cases: MultiviewCase[] }>('/api/multiview/cases');
    const visible = displayCases(result.cases);
    setCases(visible);
    setSelection(visibleSelection(id, visible));
    setMode('video');
    await reload();
  }
  return (
    <DemoWorkbench
      key={selection}
      selection={selection}
      cases={cases}
      mode={mode}
      onModeChange={setMode}
      onChoose={setSelection}
      onCapture={capture}
    />
  );
}

// Remounting the entire case workspace clears facts, results, guide UI and pending requests.
function DemoWorkbench({
  selection,
  cases,
  mode,
  onModeChange,
  onChoose,
  onCapture,
}: {
  selection: string;
  cases: MultiviewCase[];
  mode: 'video' | 'live';
  onModeChange: (mode: 'video' | 'live') => void;
  onChoose: (id: string) => void;
  onCapture: (id: string) => Promise<void>;
}) {
  const [readiness, setReadiness] = useState(noMedia);
  const [focusRequest, setFocusRequest] = useState<{
    token: string | number;
    commonTimeS: number;
  }>();
  const status = useResource(api.multiviewStatus, 15000);
  const workflow = useMultiviewReview(selection);
  const current = cases.find((c) => c.case_id === selection);
  function reset() {
    setFocusRequest(undefined);
    workflow.reset();
  }
  async function analyze() {
    // Preserve the last successful attribution while a retry is pending or fails.
    const result = await workflow.analyze();
    if (result && current) {
      const commonTimeS = decisionFocusTime(current, result);
      setFocusRequest(
        commonTimeS === null ? undefined : { token: result.analysis_id, commonTimeS },
      );
    }
  }
  async function capture(id: string) {
    try {
      await onCapture(id);
    } catch (error) {
      workflow.showError(error);
    }
  }
  const decision = workflow.decision;
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
  const editable = Boolean(current);
  return (
    <>
      <div className="mv-workbench">
        <main className="mv-evidence-column">
          <PageHeader
            title="多视角判罚"
            leading={
              <Modes
                label="输入模式"
                value={mode}
                onChange={onModeChange}
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
              workflow.retryPreview();
            }}
          />
          {mode === 'live' ? (
            <LiveInputs onCapture={(id) => void capture(id)} />
          ) : (
            <>
              <section className="mv-case-queue" aria-label="案例队列">
                <div className="mv-case-list">
                  {cases.map((item) => {
                    return (
                      <button
                        key={item.case_id}
                        className={`mv-case-option ${item.case_id === selection ? 'selected' : ''}`}
                        aria-pressed={item.case_id === selection}
                        onClick={() => onChoose(item.case_id)}
                      >
                        <strong>{item.title}</strong>
                      </button>
                    );
                  })}
                </div>
                {!cases.length && <Empty compact>正在加载案例</Empty>}
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
              {blockedReason && (
                <p className="quiet mv-readiness" role="status">
                  {blockedReason}
                </p>
              )}
            </>
          )}
        </main>
        <aside className="mv-inspector" aria-label="判罚演示">
          {mode === 'video' ? (
            <ReviewGuide
              key={selection}
              facts={workflow.facts}
              step={workflow.step}
              onStepChange={workflow.setStep}
              preview={workflow.preview}
              previewLoading={workflow.previewLoading}
              previewError={workflow.previewError}
              onRetryPreview={workflow.retryPreview}
              decision={decision}
              newModelFields={workflow.newModelFields}
              prefillToken={workflow.prefillToken}
              protectedFields={workflow.protectedFields}
              busy={workflow.analyzing}
              disabled={!editable || workflow.analyzing}
              onChange={workflow.edit}
              onAdopt={(fields) => workflow.edit(adoptModelFields(workflow.facts, fields))}
              onLocationChange={(location) => workflow.edit({ ...workflow.facts, location })}
              onReset={reset}
            />
          ) : (
            <Empty compact>捕获回放后开始判罚演示</Empty>
          )}
        </aside>
      </div>
      <Footer>
        {mode === 'live' ? '现场输入 · 设备状态来自后端' : '案例演示 · 切换案例后重新开始判罚'}
      </Footer>
    </>
  );
}
