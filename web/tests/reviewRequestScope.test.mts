import test from 'node:test';
import assert from 'node:assert/strict';
import { ReviewRequestScope } from '../src/features/reviewRequestScope.ts';
test('rejects results from an earlier visit to the same case', () => {
  const scope = new ReviewRequestScope();
  scope.activate('A');
  const first = scope.ticket();
  scope.activate('B');
  scope.activate('A');
  assert.equal(scope.current(first), false);
  assert.equal(scope.current(scope.ticket()), true);
});
test('editing invalidates delayed explanations without discarding an analysis for the active case', () => {
  const scope = new ReviewRequestScope();
  scope.activate('A');
  const request = scope.ticket();
  scope.edited();
  assert.equal(scope.current(request), true);
  assert.equal(scope.current(request, true), false);
});
test('a save revision starts a new explanation generation and unmount rejects pending responses', () => {
  const scope = new ReviewRequestScope();
  scope.activate('A');
  scope.edited();
  const first = scope.ticket();
  scope.edited();
  assert.equal(scope.current(first, true), false);
  const next = scope.ticket();
  assert.equal(scope.current(next, true), true);
  scope.activate('');
  assert.equal(scope.current(next), false);
});
