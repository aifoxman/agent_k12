import assert from 'node:assert/strict';
import test from 'node:test';
import { teachingPaperPresentation } from '../src/teaching-layout.js';

const visiblePaper = { paperVisible: true };
const prose = (text) => ({ kind: 'text', role: 'prose', x: 3, y: 3, text });
const label = (text) => ({ kind: 'text', role: 'label', x: 8, y: 15, text });
const source = { kind: 'circle', anchor_id: 'source-a', box: [0.2, 0.3, 0.1, 0.1] };
const operation = {
  kind: 'note',
  anchor_id: 'source-a',
  box: [0.2, 0.3, 0.1, 0.1],
  text: '左侧2个搬入缺口底层',
};
const diagram = () => [
  label('缺口局部示意（不是整堆图）'),
  label('来源：左侧2个'),
  label('目标：缺口底层'),
  { kind: 'arrow', x1: 12, y1: 35, x2: 45, y2: 35 },
  { kind: 'rect', x: 50, y: 30, w: 12, h: 10 },
];
const lesson = (speech, extra = []) => [
  {
    step_id: 2,
    speech,
    page: [structuredClone(source), structuredClone(operation)],
    scratch: [...diagram(), ...extra],
  },
];

function freezeTree(value) {
  if (value && typeof value === 'object') {
    for (const entry of Object.values(value)) freezeTree(entry);
    Object.freeze(value);
  }
  return value;
}

test('visible original operations can replace only a pure display reference without changing the raw lesson', () => {
  const speech =
    '左侧2个搬入缺口底层。草稿只画缺口局部示意：把这2个放入底层一前一后的两个空位，总数仍是24个。';
  const proof = prose('总数仍是24个，至少搬24−20＝4个。');
  const raw = lesson(speech, [proof]);
  const snapshot = structuredClone(raw);
  freezeTree(raw);

  const presented = teachingPaperPresentation(raw, visiblePaper);
  assert.equal(
    presented[0].speech,
    '左侧2个搬入缺口底层。搬移步骤见原题和解题过程：把这2个放入底层一前一后的两个空位，总数仍是24个。',
  );
  assert.deepEqual(presented[0].scratch, [proof]);
  assert.deepEqual(presented[0].page, snapshot[0].page);
  assert.deepEqual(presented[0].speech.match(/\d+/gu), speech.match(/\d+/gu));
  assert.deepEqual(raw, snapshot);
  assert.deepEqual(teachingPaperPresentation(presented, visiblePaper), presented);
});

test('default and explicitly invisible paper retain both the diagram and the exact spoken wording', () => {
  const raw = lesson('草稿只画缺口局部示意：把左侧2个搬入缺口底层。');
  assert.equal(teachingPaperPresentation(raw), raw);
  assert.equal(teachingPaperPresentation(raw, {}), raw);
  assert.equal(teachingPaperPresentation(raw, { paperVisible: false }), raw);
});

test('a previously presented view cannot mutate a later cold review of the saved scene', () => {
  const raw = lesson('先看草稿的局部示意图。左侧2个搬入缺口底层。');
  const presented = teachingPaperPresentation(raw, visiblePaper);
  assert.notDeepEqual(presented[0].scratch, raw[0].scratch);
  const cold = teachingPaperPresentation(raw, { paperVisible: false });
  assert.equal(cold, raw);
  assert.match(cold[0].speech, /草稿/u);
  assert.deepEqual(cold[0].scratch, diagram());
});

test('numbers, movement actions, and proof claims inside a visual reference must not be silently removed', () => {
  for (const speech of [
    '草稿只画2层局部示意：比较高度。',
    '草稿只画两层局部示意：比较高度。',
    '草稿显示零个空位的局部示意：检查是否填满。',
    '草稿只画把左边搬入右边的局部示意：接着检查。',
    '草稿展示至少保留20个的局部示意：比较所有摆法。',
  ]) {
    const raw = lesson(speech);
    assert.equal(teachingPaperPresentation(raw, visiblePaper), raw, speech);
  }
});

test('unresolved legends and geometric explanations keep their original figure and speech', () => {
  for (const speech of [
    '草稿只画局部示意，虚线表示底层，实线表示上层：请看清对应关系。',
    '草稿只画局部示意，红色表示原位置，蓝色表示目标位置：请比较。',
    '草稿只画局部示意，阴影代表原有方块，空白代表新增方块：请对照。',
    '草稿显示等高关系的局部示意：先解释对应关系。',
    '看草稿里的箭头方向，下面表示另一种摆法。',
    '看草稿中的比较表格，再解释为什么不能更少。',
  ]) {
    const raw = lesson(speech);
    assert.equal(teachingPaperPresentation(raw, visiblePaper), raw, speech);
  }
});

test('future same-step notes cannot remove a diagram before the operation is visible', () => {
  const raw = [
    { step_id: 1, speech: '找到左侧2个。', page: [source], scratch: [] },
    { step_id: 2, speech: '草稿只画缺口局部示意：', page: [], scratch: diagram() },
    { step_id: 2, speech: '左侧2个搬入缺口底层。', page: [operation], scratch: [] },
    { step_id: 3, speech: '检查每个位置。', page: [], scratch: [prose('证明必须保留。')] },
  ];
  const earlier = raw.slice(0, 2);
  assert.equal(teachingPaperPresentation(earlier, visiblePaper), earlier);
  const later = teachingPaperPresentation(raw, visiblePaper);
  assert.deepEqual(later[1].scratch, []);
  assert.deepEqual(later[3].scratch, raw[3].scratch);
  assert.equal(teachingPaperPresentation(earlier, visiblePaper), earlier);
});

test('a note from another step or anchor is not evidence that the current sketch is redundant', () => {
  const raw = lesson('草稿只画缺口局部示意：');
  raw[0].page = [source];
  raw.push({ step_id: 3, speech: '左侧2个搬入缺口底层。', page: [operation], scratch: [] });
  assert.equal(teachingPaperPresentation(raw, visiblePaper), raw);
  const wrongAnchor = lesson('草稿只画缺口局部示意：');
  wrongAnchor[0].page[1].anchor_id = 'source-b';
  assert.equal(teachingPaperPresentation(wrongAnchor, visiblePaper), wrongAnchor);
});

test('one covered pair cannot remove a second source, a missing destination, or genuine geometry', () => {
  for (const extra of [
    [label('来源：右侧2个'), label('目标：缺口上层')],
    [label('目标：另一层空位')],
    [{ kind: 'line', x1: 0, y1: 0, x2: 50, y2: 50 }],
    [label('面积比较图')],
    [label('等高关系示意')],
    [prose('图示中的箭头比较两个高度，不能删除。')],
  ]) {
    const raw = lesson('草稿只画缺口局部示意：', extra);
    assert.equal(teachingPaperPresentation(raw, visiblePaper), raw);
  }
});
