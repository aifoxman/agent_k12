import assert from 'node:assert/strict';
import test from 'node:test';
import {
  chooseScratchPlacement,
  currentPageMarks,
  markBounds,
  overlapArea,
  pageMarkPath,
  placePageLabels,
  scratchLayout,
  scratchLiveContent,
  scratchPlaybackStep,
  scratchScrollFocus,
  scratchDiagramFocusBeats,
  scratchSnapshots,
  teachingBeatDuration,
  teachingElementDelay,
  teachingPaperFrame,
  teachingProcessReserve,
} from '../src/teaching-layout.js';

test('later reasoning keeps the latest complete semantic figure while history and future stay separate', () => {
  const prose = (text) => ({ kind: 'text', role: 'prose', text, x: 5, y: 8 });
  const beats = [
    { step_id: 1, scratch: [prose('旧思路'), { kind: 'rect', x: 3, y: 80, w: 5, h: 5 }] },
    {
      step_id: 3,
      scratch: [
        { kind: 'rect', x: 10, y: 20, w: 20, h: 20 },
        { kind: 'arrow', x1: 30, y1: 30, x2: 45, y2: 30 },
      ],
    },
    {
      step_id: 3,
      scratch: [
        { kind: 'rect', x: 60, y: 20, w: 20, h: 20 },
        { kind: 'text', role: 'label', text: '补入空位', x: 60, y: 55 },
      ],
    },
    { step_id: 4, scratch: [prose('每块只搬一次，因此不需要反复移动。')] },
    { step_id: 5, scratch: [] },
    { step_id: 6, scratch: [{ kind: 'rect', x: 85, y: 70, w: 8, h: 8 }] },
  ];
  const live = scratchLiveContent(beats, 4, 320);
  assert.deepEqual([...new Set(live.diagram.map((e) => e.beatIndex))], [1, 2]);
  assert.deepEqual(
    live.prose.map((e) => e.text),
    ['每块只搬一次，因此不需要反复移动。'],
  );
  assert.ok(scratchLayout(beats.slice(0, 5), 320).prose.some((e) => e.text === '旧思路'));
  assert.equal(
    live.diagram.some((e) => e.beatIndex === 5),
    false,
  );
  const next = scratchLiveContent(beats, 5, 320);
  assert.ok(next.diagram.every((e) => e.beatIndex === 5));
});

test('question zoom retains its text margin and related marks with one shared image/ink mapping', () => {
  for (const size of [
    { width: 760, height: 560 },
    { width: 600, height: 360 },
  ]) {
    const box = [0.15, 0.62, 0.28, 0.23];
    const mark = { kind: 'line', x1: 0.12, y1: 0.68, x2: 0.52, y2: 0.75 };
    const frame = teachingPaperFrame({ ...size, aspect: 16 / 9, box, marks: [mark] });
    assert.ok(frame.region[0] <= 0.02 + 1e-9);
    assert.ok(frame.region[1] <= 0.5);
    assert.ok(frame.region[0] + frame.region[2] >= 0.62 - 1e-9);
    for (const [x, y] of [
      [box[0], box[1]],
      [box[0] + box[2], box[1] + box[3]],
      [mark.x2, mark.y2],
    ]) {
      const px = frame.left + x * frame.width;
      const py = frame.top + y * frame.height;
      assert.ok(px >= 0 && px <= size.width);
      assert.ok(py >= 0 && py <= size.height);
    }
    assert.ok(Math.abs(frame.width / frame.height - 16 / 9) < 1e-9);
    const whole = teachingPaperFrame({ ...size, aspect: 16 / 9, box, fullPage: true });
    assert.deepEqual(whole.region, [0, 0, 1, 1]);
    assert.ok(whole.left >= 0 && whole.top >= 0);
  }
});

test('a wide question in a tall portrait column starts below the toolbar without a centered void', () => {
  const frame = teachingPaperFrame({
    width: 449.641,
    height: 811.692,
    aspect: 960 / 1280,
    box: [0.09, 0.06, 0.78, 0.29],
    toolbarInset: 64,
  });
  assert.equal(frame.top + frame.region[1] * frame.height, 64);
  assert.equal(frame.viewport.top, 64);
  assert.ok(frame.viewport.height < 300);
  assert.ok(Math.abs(frame.width / frame.height - 0.75) < 1e-9);
  assert.ok(
    Math.abs(frame.viewport.height - frame.region[3] * frame.height) < 1e-9,
    'The question window clips later questions rather than filling the column with them',
  );
});

test('top-aligned question crops retain all four target corners and related ink in every pane shape', () => {
  for (const size of [
    { width: 322, height: 810 },
    { width: 450, height: 812 },
    { width: 850, height: 330 },
  ]) {
    for (const box of [
      [0.09, 0.06, 0.78, 0.29],
      [0.18, 0.45, 0.58, 0.2],
      [0.12, 0.72, 0.72, 0.23],
    ]) {
      const mark = {
        kind: 'arrow',
        x1: box[0],
        y1: box[1],
        x2: box[0] + box[2],
        y2: box[1] + box[3],
      };
      const frame = teachingPaperFrame({
        ...size,
        aspect: 0.75,
        box,
        marks: [mark],
        toolbarInset: 70,
      });
      const crop = frame.viewport;
      for (const [u, v] of [
        [box[0], box[1]],
        [box[0] + box[2], box[1]],
        [box[0], box[1] + box[3]],
        [box[0] + box[2], box[1] + box[3]],
      ]) {
        const x = frame.left + u * frame.width;
        const y = frame.top + v * frame.height;
        assert.ok(x >= -1e-9 && x <= crop.width + 1e-9);
        assert.ok(y >= crop.top - 1e-9 && y <= crop.top + crop.height + 1e-9);
      }
      assert.ok(Math.abs(frame.top + frame.region[1] * frame.height - 70) < 1e-9);
      assert.ok(crop.top + crop.height <= size.height + 1e-9);
    }
  }
});

