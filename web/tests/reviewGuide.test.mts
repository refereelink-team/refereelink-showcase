import test from 'node:test';
import assert from 'node:assert/strict';
import { emptyFacts } from '../src/components/multiview/reviewFacts.ts';
import {
  adoptModelFields,
  attemptRelevant,
  contextRelevant,
  confirmGuidePage,
  guideStepForFact,
  nextGuideStep,
  normalizeLegacyDerivedFacts,
  pageAdoptableFields,
  pageReviewPresets,
} from '../src/components/multiview/reviewGuideFlow.ts';
import { ReviewRequestScope } from '../src/features/reviewRequestScope.ts';
import type { EvidenceValue, FoulFacts } from '../src/types/multiview.ts';

function human<T>(value: T): EvidenceValue<T> {
  return { value, source: 'human', confidence: null, confirmed: true };
}
function model<T>(value: T): EvidenceValue<T> {
  return { value, source: 'model', confidence: 0.8, confirmed: false };
}
function rule<T>(value: T): EvidenceValue<T> {
  return { value, source: 'rule', confidence: 0.7, confirmed: true };
}
function physicalFacts(): FoulFacts {
  return {
    ...emptyFacts(),
    offence_confirmed: human(true),
    action: human('Tackle'),
    contact: human(true),
    intensity: human('reckless'),
    ball_in_play: human(true),
  };
}

test('unknown core answers remain traversable without confirming facts', () => {
  const facts = emptyFacts();
  const before = structuredClone(facts);
  assert.equal(nextGuideStep('action', facts), 'severity');
  assert.equal(nextGuideStep('severity', facts), 'location');
  assert.equal(nextGuideStep('location', facts), 'context');
  assert.equal(nextGuideStep('context', facts), 'result');
  assert.equal(nextGuideStep('result', facts), 'result');
  assert.deepEqual(facts, before);
});

test('ordinary physical actions expose a contact preset without manufacturing a fact', () => {
  for (const action of [
    'Tackle',
    'Standing Tackle',
    'Holding',
    'Pushing',
    'Elbowing',
    'Challenge',
  ]) {
    const facts = emptyFacts();
    facts.offence_confirmed = model(true);
    facts.action = model(action);
    const before = structuredClone(facts);
    assert.deepEqual(
      pageReviewPresets('action', facts).map((preset) => preset.name),
      ['contact'],
    );
    assert.equal(facts.contact.value, null);
    assert.equal(facts.contact.confirmed, false);
    assert.deepEqual(facts, before);
    nextGuideStep('action', facts);
    assert.deepEqual(facts, before);
  }
});

test('dangerous play, simulation, unknown actions and no-offence suggestions do not presume contact', () => {
  for (const action of ['High Leg', 'Dive', '', 'Unknown future action']) {
    const facts = emptyFacts();
    facts.offence_confirmed = model(true);
    facts.action = model(action);
    assert.deepEqual(pageReviewPresets('action', facts), []);
    assert.equal(confirmGuidePage('action', facts).contact.value, null);
  }
  const facts = emptyFacts();
  facts.action = model('Tackle');
  facts.offence_confirmed = model(false);
  assert.deepEqual(pageReviewPresets('action', facts), []);
  assert.deepEqual(pageReviewPresets('severity', facts), []);
  const accepted = confirmGuidePage('action', facts);
  assert.equal(accepted.offence_confirmed.confirmed, true);
  assert.equal(accepted.action.confirmed, false);
  assert.equal(accepted.contact.value, null);
  assert.equal(nextGuideStep('action', accepted), 'result');
});

test('one explicit confirmation accepts this page and retains model and reviewer provenance', () => {
  const facts = emptyFacts();
  facts.offence_confirmed = model(true);
  facts.action = model('Pushing');
  facts.intensity = model('reckless');
  const before = structuredClone(facts);
  const action = confirmGuidePage('action', facts);
  assert.deepEqual(action.action, { ...facts.action, confirmed: true });
  assert.deepEqual(action.offence_confirmed, { ...facts.offence_confirmed, confirmed: true });
  assert.deepEqual(action.contact, human(true));
  assert.equal(action.intensity.confirmed, false);
  assert.equal(action.ball_in_play.value, null);
  assert.equal(action.tactical_impact.value, null);
  const severity = confirmGuidePage('severity', action);
  assert.deepEqual(severity.intensity, { ...facts.intensity, confirmed: true });
  assert.deepEqual(severity.ball_in_play, human(true));
  assert.equal(severity.location, null);
  assert.equal(severity.offender_team.value, null);
  assert.equal(severity.tactical_impact.value, null);
  assert.deepEqual(facts, before);
  assert.equal(confirmGuidePage('severity', severity), severity);
});

