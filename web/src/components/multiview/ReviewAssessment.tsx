import type { ExplanationResponse, RuleAssessment } from '../../types/multiview';
import { factLabels } from './reviewFacts';
import './review-panels.css';

export interface ReviewAssessmentProps {
  assessment: RuleAssessment | null;
  explanation: ExplanationResponse | null;
  revision?: number | null;
  dirty: boolean;
  explaining?: boolean;
  onExplain?: () => void;
  explainDisabled?: boolean;
}

const restartLabels: Record<string, string> = {
  play_on: '继续比赛',
  direct_free_kick: '直接任意球',
  indirect_free_kick: '间接任意球',
  penalty: '罚球点球',
  previous_restart: '维持原恢复方式',
  unknown: '待确认',
};
const sanctionLabels: Record<string, string> = {
  none: '不出牌',
  yellow_card: '黄牌',
  red_card: '红牌',
  pending: '待确认',
};
const statusLabels: Record<string, string> = {
  complete: '完整结论',
  incomplete: '部分结论',
  unsupported: '超出规则范围',
};
const labelFact = (name: string) => factLabels[name as keyof typeof factLabels] ?? name;

export default function ReviewAssessment({
  assessment,
  explanation,
  revision,
  dirty,
  explaining = false,
  onExplain,
  explainDisabled = false,
}: ReviewAssessmentProps) {
  if (dirty)
    return (
      <div className="rv-assessment-empty rv-assessment-stale" role="status">
        <strong>人工事实已修改</strong>
        <p>保存后重新计算规则结论，当前草稿尚未评估。</p>
      </div>
    );
  if (!assessment)
    return (
      <div className="rv-assessment-empty">
        <strong>等待事实确认</strong>
        <p>保存事实后，显示规则结论和条款依据。</p>
      </div>
    );
  // An explanation belongs to one persisted revision, never to an unsaved draft.
  const currentExplanation =
    explanation && (revision == null || explanation.revision === revision) ? explanation : null;
  const geometry = assessment.geometry;
  return (
    <div className="rv-assessment">
      <div className={`rv-rule-hero ${assessment.sanction}`}>
        <div className="rv-rule-meta">
          <span className={`rv-rule-status ${assessment.status}`}>
            {statusLabels[assessment.status] ?? assessment.status}
          </span>
          <span>{revision != null ? `REV ${revision}` : ''}</span>
        </div>
        <div className="rv-rule-outcomes">
          <div>
            <span>比赛重启</span>
            <strong>{restartLabels[assessment.restart] ?? assessment.restart}</strong>
          </div>
          <div>
            <span>纪律处罚</span>
            <strong>{sanctionLabels[assessment.sanction] ?? assessment.sanction}</strong>
          </div>
        </div>
      </div>
      {assessment.missing_facts.length > 0 && (
        <div className="rv-rule-warning">
          <strong>仍需确认</strong>
          <ul>
            {assessment.missing_facts.map((name) => (
              <li key={name}>{labelFact(name)}</li>
            ))}
          </ul>
        </div>
      )}
      {assessment.conflicts.length > 0 && (
        <div className="rv-rule-conflicts">
          <strong>事实与模型建议有差异</strong>
          <ul>
            {assessment.conflicts.map((conflict, index) => (
              <li key={`${index}:${conflict}`}>{conflict}</li>
            ))}
          </ul>
          <p>规则结论依据已确认的事实，请结合证据复核。</p>
        </div>
      )}
      {geometry && (
        <section className="rv-geometry">
          <h3>位置与几何判断</h3>
          <dl>
            <div>
              <dt>区域</dt>
              <dd>{geometry.zone}</dd>
            </div>
            <div>
              <dt>禁区</dt>
              <dd>{geometry.in_penalty_area ? '禁区内' : '禁区外'}</dd>
            </div>
            <div>
              <dt>犯规方本方禁区</dt>
              <dd>
                {geometry.in_offender_own_penalty_area == null
                  ? '条件待确认'
                  : geometry.in_offender_own_penalty_area
                    ? '是'
                    : '否'}
              </dd>
            </div>
            <div>
              <dt>距左侧 / 右侧球门</dt>
              <dd>
                {geometry.distance_to_left_goal_m.toFixed(1)} /{' '}
                {geometry.distance_to_right_goal_m.toFixed(1)} m
              </dd>
            </div>
          </dl>
        </section>
      )}
      <section className="rv-explanation-section">
        <div className="rv-panel-heading">
          <h3>判罚说明</h3>
          {explaining && (
            <span className="rv-explaining" role="status">
              正在整理 AI 说明…
            </span>
          )}
        </div>
        <p className="rv-rule-summary">
          {currentExplanation?.summary ?? assessment.explanation_template}
        </p>
        <div className="rv-explanation-meta">
          <span>
            {currentExplanation?.source === 'local_llm' ? 'AI 生成' : '确定性规则模板'}
            {' · '}
            {currentExplanation ? `REV ${currentExplanation.revision}` : assessment.ruleset_version}
          </span>
          {onExplain && (
            <button
              type="button"
              className="rv-explain-button"
              disabled={explaining || explainDisabled}
              onClick={onExplain}
            >
              {currentExplanation ? '重新生成说明' : '生成 AI 说明'}
            </button>
          )}
        </div>
        {currentExplanation?.fallback_reason && (
          <p className="rv-explanation-fallback">
            模板回退原因：{currentExplanation.fallback_reason}
          </p>
        )}
      </section>
      <section className="rv-rule-trace">
        <div className="rv-panel-heading">
          <h3>规则依据</h3>
          <span>{assessment.ruleset_version}</span>
        </div>
        {assessment.rule_trace.map((rule, index) => (
          <details key={`${index}:${rule.rule_id}`}>
            <summary>
              <strong>{rule.law}</strong>
              <span>{rule.rule_id}</span>
            </summary>
            <p>{rule.result}</p>
            {rule.law_excerpt && <blockquote>{rule.law_excerpt}</blockquote>}
            <p className="rv-trace-facts">
              使用事实：{rule.facts_used.map(labelFact).join('、') || '无额外事实'}
            </p>
            <small>{rule.section}</small>
          </details>
        ))}
        {assessment.rule_trace.length === 0 && (
          <p className="rv-field-note">暂无适用条款，先补全判罚事实。</p>
        )}
      </section>
    </div>
  );
}