test('whole-page mode reversibly contains every source corner below its toolbar', () => {
  const props = {
    width: 450,
    height: 812,
    aspect: 0.75,
    box: [0.12, 0.72, 0.72, 0.23],
    toolbarInset: 64,
  };
  const question = teachingPaperFrame(props);
  const whole = teachingPaperFrame({ ...props, fullPage: true });
  assert.deepEqual(whole.region, [0, 0, 1, 1]);
  for (const [u, v] of [
    [0, 0],
    [0, 1],
    [1, 0],
    [1, 1],
  ]) {
    assert.ok(whole.left + u * whole.width >= 0);
    assert.ok(whole.left + u * whole.width <= props.width);
    assert.ok(whole.top + v * whole.height >= props.toolbarInset);
    assert.ok(whole.top + v * whole.height <= props.height);
  }
  assert.deepEqual(teachingPaperFrame(props), question);
});

test('measured multiline process text reserves a separate row while the complete paper stays visible', () => {
  for (const height of [812, 330]) {
    for (const metrics of [
      { summaryHeight: 44, contentHeight: 72.641 },
      { summaryHeight: 56, contentHeight: 240 },
    ]) {
      const reserve = teachingProcessReserve({ height, ...metrics, expanded: true });
      const frame = teachingPaperFrame({
        width: 450,
        height: height - reserve,
        aspect: 0.75,
        fullPage: true,
        toolbarInset: 64,
      });
      assert.ok(frame.height >= 80, 'Even a short pane retains a visible, complete image');
      assert.ok(frame.top + frame.height + reserve <= height + 1e-9);
      assert.ok(reserve >= metrics.summaryHeight + 20);
      assert.ok(reserve <= Math.max(metrics.summaryHeight + 2, Math.min(280, height * 0.4)) + 20);
      const collapsed = teachingProcessReserve({ height, ...metrics, expanded: false });
      assert.equal(collapsed, metrics.summaryHeight + 22);
      assert.ok(collapsed <= reserve);
    }
  }
});

test('a saved portrait page keeps its true aspect and annotation coordinates in either screen orientation', () => {
  const box = [0.18, 0.25, 0.62, 0.5];
  const mark = { kind: 'circle', box: [0.24, 0.3, 0.1, 0.08] };
  for (const size of [
    { width: 322, height: 610 },
    { width: 500, height: 900 },
    { width: 980, height: 550 },
  ]) {
    for (const fullPage of [false, true]) {
      const frame = teachingPaperFrame({ ...size, aspect: 9 / 16, box, marks: [mark], fullPage });
      assert.ok(Math.abs(frame.width / frame.height - 9 / 16) < 1e-9);
      const [x, y, width, height] = fullPage ? [0, 0, 1, 1] : box;
      for (const [u, v] of [
        [x, y],
        [x + width, y + height],
      ]) {
        assert.ok(frame.left + u * frame.width >= -1e-9);
        assert.ok(frame.left + u * frame.width <= size.width + 1e-9);
        assert.ok(frame.top + v * frame.height >= -1e-9);
        assert.ok(frame.top + v * frame.height <= size.height + 1e-9);
      }
    }
  }
});

test('calculations sharing one condition stay cumulative, nonoverlapping and absent before being spoken', () => {
  const operation = (text) => ({
    kind: 'note',
    anchor_id: 'same-condition',
    text,
    box: [0.35, 0.35, 0.08, 0.05],
  });
  const beats = ['36÷12＝3', '12÷2＝6', '2×6＝12'].map((text) => ({ page: [operation(text)] }));
  assert.equal(currentPageMarks(beats, 2, 0).length, 1);
  const marks = currentPageMarks([...beats, { page: [operation('12÷2＝6')] }]);
  assert.deepEqual(
    marks.map((mark) => mark.text),
    ['36÷12＝3', '2×6＝12', '12÷2＝6'],
  );
  const labels = placePageLabels(marks, 1000, 700, { targetBox: [0.3, 0.2, 0.5, 0.5] });
  assert.equal(labels.length, 3);
  for (let index = 0; index < labels.length; index++)
    for (const other of labels.slice(index + 1)) assert.equal(overlapArea(labels[index], other), 0);
});

test('zoomed paper keeps calculation notes, point labels and the condition badge in the visible region', () => {
  const visibleBox = [0.12, 0.52, 0.5, 0.42];
  const targetBox = [0.23, 0.65, 0.22, 0.18];
  const marks = [
    { kind: 'check', anchor_id: 'given', box: [0.25, 0.7, 0.03, 0.03] },
    { kind: 'label', text: 'A', box: [0.45, 0.8, 0.01, 0.01] },
    ...['36÷12＝3', '12÷2＝6', '2×6＝12'].map((text) => ({
      kind: 'note',
      anchor_id: 'given',
      text,
      box: [0.25, 0.7, 0.03, 0.03],
    })),
  ];
  const labels = placePageLabels(marks, 1600, 900, { visibleBox, targetBox });
  assert.equal(labels.length, 5);
  for (const label of labels.filter((label) => !label.docked)) {
    assert.ok(label.x >= visibleBox[0] * 1600);
    assert.ok(label.y >= visibleBox[1] * 900);
    assert.ok(label.x + label.width <= (visibleBox[0] + visibleBox[2]) * 1600);
    assert.ok(label.y + label.height <= (visibleBox[1] + visibleBox[3]) * 900);
    for (const mark of marks) {
      const [x, y, w, h] = mark.box;
      assert.equal(
        overlapArea(label, { x: x * 1600, y: y * 900, width: w * 1600, height: h * 900 }),
        0,
      );
    }
  }
  const placedNotes = labels.filter((label) => label.mark.kind === 'note' && !label.docked);
  for (let index = 0; index < placedNotes.length; index++)
    for (const other of placedNotes.slice(index + 1))
      assert.equal(overlapArea(placedNotes[index], other), 0);
});