test('manual no-contact and dead-ball answers take priority over presets and model suggestions', () => {
  const facts = emptyFacts();
  facts.offence_confirmed = model(true);
  facts.action = model('Tackle');
  facts.intensity = model('excessive_force');
  facts.contact = human(false);
  facts.ball_in_play = human(false);
  const action = confirmGuidePage('action', facts);
  const severity = confirmGuidePage('severity', action);
  assert.equal(action.contact, facts.contact);
  assert.equal(severity.ball_in_play, facts.ball_in_play);
  assert.equal(nextGuideStep('severity', severity), 'result');
  facts.ball_in_play = model(false);
  assert.equal(nextGuideStep('severity', facts), 'location');
});

test('explicit unknowns and saved unknowns remain unknown through page confirmation', () => {
  const facts = emptyFacts();
  facts.offence_confirmed = model(true);
  facts.action = model('Holding');
  const protectedFields = ['contact', 'ball_in_play'] as const;
  assert.deepEqual(pageReviewPresets('action', facts, { protectedFields }), []);
  const action = confirmGuidePage('action', facts, { protectedFields });
  const severity = confirmGuidePage('severity', action, { protectedFields });
  assert.equal(severity.contact, facts.contact);
  assert.equal(severity.ball_in_play, facts.ball_in_play);
  assert.equal(confirmGuidePage('result', facts), facts);
  assert.deepEqual(pageReviewPresets('action', facts, { hasSavedReview: true }), []);
  assert.deepEqual(pageReviewPresets('severity', facts, { hasSavedReview: true }), []);
  const saved = confirmGuidePage('severity', facts, { hasSavedReview: true });
  assert.equal(saved.contact, facts.contact);
  assert.equal(saved.ball_in_play, facts.ball_in_play);
});

test('contact presets track the currently selected action before the reviewer accepts them', () => {
  const facts = emptyFacts();
  facts.action = model('Tackle');
  assert.equal(pageReviewPresets('action', facts).length, 1);
  facts.action = human('High Leg');
  assert.deepEqual(pageReviewPresets('action', facts), []);
  assert.equal(confirmGuidePage('action', facts).contact.value, null);
  facts.contact = { value: false, source: 'rule', confidence: null, confirmed: false };
  facts.action = human('Tackle');
  assert.deepEqual(pageReviewPresets('action', facts), []);
  assert.equal(confirmGuidePage('action', facts).contact, facts.contact);
});

test('blank supplements are visited in order without changing the draft', () => {
  const facts = physicalFacts();
  const snapshot = structuredClone(facts);
  let step = nextGuideStep('severity', facts);
  assert.equal(step, 'location');
  step = nextGuideStep(step, facts);
  assert.equal(step, 'context');
  step = nextGuideStep(step, facts);
  assert.equal(step, 'result');
  assert.deepEqual(facts, snapshot);
  assert.equal(facts.location, null);
  assert.equal(facts.tactical_impact.confirmed, false);
});

test('only a confirmed dive skips irrelevant supplements after severity', () => {
  const facts = physicalFacts();
  facts.action = model('Dive');
  assert.equal(nextGuideStep('severity', facts), 'location');
  facts.action = human('Dive');
  assert.equal(nextGuideStep('severity', facts), 'result');
});

test('only a confirmed no-offence answer bypasses the remaining questions', () => {
  const facts = emptyFacts();
  facts.offence_confirmed = model(false);
  assert.equal(nextGuideStep('action', facts), 'severity');
  facts.offence_confirmed = human(false);
  assert.equal(nextGuideStep('action', facts), 'result');
  assert.deepEqual(pageAdoptableFields('action', facts), []);
});

