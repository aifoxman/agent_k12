import assert from 'node:assert/strict';
import test from 'node:test';
import { processOperationNotes } from '../src/teaching-layout.js';

test('one question displays an identical bare calculation once and keeps its latest source', () => {
  const first = { text: '1+6=7', anchor_id: 'order-condition', beatIndex: 2 };
  const latest = { text: '1+6=7', anchor_id: 'category-hint', beatIndex: 4 };
  const other = { text: '2+5=7', anchor_id: 'category-hint', beatIndex: 4 };
  const notes = [first, latest, other];
  const before = JSON.stringify(notes);
  assert.deepEqual(processOperationNotes(notes), [latest, other]);
  assert.equal(processOperationNotes(notes)[0], latest);
  assert.equal(
    JSON.stringify(notes),
    before,
    'The raw notes and their source references are immutable',
  );
});

test('different calculations and explanatory units or context are not merged', () => {
  const texts = ['1+6=7', '2+5=7', '1+6=7，检查', '面积：1+6=7', '1+6=7厘米', '共3种'];
  const notes = texts.flatMap((text, index) => [{ text, anchor_id: String(index) }]);
  assert.deepEqual(processOperationNotes(notes), notes);
  const contextual = [{ text: '检查：1+6=7' }, { text: '检查：1+6=7' }];
  assert.deepEqual(processOperationNotes(contextual), contextual);
});

test('reviewing a prefix never borrows a later source and another question has its own list', () => {
  const first = { text: '1+6=7', anchor_id: 'first', beatIndex: 2 };
  const next = { text: '1+6=7', anchor_id: 'next', beatIndex: 4 };
  assert.deepEqual(processOperationNotes([first]), [first]);
  assert.deepEqual(processOperationNotes([first, next]), [next]);
  assert.deepEqual(processOperationNotes([next]), [next], 'No cross-question deduplication cache');
});

test('formula spelling is not rewritten or equated and multiline prose remains intact', () => {
  const notes = ['1+6=7', '1 + 6 = 7', '1×6=6', '1*6=6', '1\n+6=7', '1+6=7；所以成立'].map(
    (text) => ({ text }),
  );
  assert.deepEqual(processOperationNotes(notes), notes);
});

test('bare numeric statements without an arithmetic equality are not generically deduplicated', () => {
  for (const text of ['7', '7=7', 'x+6=7', '第1步', '可以重复']) {
    const notes = [{ text }, { text }];
    assert.deepEqual(processOperationNotes(notes), notes);
  }
});