test('a new divider focuses its complete earlier rectangle and labels instead of unrelated old diagrams', () => {
  const content = scratchLayout(
    [
      {
        scratch: [
          { kind: 'line', x1: 5, y1: 20, x2: 60, y2: 20 },
          { kind: 'text', role: 'label', x: 5, y: 12, text: '旧长度示意' },
        ],
      },
      {
        scratch: [
          { kind: 'rect', x: 6, y: 69, w: 10, h: 20 },
          { kind: 'text', role: 'label', x: 5, y: 66, text: '右上局部示意' },
          { kind: 'text', role: 'label', x: 18, y: 76, text: '高：2个小边长' },
          { kind: 'text', role: 'label', x: 4, y: 91, text: '宽：1个小边长' },
        ],
      },
      { scratch: [{ kind: 'line', x1: 6, y1: 79, x2: 16, y2: 79 }] },
      { scratch: [{ kind: 'line', x1: 6, y1: 84, x2: 16, y2: 84 }] },
    ],
    300,
  );
  assert.deepEqual(scratchDiagramFocusBeats(content, 2), [1, 2]);
  assert.deepEqual(scratchDiagramFocusBeats(content, 0), [0]);
  const focused = content.diagram.filter((element) =>
    scratchDiagramFocusBeats(content, 2).includes(element.beatIndex),
  );
  assert.ok(focused.some((element) => element.kind === 'rect'));
  assert.deepEqual(
    focused.filter((element) => element.kind === 'text').map((element) => element.text),
    ['右上局部示意', '高：2个小边长', '宽：1个小边长'],
  );
});

test('label-only increments inherit an explicitly shared logical step but do not guess an unrelated earlier figure', () => {
  const beats = [
    { step_id: 1, scratch: [{ kind: 'rect', x: 5, y: 5, w: 20, h: 20 }] },
    { step_id: 2, scratch: [{ kind: 'rect', x: 60, y: 60, w: 20, h: 20 }] },
    { step_id: 1, scratch: [{ kind: 'text', role: 'label', x: 28, y: 15, text: 'A' }] },
  ];
  assert.deepEqual(scratchDiagramFocusBeats(scratchLayout(beats), 2), [0, 2]);
  assert.deepEqual(
    scratchDiagramFocusBeats(
      scratchLayout([
        ...beats,
        { step_id: 1, scratch: [{ kind: 'rect', x: 5, y: 5, w: 10, h: 10 }] },
      ]),
      2,
    ),
    [0, 2],
  );
  assert.deepEqual(
    scratchDiagramFocusBeats(scratchLayout(beats.map(({ scratch }) => ({ scratch }))), 2),
    [2],
  );
});

test('separate diagonals with overlapping boxes do not merge while actual intersections retain their context', () => {
  const beats = [
    { scratch: [{ kind: 'line', x1: 5, y1: 5, x2: 95, y2: 95 }] },
    { scratch: [{ kind: 'line', x1: 5, y1: 85, x2: 35, y2: 55 }] },
  ];
  assert.deepEqual(scratchDiagramFocusBeats(scratchLayout(beats), 1), [1]);
  const crossed = [beats[0], { scratch: [{ kind: 'line', x1: 5, y1: 95, x2: 95, y2: 5 }] }];
  assert.deepEqual(scratchDiagramFocusBeats(scratchLayout(crossed), 1), [0, 1]);
});

test('scratch playback follows the current diagram or reason rather than the first historical paragraph', () => {
  const beats = [
    { scratch: [{ kind: 'text', role: 'prose', text: '先读已知面积。', x: 3, y: 3 }] },
    { scratch: [{ kind: 'rect', x: 10, y: 30, w: 30, h: 10 }] },
    { scratch: [{ kind: 'text', role: 'prose', text: '同样高，所以比较横边。', x: 3, y: 50 }] },
    { scratch: [] },
  ];
  const snapshots = scratchSnapshots(beats);
  assert.deepEqual(
    snapshots.map((snapshot) => scratchScrollFocus(snapshot.content, snapshot.beatIndex)),
    ['prose', 'diagram', 'prose'],
  );
  assert.ok(snapshots.at(-1).content.diagram.length > 0);
  // A following empty wait retains the latest explanation instead of creating
  // an empty focus or jumping back to the older diagram/history.
  assert.equal(snapshots.at(-1).beatIndex, 2);
  assert.equal(scratchScrollFocus(snapshots.at(-1).content, 2), 'prose');
  assert.equal(scratchScrollFocus(snapshots[1].content, 1), 'diagram');
});

