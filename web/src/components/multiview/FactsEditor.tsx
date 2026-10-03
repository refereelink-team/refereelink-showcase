import { useEffect, useState } from 'react';
import type { EvidenceValue, FoulFacts } from '../../types/multiview';
import { factLabels, setHumanFact } from './reviewFacts';
import type { FactName } from './reviewFacts';
import './review-panels.css';

export interface FactsEditorProps {
  facts: FoulFacts;
  onChange: (facts: FoulFacts, changedField?: FactName) => void;
  newModelFields?: readonly FactName[];
  prefillToken?: string | number | null;
  disabled?: boolean;
}

type Option = readonly [string, string];
const yesNo: Option[] = [
  ['true', '是'],
  ['false', '否'],
];
const fields: { name: FactName; options: Option[] }[] = [
  {
    name: 'offence_confirmed',
    options: [
      ['true', '确认犯规'],
      ['false', '不犯规'],
    ],
  },
  {
    name: 'action',
    options: [
      ['Tackle', '铲球'],
      ['Standing Tackle', '站立抢断'],
      ['High Leg', '抬脚过高'],
      ['Holding', '拉扯'],
      ['Pushing', '推搡'],
      ['Elbowing', '肘击'],
      ['Challenge', '身体争抢'],
      ['Dive', '假摔'],
    ],
  },
  {
    name: 'intensity',
    options: [
      ['careless', '草率'],
      ['reckless', '鲁莽'],
      ['excessive_force', '使用过分力量'],
    ],
  },
  {
    name: 'offender_team',
    options: [
      ['home', '主队'],
      ['away', '客队'],
    ],
  },
  {
    name: 'home_defends_side',
    options: [
      ['left', '左侧球门'],
      ['right', '右侧球门'],
    ],
  },
  {
    name: 'victim_team',
    options: [
      ['home', '主队'],
      ['away', '客队'],
    ],
  },
  { name: 'ball_in_play', options: yesNo },
  { name: 'contact', options: yesNo },
  {
    name: 'contact_region',
    options: [
      ['leg', '腿部'],
      ['body', '身体'],
      ['head', '头部'],
    ],
  },
  { name: 'attempt_to_play_ball', options: yesNo },
  {
    name: 'tactical_impact',
    options: [
      ['none', '无'],
      ['spa', '破坏有希望进攻 (SPA)'],
      ['dogso', '破坏明显进球机会 (DOGSO)'],
    ],
  },
];

function sourceText(fact: EvidenceValue): string {
  if (fact.source === 'model') return fact.confirmed ? 'AI 建议 · 人工已确认' : 'AI 建议 · 待确认';
  if (fact.source === 'geometry') return fact.confirmed ? '几何推导 · 已确认' : '几何推导 · 待确认';
  if (fact.source === 'rule')
    return fact.confirmed ? '规则推导 · 已确认' : '规则 / 脚本建议 · 待确认';
  return fact.value === null ? '待人工填写' : '人工填写';
}

export default function FactsEditor({
  facts,
  onChange,
  newModelFields = [],
  prefillToken,
  disabled = false,
}: FactsEditorProps) {
  const [animated, setAnimated] = useState<readonly FactName[]>([]);
  const fieldKey = newModelFields.join('|');
  useEffect(() => {
    if (!prefillToken || !fieldKey) {
      setAnimated([]);
      return;
    }
    setAnimated(fieldKey.split('|') as FactName[]);
    const timer = window.setTimeout(() => setAnimated([]), 1100);
    return () => window.clearTimeout(timer);
  }, [prefillToken, fieldKey]);

  function edit(name: FactName, raw: string) {
    onChange(setHumanFact(facts, name, raw), name);
  }

  function confirm(name: FactName) {
    if (facts[name].value === null) return;
    // Confirmation records human acceptance without erasing model provenance.
    onChange({ ...facts, [name]: { ...facts[name], confirmed: true } }, name);
  }

  function renderField(field: (typeof fields)[number]) {
    const fact = facts[field.name];
    const raw = fact.value === null ? '' : String(fact.value);
    const isSuggestion = fact.value !== null && fact.source !== 'human' && !fact.confirmed;
    const isNew = fact.source === 'model' && animated.includes(field.name);
    const extraOption = raw && !field.options.some(([value]) => value === raw);
    return (
      <div
        className={`rv-fact rv-fact-${fact.source}${isNew ? ' rv-fact-new' : ''}`}
        key={field.name}
      >
        <label className="rv-fact-label">
          <span>{factLabels[field.name]}</span>
          <select
            aria-label={factLabels[field.name]}
            value={raw}
            disabled={disabled}
            onChange={(event) => edit(field.name, event.target.value)}
          >
            <option value="">未确认</option>
            {extraOption && <option value={raw}>{raw}</option>}
            {field.options.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <div className="rv-fact-meta">
          <span className={`rv-source-chip ${fact.source}`}>
            {sourceText(fact)}
            {fact.source === 'model' &&
            typeof fact.confidence === 'number' &&
            Number.isFinite(fact.confidence)
              ? ` · ${Math.round(Math.min(1, Math.max(0, fact.confidence)) * 100)}%`
              : ''}
          </span>
          {isSuggestion && (
            <button
              type="button"
              className="rv-confirm"
              aria-label={`确认${factLabels[field.name]}建议`}
              disabled={disabled}
              onClick={() => confirm(field.name)}
            >
              确认建议
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="rv-facts-editor">
      <div className="rv-facts-principal">{fields.slice(0, 3).map(renderField)}</div>
      <div className="rv-facts-context">{fields.slice(3, 5).map(renderField)}</div>
      <details className="rv-supplement">
        <summary>
          补充判罚事实<span>受害方、接触与比赛情境</span>
        </summary>
        <div>{fields.slice(5).map(renderField)}</div>
      </details>
    </div>
  );
}
