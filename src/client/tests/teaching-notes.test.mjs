import assert from 'node:assert/strict';
import test from 'node:test';
import {
  latestTeachingDraft,
  teachingDraftFromTarget,
  teachingDraftInScope,
} from '../src/teaching-notes.js';
import { teachingSteps } from '../src/teaching-steps.js';

const steps = [
  { speech: '先看条件', scratch: [{ kind: 'text', role: 'prose', text: '已知条件' }] },
  { speech: '请自己试试', scratch: [] },
];

test('the independent draft keeps all steps and contains no camera image', () => {
  const target = {
    kind: 'teaching',
    box: [0.1, 0.2, 0.4, 0.3],
    label: '当前题目',
    imageUrl: 'blob:private-camera',
    beats: steps,
  };
  const draft = teachingDraftFromTarget(target, 'session', 'task');
  assert.deepEqual(draft.beats, steps);
  assert.equal(draft.imageUrl, undefined);
  assert.equal(teachingDraftInScope(draft, 'session', 'task'), draft);
  assert.equal(teachingDraftFromTarget({ ...target, kind: 'offer' }, 'session', 'task'), null);
});

test('restoring a board is restricted to the same session and task', () => {
  const hint = {
    id: 'h',
    session_id: 's',
    task_id: null,
    teaching_scene: { box: [0.1, 0.2, 0.3, 0.4], label: '题目', beats: steps },
  };
  const draft = latestTeachingDraft([hint], 's');
  assert.deepEqual(draft.beats, steps);
  assert.equal(draft.hintId, 'h');
  assert.equal(latestTeachingDraft([hint], 'another'), null);
  assert.equal(latestTeachingDraft([hint], 's', 'another-task'), null);
  assert.equal(teachingDraftInScope(draft, 'another'), null);
  assert.equal(teachingDraftInScope(draft, 's', 'another-task'), null);
});

test('an empty waiting beat does not erase the preceding saved board', () => {
  const hints = [
    { id: 'wait', session_id: 's', teaching_scene: { beats: [{ scratch: [] }] } },
    { id: 'board', session_id: 's', teaching_scene: { label: '题目', beats: steps } },
  ];
  assert.equal(latestTeachingDraft(hints, 's').hintId, 'board');
});

test('one lecture keeps a stable scene identity while another lecture changes it', () => {
  const target = { kind: 'teaching', sceneKey: 'live:1', beats: steps.slice(0, 1) };
  const first = teachingDraftFromTarget(target, 's');
  const second = teachingDraftFromTarget({ ...target, beats: steps }, 's');
  const next = teachingDraftFromTarget({ ...target, sceneKey: 'live:2' }, 's');
  assert.equal(first.sceneKey, second.sceneKey);
  assert.notEqual(first.sceneKey, next.sceneKey);
});

test('old records restore only existing words and do not invent paper coordinates or diagrams', () => {
  const response = '先读已知条件，再用自己的方法试一试。';
  const draft = latestTeachingDraft([{ id: 'old', session_id: 's', response }], 's');
  assert.equal(draft.legacy, true);
  assert.equal(draft.box, null);
  assert.equal(draft.beats[0].scratch[0].text, response);
  assert.deepEqual(draft.beats[0].page, []);
});

test('cold JSON restoration preserves all 48 short beats, logical-step boundaries and anchor text', () => {
  const beats = Array.from({ length: 48 }, (_, index) => ({
    speech: `第${index + 1}句：先看原图，再说明这一小步的理由。`,
    step_id: Math.floor(index / 2) + 1,
    step_title: `第${Math.floor(index / 2) + 1}步`,
    step_end: index % 2 === 1,
    page: [
      {
        kind: 'circle',
        anchor_id: `a${index}`,
        anchor_text: `条件${index}`,
        box: [0.1, 0.1, 0.1, 0.1],
      },
    ],
    scratch: [{ kind: 'text', role: 'prose', x: 8, y: 16, text: `保留第${index + 1}句的思考。` }],
  }));
  const hints = JSON.parse(
    JSON.stringify([
      {
        id: 'complete',
        session_id: 's',
        task_id: 't',
        teaching_scene: { label: '完整讲解', beats },
      },
    ]),
  );
  const draft = latestTeachingDraft(hints, 's', 't');
  assert.equal(draft.beats.length, 48);
  assert.deepEqual(draft.beats, beats);
  assert.equal(draft.beats.at(-1).step_id, 24);
  assert.equal(draft.beats.at(-1).step_end, true);
  assert.equal(draft.beats.at(-1).page[0].anchor_text, '条件47');
  const restoredSteps = teachingSteps(draft.beats);
  assert.equal(
    restoredSteps.map((step) => step.speech).join(''),
    beats.map((beat) => beat.speech).join(''),
  );
  assert.equal(restoredSteps.at(-1).sourceBeatIndex, 47);
  assert.equal(restoredSteps.at(-1).step_title, '第24步');
  assert.equal(restoredSteps.at(-1).step_end, true);
  assert.equal(latestTeachingDraft(hints, 's', 'another-task'), null);
});