test('current teaching emphasis highlights two recent locations while retaining earlier page evidence', () => {
  const beats = Array.from({ length: 6 }, (_, index) => ({
    page: [
      { kind: 'circle', anchor_id: `word-${index}`, box: [0.1, index / 10, 0.1, 0.03] },
      { kind: 'label', anchor_id: `word-${index}`, box: [0.1, index / 10, 0.1, 0.03], text: 'A' },
    ],
  }));
  const marks = currentPageMarks(beats);
  assert.deepEqual(
    [...new Set(marks.filter((mark) => mark.emphasized).map((mark) => mark.anchor_id))],
    ['word-4', 'word-5'],
  );
  assert.equal(marks.length, 12);
  assert.ok(marks.some((mark) => mark.anchor_id === 'word-0' && mark.kind === 'circle'));
  assert.deepEqual(
    marks.filter((mark) => mark.beatIndex === 5).map((mark) => mark.animationIndex),
    [0, 1],
  );
  assert.equal(teachingElementDelay(marks.at(-2).animationIndex), 0);
});

test('a repeated location uses the current beat instead of a stale first occurrence', () => {
  const mark = { kind: 'underline', anchor_id: 'total', box: [0.1, 0.2, 0.1, 0.02] };
  const marks = currentPageMarks([{ page: [mark] }, { page: [mark] }]);
  assert.equal(marks.length, 1);
  assert.equal(marks[0].beatIndex, 1);
});

test('arrows preserve actual upward and leftward endpoints without requiring a box', () => {
  const mark = { kind: 'arrow', x1: 0.8, y1: 0.7, x2: 0.2, y2: 0.1 };
  assert.deepEqual(
    markBounds(mark).map((number) => Math.round(number * 10)),
    [2, 1, 6, 6],
  );
  assert.ok(pageMarkPath(mark, 1000, 500).startsWith('M 800 350 L 200 50'));
  assert.equal(pageMarkPath({ ...mark, kind: 'line' }, 1000, 500), 'M 800 350 L 200 50');
});

test('keyword underlines use a small pixel offset and condition checks stay distinct', () => {
  const underline = pageMarkPath({ kind: 'underline', box: [0.2, 0.3, 0.1, 0.02] }, 1000, 1000);
  assert.ok(underline.startsWith('M 200 322 Q 250 323'));
  const check = { kind: 'check', box: [0.2, 0.3, 0.1, 0.02] };
  assert.ok(pageMarkPath(check, 1000, 1000).includes('h 16 v 16 h -16 Z'));
  assert.equal(placePageLabels([check], 1000, 1000)[0].text, '已找到条件');
});

test('diagram labels retain clear coordinates and move off nearby shape edges at readable size', () => {
  const result = scratchLayout(
    [
      {
        scratch: [
          { kind: 'rect', x: 10, y: 20, w: 60, h: 30 },
          { kind: 'text', x: 12, y: 18, text: 'A' },
          { kind: 'text', role: 'label', x: 65, y: 54, text: '6厘米' },
          { kind: 'text', role: 'prose', x: 0, y: 0, text: '先找相同的边。' },
        ],
      },
    ],
    240,
  );
  assert.deepEqual(
    result.diagram.slice(0, 2).map(({ x, y }) => [x, y]),
    [
      [10, 20],
      [12, 18],
    ],
  );
  const measurement = result.diagram[2];
  assert.equal(measurement.x, 65);
  assert.ok(measurement.y - result.fontSize > 50);
  assert.deepEqual(measurement.lines, ['6厘米']);
  assert.equal(result.prose.length, 1);
  assert.ok((result.fontSize * 240) / 100 >= 16);
  assert.ok(result.height >= 180);
});

test('animation duration includes every current scratch element without accumulating previous beats', () => {
  assert.equal(teachingBeatDuration({ page: [], scratch: [] }), 0);
  assert.equal(teachingBeatDuration({ scratch: Array(12).fill({ kind: 'line' }) }), 2220);
  assert.equal(
    teachingBeatDuration({ page: [{ kind: 'circle', box: [0.1, 0.2, 0.2, 0.1] }] }),
    900,
  );
});

test('labels avoid printed text and stay inside the paper at its edges', () => {
  const marks = [
    { kind: 'label', text: 'A', box: [0.94, 0.94, 0.03, 0.03] },
    { kind: 'check', box: [0.1, 0.3, 0.2, 0.04] },
  ];
  const labels = placePageLabels(marks, 800, 600);
  for (const label of labels) {
    assert.ok(label.x >= 0 && label.y >= 0);
    assert.ok(label.x + label.width <= 800 && label.y + label.height <= 600);
  }
  assert.equal(overlapArea(labels[1], { x: 80, y: 180, width: 160, height: 24 }), 0);
});

test('scratch chooses an unobstructed corner and docks if every corner hides content', () => {
  const target = { x: 600, y: 350, width: 300, height: 200 };
  const options = {
    width: 1000,
    height: 700,
    panelWidth: 300,
    panelHeight: 250,
    target,
    avoid: [{ x: 0, y: 0, width: 1000, height: 100 }],
  };
  const placement = chooseScratchPlacement(options);
  assert.equal(overlapArea(placement, target), 0);
  assert.equal(placement.docked, false);
  assert.equal(
    chooseScratchPlacement({ ...options, avoid: [{ x: 0, y: 0, width: 1000, height: 700 }] })
      .docked,
    true,
  );
});

