import assert from 'node:assert/strict';
import test from 'node:test';
import { paperMarkEdge, visiblePaperMarks } from '../src/paper-marks.js';

test('broad duplicate correct marks give way to precise answers', () => {
  const markers = [
    { id: 'line', box: [0.2, 0.2, 0.55, 0.1], status: 'looks_correct', stepIndex: null },
    { id: 'answer', box: [0.42, 0.22, 0.08, 0.04], status: 'looks_correct', stepIndex: null },
    { id: 'issue', box: [0.6, 0.24, 0.08, 0.04], status: 'check_again', stepIndex: null },
  ];
  assert.deepEqual(visiblePaperMarks(markers).map(({ id }) => id), ['answer', 'issue']);
});

test('a broad answer without a more precise mark remains visible', () => {
  const markers = [
    { id: 'single', box: [0.15, 0.12, 0.5, 0.07], status: 'looks_correct', stepIndex: null },
    { id: 'unlocated', box: null, status: 'unclear', stepIndex: null },
  ];
  assert.deepEqual(visiblePaperMarks(markers).map(({ id }) => id), ['single']);
});

test('a parent placed mark stays visible even when AI marks overlap it', () => {
  const markers = [
    { id: 'parent', box: [0.2, 0.2, 0.5, 0.08], status: 'looks_correct', reviewed: true },
    { id: 'ai', box: [0.4, 0.22, 0.06, 0.04], status: 'looks_correct' },
  ];
  assert.deepEqual(visiblePaperMarks(markers).map(({ id }) => id), ['parent', 'ai']);
});

test('marks near the paper edge place their ink inside the image', () => {
  assert.equal(paperMarkEdge([0.89, 0.9, 0.08, 0.08]), 'edge-right edge-bottom');
  assert.equal(paperMarkEdge([0.2, 0.2, 0.05, 0.04]), '');
});
