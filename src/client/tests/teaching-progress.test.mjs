import assert from 'node:assert/strict';
import test from 'node:test';
import { logicalTeachingSteps, teachingStepIndex } from '../src/teaching-progress.js';

test('live clips and cold restored model beats share one logical progress scale', () => {
  const saved = [
    { step_id: 2, step_title: '观察' },
    { step_id: 2 },
    { step_id: 8, step_title: '检查' },
  ];
  const live = saved.flatMap((beat) => [
    { ...beat, step_end: true },
    { ...beat, step_end: false },
  ]);
  assert.equal(logicalTeachingSteps(saved).length, 2);
  assert.equal(logicalTeachingSteps(live).length, 2);
  const groups = logicalTeachingSteps(live);
  assert.equal(teachingStepIndex(groups, 2), 0);
  assert.equal(teachingStepIndex(groups, 5), 1);
  assert.equal(groups[0].end, 3);
  assert.equal(groups[1].title, '检查');
});
test('legacy split fragments use their source step and reviewing a step never includes a future one', () => {
  const groups = logicalTeachingSteps([
    { sourceBeatIndex: 0 },
    { sourceBeatIndex: 0 },
    { sourceBeatIndex: 1 },
  ]);
  assert.deepEqual(
    groups.map(({ start, end }) => [start, end]),
    [
      [0, 1],
      [2, 2],
    ],
  );
  assert.equal(teachingStepIndex(groups, 1), 0);
  assert.equal(logicalTeachingSteps([{}, {}]).length, 2);
});