test('scratch can use space below a header instead of unnecessarily collapsing', () => {
  const result = chooseScratchPlacement({
    width: 800,
    height: 1280,
    panelWidth: 288,
    panelHeight: 360,
    target: { x: 80, y: 680, width: 500, height: 250 },
    avoid: [
      { x: 0, y: 0, width: 800, height: 35 },
      { x: 200, y: 1200, width: 400, height: 60 },
    ],
  });
  assert.equal(result.docked, false);
  assert.ok(result.y >= 47);
});

test('short measurements stay on one line and move inside the diagram without moving their baseline', () => {
  const { diagram } = scratchLayout(
    [
      {
        scratch: [
          { kind: 'text', role: 'label', x: 95, y: 48, text: '3厘米' },
          { kind: 'text', x: 97, y: 72, text: '6 cm' },
        ],
      },
    ],
    240,
  );
  assert.deepEqual(
    diagram.map((element) => element.lines),
    [['3厘米'], ['6 cm']],
  );
  assert.deepEqual(
    diagram.map((element) => element.y),
    [48, 72],
  );
  assert.ok(diagram[0].x < 95 && diagram[1].x < 97);
});

test('the condition legend stays in the control margin away from dense preceding text', () => {
  const [label] = placePageLabels(
    [{ kind: 'check', box: [0.223, 0.344, 0.047, 0.035] }],
    1000,
    750,
  );
  assert.equal(label.y, 128);
  assert.ok(label.x > 800);
  assert.equal(overlapArea(label, { x: 70, y: 210, width: 800, height: 85 }), 0);
});

test('legacy text-only teaching beats flow as separate readable steps instead of overlapping SVG coordinates', () => {
  const beats = [
    {
      scratch: [
        { kind: 'text', x: 8, y: 16, text: '先排除纸上的横线、数字和角弧，只看构成图形的' },
        { kind: 'text', x: 8, y: 32, text: '线段；尝试先固定A为顶点，按不同底边分类数。' },
      ],
    },
    { scratch: [{ kind: 'text', x: 10, y: 8, text: '只看图形线段，从A、B两个顶点分开数。' }] },
    { scratch: [{ kind: 'text', x: 10, y: 24, text: '现在先试着固定一个顶点。' }] },
  ];
  const layout = scratchLayout(beats, 240);
  assert.equal(layout.diagram.length, 0);
  assert.equal(layout.prose.length, 4);
  assert.deepEqual(
    layout.proseSteps.map((step) => step.beatIndex),
    [0, 1, 2],
  );
  assert.deepEqual(
    layout.proseSteps.map((step) => step.elements.length),
    [2, 1, 1],
  );
});

test('incremental diagrams keep their shapes but replace text in the same coordinate slot', () => {
  const layout = scratchLayout(
    [
      {
        scratch: [
          { kind: 'rect', x: 10, y: 20, w: 60, h: 30 },
          { kind: 'text', role: 'label', x: 65, y: 54, text: '6厘米' },
        ],
      },
      {
        scratch: [
          { kind: 'text', role: 'label', x: 65, y: 54, text: '8厘米' },
          { kind: 'arrow', x1: 10, y1: 65, x2: 70, y2: 65 },
        ],
      },
    ],
    240,
  );
  assert.equal(layout.diagram.filter((element) => element.kind === 'rect').length, 1);
  const labels = layout.diagram.filter((element) => element.kind === 'text');
  assert.deepEqual(
    labels.map((element) => element.text),
    ['8厘米'],
  );
  assert.equal(labels[0].beatIndex, 1);
  assert.equal(labels[0].x, 65);
  assert.ok(labels[0].y - layout.fontSize > 50);
  assert.equal(layout.diagram.at(-1).kind, 'arrow');
});

test('long legacy explanation becomes prose while short labels retain geometry coordinates', () => {
  const layout = scratchLayout(
    [
      {
        scratch: [
          { kind: 'line', x1: 10, y1: 30, x2: 80, y2: 30 },
          { kind: 'text', x: 8, y: 25, text: 'A' },
          { kind: 'text', x: 10, y: 8, text: '先观察这两条边的位置，再尝试说说它们有什么关系。' },
          { kind: 'text', role: 'prose', x: 10, y: 24, text: '自己试试。' },
        ],
      },
    ],
    240,
  );
  assert.deepEqual(
    layout.diagram
      .filter((element) => element.kind === 'text')
      .map(({ x, y, text }) => [x, y, text]),
    [[8, 25, 'A']],
  );
  assert.equal(layout.prose.length, 2);
});

test('one incremental diagram trims vertical blank space so its explanation and labels fit a tablet panel', () => {
  const layout = scratchLayout(
    [
      {
        scratch: [
          { kind: 'text', role: 'label', x: 20, y: 12, text: '示意图：比较两段一样长的边' },
          { kind: 'rect', x: 25, y: 25, w: 48, h: 48 },
          { kind: 'line', x1: 51, y1: 25, x2: 51, y2: 73 },
          { kind: 'text', role: 'label', x: 31, y: 44, text: '36' },
          { kind: 'text', role: 'label', x: 57, y: 40, text: '12' },
          { kind: 'text', role: 'label', x: 24, y: 82, text: '上面一段＝右边一段' },
        ],
      },
      { scratch: [] },
    ],
    318,
  );
  const [, top, , span] = layout.viewBox.split(' ').map(Number);
  assert.ok(top > 0 && top < 12 - layout.fontSize);
  assert.ok(top + span > 82);
  assert.ok(layout.height + 24 <= 287, 'Pure blank canvas must not create scroll overflow');
  assert.ok((layout.fontSize * 318) / 100 >= 16);
});

