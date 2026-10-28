import assert from 'node:assert/strict';
import test from 'node:test';
import { teachingExplanation } from '../src/teaching-levels.js';

test('uncleared explanations remain a same-tier retry while parent handoff never becomes an automatic retry', () => {
  const unclear = teachingExplanation({ explanation_status: 'needs_clarification' });
  assert.equal(unclear.ready, false);
  assert.equal(unclear.retry, true);
  assert.match(unclear.message, /核对/u);
  const parent = teachingExplanation({ explanation_status: 'needs_parent' });
  assert.equal(parent.ready, false);
  assert.equal(parent.retry, false);
  assert.match(parent.message, /家长/u);
});

test('interrupted delivery overrides ready and legacy successful delivery remains compatible', () => {
  assert.equal(teachingExplanation().ready, true);
  assert.equal(teachingExplanation({ explanation_status: 'ready' }).retry, false);
  assert.equal(
    teachingExplanation({ explanation_status: 'ready', delivery_complete: false }).status,
    'interrupted',
  );
  assert.equal(teachingExplanation({ explanation_status: 'unknown' }).ready, false);
});

test('saved scenes ending in clarification remain incomplete after earlier teaching', () => {
  assert.equal(
    teachingExplanation({ beats: [{ teaching_move: 'clarify_target' }] }).status,
    'needs_clarification',
  );
  assert.equal(
    teachingExplanation({
      beats: [{ teaching_move: 'clarify_target' }, { teaching_move: 'parent_handoff' }],
    }).status,
    'needs_parent',
  );
  assert.equal(
    teachingExplanation({
      beats: [
        { speech: 'First compare the shared side.', teaching_move: 'give_clue' },
        { speech: 'The remaining condition needs checking.', teaching_move: 'clarify_target' },
      ],
    }).status,
    'needs_clarification',
  );
});

test('terminal parent moves and a real needs_parent flag preserve parent review in cold scenes', () => {
  for (const terminal of [
    { teaching_move: 'parent_handoff' },
    { teaching_move: 'parent' },
    {
      speech: 'Please check this condition together.',
      teaching_move: 'give_clue',
      needs_parent: true,
    },
  ]) {
    const state = teachingExplanation({
      visual_beats: [{ speech: 'Here is the method.', teaching_move: 'give_clue' }, terminal],
    });
    assert.equal(state.status, 'needs_parent');
    assert.equal(state.ready, false);
    assert.equal(state.retry, false);
  }
});

test('an earlier clarification or parent handoff does not override a later ordinary lesson ending', () => {
  for (const earlier of ['clarify_target', 'parent_handoff', 'parent']) {
    for (const ending of [
      { speech: 'Substituting both conditions gives the same result.', teaching_move: 'give_clue' },
      { teaching_move: 'review_attempt', needs_parent: false },
      { speech: 'The check is now complete.' },
      {
        speech: 'You can try writing it now.',
        teaching_move: 'wait_for_writing',
        reply_mode: 'writing',
      },
    ]) {
      const state = teachingExplanation({ beats: [{ teaching_move: earlier }, ending] });
      assert.equal(state.status, 'ready');
      assert.equal(state.ready, true);
    }
  }
});

test('empty or malformed trailing records cannot hide the last saved clarification', () => {
  const state = teachingExplanation({
    beats: [
      { speech: 'Compare the two parts.', teaching_move: 'give_clue' },
      { speech: 'One measurement is not clear.', teaching_move: 'clarify_target' },
      null,
      [],
      'not a beat',
      {},
      { speech: '  ', teaching_move: '', needs_parent: false },
    ],
  });
  assert.equal(state.status, 'needs_clarification');
  assert.equal(state.retry, true);
});

test('explicit server status and incomplete delivery keep precedence over the saved-scene fallback', () => {
  const beats = [{ teaching_move: 'give_clue' }, { teaching_move: 'clarify_target' }];
  assert.equal(teachingExplanation({ beats, explanation_status: 'ready' }).status, 'ready');
  assert.equal(
    teachingExplanation({ beats, explanation_status: 'needs_parent' }).status,
    'needs_parent',
  );
  assert.equal(teachingExplanation({ beats, delivery_complete: false }).status, 'interrupted');
  assert.equal(teachingExplanation({ beats, explanation_status: 'unknown' }).status, 'interrupted');
});

test('normal legacy endings stay ready and a truthy non-boolean parent flag grants no new state', () => {
  assert.equal(teachingExplanation({ beats: [] }).status, 'ready');
  assert.equal(
    teachingExplanation({
      beats: [{ speech: 'Check the original conditions.', needs_parent: 'false' }],
    }).status,
    'ready',
  );
});
