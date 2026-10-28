import assert from 'node:assert/strict';
import test from 'node:test';
import { splitTeachingBeat, splitTeachingSpeech, teachingSteps } from '../src/teaching-steps.js';

test('an operation and its reason remain a semantic passage; a later condition gets its own ink onset', () => {
  const beat = {
    speech:
      '先看左边的面积36。它和右边有同样长的边，所以面积的倍数也能比较另一条边。再看右上面积12。现在能把两处条件联系起来。',
    page: [
      { kind: 'circle', text: '36' },
      { kind: 'circle', text: '12' },
    ],
  };
  const steps = splitTeachingBeat(beat);
  assert.equal(steps.map((step) => step.speech).join(''), beat.speech);
  assert.equal(steps.length, 2);
  assert.ok(steps[0].speech.includes('所以'));
  assert.deepEqual(
    steps.map((step) => step.page[0].text),
    ['36', '12'],
  );
  assert.equal(steps[0].step_end, false);
});

test('a complete local sketch appears with its drawing explanation without a 28-character delay', () => {
  const scratch = [
    { kind: 'rect', x: 10, y: 30, w: 20, h: 20 },
    { kind: 'arrow', x1: 30, y1: 40, x2: 60, y2: 40 },
  ];
  const steps = splitTeachingBeat({
    speech: '看这个局部示意图，一块突出的积木，搬一次就能填一个空位。',
    scratch,
  });
  assert.equal(steps.length, 1);
  assert.deepEqual(steps[0].scratch, scratch);
});

test('ordinary semantic explanations stay intact while long legacy text remains lossless', () => {
  const speech =
    '你要我完整示范。题目求小正方形的面积；图中左边是36，右上是12，右下写着“正方形”和“？”。我们先用这两块已知面积找边长的关系。';
  const parts = splitTeachingSpeech(speech);
  assert.deepEqual(parts, [speech]);
  assert.equal(parts.join(''), speech);
  const long = splitTeachingSpeech(speech.repeat(5));
  assert.ok(long.length > 1);
  assert.equal(long.join(''), speech.repeat(5));
  assert.ok(long.every((part) => [...part].length <= 160));
  assert.ok(parts.every((part) => part.length > 0));
});

test('decimal measurements, labels and calculations are not torn across speech chunks', () => {
  const speech = '先比较两个长方形的面积，再算36÷12＝3，然后检查12.5cm与AB是否对应。';
  const parts = splitTeachingSpeech(speech, 20);
  assert.equal(parts.join(''), speech);
  for (const token of ['36÷12＝3', '12.5cm', 'AB'])
    assert.ok(parts.some((part) => part.includes(token)));
  const boundary = splitTeachingSpeech('先把已知条件对应清楚36÷12＝3再说出理由。', 16);
  assert.ok(boundary.some((part) => part.includes('36÷12＝3')));
});

test('word circles appear with their own spoken condition rather than all at the beginning', () => {
  const beat = {
    speech: '先看左边的面积是36。接着再看右上面积是12。最后自己指出小正方形在哪里。',
    page: [
      { kind: 'circle', text: '36', anchor_id: 'a1', box: [0.1, 0.1, 0.1, 0.1] },
      { kind: 'circle', text: '12', anchor_id: 'a2', box: [0.3, 0.1, 0.1, 0.1] },
    ],
  };
  const original = structuredClone(beat);
  const steps = splitTeachingBeat(beat, { maxChars: 20 });
  assert.equal(steps.flatMap((step) => step.page).length, 2);
  for (const step of steps)
    for (const mark of step.page) assert.ok(step.speech.includes(mark.text));
  assert.ok(
    steps.findIndex((step) => step.page.some((mark) => mark.text === '36')) <
      steps.findIndex((step) => step.page.some((mark) => mark.text === '12')),
  );
  assert.ok(
    steps.flatMap((step) => step.page_cues).every((cue) => cue.fraction >= 0 && cue.fraction < 1),
  );
  assert.deepEqual(beat, original);
});

test('formula notes match spoken operators and unmatched results wait until the last fragment', () => {
  const steps = splitTeachingBeat(
    {
      speech: '先找这两块共同的边长。它们的面积36除以12等于3。最后再说说自己的检查。',
      page: [
        { kind: 'note', text: '36÷12＝3', anchor_id: 'a1', box: [0.1, 0.1, 0.1, 0.1] },
        { kind: 'note', text: '小正方形面积＝6', anchor_id: 'a2', box: [0.3, 0.1, 0.1, 0.1] },
      ],
    },
    { maxChars: 20 },
  );
  assert.ok(
    steps
      .find((step) => step.page.some((mark) => mark.text === '36÷12＝3'))
      .speech.includes('36除以12等于3'),
  );
  assert.equal(steps.at(-1).page.at(-1).text, '小正方形面积＝6');
  assert.equal(steps[0].page.length, 0);
});

test('numeric and Latin anchor tokens do not match larger unrelated tokens', () => {
  const steps = splitTeachingBeat(
    {
      speech: '先看这里写着112和AREA。接着再看这里的12与A。最后请自己确认对应的位置。',
      page: [
        { kind: 'circle', text: '12', box: [0.1, 0.1, 0.1, 0.1] },
        { kind: 'label', text: 'A', box: [0.2, 0.1, 0.1, 0.1] },
      ],
    },
    { maxChars: 20 },
  );
  assert.equal(steps[0].page.length, 0);
  assert.ok(
    steps.find((step) => step.page.some((mark) => mark.text === '12')).speech.includes('12与A'),
  );
});