test('trimming diagram whitespace includes circle edges, arrow heads, and multiline text outside the old canvas', () => {
  const layout = scratchLayout(
    [
      {
        scratch: [
          { kind: 'circle', x: 30, y: 4, r: 10 },
          { kind: 'arrow', x1: 40, y1: 40, x2: 40, y2: 105 },
          { kind: 'text', role: 'label', x: 80, y: 90, text: '较长的图内说明文字' },
        ],
      },
    ],
    240,
  );
  const [, top, , span] = layout.viewBox.split(' ').map(Number);
  assert.ok(top < -6);
  assert.ok(top + span > 108.5);
  const label = layout.diagram.find((element) => element.kind === 'text');
  assert.ok(top + span > label.y + (label.lines.length - 1) * layout.fontSize * 1.3);
});

test('each written step retains its cumulative diagram and earlier labels for review', () => {
  const snapshots = scratchSnapshots(
    [
      {
        scratch: [
          { kind: 'rect', x: 10, y: 20, w: 50, h: 30 },
          { kind: 'text', role: 'label', x: 20, y: 15, text: '第一步关系' },
        ],
      },
      {
        scratch: [
          { kind: 'text', role: 'label', x: 20, y: 15, text: '第二步关系' },
          { kind: 'line', x1: 10, y1: 20, x2: 60, y2: 50 },
        ],
      },
      { scratch: [], reply_mode: 'writing' },
    ],
    240,
  );
  assert.equal(snapshots.length, 2);
  assert.deepEqual(
    snapshots.map((snapshot) => snapshot.beatIndex),
    [0, 1],
  );
  assert.deepEqual(
    snapshots.map(
      (snapshot) => snapshot.content.diagram.find((element) => element.kind === 'text').text,
    ),
    ['第一步关系', '第二步关系'],
  );
  assert.equal(snapshots[0].content.diagram.length, 2);
  assert.equal(snapshots[1].content.diagram.length, 3);
  assert.equal(snapshots[1].content.diagram[0].kind, 'rect');
});

test('waiting beats add no scratch pages and prose remains a continuous cumulative record', () => {
  const snapshots = scratchSnapshots(
    [
      { scratch: [{ kind: 'text', role: 'prose', x: 8, y: 16, text: '先找条件。' }] },
      { scratch: [] },
      { scratch: [{ kind: 'text', role: 'prose', x: 8, y: 16, text: '再自己试一步。' }] },
      { scratch: [] },
    ],
    240,
  );
  assert.equal(snapshots.length, 2);
  assert.deepEqual(
    snapshots[1].content.prose.map((element) => element.text),
    ['先找条件。', '再自己试一步。'],
  );
  assert.equal(snapshots[1].content.diagram.length, 0);
});

test('playback focus resolves to the corresponding saved step and null follows latest', () => {
  const snapshots = [{ beatIndex: 0 }, { beatIndex: 2 }, { beatIndex: 5 }];
  assert.equal(scratchPlaybackStep(snapshots, 0), 0);
  assert.equal(scratchPlaybackStep(snapshots, 1), 0);
  assert.equal(scratchPlaybackStep(snapshots, 2), 1);
  assert.equal(scratchPlaybackStep(snapshots, 4), 1);
  assert.equal(scratchPlaybackStep(snapshots, 5), null);
  assert.equal(scratchPlaybackStep(snapshots, 8), null);
  assert.equal(scratchPlaybackStep(snapshots, null), null);
  assert.equal(scratchPlaybackStep([], 0), null);
});

test('later point labels and an empty waiting beat do not erase an earlier condition underline', () => {
  const beats = [
    { page: [{ kind: 'underline', anchor_id: 'condition', box: [0.2, 0.2, 0.2, 0.03] }] },
    { page: [{ kind: 'label', anchor_id: 'point-a', box: [0.3, 0.4, 0.02, 0.02], text: 'A' }] },
    { page: [{ kind: 'label', anchor_id: 'point-b', box: [0.6, 0.4, 0.02, 0.02], text: 'B' }] },
    { page: [], scratch: [], reply_mode: 'writing' },
  ];
  const marks = currentPageMarks(beats);
  assert.equal(marks.length, 3);
  assert.equal(marks.find((mark) => mark.anchor_id === 'condition').kind, 'underline');
  assert.equal(marks.find((mark) => mark.anchor_id === 'condition').emphasized, false);
  assert.equal(marks.filter((mark) => mark.emphasized).length, 2);
  assert.deepEqual(
    currentPageMarks(beats, 2, 0).map((mark) => mark.anchor_id),
    ['condition'],
  );
  assert.deepEqual(
    currentPageMarks(beats, 2, 1).map((mark) => mark.anchor_id),
    ['condition', 'point-a'],
  );
});

test('reviewing an earlier teaching beat restores its own label without any future annotation', () => {
  const beats = [
    { page: [{ kind: 'label', anchor_id: 'point', box: [0.3, 0.4, 0.02, 0.02], text: 'A' }] },
    {
      page: [
        { kind: 'label', anchor_id: 'point', box: [0.3, 0.4, 0.02, 0.02], text: 'B' },
        { kind: 'line', anchor_id: 'segment', x1: 0.3, y1: 0.4, x2: 0.8, y2: 0.4 },
      ],
    },
  ];
  assert.deepEqual(
    currentPageMarks(beats)
      .filter((mark) => mark.kind === 'label')
      .map((mark) => mark.text),
    ['B'],
  );
  const first = currentPageMarks(beats, 2, 0);
  assert.deepEqual(
    first.map((mark) => mark.text),
    ['A'],
  );
  assert.equal(
    first.some((mark) => mark.kind === 'line'),
    false,
  );
});

