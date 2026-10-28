import assert from 'node:assert/strict';
import test from 'node:test';
import { createTeachingPlayback } from '../src/teaching-playback.js';

const settle = () => new Promise((resolve) => setTimeout(resolve, 15));
const immediate = { pauseMs: 0, stepPauseMs: 0 };

test('streaming progress keeps its total provisional until the entire lesson arrives', async () => {
  let state;
  const lesson = createTeachingPlayback({
    ...immediate,
    play: async () => true,
    onState: (value) => (state = value),
  });
  lesson.enqueue([{ speech: 'Observe.', step_id: 3 }]);
  await until(() => state?.phase === 'preparing');
  assert.equal(state.receivedComplete, false);
  assert.equal(state.stepCount, 1);
  lesson.enqueue([
    { speech: 'Explain.', step_id: 3 },
    { speech: 'Check.', step_id: 8 },
  ]);
  lesson.finish();
  await lesson.done;
  assert.equal(state.receivedComplete, true);
  assert.equal(state.stepCount, 2);
  assert.equal(state.stepIndex, 1);
});

test('same-ID clips retain one logical step even when legacy step_end is true on each clip', async () => {
  const indices = [];
  let state;
  const lesson = createTeachingPlayback({
    ...immediate,
    play: async () => true,
    onState: (value) => {
      state = value;
      if (value?.phase === 'playing') indices.push(value.stepIndex);
    },
  });
  lesson.enqueue([
    { speech: '操作', step_id: 4 },
    { speech: '理由', step_id: 4 },
    { speech: '检查', step_id: 9 },
  ]);
  lesson.finish();
  await lesson.done;
  assert.deepEqual(indices, [0, 0, 1]);
  assert.equal(state.stepCount, 2);
});

test('replaying a previously spoken logical step keeps future streaming items held and does not rewrite the lesson', async () => {
  const played = [];
  let state;
  const lesson = createTeachingPlayback({
    ...immediate,
    play: async (step, { index }) => {
      played.push([step.speech, index]);
      return true;
    },
    onState: (value) => (state = value),
  });
  lesson.enqueue([
    { speech: '操作', step_id: 1 },
    { speech: '理由', step_id: 1 },
    { speech: '当前练习', step_id: 2, reply_mode: 'writing' },
    { speech: '未来检查', step_id: 3 },
  ]);
  lesson.finish();
  await until(() => state?.phase === 'waiting');
  lesson.review(3);
  await settle();
  assert.equal(played.length, 3, 'An unspoken step cannot be replayed early');
  lesson.review(0);
  await until(() => played.length === 5 && state.phase === 'waiting');
  assert.deepEqual(played.slice(3), [
    ['操作', 0],
    ['理由', 1],
  ]);
  lesson.command('next');
  await lesson.done;
  assert.deepEqual(played.at(-1), ['未来检查', 3]);
  assert.equal(state.stepCount, 3);
});
async function until(predicate) {
  for (let index = 0; index < 100; index++) {
    if (predicate()) return;
    await settle();
  }
  assert.fail('playback did not reach the expected state');
}

test('a complete explanation continues through logical steps without sentence-by-sentence clicks', async () => {
  const played = [];
  let state;
  const lesson = createTeachingPlayback({
    ...immediate,
    play: async (step, options) => played.push([step.speech, options.rate]),
    onState: (value) => (state = value),
  });
  lesson.enqueue([{ speech: 'Read the condition.' }, { speech: 'Explain the relation.' }]);
  lesson.finish();
  assert.equal(await lesson.done, true);
  assert.deepEqual(played, [
    ['Read the condition.', 1],
    ['Explain the relation.', 1],
  ]);
  assert.equal(state.phase, 'complete');
});

test('one semantic step can exceed three streamed fragments without a forced stop', async () => {
  const played = [];
  const indices = [];
  const lesson = createTeachingPlayback({
    ...immediate,
    play: async (step) => played.push(step.speech),
    onState: (state) => {
      if (state?.phase === 'playing') indices.push(state.stepIndex);
    },
  });
  lesson.enqueue(
    Array.from({ length: 5 }, (_, i) => ({
      speech: `Reason ${i + 1}.`,
      step_id: 1,
      step_end: i === 4,
    })),
  );
  lesson.enqueue([{ speech: 'Check the result.', step_id: 2 }]);
  lesson.finish();
  await lesson.done;
  assert.equal(played.length, 6);
  assert.deepEqual(indices, [0, 0, 0, 0, 0, 1]);
});

test('explicit pause holds future steps and continuing repeats interrupted reasoning', async () => {
  const played = [];
  let state;
  const lesson = createTeachingPlayback({
    ...immediate,
    play: async (step, { signal }) => {
      played.push(step.speech);
      if (played.length === 1)
        await new Promise((resolve) => signal.addEventListener('abort', resolve, { once: true }));
      return !signal.aborted;
    },
    onState: (value) => (state = value),
  });
  lesson.enqueue([{ speech: 'Current reason.' }, { speech: 'Future result.' }]);
  lesson.finish();
  await settle();
  lesson.command('pause');
  await settle();
  assert.equal(state.phase, 'waiting');
  assert.equal(state.holdReason, 'pause');
  await settle();
  assert.deepEqual(played, ['Current reason.']);
  lesson.command('next');
  await lesson.done;
  assert.deepEqual(played, ['Current reason.', 'Current reason.', 'Future result.']);
});