test('legacy derived contact and victim are downgraded without altering saved objects', () => {
  const saved = physicalFacts();
  saved.contact = rule(true);
  saved.victim_team = rule('away');
  saved.action = rule('Tackle');
  saved.location = { x_m: 12, y_m: 34, source: 'human', confirmed: true };
  const snapshot = structuredClone(saved);
  const draft = normalizeLegacyDerivedFacts(saved);
  assert.notEqual(draft, saved);
  assert.deepEqual(draft.contact, {
    value: true,
    source: 'rule',
    confidence: null,
    confirmed: false,
  });
  assert.deepEqual(draft.victim_team, {
    value: 'away',
    source: 'rule',
    confidence: null,
    confirmed: false,
  });
  assert.equal(draft.action, saved.action);
  assert.equal(draft.location, saved.location);
  assert.deepEqual(saved, snapshot);
  assert.equal(normalizeLegacyDerivedFacts(draft), draft);
});

test('human overrides and already unconfirmed legacy values remain unchanged', () => {
  const facts = physicalFacts();
  facts.victim_team = human('home');
  assert.equal(normalizeLegacyDerivedFacts(facts), facts);
  facts.contact = { ...rule(false), confirmed: false };
  assert.equal(normalizeLegacyDerivedFacts(facts), facts);
  assert.equal(facts.contact.value, false);
});

test('rule-derived blank values do not create confirmation or a changed draft', () => {
  const facts = emptyFacts();
  facts.contact = { value: null, source: 'rule', confidence: null, confirmed: false };
  assert.equal(normalizeLegacyDerivedFacts(facts), facts);
});

test('page adoption selects only visible, nonblank and unconfirmed model suggestions', () => {
  const facts = physicalFacts();
  facts.offence_confirmed = model(true);
  facts.action = { ...model('Holding'), confirmed: true };
  facts.contact = human(false);
  facts.intensity = model('excessive_force');
  facts.ball_in_play = { value: null, source: 'model', confidence: 0.8, confirmed: false };
  assert.deepEqual(pageAdoptableFields('action', facts), ['offence_confirmed']);
  assert.deepEqual(pageAdoptableFields('severity', facts), ['intensity']);
  assert.deepEqual(pageAdoptableFields('result', facts), []);
});

test('a confirmed dive does not offer an irrelevant intensity suggestion', () => {
  const facts = physicalFacts();
  facts.action = human('Dive');
  facts.intensity = model('reckless');
  facts.ball_in_play = model(true);
  assert.equal(nextGuideStep('action', facts), 'severity');
  assert.deepEqual(pageAdoptableFields('severity', facts), ['ball_in_play']);
});

test('location ownership suggestions appear only when a confirmed pitch point needs them', () => {
  const facts = physicalFacts();
  facts.offender_team = model('home');
  facts.home_defends_side = model('left');
  assert.deepEqual(pageAdoptableFields('location', facts), []);
  facts.location = { x_m: 52.5, y_m: 34, source: 'human', confirmed: true };
  assert.deepEqual(pageAdoptableFields('location', facts), []);
  facts.location = { x_m: 12, y_m: 34, source: 'human', confirmed: true };
  assert.deepEqual(pageAdoptableFields('location', facts), ['offender_team', 'home_defends_side']);
  facts.ball_in_play = human(false);
  assert.deepEqual(pageAdoptableFields('location', facts), []);
});

test('accepting suggestions preserves model provenance and manual overrides', () => {
  const facts = emptyFacts();
  facts.offence_confirmed = model(true);
  facts.action = model('Holding');
  facts.contact = human(false);
  facts.intensity = { ...model('reckless'), confirmed: true };
  facts.victim_team = { value: null, source: 'model', confidence: null, confirmed: false };
  const before = structuredClone(facts);
  const accepted = adoptModelFields(facts, [
    'offence_confirmed',
    'action',
    'contact',
    'intensity',
    'victim_team',
  ]);
  assert.notEqual(accepted, facts);
  assert.equal(accepted.offence_confirmed.confirmed, true);
  assert.deepEqual(accepted.action, { ...facts.action, confirmed: true });
  assert.equal(accepted.action.source, 'model');
  assert.equal(accepted.action.confidence, 0.8);
  assert.equal(accepted.contact, facts.contact);
  assert.equal(accepted.intensity, facts.intensity);
  assert.equal(accepted.victim_team, facts.victim_team);
  assert.deepEqual(facts, before);
});