test('anchored operation notes sit outside the question and known keywords with a leader', () => {
  const marks = [
    { kind: 'circle', anchor_id: 'amount', box: [0.4, 0.4, 0.08, 0.05] },
    { kind: 'underline', anchor_id: 'part', box: [0.55, 0.4, 0.08, 0.05] },
    { kind: 'note', anchor_id: 'amount', box: [0.4, 0.4, 0.08, 0.05], text: '36 ÷ 12 = 3' },
  ];
  const [note] = placePageLabels(marks, 1000, 750, { targetBox: [0.3, 0.3, 0.4, 0.4] });
  assert.equal(note.docked, undefined);
  assert.equal(note.text, '36 ÷ 12 = 3');
  assert.equal(overlapArea(note, { x: 300, y: 225, width: 400, height: 300 }), 0);
  assert.equal(overlapArea(note, { x: 400, y: 300, width: 80, height: 37.5 }), 0);
  assert.deepEqual([note.leader.x1, note.leader.y1], [440, 318.75]);
  assert.equal(pageMarkPath(marks.at(-1), 1000, 750), '');
});

test('notes on horizontal segment anchors avoid their zero-height line and retain a nearby connection', () => {
  const [note] = placePageLabels(
    [{ kind: 'note', anchor_id: 'edge-ab', box: [0.2, 0.4, 0.5, 0], text: 'AB = AC' }],
    800,
    600,
  );
  assert.equal(note.docked, undefined);
  assert.equal(note.leader.x1, 360);
  assert.equal(note.leader.y1, 240);
  assert.equal(overlapArea(note, { x: 155, y: 235, width: 410, height: 10 }), 0);
});

test('notes without reliable anchors or longer than the short-note contract are not placed arbitrarily', () => {
  assert.deepEqual(
    placePageLabels([{ kind: 'note', box: [0.2, 0.3, 0.1, 0.05], text: '36 ÷ 12' }], 800, 600),
    [],
  );
  assert.deepEqual(
    placePageLabels(
      [{ kind: 'note', anchor_id: 'a', box: [0.2, 0.3, 0.1, 0.05], text: '字'.repeat(61) }],
      800,
      600,
    ),
    [],
  );
});

test('crowded notes keep their full operation in a separate docked note area instead of covering keywords', () => {
  const text = '先比较相同边长，再按面积关系列出对应的短算式。';
  const [note] = placePageLabels(
    [{ kind: 'note', anchor_id: 'full', box: [0, 0, 1, 1], text }],
    320,
    200,
  );
  assert.equal(note.docked, true);
  assert.equal(note.text, text);
  assert.equal(note.x, undefined);
  const beats = [
    { page: [{ kind: 'note', anchor_id: 'a', box: [0.2, 0.2, 0.1, 0.1], text: 'x + 2' }] },
    { page: [{ kind: 'note', anchor_id: 'a', box: [0.2, 0.2, 0.1, 0.1], text: 'x + 3' }] },
  ];
  assert.deepEqual(
    currentPageMarks(beats).map((mark) => mark.text),
    ['x + 2', 'x + 3'],
  );
  assert.equal(currentPageMarks(beats, 2, 0)[0].text, 'x + 2');
});

test('a diagram caption clears intersecting rectangle edges without changing figure proportions or short labels', () => {
  const scratch = [
    { kind: 'text', role: 'label', x: 5, y: 56, text: '右上局部示意图' },
    { kind: 'rect', x: 40, y: 52, w: 12, h: 24 },
    { kind: 'line', x1: 40, y1: 64, x2: 52, y2: 64 },
    { kind: 'text', role: 'label', x: 56, y: 58, text: '一份' },
    { kind: 'text', role: 'label', x: 56, y: 71, text: '一份' },
  ];
  const original = structuredClone(scratch);
  for (const width of [240, 318]) {
    const layout = scratchLayout([{ scratch }], width);
    const caption = layout.diagram[0];
    assert.deepEqual(caption.lines, ['右上局部示意图']);
    assert.ok(caption.y + layout.fontSize * 0.2 < 52);
    assert.deepEqual(
      layout.diagram.slice(1, 3).map(({ beatIndex, localIndex, ...shape }) => shape),
      scratch.slice(1, 3),
    );
    assert.deepEqual(
      layout.diagram.slice(3).map(({ x, y }) => [x, y]),
      [
        [56, 58],
        [56, 71],
      ],
    );
    assert.ok((layout.fontSize * width) / 100 >= 16);
  }
  assert.deepEqual(scratch, original, 'Layout must not mutate source positions or old snapshots');
});

test('interior area labels remain inside shapes while text crossing a diagonal moves to nearby clear space', () => {
  const beats = [
    {
      scratch: [
        { kind: 'rect', x: 10, y: 20, w: 70, h: 60 },
        { kind: 'text', role: 'label', x: 20, y: 40, text: '36' },
        { kind: 'line', x1: 45, y1: 25, x2: 70, y2: 65 },
        { kind: 'text', role: 'label', x: 55, y: 45, text: 'AB' },
      ],
    },
  ];
  const layout = scratchLayout(beats, 300);
  const labels = layout.diagram.filter((element) => element.kind === 'text');
  assert.deepEqual([labels[0].x, labels[0].y], [20, 40]);
  assert.notDeepEqual([labels[1].x, labels[1].y], [55, 45]);
  const snapshots = scratchSnapshots(
    [...beats, { scratch: [{ kind: 'text', role: 'label', x: 55, y: 45, text: 'CD' }] }],
    300,
  );
  assert.deepEqual(snapshots[0].content, layout);
  assert.equal(snapshots[1].content.diagram.filter((element) => element.kind === 'line').length, 1);
  assert.equal(snapshots[0].content.diagram.at(-1).text, 'AB');
  assert.equal(snapshots[1].content.diagram.at(-1).text, 'CD');
});

