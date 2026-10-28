import assert from 'node:assert/strict';
import test from 'node:test';
import { scratchLayout, teachingPaperFrame, teachingPaperLayout } from '../src/teaching-layout.js';

const question = { aspect: 960 / 1280, box: [0.039, 0.009, 0.845, 0.218] };

test('a horizontal question on a portrait photograph requests full reading width', () => {
  assert.equal(teachingPaperLayout(question), 'wide-question');
  const old = teachingPaperFrame({ ...question, width: 449.641, height: 811, toolbarInset: 64 });
  for (const size of [
    { width: 718, height: 540 },
    { width: 603, height: 416 },
  ]) {
    const frame = teachingPaperFrame({ ...question, ...size, toolbarInset: 64 });
    assert.ok(
      frame.width > old.width * 1.3,
      'Full width enlarges real source pixels, not only CSS',
    );
    assert.ok(Math.abs(frame.width / frame.height - question.aspect) < 1e-9);
    for (const [x, y] of [
      [question.box[0], question.box[1]],
      [question.box[0] + question.box[2], question.box[1] + question.box[3]],
    ]) {
      assert.ok(frame.left + x * frame.width >= 0);
      assert.ok(frame.left + x * frame.width <= size.width);
      assert.ok(frame.top + y * frame.height >= 64);
      assert.ok(frame.top + y * frame.height <= size.height);
    }
  }
});

test('a tall question and whole-page view retain their page arrangement', () => {
  assert.equal(teachingPaperLayout({ aspect: 0.75, box: [0.15, 0.1, 0.6, 0.7] }), 'page');
  assert.equal(teachingPaperLayout({ ...question, fullPage: true }), 'page');
  assert.equal(
    teachingPaperLayout(question),
    'wide-question',
    'Returning to the question is reversible',
  );
});

test('later and future marks cannot change a frozen question arrangement', () => {
  for (const marks of [
    [],
    [{ kind: 'circle', box: [0.77, 0.08, 0.08, 0.02] }],
    [{ kind: 'note', box: [0.1, 0.8, 0.7, 0.1] }],
  ]) {
    assert.equal(teachingPaperLayout({ ...question, marks }), 'wide-question');
  }
});

test('the larger paper preserves a single source-to-ink transform', () => {
  const mark = { kind: 'circle', box: [0.768331, 0.081429, 0.074262, 0.020296] };
  const frame = teachingPaperFrame({
    ...question,
    width: 718,
    height: 540,
    toolbarInset: 64,
    marks: [mark],
  });
  const imageCenter = [
    frame.left + (mark.box[0] + mark.box[2] / 2) * frame.width,
    frame.top + (mark.box[1] + mark.box[3] / 2) * frame.height,
  ];
  const [x, y, w, h] = frame.region;
  const windowCenter = [
    ((mark.box[0] + mark.box[2] / 2 - x) / w) * frame.viewport.width,
    frame.viewport.top + ((mark.box[1] + mark.box[3] / 2 - y) / h) * frame.viewport.height,
  ];
  assert.ok(Math.abs(imageCenter[0] - windowCenter[0]) < 1e-9);
  assert.ok(Math.abs(imageCenter[1] - windowCenter[1]) < 1e-9);
});

test('missing and malformed targets cannot silently enter a focused reading layout', () => {
  for (const box of [null, [], [0, 0, 1], [0, 0, 0, 0.2], [0, 0, 0.2, -1], [0, 0, NaN, 1]]) {
    assert.equal(teachingPaperLayout({ aspect: 0.75, box }), 'page');
  }
  assert.equal(teachingPaperLayout({ ...question, aspect: Infinity }), 'page');
});

test('a text-only equation uses its real height without consuming a whole diagram panel', () => {
  const text = { kind: 'text', role: 'label', x: 4, y: 60, text: '1 + 1 + 5' };
  const label = scratchLayout([{ scratch: [text] }], 320);
  assert.ok(label.height >= 48 && label.height < 100);
  assert.equal(label.diagram[0].text, text.text);
  const figure = scratchLayout(
    [{ scratch: [text, { kind: 'rect', x: 5, y: 20, w: 30, h: 20 }] }],
    320,
  );
  assert.ok(figure.height >= 180, 'Real geometry retains its existing readable drawing space');
  assert.ok(figure.diagram.some((element) => element.kind === 'rect'));
});
