import assert from 'node:assert/strict';
import test from 'node:test';
import { scratchWithPaperOperations, teachingPaperPresentation } from '../src/teaching-layout.js';

const prose = (text) => ({ kind: 'text', role: 'prose', x: 3, y: 3, text });
const label = (text) => ({ kind: 'text', role: 'label', x: 5, y: 20, text });
const sketch = [
  label('搬移示意'),
  label('来源：左端两块'),
  label('目标：上方空位'),
  { kind: 'arrow', x1: 10, y1: 20, x2: 40, y2: 20 },
  { kind: 'rect', x: 45, y: 15, w: 10, h: 10 },
];
const circle = { kind: 'circle', anchor_id: 'source-left', box: [0.1, 0.2, 0.1, 0.1] };
const note = {
  kind: 'note',
  anchor_id: 'source-left',
  box: [0.1, 0.2, 0.1, 0.1],
  text: '左端两块搬入上方空位',
};

test('explicit duplicate movement sketches disappear while every mathematical prose item and raw scene remain intact', () => {
  const beats = [
    { step_id: 1, page: [circle], scratch: [prose('比较保留的数量')] },
    { step_id: 2, page: [note], scratch: [...sketch, prose('搬移后总数不变')] },
    { step_id: 3, page: [], scratch: [prose('比较所有方案，得到下界')] },
  ];
  const original = structuredClone(beats),
    visible = scratchWithPaperOperations(beats, { paperVisible: true });
  assert.deepEqual(visible[1].scratch, [prose('搬移后总数不变')]);
  assert.deepEqual(
    visible.flatMap((b) => b.scratch).filter((e) => e.role === 'prose'),
    original.flatMap((b) => b.scratch).filter((e) => e.role === 'prose'),
  );
  assert.deepEqual(beats, original);
  assert.deepEqual(scratchWithPaperOperations(visible, { paperVisible: true }), visible);
});

test('an uncovered source or target and mismatched original-page anchor preserve the complete diagram', () => {
  for (const update of [
    { ...note, text: '左端两块搬入下方空位' },
    { ...note, anchor_id: 'another-source' },
    { ...note, text: '别处两块搬入上方空位' },
  ]) {
    const beats = [{ step_id: 2, page: [circle, update], scratch: sketch }];
    assert.equal(scratchWithPaperOperations(beats, { paperVisible: true }), beats);
  }
  const beats = [
    {
      step_id: 2,
      page: [circle, note],
      scratch: [...sketch, label('来源：右端两块'), label('目标：下方空位')],
    },
  ];
  assert.equal(
    scratchWithPaperOperations(beats, { paperVisible: true }),
    beats,
    'One covered source cannot remove the other source and target',
  );
});

test('proof retains the suppression but review before the operation note cannot use future coverage', () => {
  const beats = [
    { step_id: 1, page: [circle], scratch: [] },
    { step_id: 2, page: [], scratch: sketch },
    { step_id: 2, page: [note], scratch: [] },
    { step_id: 3, page: [], scratch: [prose('检查与证明')] },
  ];
  assert.deepEqual(
    scratchWithPaperOperations(beats.slice(0, 2), { paperVisible: true })[1].scratch,
    sketch,
  );
  assert.deepEqual(
    scratchWithPaperOperations(beats.slice(0, 3), { paperVisible: true })[1].scratch,
    [],
  );
  assert.deepEqual(scratchWithPaperOperations(beats, { paperVisible: true })[1].scratch, []);
  assert.deepEqual(
    scratchWithPaperOperations(beats.slice(0, 2), { paperVisible: true })[1].scratch,
    sketch,
  );
});

test('genuine geometry, comparison figures and unknown labels never become movement duplicates', () => {
  for (const extra of [
    { kind: 'line', x1: 5, y1: 5, x2: 50, y2: 5 },
    label('面积比较'),
    label('4厘米'),
    { kind: 'text', x: 5, y: 5, text: '需要保留的解释' },
  ]) {
    const beats = [{ step_id: 2, page: [circle, note], scratch: [...sketch, extra] }];
    assert.equal(scratchWithPaperOperations(beats, { paperVisible: true }), beats);
  }
  const beats = [
    {
      step_id: 2,
      page: [circle, note],
      scratch: sketch.filter((e) => e.text !== '来源：左端两块'),
    },
  ];
  assert.equal(scratchWithPaperOperations(beats, { paperVisible: true }), beats);
});

test('the saved 823-character lesson visual instruction points at the paper without changing the operation or its numbers', () => {
  const speech =
    '看左侧伸出的小正方体部分，一共有2个。左侧2个搬入缺口底层。草稿只画缺口的局部示意，上面表示后面，下面表示前面：把这2个放入底层一前一后的两个空位，先给上层准备好支撑。';
  const beats = [{ step_id: 2, speech, page: [circle, note], scratch: sketch }];
  const presented = teachingPaperPresentation(beats, { paperVisible: true })[0];
  assert.equal(
    presented.speech,
    speech.replace(
      '草稿只画缺口的局部示意，上面表示后面，下面表示前面：',
      '搬移步骤见原题和解题过程：',
    ),
  );
  assert.deepEqual(presented.speech.match(/\d+/gu), speech.match(/\d+/gu));
  assert.ok(presented.speech.includes('把这2个放入底层一前一后的两个空位，先给上层准备好支撑。'));
  assert.deepEqual(presented.scratch, []);
  assert.equal(beats[0].speech, speech);
  const uncovered = [{ ...beats[0], page: [circle] }];
  assert.equal(teachingPaperPresentation(uncovered, { paperVisible: true }), uncovered);
});

test('unresolved visual references and mathematical prose retain their figure and original speech', () => {
  for (const speech of [
    '草稿只画3格局部示意：比较面积。',
    '看草稿箭头，至少搬3个。',
    '看草稿的列数，连续3列最多20个。',
  ]) {
    const beats = [
      { step_id: 2, speech, page: [circle, note], scratch: [...sketch, prose('搬移后数量不变')] },
    ];
    assert.equal(teachingPaperPresentation(beats, { paperVisible: true }), beats);
  }
  const explanation = prose('箭头表示两种情况相同，不是搬移。');
  const beats = [
    {
      step_id: 2,
      speech: '比较这两种情况。',
      page: [circle, note],
      scratch: [...sketch, explanation],
    },
  ];
  assert.equal(teachingPaperPresentation(beats, { paperVisible: true }), beats);
});
