import test from 'node:test';
import assert from 'node:assert/strict';
import { ExperimentRequestScope } from '../src/experimentRequestScope.ts';

test('a late job-history response cannot replace a newly submitted task', () => {
  const scope = new ExperimentRequestScope();
  scope.activate('foul:video1:mvit-contact-v3');
  const history = scope.ticket();
  const submitted = scope.submitted();
  assert.equal(scope.current(history), false);
  assert.equal(scope.current(submitted), true);
});

test('an earlier visit and polling response remain stale after returning to the same profile', () => {
  const scope = new ExperimentRequestScope();
  scope.activate('A');
  const polling = scope.ticket();
  scope.activate('B');
  scope.activate('A');
  assert.equal(scope.current(polling), false);
  assert.equal(scope.current(scope.ticket()), true);
});

test('a qualification change starts a different task-response scope', () => {
  const scope = new ExperimentRequestScope();
  scope.activate('foul:video1:v3:accepted-configuration-1');
  const old = scope.submitted();
  scope.activate('foul:video1:v3:accepted-configuration-2');
  assert.equal(scope.current(old), false);
});
