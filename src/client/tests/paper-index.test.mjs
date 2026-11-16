import test from 'node:test';
import assert from 'node:assert/strict';
import { selectQuestion, bindPaperIndex } from '../src/paper-index.js';
import {
  IDENTITY,
  fitHomography,
  pageAgreement,
  project,
  projectBox,
  registerPaper,
} from '../src/paper-registration.js';

const question = (id, box, answer_regions = []) => ({
  id,
  box,
  answer_regions,
  read_confidence: 0.96,
  progress: 'blank',
  label: id,
});

test('selection uses length in stem and answer area rather than the largest region', () => {
  const questions = [
    question('first', [0.1, 0.1, 0.7, 0.15], [[0.1, 0.25, 0.7, 0.1]]),
    question('second', [0.1, 0.4, 0.7, 0.4]),
  ];
  assert.equal(
    selectQuestion(questions, [
      [
        [0.2, 0.3],
        [0.6, 0.3],
      ],
    ]).question.id,
    'first',
  );
  assert.equal(
    selectQuestion(questions, [
      [
        [0.2, 0.45],
        [0.6, 0.45],
      ],
    ]).question.id,
    'second',
  );
});

test('cross-question strokes and overlapping parent regions require an explicit choice', () => {
  const questions = [
    question('first', [0.1, 0.1, 0.7, 0.2]),
    question('second', [0.1, 0.3, 0.7, 0.2]),
  ];
  const ambiguous = selectQuestion(questions, [
    [
      [0.4, 0.15],
      [0.4, 0.45],
    ],
  ]);
  assert.equal(ambiguous.question, null);
  assert.deepEqual(ambiguous.candidates.map((q) => q.id).sort(), ['first', 'second']);
  assert.equal(
    selectQuestion(
      [question('parent', [0, 0, 1, 1]), questions[0]],
      [
        [
          [0.2, 0.2],
          [0.6, 0.2],
        ],
      ],
    ).question,
    null,
  );
  assert.equal(
    selectQuestion(questions, [
      [
        [0.85, 0.6],
        [0.9, 0.7],
      ],
    ]).question,
    null,
  );
});

function page(seed = 13) {
  const width = 400,
    height = 480,
    data = new Uint8Array(width * height).fill(245);
  const random = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed;
  };
  for (let y = 35; y < height - 35; y += 29)
    for (let x = 35; x < width - 35; x += 31) {
      const w = 7 + (random() % 13),
        h = 9 + (random() % 12);
      for (let dy = 0; dy < h; dy++)
        for (let dx = 0; dx < w; dx++)
          if (dy < 3 || dx < 3 || (dy > h - 4 && dx < w / 2)) data[(y + dy) * width + x + dx] = 25;
    }
  return { width, height, data };
}

test('stationary identity is pixel checked and flat paper or a different page is rejected', () => {
  const first = page(),
    changed = page(987123);
  assert.ok(pageAgreement(first, first, IDENTITY).reliable);
  assert.equal(pageAgreement(first, changed, IDENTITY).reliable, false);
  const flat = { ...first, data: new Uint8Array(first.data.length).fill(245) };
  assert.equal(registerPaper(flat, flat), null);
  const bound = bindPaperIndex(
    {
      pixels: first,
      page_id: 'p',
      frame_sha256: 'sha',
      questions: [question('one', [0.1, 0.1, 0.7, 0.7])],
    },
    first,
    [
      [
        [0.2, 0.3],
        [0.6, 0.3],
      ],
    ],
  );
  assert.equal(bound.question.id, 'one');
  assert.deepEqual(bound.geometry.matrix, IDENTITY);
});

test('local features recover a moved page; keyword positions follow the same transform', () => {
  const first = page(),
    moved = { ...first, data: new Uint8Array(first.data.length).fill(245) };
  for (let y = 0; y < first.height - 10; y++)
    for (let x = 0; x < first.width - 8; x++)
      moved.data[(y + 10) * first.width + x + 8] = first.data[y * first.width + x];
  const found = registerPaper(first, moved);
  assert.ok(found, 'a small translation should be recovered without a model');
  assert.equal(found.method, 'registered');
  const [x, y] = project(found.matrix, 0.5, 0.5);
  assert.ok(Math.abs(x - (0.5 + 8 / 399)) < 0.008);
  assert.ok(Math.abs(y - (0.5 + 10 / 479)) < 0.008);
  const transformed = projectBox(found.matrix, [0.2, 0.2, 0.1, 0.04]);
  assert.ok(transformed[0] > 0.21 && transformed[1] > 0.21);
});

test('homography preserves rotation and perspective and rejects degenerate samples', () => {
  const expected = [0.9, -0.1, 0.1, 0.08, 0.92, 0.02, 0.04, -0.02, 1];
  const source = [
    [0.1, 0.1],
    [0.8, 0.1],
    [0.8, 0.8],
    [0.1, 0.8],
    [0.5, 0.4],
    [0.3, 0.7],
  ];
  const fit = fitHomography(source.map((p) => [p, project(expected, ...p)]));
  for (let i = 0; i < 9; i++) assert.ok(Math.abs(fit[i] - expected[i]) < 1e-6);
  assert.equal(
    fitHomography(
      Array.from({ length: 4 }, () => [
        [0.1, 0.1],
        [0.2, 0.2],
      ]),
    ),
    null,
  );
  assert.equal(projectBox([1, 0, 0.5, 0, 1, 0, 0, 0, 1], [0.6, 0.1, 0.2, 0.2]), null);
});