test('slower replays the current operation and reason at 0.85 then waits before the next result', async () => {
  const played = [];
  let state;
  const lesson = createTeachingPlayback({
    ...immediate,
    play: async (step, options) => {
      played.push({ text: step.speech, rate: options.rate, repeat: options.repeat });
      if (played.length === 2)
        await new Promise((resolve) =>
          options.signal.addEventListener('abort', resolve, { once: true }),
        );
      return !options.signal.aborted;
    },
    onState: (value) => (state = value),
  });
  lesson.enqueue([
    { speech: 'Operation.', step_id: 1, step_end: false },
    { speech: 'Its reason.', step_id: 1 },
    { speech: 'Future answer.', step_id: 2 },
  ]);
  lesson.finish();
  await until(() => played.length === 2);
  lesson.command('slower');
  await until(() => state.phase === 'waiting');
  assert.deepEqual(
    played.map((item) => item.text),
    ['Operation.', 'Its reason.', 'Operation.', 'Its reason.'],
  );
  assert.ok(played.slice(2).every((item) => item.rate === 0.85 && item.repeat));
  assert.equal(state.phase, 'waiting');
  lesson.command('next');
  await lesson.done;
  assert.equal(played.at(-1).text, 'Future answer.');
});

test('a real child-writing handoff waits and replay keeps the complete semantic step', async () => {
  const played = [];
  const lesson = createTeachingPlayback({
    ...immediate,
    play: async (step) => played.push(step.speech),
  });
  lesson.enqueue([
    { speech: 'Find the common side.', step_id: 1, step_end: false },
    { speech: 'Try this comparison yourself.', step_id: 1, reply_mode: 'writing' },
    { speech: 'Check your comparison.', step_id: 2 },
  ]);
  lesson.finish();
  await until(() => lesson.waiting());
  assert.equal(lesson.waiting(), true);
  lesson.command('replay');
  await until(() => played.length === 4 && lesson.waiting());
  assert.deepEqual(played, [
    'Find the common side.',
    'Try this comparison yourself.',
    'Find the common side.',
    'Try this comparison yourself.',
  ]);
  assert.equal(lesson.waiting(), true);
  lesson.command('next');
  await lesson.done;
  assert.equal(played.at(-1), 'Check your comparison.');
});

test('cancelling a stream wait prevents late received steps from starting playback', async () => {
  const played = [];
  const lesson = createTeachingPlayback({ ...immediate, play: async (step) => played.push(step) });
  await settle();
  lesson.cancel();
  lesson.enqueue([{ speech: 'Late response.' }]);
  lesson.finish();
  assert.equal(await lesson.done, false);
  assert.deepEqual(played, []);
});

test('SSE ending cannot complete interrupted speech and replay remains held until continue', async () => {
  let plays = 0;
  let state;
  let completed = false;
  const lesson = createTeachingPlayback({
    ...immediate,
    play: async () => ++plays > 1,
    onState: (value) => (state = value),
  });
  lesson.enqueue([{ speech: 'Explain your reasoning.' }]);
  lesson.finish();
  lesson.done.then(() => (completed = true));
  await settle();
  assert.equal(completed, false);
  assert.equal(state.interrupted, true);
  lesson.finish();
  await settle();
  assert.equal(completed, false);
  lesson.command('replay');
  await settle();
  assert.equal(completed, false);
  assert.equal(state.phase, 'waiting');
  lesson.command('next');
  await lesson.done;
  assert.equal(plays, 2);
  assert.equal(state.phase, 'complete');
});

test('changed logical IDs create a boundary without a mandatory manual stop', async () => {
  const played = [];
  const lesson = createTeachingPlayback({
    ...immediate,
    play: async (step) => played.push(step.speech),
  });
  lesson.enqueue([
    { speech: 'One.', step_id: 1, step_end: false },
    { speech: 'Two.', step_id: 2 },
  ]);
  lesson.finish();
  await lesson.done;
  assert.deepEqual(played, ['One.', 'Two.']);
});

test('manual speech interruption holds the next clause and late streaming fragments', async () => {
  const played = [];
  const lesson = createTeachingPlayback({
    ...immediate,
    play: async (step, { signal }) => {
      played.push(step.speech);
      await new Promise((resolve) => signal.addEventListener('abort', resolve, { once: true }));
      return false;
    },
  });
  lesson.enqueue([{ speech: 'Current.', step_end: false }]);
  await settle();
  lesson.interrupt();
  await settle();
  lesson.enqueue([{ speech: 'Future.' }]);
  lesson.finish();
  await settle();
  assert.deepEqual(played, ['Current.']);
  assert.equal(lesson.waiting(), true);
  lesson.cancel();
  await lesson.done;
});
