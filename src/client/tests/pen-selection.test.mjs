import assert from 'node:assert/strict';
import test from 'node:test';
import {
  createPenHintScheduler,
  penSelectionBounds,
  penSelectionReady,
} from '../src/pen-selection.js';

test('freehand strokes produce one padded crop without forcing a square', () => {
  assert.deepEqual(
    penSelectionBounds([
      [
        [0.2, 0.3],
        [0.4, 0.32],
        [0.35, 0.5],
      ],
    ]).map((value) => Number(value.toFixed(3))),
    [0.188, 0.288, 0.224, 0.224],
  );
  assert.deepEqual(
    penSelectionBounds([
      [
        [0.1, 0.1],
        [0.2, 0.12],
      ],
      [
        [0.6, 0.7],
        [0.7, 0.8],
      ],
    ]).map((value) => Number(value.toFixed(3))),
    [0.088, 0.088, 0.624, 0.724],
  );
});

test('an unfinished tap is not a selected region', () => {
  assert.equal(penSelectionBounds([]), null);
  assert.equal(penSelectionBounds([[[0.4, 0.5]]]), null);
});

test('a horizontal underline and several short marks can request a hint', () => {
  assert.equal(penSelectionReady([[[0.2, 0.51], [0.55, 0.51]]]), true);
  assert.equal(penSelectionReady([[[0.2, 0.51], [0.22, 0.51]], [[0.23, 0.5], [0.27, 0.5]]]), true);
  assert.equal(penSelectionReady([[[0.2, 0.51], [0.21, 0.51]]]), false);
});

test('completed strokes request one hint after a pause; edits and cancel discard older marks', () => {
  const timers = new Map();
  let next = 0;
  const clock = {
    setTimeout(callback) {
      timers.set(++next, callback);
      return next;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  };
  const requests = [];
  const scheduler = createPenHintScheduler((marks, revision) => requests.push([marks, revision]), 1200, clock);
  const first = [[[0.2, 0.5], [0.6, 0.5]]];
  scheduler.change();
  scheduler.finish(first);
  scheduler.change();
  scheduler.finish([...first, [[0.7, 0.5], [0.8, 0.5]]]);
  assert.equal(timers.size, 1);
  const [pendingId, pending] = [...timers.entries()][0];
  timers.delete(pendingId);
  pending();
  assert.equal(requests.length, 1);
  assert.equal(requests[0][0].length, 2);
  scheduler.finish(first);
  scheduler.cancel();
  assert.equal(timers.size, 0);
});