test('small grid labels stay in their original cells and displaced captions keep an explicit cell anchor', () => {
  const scratch = [
    { kind: 'rect', x: 22, y: 30, w: 30, h: 18 },
    { kind: 'line', x1: 32, y1: 30, x2: 32, y2: 48 },
    { kind: 'line', x1: 42, y1: 30, x2: 42, y2: 48 },
    { kind: 'line', x1: 22, y1: 39, x2: 52, y2: 39 },
    { kind: 'text', role: 'label', text: '有', x: 23, y: 33 },
    { kind: 'text', role: 'label', text: '目标格', x: 33, y: 42 },
  ];
  const original = structuredClone(scratch);
  const content = scratchLayout([{ scratch }], 228);
  const small = content.diagram.find((element) => element.text === '有');
  assert.equal(small.centered, true);
  assert.ok(small.x > 22 && small.x < 32 && small.y > 30 && small.y < 39);
  assert.equal(small.leader, undefined, 'A readable in-cell label needs no detached callout');
  const caption = content.diagram.find((element) => element.text === '目标格');
  assert.deepEqual(caption.anchor, { x: 33, y: 42 });
  assert.deepEqual([caption.leader.x2, caption.leader.y2], [33, 42]);
  assert.ok(Math.hypot(caption.leader.x1 - 33, caption.leader.y1 - 42) > 0.5);
  assert.deepEqual(
    scratch,
    original,
    'Label placement never changes stored cells or old snapshots',
  );
  assert.deepEqual(
    content.diagram
      .filter((element) => element.kind !== 'text')
      .map(({ beatIndex, localIndex, ...shape }) => shape),
    scratch.filter((element) => element.kind !== 'text'),
  );
});

test('a displaced source caption connects to its nearby operation endpoint rather than an unrelated shape', () => {
  const content = scratchLayout(
    [
      {
        scratch: [
          { kind: 'rect', x: 50, y: 30, w: 20, h: 20 },
          { kind: 'arrow', x1: 24, y1: 41, x2: 56, y2: 44 },
          { kind: 'text', role: 'label', text: '来源：上层', x: 2, y: 40 },
        ],
      },
    ],
    228,
  );
  const label = content.diagram.find((element) => element.kind === 'text');
  assert.deepEqual(label.anchor, { x: 24, y: 41 });
  assert.deepEqual([label.leader.x2, label.leader.y2], [24, 41]);
  assert.deepEqual(
    content.diagram.find((element) => element.kind === 'arrow'),
    {
      kind: 'arrow',
      x1: 24,
      y1: 41,
      x2: 56,
      y2: 44,
      beatIndex: 0,
      localIndex: 1,
    },
  );
  const [, top, , height] = content.viewBox.split(' ').map(Number);
  assert.ok(top <= label.anchor.y && top + height >= label.anchor.y);
});

test('nearby source captions keep separate callouts and avoid obscuring earlier label text', () => {
  const content = scratchLayout(
    [
      {
        scratch: [
          { kind: 'rect', x: 34, y: 38, w: 28, h: 24 },
          { kind: 'arrow', x1: 24, y1: 41, x2: 44, y2: 48 },
          { kind: 'arrow', x1: 24, y1: 46, x2: 56, y2: 48 },
          { kind: 'text', role: 'label', text: '甲：较高来源', x: 2, y: 40 },
          { kind: 'text', role: 'label', text: '乙：较低来源', x: 2, y: 46 },
        ],
      },
    ],
    228,
  );
  const [first, second] = content.diagram.filter((element) => element.kind === 'text');
  assert.deepEqual(first.anchor, { x: 24, y: 41 });
  assert.deepEqual(second.anchor, { x: 24, y: 46 });
  assert.equal(first.leader.blocked, false);
  assert.equal(second.leader.blocked, false);
  assert.notDeepEqual(first.leader.points, second.leader.points);
  assert.deepEqual(second.leader.points.at(-1), second.anchor);
  assert.ok(second.leader.points.length >= 2);
});

test('original-page operation notes protect same-image local text boxes and keep schematic movement labels anchored', () => {
  const note = { kind: 'note', text: '2＋2＝4', anchor_id: 'region-a', box: [0.3, 0.4, 0.1, 0.1] };
  const protectedBoxes = [
    [0.2, 0.25, 0.5, 0.12],
    [0.2, 0.52, 0.5, 0.12],
  ];
  const labels = placePageLabels([note], 1000, 750, { protectedBoxes });
  for (const label of labels.filter((label) => !label.docked))
    for (const [x, y, width, height] of protectedBoxes)
      assert.equal(
        overlapArea(label, { x: x * 1000, y: y * 750, width: width * 1000, height: height * 750 }),
        0,
      );
  assert.ok(labels[0].leader || labels[0].docked);
  const move = { kind: 'label', purpose: 'move', text: '搬入示意', box: [0.3, 0.3, 0.3, 0.2] };
  const placed = placePageLabels([move], 1000, 750)[0];
  assert.ok(placed.leader);
  assert.equal(placed.text, '搬入示意');
});
