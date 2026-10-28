import assert from 'node:assert/strict';
import test from 'node:test';
import {
  READING_QUESTION,
  THINKING_QUESTION,
  asksForCompleteTeaching,
  guidanceInScope,
  guidanceRequest,
  teachingTier,
} from '../src/teaching-levels.js';

const scope = { sessionId: 'session-a', taskId: 'task-a', frameRevision: 7 };
const image = new Blob(['original frozen jpeg bytes'], { type: 'image/jpeg' });
const original = {
  ...scope,
  image,
  hintId: 'saved-first-tier',
  box: [0.1, 0.2, 0.5, 0.4],
  reference: '第4题',
  level: 1,
};
const request = (change = {}) =>
  guidanceRequest({ ...scope, binding: original, reference: '第4题', ...change });

test('a fresh default request explains the question at tier one without a frozen binding', () => {
  assert.deepEqual(request({ binding: null, prompt: READING_QUESTION }), {
    level: 1,
    binding: null,
    replay: false,
  });
  assert.equal(asksForCompleteTeaching(READING_QUESTION), false);
});

test('explicit complete thinking can start at tier two without inventing an old target', () => {
  const result = request({ binding: null, prompt: THINKING_QUESTION });
  assert.equal(result.level, 2);
  assert.equal(result.binding, null);
  assert.equal(result.replay, false);
});

test('advancing the same question keeps the exact original Blob and server hint identity', () => {
  const result = request({ advance: true });
  assert.equal(result.level, 2);
  assert.equal(result.binding, original);
  assert.equal(result.binding.image, image);
  assert.equal(result.binding.hintId, 'saved-first-tier');
  assert.equal(result.replay, false);
  assert.equal(original.level, 1);
});

test('clarifying the first method does not silently advance its help tier', () => {
  const result = request({ prompt: '为什么要先比较这两个部分？', studentThought: '这一步没懂' });
  assert.equal(result.level, 1);
  assert.equal(result.binding, original);
  assert.equal(result.replay, false);
});

test('tier two and a restored legacy tier three replay instead of creating a third help tier', () => {
  for (const level of [2, 3, 20]) {
    const binding = { ...original, level };
    const result = request({ binding, advance: true });
    assert.equal(result.level, 2);
    assert.equal(result.binding, binding);
    assert.equal(result.replay, true);
  }
  assert.equal(teachingTier(0), 1);
  assert.equal(teachingTier('3'), 2);
});

test('a specific new student thought at tier two is not discarded as an automatic replay', () => {
  const binding = { ...original, level: 2 };
  assert.equal(
    request({ binding, advance: true, studentThought: '为什么不能只留一层？' }).replay,
    false,
  );
  assert.equal(request({ binding, advance: true, studentThought: '  ' }).replay, true);
  assert.equal(request({ binding, studentThought: '为什么不能只留一层？' }).level, 2);
});

test('another session, task, or camera revision cannot borrow a frozen picture', () => {
  for (const change of [
    { sessionId: 'session-b' },
    { taskId: 'task-b' },
    { taskId: '' },
    { frameRevision: 8 },
  ]) {
    const result = request(change);
    assert.equal(result.binding, null);
    assert.equal(result.level, 1);
    assert.equal(result.replay, false);
  }
});

test('a new selection, offer, or explicit question reference starts a new binding', () => {
  for (const change of [
    { newSelection: true },
    { candidateId: 'new-offer' },
    { reference: '第5题' },
    { prompt: '换一题，看看旁边这道。' },
    { prompt: '请读下一题。' },
    { prompt: '看看另一道题。' },
  ]) {
    const result = request(change);
    assert.equal(result.binding, null);
    assert.equal(result.level, 1);
    assert.equal(result.replay, false);
  }
});

test('an automatic offer remains first-tier even if stale caller arguments request advancement', () => {
  for (const change of [
    { prompt: THINKING_QUESTION },
    { advance: true },
    { advance: true, prompt: THINKING_QUESTION },
  ]) {
    const result = request({ candidateId: 'new-offer', ...change });
    assert.equal(result.level, 1);
    assert.equal(result.binding, null);
    assert.equal(result.replay, false);
  }
});

test('requesting a complete new question never sends the previous question binding', () => {
  const result = request({ prompt: THINKING_QUESTION, reference: '第5题' });
  assert.equal(result.level, 2);
  assert.equal(result.binding, null);
  assert.equal(result.replay, false);
});

test('incomplete old history cannot acquire a photo or continuation identity', () => {
  for (const missing of ['image', 'hintId', 'box']) {
    const binding = { ...original, [missing]: null };
    assert.equal(guidanceInScope(binding, scope), false);
    assert.equal(request({ binding, advance: true }).binding, null);
  }
});

test('withholding an answer alone does not request a full lesson', () => {
  for (const prompt of [
    '不要直接说出答案，先解释方法。',
    '先不直接给最终答案。',
    '不用完整讲解，只给我思路。',
  ]) {
    assert.equal(asksForCompleteTeaching(prompt), false, prompt);
    assert.equal(request({ binding: null, prompt }).level, 1);
  }
});

test('a complete explanation remains tier two when the answer must be withheld', () => {
  for (const prompt of ['请完整讲解，但不要答案。', '请完整讲解解题思考和检查，先不报最终答案。']) {
    assert.equal(asksForCompleteTeaching(prompt), true, prompt);
    const result = request({ binding: null, prompt });
    assert.equal(result.level, 2);
    assert.equal(result.binding, null);
    assert.equal(result.replay, false);
  }
});
