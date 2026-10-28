import assert from 'node:assert/strict';
import test from 'node:test';
import {
  mergeTeachingTarget,
  teachingFollowupPrompt,
  visibleTeachingDraft,
} from '../src/teaching-session.js';

test('late localization and a done event without a box preserve already spoken page operations', () => {
  const first = { speech: '先圈出条件', page: [{ kind: 'circle', box: [0.1, 0.2, 0.1, 0.1] }] };
  let target = mergeTeachingTarget(null, { imageUrl: 'blob:current', beats: [first] });
  assert.equal(target.box, null);
  target = mergeTeachingTarget(
    { ...target, playbackVersion: 3 },
    {
      imageUrl: 'blob:current',
      box: [0.1, 0.1, 0.5, 0.5],
      label: '第三题',
    },
  );
  target = mergeTeachingTarget(target, { imageUrl: 'blob:current', box: null });
  assert.deepEqual(target.beats, [first]);
  assert.deepEqual(target.box, [0.1, 0.1, 0.5, 0.5]);
  assert.equal(target.playbackVersion, 3);
  assert.deepEqual(mergeTeachingTarget(target, { imageUrl: 'blob:new' }).beats, []);
});

const savedDraft = {
  sessionId: 's',
  taskId: 't',
  label: '旧题',
  beats: [{ scratch: [{ kind: 'text', role: 'prose', text: '先理解条件的关系' }] }],
};
const base = { savedDraft, sessionId: 's', taskId: 't' };

test('saved notes remain hidden in ordinary work, pause, completion and cold start', () => {
  for (const stage of ['active', 'paused', 'home', 'done', 'setup'])
    assert.equal(visibleTeachingDraft({ ...base, stage, mode: 'idle' }), null);
  assert.equal(visibleTeachingDraft({ ...base, stage: 'paused', mode: 'lesson' }), null);
});

test('starting another lesson never substitutes the preceding question board', () => {
  assert.equal(
    visibleTeachingDraft({
      ...base,
      stage: 'active',
      mode: 'lesson',
      target: { kind: 'teaching', label: '新题', beats: [] },
    }),
    null,
  );
  const target = {
    kind: 'teaching',
    label: '新题',
    beats: [
      { scratch: [{ kind: 'text', role: 'prose', text: '本题的依据' }] },
      { scratch: [], teaching_move: 'wait_for_writing' },
    ],
  };
  assert.equal(
    visibleTeachingDraft({ ...base, stage: 'active', mode: 'lesson', target }).label,
    '新题',
  );
});

test('review requires an explicit mode and retains session/task boundaries', () => {
  for (const stage of ['active', 'paused'])
    assert.equal(visibleTeachingDraft({ ...base, stage, mode: 'review' }), savedDraft);
  assert.equal(
    visibleTeachingDraft({ ...base, stage: 'paused', mode: 'review', taskId: 'other' }),
    null,
  );
  assert.equal(visibleTeachingDraft({ ...base, stage: 'done', mode: 'review' }), null);
});

test('a partial replay is displayed separately and never replaces the complete saved archive', () => {
  const archive = { ...savedDraft, beats: [...savedDraft.beats, ...savedDraft.beats] };
  const prefix = { ...archive, beats: archive.beats.slice(0, 1) };
  const context = { ...base, savedDraft: archive, stage: 'active', mode: 'review' };
  assert.equal(visibleTeachingDraft({ ...context, reviewDraft: prefix }), prefix);
  assert.equal(visibleTeachingDraft(context), archive);
  assert.equal(archive.beats.length, 2);
});

test('the reply caption keeps only the final question and its logical step context', () => {
  const result = {
    response: '完整课程仍然保存在档案中',
    visual_beats: [
      { speech: '先看图中的条件。', step_id: 1 },
      { speech: '请比较这两条边。', step_id: 2 },
      { speech: '你能解释它们为什么一样长吗？', step_id: 2, reply_mode: 'spoken' },
    ],
  };
  assert.equal(teachingFollowupPrompt(result), '请比较这两条边。你能解释它们为什么一样长吗？');
  assert.equal(result.response, '完整课程仍然保存在档案中');
  assert.equal(result.visual_beats.length, 3);
});

test('legacy reply cues retain the last actual beat or the original response', () => {
  assert.equal(
    teachingFollowupPrompt({
      response: '旧课程全文',
      visual_beats: [{ speech: '早先的讲解' }, { speech: '现在你准备怎样试？' }],
    }),
    '现在你准备怎样试？',
  );
  assert.equal(teachingFollowupPrompt({ response: '你指哪一道题？' }), '你指哪一道题？');
  assert.equal(teachingFollowupPrompt(), '');
});