test('missing-fact links return to the page that owns that question', () => {
  for (const field of ['offence_confirmed', 'action', 'contact']) {
    assert.equal(guideStepForFact(field), 'action');
  }
  for (const field of ['intensity', 'ball_in_play']) {
    assert.equal(guideStepForFact(field), 'severity');
  }
  for (const field of ['location', 'offender_team', 'home_defends_side']) {
    assert.equal(guideStepForFact(field), 'location');
  }
  for (const field of [
    'tactical_impact',
    'attempt_to_play_ball',
    'victim_team',
    'contact_region',
  ]) {
    assert.equal(guideStepForFact(field), 'context');
  }
});

test('preview requests cannot restore certainty after a later edit or a case revisit', () => {
  const scope = new ReviewRequestScope();
  scope.activate('case-A');
  const first = scope.ticket();
  scope.edited();
  const second = scope.ticket();
  assert.equal(scope.current(first, true), false);
  assert.equal(scope.current(second, true), true);
  scope.activate('case-B');
  scope.activate('case-A');
  assert.equal(scope.current(second, true), false);
});

test('collapsed additional context is not silently confirmed by page adoption', () => {
  const facts = physicalFacts();
  facts.tactical_impact = model('none');
  facts.victim_team = model('away');
  facts.contact_region = model('lower_body');
  assert.deepEqual(pageAdoptableFields('context', facts), ['tactical_impact']);
  assert.deepEqual(pageAdoptableFields('context', facts, true), [
    'tactical_impact',
    'victim_team',
    'contact_region',
  ]);
});

test('legacy contact-region aliases normalize without changing provenance or saved history', () => {
  const facts = physicalFacts();
  facts.contact_region = human('leg');
  const next = normalizeLegacyDerivedFacts(facts);
  assert.equal(next.contact_region.value, 'lower_body');
  assert.equal(next.contact_region.source, 'human');
  assert.equal(next.contact_region.confirmed, true);
  assert.equal(facts.contact_region.value, 'leg');
  assert.equal(normalizeLegacyDerivedFacts(next), next);
  facts.contact_region = human('body');
  assert.equal(normalizeLegacyDerivedFacts(facts).contact_region.value, 'upper_body');
});

test('attempt and tactical questions follow the actual rule dependencies', () => {
  const facts = physicalFacts();
  facts.intensity = human('careless');
  facts.tactical_impact = human('spa');
  facts.attempt_to_play_ball = model(true);
  assert.equal(contextRelevant(facts), true);
  assert.equal(attemptRelevant(facts), true);
  assert.ok(pageAdoptableFields('context', facts).includes('attempt_to_play_ball'));
  facts.intensity = human('reckless');
  assert.equal(attemptRelevant(facts), false);
  assert.equal(pageAdoptableFields('context', facts).includes('attempt_to_play_ball'), false);
  facts.tactical_impact = human('dogso');
  assert.equal(attemptRelevant(facts), true);
  facts.intensity = human('excessive_force');
  assert.equal(contextRelevant(facts), false);
  assert.equal(attemptRelevant(facts), false);
  facts.intensity = human('careless');
  facts.ball_in_play = human(false);
  assert.equal(contextRelevant(facts), false);
  assert.equal(attemptRelevant(facts), false);
});

test('inapplicable optional pages never accept hidden model suggestions', () => {
  const facts = physicalFacts();
  facts.offender_team = model('home');
  facts.home_defends_side = model('left');
  facts.tactical_impact = model('spa');
  facts.location = { x_m: 12, y_m: 34, source: 'human', confirmed: true };
  facts.action = human('Dive');
  assert.deepEqual(pageAdoptableFields('location', facts), []);
  assert.deepEqual(pageAdoptableFields('context', facts, true), []);
  facts.action = human('Tackle');
  facts.offence_confirmed = human(false);
  assert.deepEqual(pageAdoptableFields('location', facts), []);
  assert.deepEqual(pageAdoptableFields('context', facts, true), []);
});
