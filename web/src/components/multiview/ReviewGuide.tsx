import { useEffect, useId, useRef, useState } from 'react';
import type {
  EvidenceValue,
  FoulFacts,
  FoulLocation,
  MultiviewDecision,
  ReviewPreview,
  RuleTraceEntry,
} from '../../types/multiview';
import FoulLocationPitch from './FoulLocationPitch';
import { factLabels, modelRecommendations, setHumanFact } from './reviewFacts';
import type { FactName } from './reviewFacts';
import {
  attemptRelevant,
  contextRelevant,
  confirmGuidePage,
  guideStepForFact,
  nextGuideStep,
  pageAdoptableFields,
  pageReviewPresets,
} from './reviewGuideFlow';
import type { ReviewGuideStep, ReviewPreset } from './reviewGuideFlow';
import './review-guide.css';

export interface ReviewGuideProps {
  facts: FoulFacts;
  step: ReviewGuideStep;
  onStepChange: (step: ReviewGuideStep) => void;
  preview: ReviewPreview | null;
  previewLoading: boolean;
  previewError: string | null;
  decision: MultiviewDecision | null;
  newModelFields?: readonly FactName[];
  prefillToken?: string | number | null;
  protectedFields?: readonly FactName[];
  busy: boolean;
  disabled: boolean;
  onChange: (facts: FoulFacts, field?: FactName) => void;
  onAdopt: (fields: readonly FactName[]) => void;
  onLocationChange: (location: FoulLocation | null) => void;
  onRetryPreview?: () => void;
  onReset: () => void;
}

interface Choice {
  value: string;
  label: string;
  detail?: string;
}

const actions: Choice[] = [
  { value: 'Tackle', label: '铲球' },
  { value: 'Standing Tackle', label: '站立抢断' },
  { value: 'High Leg', label: '抬脚过高' },
  { value: 'Holding', label: '拉扯' },
  { value: 'Pushing', label: '推搡' },
  { value: 'Elbowing', label: '肘击' },
  { value: 'Challenge', label: '身体争抢' },
  { value: 'Dive', label: '假摔' },
];
const yesNo: Choice[] = [
  { value: 'true', label: '是' },
  { value: 'false', label: '否' },
];
const teams: Choice[] = [
  { value: 'home', label: '主队' },
  { value: 'away', label: '客队' },
];
const restartLabels: Record<string, string> = {
  play_on: '继续比赛',
  direct_free_kick: '直接任意球',
  indirect_free_kick: '间接任意球',
  penalty: '罚球点球',
  previous_restart: '维持原恢复方式',
  unknown: '信息不足',
};
const sanctionLabels: Record<string, string> = {
  none: '不出牌',
  yellow_card: '黄牌',
  red_card: '红牌',
  pending: '信息不足',
};
const headings: Record<ReviewGuideStep, { title: string; description: string; eyebrow: string }> = {
  action: { title: '这个动作是否构成犯规？', description: '', eyebrow: '核心判断 · 1 / 2' },
  severity: { title: '动作有多严重？', description: '', eyebrow: '核心判断 · 2 / 2' },
  location: {
    title: '犯规发生在哪里？',
    description: '在球场上标出位置，或跳过。',
    eyebrow: '补充 · 位置',
  },
  context: {
    title: '是否影响进攻机会？',
    description: '不确定的内容可以留空。',
    eyebrow: '补充 · 情境',
  },
  result: { title: '判罚建议', description: '', eyebrow: '演示结果' },
};
const labelFact = (name: string) => factLabels[name as keyof FoulFacts] ?? name;

