import { useCallback, useEffect, useState, useRef } from 'react';
import type { Catalog } from '../types';
import type {
  FoulFacts,
  ReviewRecord,
  MultiviewDecision,
  MultiviewCase,
  ExplanationResponse,
} from '../types/multiview';
import { api, mediaUrl, request } from '../api';
import { useAction, useResource } from '../hooks';
import MediaPlayer from '../components/MediaPlayer';
import { Alert, Empty, Footer, Modes, PageHeader, Icon } from '../components/UI';
function emptyFacts(): FoulFacts {
  const fact = { value: null, source: 'human' as const, confidence: null, confirmed: false };
  return {
    offence_confirmed: { ...fact },
    action: { ...fact },
    offender_team: { ...fact },
    victim_team: { ...fact },
    ball_in_play: { ...fact },
    contact: { ...fact },
    contact_region: { ...fact },
    intensity: { ...fact },
    attempt_to_play_ball: { ...fact },
    tactical_impact: { ...fact },
    home_defends_side: { ...fact },
    location: null,
  };
}
const restartLabels: Record<string, string> = {
  play_on: '继续比赛',
  direct_free_kick: '直接任意球',
  indirect_free_kick: '间接任意球',
  penalty: '罚球点球',
  unknown: '待判定',
  previous_restart: '恢复原重启',
};
const sanctionLabels: Record<string, string> = {
  none: '无牌',
  yellow_card: '黄牌',
  red_card: '红牌',
  pending: '待判定',
};
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
  const [mode, setMode] = useState<'video' | 'live'>('video'),
    [selection, setSelection] = useState(''),
    [cases, setCases] = useState<MultiviewCase[]>([]),
    [angle, setAngle] = useState(0),
    [seek, setSeek] = useState(0),
    playhead = useRef(0),
    [decision, setDecision] = useState<MultiviewDecision | null>(null),
    [review, setReview] = useState<ReviewRecord | null>(null),
    [facts, setFacts] = useState<FoulFacts>(emptyFacts),
    [explanation, setExplanation] = useState<ExplanationResponse | null>(null),
    [notice, setNotice] = useState<string | null>(null);
  const action = useAction();
  useEffect(() => {
    if (catalog) {
      setCases(catalog.cases);
      setSelection((current) => current || catalog.cases[0]?.case_id || '');
    }
  }, [catalog]);
  const current = cases.find((c) => c.case_id === selection),
    view = current?.videos[angle];
  const reviewLoader = useCallback(
    () => (selection ? api.review(selection) : Promise.resolve({ review: null, analysis: null })),
    [selection],
  );
  const saved = useResource(reviewLoader);
  useEffect(() => {
    setAngle(0);
    playhead.current = 0;
    setSeek(0);
    setDecision(null);
    setReview(null);
    setFacts(emptyFacts());
    setExplanation(null);
    setNotice(null);
  }, [selection]);
  useEffect(() => {
    if (saved.data) {
      setReview(saved.data.review);
      setDecision(saved.data.analysis);
      setFacts(saved.data.review?.facts || emptyFacts());
    }
  }, [saved.data]);
  async function analyze() {
    if (!current) return;
    setNotice(null);
    const result = await action.run(() => api.analyze(current.case_id));
    if (result) {
      if (result.status === 'ok' && result.decision) {
        setDecision(result.decision);
        setNotice('模型分析已完成，人工复核仍由你确认');
      } else action.setError(result.message || '分析未产生结果');
    }
  }
  async function capture(id: string) {
    const next = await action.run(() =>
      request<{ cases: MultiviewCase[] }>('/api/multiview/cases'),
    );
    if (next) {
      setCases(next.cases);
      setSelection(id);
      setMode('video');
      await reload();
    }
  }
  function setFact(name: keyof FoulFacts, value: string) {
    const parsed =
      value === '' ? null : ['true', 'false'].includes(value) ? value === 'true' : value;
    setFacts((f) => ({
      ...f,
      [name]: { value: parsed, source: 'human', confidence: null, confirmed: parsed !== null },
    }));
  }
  function factSelect(label: string, name: keyof FoulFacts, options: [string, string][]) {
    const value = facts[name];
    return (
      <label className="field">
        <span>{label}</span>
        <select
          value={value && 'value' in value && value.value !== null ? String(value.value) : ''}
          onChange={(e) => setFact(name, e.target.value)}
        >
          <option value="">未确认</option>
          {options.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      </label>
    );
  }
  async function save() {
    if (!current) return;
    setNotice(null);
    const result = await action.run(() =>
      api.saveReview(current.case_id, {
        expected_revision: review?.revision || 0,
        analysis_id: decision?.analysis_id || null,
        facts,
        review_state: 'reviewed',
      }),
    );
    if (result) {
      setReview(result.review);
      setNotice(`复核已保存 · 修订 ${result.review.revision}`);
    }
  }
  async function explain() {
    if (!review) return;
    const result = await action.run(() => api.explain(review.case_id, review.revision));
    if (result) setExplanation(result);
  }
  return (
    <>
      <div className="workspace">
        <main className="main-column">
          <PageHeader
            title="多视角判罚"
            description="让每一个角度成为判罚依据"
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
              disabled={!current || action.busy || mode === 'live'}
              onClick={() => void analyze()}
            >
              <Icon name="play" /> {action.busy ? '处理中…' : '开始分析'}
            </button>
          </PageHeader>
          <Alert message={action.error || saved.error} />
          {mode === 'live' ? (
            <LiveInputs onCapture={(id) => void capture(id)} />
          ) : (
            <>
              <MediaPlayer
                src={view?.media_url}
                label={
                  view ? `${String(angle + 1).padStart(2, '0')} ${view.display_name}` : undefined
                }
                onTime={(t) => {
                  playhead.current = t;
                }}
                seekTo={seek}
              />
              <div className="view-rail">
                {current?.videos.map((v, i) => (
                  <button
                    key={v.camera_id}
                    className={`view-choice ${i === angle ? 'selected' : ''}`}
                    onClick={() => {
                      setSeek(
                        Math.max(
                          0,
                          playhead.current -
                            (view?.sync_offset_ms || 0) / 1000 +
                            (v.sync_offset_ms || 0) / 1000,
                        ),
                      );
                      setAngle(i);
                    }}
                  >
                    {v.media_url && v.media_kind === 'video' ? (
                      <video src={mediaUrl(v.media_url)} preload="metadata" muted playsInline />
                    ) : v.media_url ? (
                      <img src={mediaUrl(v.media_url)} alt="" />
                    ) : (
                      <span className="view-placeholder" />
                    )}
                    <span>
                      {String(i + 1).padStart(2, '0')} {v.display_name}
                    </span>
                  </button>
                ))}
              </div>
              <section className="case-section">
                <div className="section-line">
                  <h2>准备案例</h2>
                  <span>{cases.length} 个案例 · 同步视角</span>
                </div>
                <div className="case-rail">
                  {cases.map((item, i) => (
                    <button
                      key={item.case_id}
                      className={`case-choice ${selection === item.case_id ? 'selected' : ''}`}
                      onClick={() => setSelection(item.case_id)}
                    >
                      <span className="case-number">{String(i + 1).padStart(2, '0')}</span>
                      <span>
                        <strong>{item.title}</strong>
                        <small>{item.match_name}</small>
                        <small>
                          {item.match_clock} · {item.videos.length} 视角
                        </small>
                      </span>
                    </button>
                  ))}
                </div>
                {!cases.length && <Empty compact>准备案例加载后可开始分析</Empty>}
              </section>
            </>
          )}
          {notice && mode === 'video' && (
            <p className="notice" role="status">
              {notice}
            </p>
          )}
        </main>
        <aside className="inspector">
          <section>
            <h2>模型分析</h2>
            {mode === 'live' ? (
              <Empty>捕获回放后分析</Empty>
            ) : decision ? (
              <div className="analysis-result">
                <div className="result-source">
                  {decision.mode === 'model' ? '真实模型推理' : '脚本回退结果'}
                </div>
                <h3>{decision.decision_zh || decision.decision}</h3>
                <p>
                  {decision.action} · {decision.severity}
                </p>
                <div className="result-line">
                  <span>置信度</span>
                  <strong>{Math.round(decision.confidence * 100)}%</strong>
                </div>
                {decision.inference_ms !== null && (
                  <div className="result-line">
                    <span>推理耗时</span>
                    <span>{Math.round(decision.inference_ms)} ms</span>
                  </div>
                )}
                <p className="quiet">模型候选结果，需人工复核</p>
              </div>
            ) : (
              <Empty>尚未分析</Empty>
            )}
          </section>
          <section className="human-review">
            <h2>人工判罚</h2>
            {mode === 'live' ? (
              <Empty compact>捕获回放后进行人工复核</Empty>
            ) : (
              <>
                {[facts.offence_confirmed, facts.action, facts.intensity].some(
                  (f) => f.source === 'model' && f.value !== null,
                ) && <p className="review-prefill">部分事实来自模型预填，请逐项确认。</p>}
                {factSelect('事件确认', 'offence_confirmed', [
                  ['true', '确认犯规'],
                  ['false', '不犯规'],
                ])}
                {facts.offence_confirmed.value === true && (
                  <>
                    {factSelect('动作类型', 'action', [
                      ['Tackle', '铲球'],
                      ['Standing Tackle', '站立抢断'],
                      ['High Leg', '抬脚过高'],
                      ['Holding', '拉扯'],
                      ['Pushing', '推搡'],
                      ['Elbowing', '肘击'],
                      ['Challenge', '身体争抢'],
                      ['Dive', '假摔'],
                    ])}
                    {factSelect('动作强度', 'intensity', [
                      ['careless', '草率'],
                      ['reckless', '鲁莽'],
                      ['excessive_force', '使用过分力量'],
                    ])}
                    <details className="review-details">
                      <summary>补充判罚事实</summary>
                      {factSelect('犯规方', 'offender_team', [
                        ['home', '主队'],
                        ['away', '客队'],
                      ])}
                      {factSelect('受害方', 'victim_team', [
                        ['home', '主队'],
                        ['away', '客队'],
                      ])}
                      {factSelect('球在比赛中', 'ball_in_play', [
                        ['true', '是'],
                        ['false', '否'],
                      ])}
                      {factSelect('发生接触', 'contact', [
                        ['true', '是'],
                        ['false', '否'],
                      ])}
                      {factSelect('接触部位', 'contact_region', [
                        ['leg', '腿部'],
                        ['body', '身体'],
                        ['head', '头部'],
                      ])}
                      {factSelect('尝试争抢球', 'attempt_to_play_ball', [
                        ['true', '是'],
                        ['false', '否'],
                      ])}
                      {factSelect('战术影响', 'tactical_impact', [
                        ['none', '无'],
                        ['spa', '破坏有希望进攻'],
                        ['dogso', '破坏明显进球机会'],
                      ])}
                      {factSelect('主队防守方向', 'home_defends_side', [
                        ['left', '左'],
                        ['right', '右'],
                      ])}
                      <div className="field">
                        <span>犯规位置 (m)</span>
                        <div className="coordinate-inputs">
                          {['x_m', 'y_m'].map((axis) => (
                            <input
                              key={axis}
                              aria-label={`犯规位置 ${axis}`}
                              type="number"
                              step="0.1"
                              placeholder={axis === 'x_m' ? 'X' : 'Y'}
                              value={facts.location?.[axis as 'x_m' | 'y_m'] ?? ''}
                              onChange={(e) => {
                                const n = Number(e.target.value);
                                setFacts((f) => ({
                                  ...f,
                                  location:
                                    e.target.value === ''
                                      ? null
                                      : {
                                          x_m: f.location?.x_m ?? 0,
                                          y_m: f.location?.y_m ?? 0,
                                          [axis]: n,
                                          source: 'human',
                                          confirmed: true,
                                        },
                                }));
                              }}
                            />
                          ))}
                        </div>
                      </div>
                    </details>
                  </>
                )}
                <button
                  className="button secondary full"
                  disabled={!current || action.busy}
                  onClick={() => void save()}
                >
                  保存复核
                </button>
                {review && (
                  <div className="assessment">
                    <div className="result-line">
                      <span>重启方式</span>
                      <strong>
                        {restartLabels[review.assessment.restart] || review.assessment.restart}
                      </strong>
                    </div>
                    <div className="result-line">
                      <span>处罚</span>
                      <strong>
                        {sanctionLabels[review.assessment.sanction] || review.assessment.sanction}
                      </strong>
                    </div>
                    {review.assessment.missing_facts.length > 0 && (
                      <p className="quiet">判罚信息尚未完整，请补充事实</p>
                    )}
                    <button
                      className="text-button"
                      disabled={action.busy}
                      onClick={() => void explain()}
                    >
                      生成规则解释 <Icon name="arrow" size={14} />
                    </button>
                    {explanation && (
                      <p className="explanation">
                        {explanation.summary}
                        <small>
                          来源：{explanation.source === 'local_llm' ? '本地语言模型' : '规则模板'}
                        </small>
                      </p>
                    )}
                  </div>
                )}
              </>
            )}
          </section>
        </aside>
      </div>
      <Footer>
        {mode === 'live' ? '现场输入 · 设备状态来自后端' : '视频案例 · 后台分析与人工复核独立记录'}
      </Footer>
    </>
  );
}
