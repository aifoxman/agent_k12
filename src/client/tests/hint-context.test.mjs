import assert from 'node:assert/strict';
import test from 'node:test';
import { createHintContext, nextHintLevel, finishConfirmationText } from '../src/hint-context.js';

test('task changes cancel all old callbacks while the new request remains current', async () => {
  const scope = createHintContext();
  const old = scope.begin();
  const callbacks = [];
  const late = Promise.resolve().then(() => {
    if (old.current()) callbacks.push('old target');
  });
  scope.invalidate();
  const fresh = scope.begin();
  await late;
  assert.equal(old.signal.aborted, true);
  assert.equal(old.current(), false);
  assert.equal(fresh.current(), true);
  if (old.current()) scope.invalidate(); // A stale finally must not cancel fresh work.
  assert.equal(fresh.current(), true);
  assert.deepEqual(callbacks, []);
  scope.invalidate();
  assert.equal(fresh.current(), false);
});

test('clarifications keep the teaching level and only explicit next steps advance it', () => {
  assert.equal(nextHintLevel(2, true), 2);
  assert.equal(nextHintLevel(1, true, true), 2);
  assert.equal(nextHintLevel(2, true, true), 2);
  assert.equal(nextHintLevel(3, false, true), 1);
  assert.equal(nextHintLevel(3, true, true), 2);
});

test('ending warns about unsubmitted photographs without requiring every task to finish', () => {
  assert.match(finishConfirmationText(2, 3), /2项任务未提交/);
  assert.match(finishConfirmationText(2, 3), /3页尚未提交/);
  assert.doesNotMatch(finishConfirmationText(0, 0), /重新拍摄/);
});
