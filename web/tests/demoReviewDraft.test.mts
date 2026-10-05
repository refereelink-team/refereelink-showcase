import test from 'node:test';
import assert from 'node:assert/strict';
import { createDemoReviewDraft } from '../src/features/demoReviewDraft.ts';
import { emptyFacts } from '../src/components/multiview/reviewFacts.ts';
import type { MultiviewDecision } from '../src/types/multiview.ts';

test('returning to a case creates a blank demonstration with no earlier ruling or selections', () => {
  const firstVisit = createDemoReviewDraft();
  firstVisit.facts.action = {
    value: 'Pushing',
    source: 'model',
    confidence: 0.9,
    confirmed: true,
  };
  firstVisit.facts.offence_confirmed = {
    value: true,
    source: 'human',
    confidence: null,
    confirmed: true,
  };
  firstVisit.facts.location = { x_m: 40, y_m: 20, source: 'human', confirmed: true };
  firstVisit.decision = { analysis_id: 'earlier-analysis' } as MultiviewDecision;
  firstVisit.protectedFields.push('offence_confirmed');
  firstVisit.step = 'result';
  const nextVisit = createDemoReviewDraft();
  assert.deepEqual(nextVisit.facts, emptyFacts());
  assert.equal(nextVisit.decision, null);
  assert.equal(nextVisit.step, 'action');
  assert.deepEqual(nextVisit.protectedFields, []);
  assert.equal(firstVisit.facts.action.value, 'Pushing');
});

test('new demonstrations and reset sessions have independent nested facts and override lists', () => {
  const first = createDemoReviewDraft();
  const second = createDemoReviewDraft();
  const reset = createDemoReviewDraft();
  first.facts.contact.value = true;
  first.facts.contact.confirmed = true;
  first.protectedFields.push('contact');
  assert.deepEqual(second.facts, emptyFacts());
  assert.deepEqual(reset.facts, emptyFacts());
  assert.deepEqual(second.protectedFields, []);
  assert.notEqual(first.facts, second.facts);
  assert.notEqual(second.facts.contact, reset.facts.contact);
});
