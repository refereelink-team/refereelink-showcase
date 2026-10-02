import type { EvidenceValue, FoulFacts, MultiviewDecision } from '../../types/multiview';

export type FactName = Exclude<keyof FoulFacts, 'location'>;

export const factLabels: Record<keyof FoulFacts, string> = {
  offence_confirmed: '事件确认',
  action: '动作类型',
  offender_team: '犯规方',
  victim_team: '受害方',
  ball_in_play: '球在比赛中',
  contact: '发生接触',
  contact_region: '接触部位',
  intensity: '动作强度',
  attempt_to_play_ball: '尝试争抢球',
  tactical_impact: '战术影响',
  home_defends_side: '主队防守方向',
  location: '犯规位置',
};

export function emptyFacts(): FoulFacts {
  const empty = () => ({
    value: null,
    source: 'human' as const,
    confidence: null,
    confirmed: false,
  });
  return {
    offence_confirmed: empty(),
    action: empty(),
    offender_team: empty(),
    victim_team: empty(),
    ball_in_play: empty(),
    contact: empty(),
    contact_region: empty(),
    intensity: empty(),
    attempt_to_play_ball: empty(),
    tactical_impact: empty(),
    home_defends_side: empty(),
    location: null,
  };
}

function boundedConfidence(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.min(1, Math.max(0, value))
    : null;
}

export function factsFromDecision(decision: MultiviewDecision): FoulFacts {
  const facts = emptyFacts();
  const isModel = decision.mode === 'model';
  const severityConfidence = boundedConfidence(
    decision.severity_candidates[0]?.confidence ?? decision.confidence,
  );
  const actionConfidence = boundedConfidence(
    decision.action_candidates[0]?.confidence ?? decision.confidence,
  );
  const suggested = <T>(value: T, confidence: number | null) => ({
    value,
    source: isModel ? ('model' as const) : ('rule' as const),
    confidence: isModel ? confidence : null,
    confirmed: false,
  });
  const offence = decision.severity.trim().toLowerCase() !== 'no offence';
  facts.offence_confirmed = suggested(offence, severityConfidence);
  if (decision.action.trim()) facts.action = suggested(decision.action, actionConfidence);
  if (offence) {
    const intensity =
      decision.suggested_intensity ??
      (decision.card === 'red'
        ? 'excessive_force'
        : decision.card === 'yellow'
          ? 'reckless'
          : 'careless');
    facts.intensity = suggested(intensity, severityConfidence);
  }
  // Team, location, contact and match context cannot be inferred from these logits.
  return facts;
}

export function setHumanFact(current: FoulFacts, name: FactName, raw: string): FoulFacts {
  const value = raw === '' ? null : raw === 'true' ? true : raw === 'false' ? false : raw;
  const next = {
    ...current,
    [name]: { value, source: 'human' as const, confidence: null, confirmed: value !== null },
  };
  // Derived contact belongs to its original action, while a human contact override is authoritative.
  if (name === 'action' && current.contact.source === 'rule') {
    next.contact = { value: null, source: 'rule', confidence: null, confirmed: false };
  }
  return next;
}

export interface PrefillOptions {
  protectedFields?: ReadonlySet<FactName> | readonly FactName[];
  hasSavedReview?: boolean;
}

export function prefillFacts(
  current: FoulFacts,
  decision: MultiviewDecision,
  options: PrefillOptions = {},
): { facts: FoulFacts; changedFields: FactName[] } {
  // A persisted review is authoritative, including facts intentionally left blank.
  if (options.hasSavedReview) return { facts: current, changedFields: [] };
  const protectedFields = new Set(options.protectedFields ?? []);
  const candidates = factsFromDecision(decision);
  const facts = { ...current };
  const changedFields: FactName[] = [];
  const names: FactName[] = ['offence_confirmed', 'action', 'intensity'];
  for (const name of names) {
    const previous = current[name];
    const next = candidates[name];
    if (
      protectedFields.has(name) ||
      previous.confirmed ||
      (previous.source !== 'model' && previous.value !== null) ||
      next.value === null
    )
      continue;
    if (equalFact(previous, next)) continue;
    Object.assign(facts, { [name]: { ...next } });
    changedFields.push(name);
  }
  return { facts, changedFields };
}

function equalFact(a: EvidenceValue, b: EvidenceValue): boolean {
  return (
    a.value === b.value &&
    a.source === b.source &&
    a.confidence === b.confidence &&
    a.confirmed === b.confirmed
  );
}
