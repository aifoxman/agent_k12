import test from 'node:test';
import assert from 'node:assert/strict';

import { needsSpokenReply, shouldProbeHintAttempt } from '../src/hint-followup.js';

test('quiet paper checks wait for a stable interval and stop after visible work', () => {
  const now = 2_000_000;
  const snapshot = {
    createdAt: now - 90_000,
    lastProbeAt: 0,
    probeCount: 0,
    observed: false,
  };
  assert.equal(shouldProbeHintAttempt(snapshot, now), true);
  snapshot.lastProbeAt = now - 30_000;
  snapshot.probeCount = 1;
  assert.equal(shouldProbeHintAttempt(snapshot, now), false);
  snapshot.lastProbeAt = now - 70_000;
  assert.equal(shouldProbeHintAttempt(snapshot, now, true), false);
  assert.equal(shouldProbeHintAttempt(snapshot, now), true);
  snapshot.observed = true;
  assert.equal(shouldProbeHintAttempt(snapshot, now), false);
});

test('old hints and repeated uncertain comparisons fall back to ordinary observation', () => {
  const now = 2_000_000;
  assert.equal(shouldProbeHintAttempt(null, now), false);
  assert.equal(shouldProbeHintAttempt({ createdAt: now - 600_000, lastProbeAt: 0, probeCount: 0 }, now), false);
  assert.equal(shouldProbeHintAttempt({ createdAt: now - 90_000, lastProbeAt: 0, probeCount: 3 }, now), false);
});

test('only an explicit teacher question opens an automatic reply window', () => {
  assert.equal(needsSpokenReply('先找出题目给的条件，你发现了什么？'), true);
  assert.equal(needsSpokenReply('先试着在纸上圈出关键字。'), false);
});
