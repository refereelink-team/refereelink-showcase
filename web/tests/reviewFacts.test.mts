import test from 'node:test';
import assert from 'node:assert/strict';
import {
  emptyFacts,
  factsFromDecision,
  prefillFacts,
  setHumanFact,
} from '../src/components/multiview/reviewFacts.ts';
import type { MultiviewDecision } from '../src/types/multiview.ts';

const decision: MultiviewDecision = {
  analysis_id: 'model-analysis',
  event_id: 'event',
  case_id: 'case',
  timestamp: 1,
  decision: 'Yellow Card',
  decision_zh: '黄牌',
  action: 'Tackle',
  severity: 'Reckless',
  confidence: 0.8,
  action_candidates: [{ label: 'Tackle', confidence: 0.85 }],
  severity_candidates: [{ label: 'Reckless', confidence: 0.78 }],
  checkpoint_hash: 'hash',
  ruleset_compatible: true,
  card: 'yellow',
  suggested_intensity: 'reckless',
  mode: 'model',
  model: 'MVFoul',
  device: 'cuda',
  inference_ms: 100,
  preprocess_ms: 10,
  gradcam_ms: 20,
  gpu_mem_mb: 100,
  localization: {},
  localization_source: 'gradcam',
  view_attention: [1],
  detail: {},
};

test('new model suggestions keep provenance and require human confirmation', () => {
  const facts = factsFromDecision(decision);
  assert.deepEqual(facts.action, {
    value: 'Tackle',
    source: 'model',
    confidence: 0.85,
    confirmed: false,
  });
  assert.equal(facts.offence_confirmed.confirmed, false);
  assert.equal(facts.intensity.confirmed, false);
  for (const name of [
    'offender_team',
    'victim_team',
    'ball_in_play',
    'contact',
    'contact_region',
    'attempt_to_play_ball',
    'tactical_impact',
    'home_defends_side',
  ] as const) {
    assert.equal(facts[name].value, null);
  }
  assert.equal(facts.location, null);
});

test('prefill updates only unlocked suggestions and never overwrites manual values', () => {
  const current = emptyFacts();
  current.action = { value: 'Holding', source: 'human', confidence: null, confirmed: true };
  current.location = { x_m: 20, y_m: 34, source: 'human', confirmed: true };
  const result = prefillFacts(current, decision);
  assert.equal(result.facts.action.value, 'Holding');
  assert.deepEqual(result.facts.location, current.location);
  assert.deepEqual(result.changedFields, ['offence_confirmed', 'intensity']);
  assert.equal(current.offence_confirmed.value, null);
});

test('explicitly cleared fields remain blank when a new analysis finishes', () => {
  const current = emptyFacts();
  const result = prefillFacts(current, decision, { protectedFields: new Set(['action']) });
  assert.equal(result.facts.action.value, null);
  assert.deepEqual(result.changedFields, ['offence_confirmed', 'intensity']);
});

test('persisted reviews remain authoritative even when they contain blank facts', () => {
  const current = emptyFacts();
  const result = prefillFacts(current, decision, { hasSavedReview: true });
  assert.equal(result.facts, current);
  assert.deepEqual(result.changedFields, []);
});

test('confirmed model suggestions are protected and identical suggestions do not reanimate', () => {
  const current = factsFromDecision(decision);
  current.action.confirmed = true;
  const next = {
    ...decision,
    action: 'Holding',
    action_candidates: [{ label: 'Holding', confidence: 0.85 }],
  };
  const result = prefillFacts(current, next);
  assert.equal(result.facts.action.value, 'Tackle');
  assert.deepEqual(result.changedFields, []);
});

test('unconfirmed model suggestions can update without changing unrelated context', () => {
  const current = factsFromDecision(decision);
  const result = prefillFacts(current, { ...decision, action: 'Pushing' });
  assert.equal(result.facts.action.value, 'Pushing');
  assert.equal(result.facts.action.confirmed, false);
  assert.deepEqual(result.changedFields, ['action']);
});

test('scripted fallback is not presented as a real model suggestion', () => {
  const facts = factsFromDecision({ ...decision, mode: 'scripted' });
  assert.equal(facts.action.source, 'rule');
  assert.equal(facts.action.confidence, null);
  assert.equal(facts.action.confirmed, false);
});

test('changing an action invalidates derived contact without erasing human contact overrides', () => {
  const current = emptyFacts();
  current.contact = { value: true, source: 'rule', confidence: null, confirmed: true };
  const changed = setHumanFact(current, 'action', 'Dive');
  assert.equal(changed.action.source, 'human');
  assert.equal(changed.action.confirmed, true);
  assert.deepEqual(changed.contact, {
    value: null,
    source: 'rule',
    confidence: null,
    confirmed: false,
  });
  assert.equal(current.contact.value, true);
  current.contact.source = 'human';
  assert.deepEqual(setHumanFact(current, 'action', 'Dive').contact, current.contact);
});

test('a no-offence result does not fabricate a contact intensity', () => {
  const facts = factsFromDecision({
    ...decision,
    severity: 'No Offence',
    card: 'none',
    suggested_intensity: null,
  });
  assert.equal(facts.offence_confirmed.value, false);
  assert.equal(facts.intensity.value, null);
});
