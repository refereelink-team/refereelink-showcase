import type { FoulFacts, ReviewPreview } from '../../types/multiview';
import type { FactName } from './reviewFacts';

export type ReviewGuideStep = 'action' | 'severity' | 'location' | 'context' | 'result';

// Historical derived facts remain suggestions; the saved record is never mutated.
export function normalizeLegacyDerivedFacts(facts: FoulFacts): FoulFacts {
  let next = facts;
  for (const name of ['contact', 'victim_team'] as const) {
    const item = facts[name];
    if (item.source !== 'rule' || !item.confirmed || item.value === null) continue;
    if (next === facts) next = { ...facts };
    Object.assign(next, { [name]: { ...item, confirmed: false, confidence: null } });
  }
  const region = facts.contact_region.value;
  if (region === 'leg' || region === 'body') {
    if (next === facts) next = { ...facts };
    next.contact_region = {
      ...facts.contact_region,
      value: region === 'leg' ? 'lower_body' : 'upper_body',
    };
  }
  return next;
}

export function nextGuideStep(step: ReviewGuideStep, facts: FoulFacts): ReviewGuideStep {
  if (step === 'action')
    return facts.offence_confirmed.confirmed && facts.offence_confirmed.value === false
      ? 'result'
      : 'severity';
  if (step === 'severity')
    return facts.offence_confirmed.confirmed && facts.offence_confirmed.value === false
      ? 'result'
      : facts.action.confirmed && facts.action.value?.trim().toLowerCase() === 'dive'
        ? 'result'
        : 'location';
  if (step === 'location') return 'context';
  return 'result';
}

export function guideStepForFact(field: string): ReviewGuideStep {
  if (['offence_confirmed', 'action', 'contact'].includes(field)) return 'action';
  if (['intensity', 'ball_in_play'].includes(field)) return 'severity';
  if (['location', 'offender_team', 'home_defends_side'].includes(field)) return 'location';
  return 'context';
}

export function penaltyPossible(facts: FoulFacts): boolean {
  if (facts.ball_in_play.confirmed && facts.ball_in_play.value === false) return false;
  const location = facts.location;
  if (!location?.confirmed) return true;
  const inside =
    location.y_m >= 13.84 &&
    location.y_m <= 54.16 &&
    (location.x_m <= 16.5 || location.x_m >= 88.5);
  if (!inside) return false;
  const team = facts.offender_team,
    side = facts.home_defends_side;
  if (
    !team.confirmed ||
    !side.confirmed ||
    !['home', 'away'].includes(team.value ?? '') ||
    !['left', 'right'].includes(side.value ?? '')
  )
    return true;
  const ownSide = team.value === 'home' ? side.value : side.value === 'left' ? 'right' : 'left';
  return (location.x_m <= 16.5 ? 'left' : 'right') === ownSide;
}

export function contextRelevant(facts: FoulFacts): boolean {
  return (
    !(facts.offence_confirmed.confirmed && facts.offence_confirmed.value === false) &&
    !(facts.action.confirmed && facts.action.value?.trim().toLowerCase() === 'dive') &&
    !(facts.contact.confirmed && facts.contact.value === false) &&
    !(facts.ball_in_play.confirmed && facts.ball_in_play.value === false) &&
    !(facts.intensity.confirmed && facts.intensity.value === 'excessive_force')
  );
}

export function attemptRelevant(facts: FoulFacts): boolean {
  return (
    contextRelevant(facts) &&
    facts.tactical_impact.confirmed &&
    ['spa', 'dogso'].includes(facts.tactical_impact.value ?? '') &&
    penaltyPossible(facts) &&
    !(
      facts.tactical_impact.value === 'spa' &&
      facts.intensity.confirmed &&
      facts.intensity.value === 'reckless'
    )
  );
}

export function pageAdoptableFields(
  step: ReviewGuideStep,
  facts: FoulFacts,
  expandedDetails = false,
): FactName[] {
  let names: FactName[] = [];
  const noOffence = facts.offence_confirmed.value === false;
  const dive = facts.action.value?.trim().toLowerCase() === 'dive';
  if ((step === 'location' || step === 'context') && (noOffence || dive)) return [];
  if (step === 'action') {
    names = ['offence_confirmed'];
    if (!noOffence) names.push('action');
    if (!noOffence && !dive) names.push('contact');
  } else if (step === 'severity') {
    names = noOffence ? [] : dive ? ['ball_in_play'] : ['intensity', 'ball_in_play'];
  } else if (step === 'location') {
    const location = facts.location;
    const inside =
      location?.confirmed &&
      location.y_m >= 13.84 &&
      location.y_m <= 54.16 &&
      (location.x_m <= 16.5 || location.x_m >= 88.5);
    if (inside && !(facts.ball_in_play.confirmed && facts.ball_in_play.value === false))
      names = ['offender_team', 'home_defends_side'];
  } else if (step === 'context') {
    names = contextRelevant(facts) ? ['tactical_impact'] : [];
    if (expandedDetails) names.push('victim_team', 'contact_region');
    if (attemptRelevant(facts)) names.push('attempt_to_play_ball');
  }
  return names.filter((name) => {
    const item = facts[name];
    return item.source === 'model' && item.value !== null && !item.confirmed;
  });
}

export function adoptModelFields(facts: FoulFacts, fields: readonly FactName[]): FoulFacts {
  let next = facts;
  for (const name of fields) {
    const item = facts[name];
    if (item.source !== 'model' || item.value === null || item.confirmed) continue;
    if (next === facts) next = { ...facts };
    Object.assign(next, { [name]: { ...item, confirmed: true } });
  }
  return next;
}

export function reviewSaveState(preview: ReviewPreview | null): 'reviewed' | 'uncertain' {
  return preview?.can_finalize ? 'reviewed' : 'uncertain';
}