function Choices({
  name,
  title,
  fact,
  recommendation,
  choices,
  disabled,
  onChange,
  animate,
  compact = false,
  suggestionNote = '历史推导 · 点击选项确认',
}: {
  name: FactName;
  title: string;
  fact: EvidenceValue;
  recommendation: EvidenceValue;
  choices: readonly Choice[];
  disabled: boolean;
  onChange: (raw: string) => void;
  animate: boolean;
  compact?: boolean;
  suggestionNote?: string;
}) {
  const id = useId();
  const raw = fact.value === null ? '' : String(fact.value);
  const confirmed = fact.confirmed && fact.value !== null;
  const suggested = fact.value !== null && !fact.confirmed;
  const unknown: Choice = { value: '', label: '不确定' };
  const supported = choices.some((choice) => choice.value === raw);
  return (
    <fieldset
      className={`rg-question${compact ? ' compact' : ''}${animate ? ' rg-new-suggestion' : ''}`}
      disabled={disabled}
    >
      <legend>{title}</legend>
      <div className={`rg-choices${compact ? ' rg-choices-compact' : ''}`}>
        {[...choices, unknown].map((choice) => {
          const selected = raw === choice.value;
          const isRecommendation =
            choice.value !== '' &&
            recommendation.source === 'model' &&
            recommendation.value !== null &&
            String(recommendation.value) === choice.value;
          const isSuggestion = suggested && raw === choice.value;
          return (
            <label
              key={choice.value}
              className={`rg-choice${selected ? ' selected' : ''}${isSuggestion ? ' suggested' : ''}${isRecommendation ? ' ai-recommended' : ''}`}
            >
              <input
                type="radio"
                name={`${id}-${name}`}
                value={choice.value}
                checked={selected}
                onClick={() => {
                  // Clicking an already preselected suggestion is still an explicit human choice.
                  if (selected && !confirmed) onChange(choice.value);
                }}
                onChange={() => onChange(choice.value)}
              />
              <span className="rg-choice-content">
                <span className="rg-choice-title">{choice.label}</span>
                {choice.detail && <span className="rg-choice-detail">{choice.detail}</span>}
              </span>
              {isRecommendation ? (
                <span className="rg-choice-source rg-choice-ai" aria-label="AI 模型建议">
                  <span aria-hidden="true">✦</span> AI
                </span>
              ) : isSuggestion ? (
                <span className="rg-choice-source">建议</span>
              ) : null}
            </label>
          );
        })}
      </div>
      {suggested && fact.source !== 'model' && supported && (
        <p className="rg-source-note">{suggestionNote}</p>
      )}
      {suggested && fact.source === 'model' && supported && (
        <p className="rg-source-note">AI 建议 · 确认并继续时采纳</p>
      )}
      {suggested && !supported && <p className="rg-source-note">建议值“{raw}”需人工判断</p>}
      {confirmed && (
        <p className="rg-source-note">
          {fact.source === 'model'
            ? 'AI 建议 · 已采纳'
            : fact.source === 'human'
              ? '人工选择'
              : '已选择'}
        </p>
      )}
    </fieldset>
  );
}

type SummaryName = 'offence_confirmed' | 'intensity' | 'contact' | 'ball_in_play';
const summaryChoices: Record<SummaryName, { label: string; choices: readonly Choice[] }> = {
  offence_confirmed: {
    label: '犯规判断',
    choices: [
      { value: 'true', label: '犯规成立' },
      { value: 'false', label: '不构成犯规' },
    ],
  },
  intensity: {
    label: '动作强度',
    choices: [
      { value: 'careless', label: '草率', detail: '缺乏注意或考虑' },
      { value: 'reckless', label: '鲁莽', detail: '漠视对对手的危险或后果' },
      { value: 'excessive_force', label: '过分力量', detail: '超出必要力量，危及对手安全' },
    ],
  },
  contact: {
    label: '接触情况',
    choices: [
      { value: 'true', label: '有接触' },
      { value: 'false', label: '无接触' },
    ],
  },
  ball_in_play: {
    label: '比赛状态',
    choices: [
      { value: 'true', label: '比赛进行中' },
      { value: 'false', label: '死球 / 比赛已停止' },
    ],
  },
};