test('Chinese spoken cues place Arabic condition circles and calculation notes with the correct clause', () => {
  const steps = splitTeachingBeat(
    {
      speech: '先看左侧的三十六，它表示面积。接着计算十二除以二等于六。最后再独立检查一遍。',
      page: [
        { kind: 'circle', text: '36', anchor_text: '三十六', anchor_id: 'condition' },
        { kind: 'note', text: '12÷2＝6', anchor_text: '十二除以二等于六', anchor_id: 'operation' },
      ],
    },
    { maxChars: 20 },
  );
  const condition = steps.findIndex((step) =>
    step.page.some((mark) => mark.anchor_id === 'condition'),
  );
  const operation = steps.findIndex((step) =>
    step.page.some((mark) => mark.anchor_id === 'operation'),
  );
  assert.ok(steps[condition].speech.includes('三十六'));
  assert.ok(steps[operation].speech.includes('十二除以二等于六'));
  assert.ok(operation > condition);
  assert.equal(
    steps[condition].page.some((mark) => mark.kind === 'note'),
    false,
  );
  assert.ok(steps[operation].page_cues[0].charOffset > 0);
});

test('an actual spoken cue wins over an earlier literal and missing cues still use safe text or last-clause fallback', () => {
  const steps = splitTeachingBeat(
    {
      speech: '先记住数字6，暂时不要计算。现在计算十二除以二等于六。最后才检查自己的结果。',
      page: [
        { kind: 'note', text: '6', anchor_text: '十二除以二等于六', anchor_id: 'spoken-result' },
        { kind: 'circle', text: '6', anchor_text: '没有说出的词', anchor_id: 'literal-condition' },
        {
          kind: 'note',
          text: '未知面积＝18',
          anchor_text: '没有说出的算式',
          anchor_id: 'unknown-result',
        },
      ],
    },
    { maxChars: 20 },
  );
  assert.ok(steps[0].page.some((mark) => mark.anchor_id === 'literal-condition'));
  assert.equal(
    steps[0].page.some((mark) => mark.anchor_id === 'spoken-result'),
    false,
  );
  assert.ok(
    steps
      .find((step) => step.page.some((mark) => mark.anchor_id === 'spoken-result'))
      .speech.includes('十二除以二等于六'),
  );
  assert.ok(steps.at(-1).page.some((mark) => mark.anchor_id === 'unknown-result'));
  assert.ok(
    steps
      .slice(0, -1)
      .every((step) => step.page.every((mark) => mark.anchor_id !== 'unknown-result')),
  );
});

test('geometry follows a spoken drawing cue and otherwise waits without inventing an explanation', () => {
  const line = { kind: 'line', x1: 0.1, y1: 0.2, x2: 0.7, y2: 0.2 };
  const steps = splitTeachingBeat(
    { speech: '先读题目现在问什么。然后看原图上边与左边。最后尝试说出相同之处。', page: [line] },
    { maxChars: 20 },
  );
  assert.equal(steps[0].page.length, 0);
  assert.ok(steps.find((step) => step.page.length).speech.includes('边'));
  const unknown = splitTeachingBeat(
    { speech: '先读题目现在问什么。然后认真思考一下关系。最后尝试说出自己的想法。', page: [line] },
    { maxChars: 20 },
  );
  assert.equal(unknown.at(-1).page.length, 1);
  assert.ok(unknown.slice(0, -1).every((step) => step.page.length === 0));
});

test('scratch prose matches its phrase while a diagram remains coherent and future results stay late', () => {
  const scratch = [
    { kind: 'text', role: 'prose', x: 5, y: 8, text: '先找共同的边。' },
    { kind: 'rect', x: 30, y: 20, w: 20, h: 40 },
    { kind: 'text', role: 'label', x: 55, y: 30, text: '一份' },
    { kind: 'text', role: 'prose', x: 5, y: 80, text: '最后结果为6。' },
  ];
  const steps = splitTeachingBeat(
    { speech: '先找共同的边。再比较两块面积的倍数。然后试着说出边长关系。', scratch },
    { maxChars: 20 },
  );
  assert.equal(steps[0].scratch[0].text, '先找共同的边。');
  assert.deepEqual(steps.at(-1).scratch, scratch.slice(1));
  assert.equal(steps.flatMap((step) => step.scratch).length, scratch.length);
});

test('logical-step metadata survives splitting without making subclauses manual steps', () => {
  const beat = {
    id: 'source-42',
    step_id: 7,
    step_title: '比较面积',
    step_end: false,
    speech: '先读这两块面积之间的关系。然后说出自己观察到的相同之处。',
  };
  const steps = splitTeachingBeat(beat, { sourceBeatIndex: 4, maxChars: 20 });
  assert.ok(
    steps.every(
      (step) =>
        step.sourceBeatId === 'source-42' &&
        step.sourceBeatIndex === 4 &&
        step.step_id === 7 &&
        step.step_title === '比较面积' &&
        step.step_end === false,
    ),
  );
  const complete = splitTeachingBeat({ ...beat, step_end: undefined }, { maxChars: 20 });
  assert.ok(complete.slice(0, -1).every((step) => step.step_end === false));
  assert.equal(complete.at(-1).step_end, true);
  const combined = teachingSteps([beat, beat]);
  assert.equal(new Set(combined.map((step) => step.id)).size, combined.length);
  assert.deepEqual([...new Set(combined.map((step) => step.sourceBeatIndex))], [0, 1]);
});