function ReviewSummary({
  name,
  fact,
  recommendation,
  preset,
  disabled,
  onChange,
}: {
  name: SummaryName;
  fact: EvidenceValue;
  recommendation: EvidenceValue;
  preset?: ReviewPreset;
  disabled: boolean;
  onChange: (raw: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const contact = name === 'contact';
  const { label, choices } = summaryChoices[name];
  const displayed = preset
    ? { value: true, source: 'rule' as const, confidence: null, confirmed: false }
    : fact;
  const selected = choices.find((choice) => choice.value === String(displayed.value));
  const compact =
    Boolean(selected) && (Boolean(preset) || displayed.confirmed || displayed.source === 'model');
  const note = preset
    ? preset.explanation
    : name === 'ball_in_play' && displayed.value === false
      ? '死球时仍可能出牌；恢复比赛方式维持原决定。'
      : selected?.detail;
  return (
    <div className="rg-condition">
      {compact && (
        <div className="rg-condition-summary">
          <div>
            <span className="rg-condition-label">{label}</span>
            <strong>{selected?.label}</strong>
            <span className="rg-condition-source">
              {preset
                ? '预设'
                : displayed.source === 'model'
                  ? displayed.confirmed
                    ? 'AI · 已采纳'
                    : 'AI 建议'
                  : displayed.source === 'human'
                    ? '人工选择'
                    : '已选择'}
            </span>
          </div>
          <button
            type="button"
            disabled={disabled}
            aria-label={`${expanded ? '收起' : '更改'}${label}`}
            aria-expanded={expanded}
            aria-controls={id}
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? '收起' : '更改'}
          </button>
        </div>
      )}
      {note && <p className="rg-source-note">{note}</p>}
      <div id={id} hidden={compact && !expanded}>
        {(!compact || expanded) && (
          <>
            <Choices
              name={name}
              title={label}
              fact={displayed}
              recommendation={recommendation}
              choices={choices}
              disabled={disabled}
              onChange={onChange}
              animate={false}
              compact
              suggestionNote={preset ? '演示预设 · 确认并继续时采纳' : undefined}
            />
            {contact && <p className="rg-source-note">无接触也可能构成危险动作等犯规。</p>}
          </>
        )}
      </div>
    </div>
  );
}

function ActionChooser({
  fact,
  recommendation,
  disabled,
  animate,
  onChange,
}: {
  fact: EvidenceValue;
  recommendation: EvidenceValue;
  disabled: boolean;
  animate: boolean;
  onChange: (raw: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const summary = useRef<HTMLElement>(null);
  const label =
    actions.find((choice) => choice.value === fact.value)?.label ??
    (fact.value ? String(fact.value) : '选择动作');
  const recommendedLabel =
    recommendation.value !== null
      ? (actions.find((choice) => choice.value === recommendation.value)?.label ??
        String(recommendation.value))
      : null;
  const modelMatches = recommendation.value !== null && recommendation.value === fact.value;
  return (
    <details
      className={`rg-action-chooser${animate ? ' rg-new-suggestion' : ''}`}
      open={expanded}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary
        ref={summary}
        aria-disabled={disabled}
        className={modelMatches ? 'ai-recommended' : undefined}
      >
        <span className="rg-action-summary">
          <span className="rg-action-label">动作类型</span>
          <span className="rg-action-value">
            {label}
            {fact.value !== null && (
              <span className={`rg-action-source ${fact.source}`}>
                {fact.source === 'model'
                  ? fact.confirmed
                    ? 'AI · 已采纳'
                    : 'AI 建议'
                  : fact.confirmed
                    ? '人工选择'
                    : '建议'}
              </span>
            )}
          </span>
        </span>
        {recommendedLabel && (
          <span
            className="rg-action-model-recommendation"
            aria-label={`AI 建议：${recommendedLabel}`}
          >
            <span aria-hidden="true">✦</span> AI{!modelMatches && ` · ${recommendedLabel}`}
          </span>
        )}
        <span className="rg-action-change">
          {expanded ? '收起' : fact.value === null ? '选择' : '更改'}
        </span>
      </summary>
      <Choices
        name="action"
        title="选择动作"
        fact={fact}
        recommendation={recommendation}
        choices={actions}
        disabled={disabled}
        animate={false}
        compact
        onChange={(raw) => {
          onChange(raw);
          setExpanded(false);
          summary.current?.focus();
        }}
      />
    </details>
  );
}

function RuleDetails({ traces }: { traces: readonly RuleTraceEntry[] }) {
  return (
    <div className="rg-rule-details">
      {traces.map((rule, index) => (
        <details key={`${rule.rule_id}-${index}`}>
          <summary>
            {rule.law} · {rule.rule_id}
          </summary>
          <p>{rule.result}</p>
          {rule.law_excerpt && <blockquote>{rule.law_excerpt}</blockquote>}
          <small>{rule.facts_used.map(labelFact).join('、')}</small>
        </details>
      ))}
    </div>
  );
}

export default function ReviewGuide({
  facts,
  step,
  onStepChange,
  preview,
  previewLoading,
  previewError,
  decision,
  newModelFields = [],
  prefillToken,
  protectedFields = [],
  busy,
  disabled,
  onChange,
  onAdopt,
  onLocationChange,
  onRetryPreview,
  onReset,
}: ReviewGuideProps) {
  const [showAllScenarios, setShowAllScenarios] = useState(false);
  const [expandedDetails, setExpandedDetails] = useState(false);
  const [animated, setAnimated] = useState<readonly FactName[]>([]);
  const body = useRef<HTMLDivElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const previousStep = useRef(step);
  const changedFields = newModelFields.join('|');
  useEffect(() => {
    if (previousStep.current !== step) {
      previousStep.current = step;
      if (body.current) body.current.scrollTop = 0;
      title.current?.focus({ preventScroll: true });
    }
    setShowAllScenarios(false);
  }, [step]);
  useEffect(() => {
    if (!prefillToken || !changedFields) {
      setAnimated([]);
      return;
    }
    setAnimated(changedFields.split('|') as FactName[]);
    const timer = window.setTimeout(() => setAnimated([]), 1100);
    return () => window.clearTimeout(timer);
  }, [prefillToken, changedFields]);

  const recommendations = modelRecommendations(facts, decision);
  const noOffence = facts.offence_confirmed.value === false;
  const dive = facts.action.value?.trim().toLowerCase() === 'dive';
  const ballStopped = facts.ball_in_play.confirmed && facts.ball_in_play.value === false;
  const location = facts.location;
  const insidePenaltyArea =
    location?.confirmed &&
    location.y_m >= 13.84 &&
    location.y_m <= 54.16 &&
    (location.x_m <= 16.5 || location.x_m >= 88.5);
  const showOwnership = !noOffence && !dive && !ballStopped && insidePenaltyArea;
  const showAttempt = attemptRelevant(facts);
  const showContext = !noOffence && !dive && contextRelevant(facts);
  const adoptable = pageAdoptableFields(step, facts, expandedDetails);
  const presetOptions = { protectedFields };
  const presets = pageReviewPresets(step, facts, presetOptions);
  const coreStep = step === 'action' || step === 'severity';
  const hasPageSuggestions = adoptable.length > 0 || presets.length > 0;
  const heading =
    dive && step === 'severity'
      ? { ...headings.severity, title: '确认假摔的比赛情境' }
      : headings[step];
  const usablePreview = preview && !previewError && !previewLoading;
  const backStep: ReviewGuideStep =
    step === 'severity'
      ? 'action'
      : step === 'location'
        ? 'severity'
        : step === 'context'
          ? 'location'
          : noOffence && facts.offence_confirmed.confirmed
            ? 'action'
            : dive && facts.action.confirmed
              ? 'severity'
              : 'context';

  function field(name: FactName, question: string, choices: readonly Choice[], compact = false) {
    return (
      <Choices
        name={name}
        title={question}
        fact={facts[name]}
        recommendation={recommendations[name]}
        choices={choices}
        disabled={disabled || busy}
        onChange={(raw) => onChange(setHumanFact(facts, name, raw), name)}
        animate={
          animated.includes(name) && facts[name].source === 'model' && !facts[name].confirmed
        }
        compact={compact}
      />
    );
  }
  function goToFact(name: string) {
    if (name === 'victim_team' || name === 'contact_region') setExpandedDetails(true);
    onStepChange(guideStepForFact(name));
  }
  function next() {
    onStepChange(nextGuideStep(step, facts));
  }
  function confirmAndNext() {
    const confirmed = confirmGuidePage(step, facts, presetOptions, expandedDetails);
    if (confirmed !== facts) onChange(confirmed);
    onStepChange(nextGuideStep(step, confirmed));
  }
  function summary(name: SummaryName) {
    return (
      <ReviewSummary
        key={`${name}:${facts.action.value ?? ''}`}
        name={name}
        fact={facts[name]}
        recommendation={recommendations[name]}
        preset={presets.find((preset) => preset.name === name)}
        disabled={disabled || busy}
        onChange={(raw) => onChange(setHumanFact(facts, name, raw), name)}
      />
    );
  }

  return (
    <section className="rg-guide" aria-label="引导式判罚演示" aria-busy={busy}>
      <header className="rg-header">
        <div className="rg-header-line">
          <strong>判罚演示</strong>
          <button
            type="button"
            className="rg-text-button"
            disabled={disabled || busy}
            onClick={onReset}
          >
            重新开始
          </button>
        </div>
        <nav className="rg-progress" aria-label="演示步骤">
          <button
            type="button"
            className={step === 'action' || step === 'severity' ? 'current' : ''}
            aria-current={step === 'action' || step === 'severity' ? 'step' : undefined}
            onClick={() => onStepChange('action')}
          >
            <span>1</span>核心判断
          </button>
          <button
            type="button"
            className={step === 'location' || step === 'context' ? 'current' : ''}
            aria-current={step === 'location' || step === 'context' ? 'step' : undefined}
            onClick={() => onStepChange('location')}
          >
            <span>2</span>补充信息
          </button>
          <button
            type="button"
            className={step === 'result' ? 'current' : ''}
            aria-current={step === 'result' ? 'step' : undefined}
            onClick={() => onStepChange('result')}
          >
            <span>3</span>判罚建议
          </button>
        </nav>
      </header>

      <div className="rg-body" ref={body}>
        <div className="rg-step-heading">
          <span className="rg-eyebrow">{heading.eyebrow}</span>
          <h2 tabIndex={-1} ref={title}>
            {heading.title}
          </h2>
          {heading.description && <p>{heading.description}</p>}
        </div>
        {adoptable.length > 0 && step !== 'result' && (
          <div
            className={`rg-ai-adopt${adoptable.some((name) => animated.includes(name)) ? ' rg-new-suggestion' : ''}`}
          >
            <span>
              <i aria-hidden="true">✦</i> AI 建议
            </span>
            {coreStep ? (
              <span>已预选 · 确认并继续时采纳</span>
            ) : (
              <button type="button" disabled={disabled || busy} onClick={() => onAdopt(adoptable)}>
                采纳本页建议
              </button>
            )}
          </div>
        )}

        {step === 'action' && (
          <>
            {summary('offence_confirmed')}
            {!noOffence && (
              <>
                <ActionChooser
                  fact={facts.action}
                  recommendation={recommendations.action}
                  disabled={disabled || busy}
                  animate={
                    animated.includes('action') &&
                    facts.action.source === 'model' &&
                    !facts.action.confirmed
                  }
                  onChange={(raw) => onChange(setHumanFact(facts, 'action', raw), 'action')}
                />
                {!dive && summary('contact')}
              </>
            )}
          </>
        )}
        {step === 'severity' && (
          <>
            {!dive && !noOffence && summary('intensity')}
            {!noOffence && summary('ball_in_play')}
            {noOffence && (
              <p className="rg-note">
                {facts.offence_confirmed.confirmed
                  ? '已选择不构成犯规，可以直接查看建议。'
                  : '不犯规建议尚未采纳，查看结果时仍会保留为未知。'}
              </p>
            )}
          </>
        )}
        {step === 'location' && (
          <>
            {noOffence || dive || ballStopped ? (
              <p className="rg-note">当前判断无需补充犯规地点。</p>
            ) : (
              <>
                <FoulLocationPitch
                  location={facts.location}
                  geometry={previewLoading ? null : (preview?.assessment.geometry ?? null)}
                  offenderTeam={facts.offender_team}
                  homeDefendsSide={facts.home_defends_side}
                  disabled={disabled || busy}
                  compact
                  onChange={onLocationChange}
                />
                {showOwnership && (
                  <div className="rg-dependent-fields">
                    <p className="rg-note">已标注禁区内，需要确认禁区归属。</p>
                    {field('offender_team', '犯规方', teams, true)}
                    {field(
                      'home_defends_side',
                      '主队防守哪侧球门？',
                      [
                        { value: 'left', label: '左侧球门' },
                        { value: 'right', label: '右侧球门' },
                      ],
                      true,
                    )}
                  </div>
                )}
              </>
            )}
          </>
        )}
        {step === 'context' && (
          <>
            {!showContext ? (
              <p className="rg-note">当前判断无需补充战术影响。</p>
            ) : (
              <>
                {field('tactical_impact', '对进攻机会的影响', [
                  { value: 'none', label: '普通犯规' },
                  { value: 'spa', label: '阻止有威胁进攻', detail: 'SPA' },
                  { value: 'dogso', label: '破坏明显得分机会', detail: 'DOGSO' },
                ])}
                {showAttempt && field('attempt_to_play_ball', '是否尝试争抢球？', yesNo, true)}
              </>
            )}
            {!noOffence && !dive && (
              <details
                className="rg-extra-details"
                open={expandedDetails}
                onToggle={(event) => setExpandedDetails(event.currentTarget.open)}
              >
                <summary>更多细节</summary>
                {field(
                  'contact_region',
                  '接触部位',
                  [
                    { value: 'lower_body', label: '下肢' },
                    { value: 'upper_body', label: '上身' },
                    { value: 'head', label: '头部' },
                  ],
                  true,
                )}
                {field('victim_team', '受害方', teams, true)}
              </details>
            )}
          </>
        )}
        {step === 'result' && (
          <div className="rg-result">
            {previewLoading && (
              <p className="rg-preview-status" role="status">
                正在更新建议…
              </p>
            )}
            {previewError && (
              <div className="rg-preview-error" role="alert">
                <p>暂时无法更新判罚建议</p>
                <small>{previewError}</small>
                {onRetryPreview && (
                  <button type="button" onClick={onRetryPreview} disabled={busy}>
                    重试
                  </button>
                )}
              </div>
            )}
            {!preview && !previewLoading && !previewError && (
              <p className="rg-note">等待评估当前事实。</p>
            )}
            {usablePreview && (
              <>
                {(preview.restart_resolution.status === 'resolved' ||
                  preview.sanction_resolution.status === 'resolved') && (
                  <section className="rg-known-outcomes" aria-label="已能确定的结论">
                    <h3>已能确定</h3>
                    <div className="rg-outcome-grid">
                      {preview.restart_resolution.status === 'resolved' &&
                        preview.restart_resolution.value && (
                          <div>
                            <span>恢复方式</span>
                            <strong>{restartLabels[preview.restart_resolution.value]}</strong>
                          </div>
                        )}
                      {preview.sanction_resolution.status === 'resolved' &&
                        preview.sanction_resolution.value && (
                          <div className={preview.sanction_resolution.value}>
                            <span>纪律处罚</span>
                            <strong>{sanctionLabels[preview.sanction_resolution.value]}</strong>
                          </div>
                        )}
                    </div>
                  </section>
                )}
                {preview.assessment.status === 'unsupported' && (
                  <div className="rg-manual-review">
                    <strong>需要人工判罚</strong>
                    <p>当前事实超出已实现的规则范围，请结合比赛规则复核。</p>
                  </div>
                )}
                {preview.scenarios.length > 0 && (
                  <section className="rg-scenarios" aria-label="条件判罚建议">
                    <h3>不同情况的建议</h3>
                    {(showAllScenarios ? preview.scenarios : preview.scenarios.slice(0, 3)).map(
                      (scenario, index) => (
                        <article className="rg-scenario" key={index}>
                          <p className="rg-condition">{scenario.conditions.join('；或')}</p>
                          <div className="rg-scenario-outcomes">
                            <strong>{restartLabels[scenario.restart]}</strong>
                            <span className={`rg-sanction ${scenario.sanction}`}>
                              {sanctionLabels[scenario.sanction]}
                            </span>
                          </div>
                          {scenario.remaining_unknowns.length > 0 && (
                            <p className="rg-remaining">
                              仍取决于：{scenario.remaining_unknowns.map(labelFact).join('、')}
                            </p>
                          )}
                          {scenario.rule_trace.length > 0 && (
                            <details className="rg-scenario-evidence">
                              <summary>依据</summary>
                              <RuleDetails traces={scenario.rule_trace} />
                            </details>
                          )}
                        </article>
                      ),
                    )}
                    {preview.scenarios.length > 3 && (
                      <button
                        className="rg-text-button"
                        type="button"
                        onClick={() => setShowAllScenarios((value) => !value)}
                      >
                        {showAllScenarios
                          ? '收起其他情况'
                          : `展开其他情况（${preview.scenarios.length - 3}）`}
                      </button>
                    )}
                  </section>
                )}
                {preview.next_fact && (
                  <section className="rg-next-question">
                    <h3>{preview.scenarios.length ? '缩小判罚范围' : '先确认这一项'}</h3>
                    <button
                      type="button"
                      disabled={disabled || busy}
                      onClick={() => goToFact(preview.next_fact!)}
                    >
                      补充{labelFact(preview.next_fact)}
                      <span aria-hidden="true"> →</span>
                    </button>
                  </section>
                )}
                {preview.required_facts.length > 1 && preview.scenarios.length > 0 && (
                  <div className="rg-missing-links">
                    {preview.required_facts
                      .filter((name) => name !== preview.next_fact)
                      .map((name) => (
                        <button
                          type="button"
                          key={name}
                          disabled={disabled || busy}
                          onClick={() => goToFact(name)}
                        >
                          补充{labelFact(name)}
                        </button>
                      ))}
                  </div>
                )}
                {preview.assessment.conflicts.length > 0 && (
                  <details className="rg-conflicts" open>
                    <summary>需要复核的差异</summary>
                    {preview.assessment.conflicts.map((conflict, index) => (
                      <p key={index}>{conflict}</p>
                    ))}
                  </details>
                )}
                {!preview.can_finalize && (
                  <p className="rg-note">补充关键信息后，条件建议会收敛为确定判罚。</p>
                )}
                <details className="rg-evidence-details">
                  <summary>查看依据与分析详情</summary>
                  {preview.can_finalize && (
                    <section className="rg-detail-section">
                      <h3>判罚说明</h3>
                      <p>{preview.assessment.explanation_template}</p>
                    </section>
                  )}
                  {decision && (
                    <section className="rg-detail-section">
                      <h3>{decision.mode === 'model' ? '模型分析' : '演示回放分析'}</h3>
                      <dl className="rg-model-meta">
                        <div>
                          <dt>建议</dt>
                          <dd>{decision.decision_zh || decision.decision}</dd>
                        </div>
                        <div>
                          <dt>置信度</dt>
                          <dd>{Math.round(decision.confidence * 100)}%</dd>
                        </div>
                        <div>
                          <dt>动作</dt>
                          <dd>{decision.action}</dd>
                        </div>
                        <div>
                          <dt>强度</dt>
                          <dd>{decision.severity}</dd>
                        </div>
                        {decision.inference_ms != null && (
                          <div>
                            <dt>推理耗时</dt>
                            <dd>{Math.round(decision.inference_ms)} ms</dd>
                          </div>
                        )}
                        {decision.device && (
                          <div>
                            <dt>计算设备</dt>
                            <dd>{decision.device}</dd>
                          </div>
                        )}
                      </dl>
                      {decision.view_attention.length > 0 && (
                        <div className="rg-view-weights">
                          {decision.view_attention.map((weight, index) => (
                            <div key={index}>
                              <span>机位 {index + 1}</span>
                              <meter
                                min={0}
                                max={1}
                                value={weight}
                                aria-label={`机位 ${index + 1} 权重`}
                              />
                              <span>{Math.round(weight * 100)}%</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </section>
                  )}
                  <section className="rg-detail-section">
                    <h3>规则依据</h3>
                    <p className="rg-detail-version">{preview.assessment.ruleset_version}</p>
                    <RuleDetails traces={preview.assessment.rule_trace} />
                    {!preview.assessment.rule_trace.length && (
                      <p className="rg-note">补充关键事实后显示适用条款。</p>
                    )}
                  </section>
                </details>
              </>
            )}
          </div>
        )}
      </div>

      <footer className="rg-footer">
        {step !== 'action' && (
          <button
            type="button"
            className="rg-back"
            disabled={busy}
            onClick={() => onStepChange(backStep)}
          >
            上一步
          </button>
        )}
        <div className="rg-footer-primary">
          {coreStep && (
            <>
              {hasPageSuggestions && (
                <p className="rg-confirm-note">确认将采纳本页预选项与预设条件。</p>
              )}
              <button
                type="button"
                className="rg-primary"
                disabled={disabled || busy}
                onClick={hasPageSuggestions ? confirmAndNext : next}
              >
                {hasPageSuggestions
                  ? '确认并继续'
                  : nextGuideStep(step, facts) === 'result'
                    ? '查看建议'
                    : '下一步'}
              </button>
              {hasPageSuggestions && (
                <button type="button" className="rg-secondary" disabled={busy} onClick={next}>
                  跳过预选，下一步
                </button>
              )}
            </>
          )}
          {step === 'location' && (
            <>
              <button type="button" className="rg-primary" disabled={busy} onClick={next}>
                下一步
              </button>
              {!noOffence && !dive && (
                <button type="button" className="rg-secondary" disabled={busy} onClick={next}>
                  跳过位置
                </button>
              )}
            </>
          )}
          {step === 'context' && (
            <>
              <button
                type="button"
                className="rg-primary"
                disabled={busy}
                onClick={() => onStepChange('result')}
              >
                查看建议
              </button>
              {!noOffence && !dive && (
                <button
                  type="button"
                  className="rg-secondary"
                  disabled={busy}
                  onClick={() => onStepChange('result')}
                >
                  跳过情境
                </button>
              )}
            </>
          )}
          {step === 'result' && (
            <button
              type="button"
              className="rg-primary"
              disabled={disabled || busy}
              onClick={onReset}
            >
              重新判罚
            </button>
          )}
        </div>
      </footer>
    </section>
  );
}
